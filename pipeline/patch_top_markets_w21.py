"""patch_top_markets_w21.py — derive W21 top markets from the delta CSV only.

Replaces the full-master-scan in aggregate_top_markets.py for the current
partial week. Writes top_markets_latest.json for W21 so fetch_market_snapshot
and build_briefings pick up current markets without a 335 GB scan.
"""
from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pandas as pd
import requests

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import utc_now, write_json
from config import DATA_OUT
from categorize import categorize as _categorize_question

DELTA   = Path("H:/Research/10. Prediction/data/blockchain/processed_trades_delta_20260522.csv")
MARKETS = Path("H:/Research/10. Prediction/data/polymarket_markets.csv")
GAMMA   = "https://gamma-api.polymarket.com/markets/{mid}"
POLY_URL = "https://polymarket.com/event/{slug}"

W21_WEEK  = "2026-W21"
W21_START = "2026-05-18"
W21_THRU  = "2026-05-24"
TOP_K     = 30


def fetch_gamma(market_id: str) -> dict | None:
    try:
        r = requests.get(GAMMA.format(mid=market_id), timeout=10)
        if r.status_code != 200:
            return None
        d = r.json()
        q = d.get("question")
        if not q:
            return None
        slug = d.get("conditionId") or d.get("slug") or market_id
        return {
            "question": q,
            "category": d.get("category"),
            "slug": slug,
        }
    except Exception:
        return None


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] W21 top markets from delta CSV ...")

    con = duckdb.connect(":memory:")
    con.execute("PRAGMA memory_limit='8GB'")
    con.execute("PRAGMA threads=8")

    print(f"[{time.strftime('%H:%M:%S')}] Aggregating W21 volume by market ...")
    vol = con.execute(
        f"SELECT market_id, "
        f"       COUNT(*) AS n_trades, "
        f"       SUM(CAST(usdc_amount AS DOUBLE)) AS volume_usd "
        f"FROM read_csv_auto('{DELTA.as_posix()}', all_varchar=TRUE) "
        f"WHERE CAST(date AS DATE) >= '{W21_START}' "
        f"  AND CAST(date AS DATE) <= '{W21_THRU}' "
        f"  AND market_id IS NOT NULL AND market_id != '' "
        f"GROUP BY market_id "
        f"ORDER BY volume_usd DESC "
        f"LIMIT {TOP_K}"
    ).fetchdf()
    con.close()

    print(f"  Got {len(vol)} markets. Top vol: ${vol['volume_usd'].iloc[0]:,.0f}", flush=True)

    # Load market metadata snapshot
    markets_df = pd.read_csv(MARKETS, dtype=str) if MARKETS.exists() else pd.DataFrame()
    if not markets_df.empty and "market_id" in markets_df.columns:
        markets_df["market_id"] = markets_df["market_id"].astype(str)
        meta = markets_df.set_index("market_id")[["question", "category"]].to_dict("index")
    else:
        meta = {}

    records = []
    for rank, row in enumerate(vol.itertuples(), 1):
        mid = str(row.market_id)
        q_info = meta.get(mid)
        if not q_info:
            print(f"  [{rank}] Fetching Gamma for {mid} ...", flush=True)
            q_info = fetch_gamma(mid)
        if not q_info:
            q_info = {"question": f"Market {mid}", "category": None, "slug": mid}

        question = q_info.get("question", f"Market {mid}")
        category = q_info.get("category") or _categorize_question(question)
        slug = q_info.get("slug", mid)
        market_url = f"https://polymarket.com/event/{slug}"

        records.append({
            "rank":          rank,
            "market_id":     mid,
            "question":      question,
            "category":      category,
            "market_url":    market_url,
            "usd_volume":    float(row.volume_usd),
            "n_trades":      int(row.n_trades),
            "week":          W21_WEEK,
        })
        print(f"  [{rank}] {question[:60]} — ${row.volume_usd/1e6:.2f}M", flush=True)

    payload = {
        "as_of_week":    W21_WEEK,
        "as_of_date":    W21_THRU,
        "n_markets":     len(records),
        "markets":       records,
        "generated_at":  utc_now(),
        "note":          f"Derived from delta CSV through {W21_THRU}; full-history sort pending next heavy scan.",
    }
    write_json(DATA_OUT / "top_markets_latest.json", payload)

    # Append to top_markets_history.csv
    hist_path = DATA_OUT / "top_markets_history.csv"
    new_rows = pd.DataFrame(records)
    if hist_path.exists():
        hist = pd.read_csv(hist_path, dtype=str)
        hist = hist[hist["week"] != W21_WEEK]
        hist = pd.concat([hist, new_rows.astype(str)], ignore_index=True)
    else:
        hist = new_rows.astype(str)
    hist.to_csv(hist_path, index=False)

    print(f"Done in {time.time()-t0:.1f}s — top market: {records[0]['question'][:60]}")


if __name__ == "__main__":
    main()
