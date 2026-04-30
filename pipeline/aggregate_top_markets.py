"""Top markets by weekly USD volume — most-popular-markets card for the landing page.

Streams processed_trades.csv via duckdb, aggregates USD volume by (week, market_id),
takes the top 5 per week, joins the market question from polymarket_markets.csv.

Outputs:
  top_markets_history.csv   — top 5 per week for the full history (long format)
  top_markets_latest.json   — top 5 for the most recent complete week (IndexCard + landing page)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb
import pandas as pd
import requests

from common import utc_now, write_json
from config import DATA_OUT
from categorize import categorize as _categorize_question

GAMMA_MARKET_ENDPOINT = "https://gamma-api.polymarket.com/markets/{mid}"
GAMMA_TIMEOUT = 10


def fetch_missing_question(market_id: str) -> dict | None:
    """Fetch question/category for a single market from Gamma API.
    Returns {question, category} or None on failure."""
    try:
        r = requests.get(GAMMA_MARKET_ENDPOINT.format(mid=market_id), timeout=GAMMA_TIMEOUT)
        if r.status_code != 200:
            return None
        d = r.json()
        q = d.get("question")
        if not q:
            return None
        return {"question": q, "category": d.get("category")}
    except Exception:
        return None

TRADES = "J:/Research/10. Prediction/data/blockchain/processed_trades.csv"
MARKETS = "J:/Research/10. Prediction/data/polymarket_markets.csv"
N_WEEKS_HISTORY = 12  # limit history written to JSON to keep payload small
TOP_K = 5


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Aggregating top markets by weekly USD volume ...")

    con = duckdb.connect(":memory:")
    con.execute("PRAGMA memory_limit='16GB'")
    con.execute("PRAGMA threads=8")

    # Weekly USD volume per market. usdc_amount is in dollars.
    print(f"[{time.strftime('%H:%M:%S')}] Streaming processed_trades.csv (one full pass) ...")
    con.execute(f"""
        CREATE TEMP TABLE weekly_market_vol AS
        SELECT
            strftime(CAST(date AS DATE), '%G-W%V') AS week,
            market_id,
            SUM(CAST(usdc_amount AS DOUBLE)) AS usd_volume,
            COUNT(*) AS n_trades
        FROM read_csv_auto('{TRADES}', all_varchar=TRUE, parallel=TRUE)
        WHERE market_id IS NOT NULL AND market_id != ''
        GROUP BY 1, 2
    """)

    # Partial-week filter: drop latest week if its total volume is < 30% of prior 4wk median
    weekly_total = con.execute("""
        SELECT week, SUM(usd_volume) AS week_vol
        FROM weekly_market_vol
        GROUP BY week ORDER BY week
    """).fetchdf()
    weekly_total["med4"] = weekly_total["week_vol"].rolling(4, min_periods=1).median().shift(1)
    partial = weekly_total[
        (weekly_total["med4"].notna()) &
        (weekly_total["week_vol"] < 0.3 * weekly_total["med4"])
    ]
    drop_weeks = set(partial["week"])
    if drop_weeks:
        for w in sorted(drop_weeks):
            print(f"  Dropped partial week: {w}")

    # Top-K per week with rank
    con.execute("""
        CREATE TEMP TABLE top_per_week AS
        SELECT week, market_id, usd_volume, n_trades, rnk
        FROM (
            SELECT week, market_id, usd_volume, n_trades,
                   ROW_NUMBER() OVER (PARTITION BY week ORDER BY usd_volume DESC) AS rnk
            FROM weekly_market_vol
        )
        WHERE rnk <= ?
    """, [TOP_K])

    # Join market question — dedup on market_id since the static CSV has duplicates
    print(f"[{time.strftime('%H:%M:%S')}] Joining market questions ...")
    con.execute(f"""
        CREATE TEMP TABLE markets AS
        SELECT market_id, question, category FROM (
            SELECT CAST(market_id AS VARCHAR) AS market_id,
                   question,
                   category,
                   ROW_NUMBER() OVER (PARTITION BY CAST(market_id AS VARCHAR)
                                      ORDER BY CASE WHEN question IS NULL THEN 1 ELSE 0 END) AS rn
            FROM read_csv_auto('{MARKETS}', all_varchar=TRUE)
        ) WHERE rn = 1
    """)

    top_full = con.execute("""
        SELECT t.week, t.rnk, t.market_id, t.usd_volume, t.n_trades,
               m.question, m.category
        FROM top_per_week t
        LEFT JOIN markets m ON t.market_id = m.market_id
        ORDER BY t.week, t.rnk
    """).fetchdf()

    # Drop partial weeks from history
    if drop_weeks:
        top_full = top_full[~top_full["week"].isin(drop_weeks)].reset_index(drop=True)

    # Full history CSV
    history_csv = DATA_OUT / "top_markets_history.csv"
    top_full.to_csv(history_csv, index=False)
    n_weeks = top_full["week"].nunique()
    print(f"  wrote {len(top_full)} rows across {n_weeks} weeks -> {history_csv.name}")

    # Latest week payload (most recent full week)
    latest_week = top_full["week"].max()
    latest = top_full[top_full["week"] == latest_week].copy().sort_values("rnk")

    markets_list = []
    for _, r in latest.iterrows():
        question = r["question"] if pd.notna(r.get("question")) else None
        category = r["category"] if pd.notna(r.get("category")) else None
        # Fallback: fetch from Gamma for markets created after the static snapshot
        if question is None:
            fetched = fetch_missing_question(str(r["market_id"]))
            if fetched:
                question = fetched["question"]
                category = fetched["category"]
                print(f"  Fetched question for market {r['market_id']} via Gamma API")
        # If category is still missing (Polymarket+Gamma both null), classify
        # heuristically from the question text (categorize.py).
        if not category and question:
            category = _categorize_question(question)
        markets_list.append({
            "rank": int(r["rnk"]),
            "market_id": str(r["market_id"]),
            "question": question,
            "category": category,
            "usd_volume": float(r["usd_volume"]),
            "n_trades": int(r["n_trades"]),
        })

    payload = {
        "index_name": "Top Markets by Weekly Volume",
        "short_name": "TopMarkets",
        "as_of_week": latest_week,
        "top_k": TOP_K,
        "markets": markets_list,
        "generated_at": utc_now(),
        "source": TRADES,
        "notes": (
            "Top 5 resolved-trade markets by USD volume in the most recent complete week. "
            "Market questions come from polymarket_markets.csv (static snapshot); new markets "
            "not in that file will appear with question=null."
        ),
    }
    write_json(DATA_OUT / "top_markets_latest.json", payload)

    # Short-history payload for a sparkline / mini-chart (last N_WEEKS_HISTORY weeks)
    recent_weeks = sorted(top_full["week"].unique())[-N_WEEKS_HISTORY:]
    short = top_full[top_full["week"].isin(recent_weeks)].copy()
    short_payload = []
    for wk, grp in short.groupby("week"):
        short_payload.append({
            "week": wk,
            "top": [
                {
                    "rank": int(r["rnk"]),
                    "market_id": str(r["market_id"]),
                    "question": (r["question"] if pd.notna(r.get("question")) else None),
                    "usd_volume": float(r["usd_volume"]),
                }
                for _, r in grp.sort_values("rnk").iterrows()
            ],
        })
    write_json(DATA_OUT / "top_markets_timeseries.json", short_payload)

    con.close()
    print(f"TopMarkets: latest={latest_week}, leader ${latest.iloc[0]['usd_volume']:,.0f} "
          f"({(time.time()-t0)/60:.1f} min)")


if __name__ == "__main__":
    main()
