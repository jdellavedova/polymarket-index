"""aggregate_market_microstructure.py — per-market microstructure for the top
weekly markets. Powers the per-card annotations on the Briefings page.

For each of the top-K markets in the most recent complete week, computes:
  - bot_share_participation: fraction of (maker+taker) participations attributed
    to algorithmic wallets
  - flagged_wallets_active: count of distinct insider-flagged wallets that
    traded the market this week (either side)
  - avg_price_by_type: volume-weighted average entry price by wallet type
  - n_participations_by_type: participation count per wallet type

Outputs market_microstructure_latest.json. Runs after aggregate_top_markets.py
in run_all.py.

Implementation: a single DuckDB pass filtered to (latest_week_date_range AND
market_id IN top_K). On the master CSV this is ~5-10 min; with a delta CSV
present (newer than master), prefers the delta for speed.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from common import utc_now, write_json
from config import DATA_OUT

TRADES = "J:/Research/10. Prediction/data/blockchain/processed_trades.csv"
WALLETS = "J:/Research/10. Prediction/data/blockchain/wallet_statistics.csv"
FLAGGED = "G:/My Drive/1. Research/1. Polymarket/2. Insider/output/stage19_significant_wallets.csv"

WALLET_TYPE_LABELS = {
    "bot": "Algorithmic",
    "active_retail": "Active Retail",
    "sophisticated": "Sophisticated",
    "casual": "Casual",
    "one_shot": "One-Shot",
}


def _iso_week_to_dates(iso_week: str) -> tuple[str, str]:
    """ISO week string '2026-W18' -> (Monday, next Monday) as 'YYYY-MM-DD'."""
    year_s, wk_s = iso_week.split("-W")
    year = int(year_s)
    week = int(wk_s)
    monday = datetime.fromisocalendar(year, week, 1).date()
    next_monday = monday + timedelta(days=7)
    return monday.isoformat(), next_monday.isoformat()


def main() -> None:
    t0 = time.time()
    top = json.loads((DATA_OUT / "top_markets_latest.json").read_text(encoding="utf-8"))
    week = top["as_of_week"]
    top_market_ids = [str(m["market_id"]) for m in top["markets"]]
    if not top_market_ids:
        print("No top markets available; skipping microstructure aggregation.")
        return

    week_start, week_end = _iso_week_to_dates(week)
    in_list = ",".join(f"'{mid}'" for mid in top_market_ids)
    print(f"[{time.strftime('%H:%M:%S')}] Microstructure: scanning {len(top_market_ids)} markets for week {week} ({week_start} -> {week_end}) ...")

    con = duckdb.connect(":memory:")
    con.execute("PRAGMA memory_limit='12GB'")
    con.execute("PRAGMA threads=8")

    # Wallet type lookup. wallet_type is one of {bot, sophisticated, active_retail,
    # casual, one_shot}. Lowercase the address to match processed_trades.
    con.execute(f"""
        CREATE TEMP TABLE wallet_type AS
        SELECT LOWER(wallet) AS wallet, wallet_type
        FROM read_csv_auto('{WALLETS}', all_varchar=TRUE)
        WHERE wallet IS NOT NULL AND wallet_type IS NOT NULL
    """)

    # Flagged wallets (significant_01 == 1)
    flagged_df = pd.read_csv(FLAGGED, usecols=["wallet", "significant_01"])
    flagged_df = flagged_df[flagged_df["significant_01"] == 1]
    flagged_set = set(flagged_df["wallet"].str.lower())
    con.register("flagged_wallets", pd.DataFrame({"wallet": list(flagged_set)}))
    print(f"  loaded {len(flagged_set):,} flagged wallets")

    # Latest-week trades for the top markets only. Filter pushdown narrows the
    # working set to the top-K markets in this week. Keep token_id so we can
    # compute execution-edge per token (binary markets have a YES and a NO
    # token; comparing prices across them mixes side-selection with execution).
    con.execute(f"""
        CREATE TEMP TABLE w_trades AS
        SELECT
            market_id,
            token_id,
            LOWER(maker_address) AS maker,
            LOWER(taker_address) AS taker,
            CAST(price AS DOUBLE) AS price,
            CAST(usdc_amount AS DOUBLE) AS usdc
        FROM read_csv_auto('{TRADES}', all_varchar=TRUE, parallel=TRUE)
        WHERE market_id IN ({in_list})
          AND CAST(date AS DATE) >= DATE '{week_start}'
          AND CAST(date AS DATE) < DATE '{week_end}'
    """)
    n_rows = con.execute("SELECT COUNT(*) FROM w_trades").fetchone()[0]
    print(f"  scanned to {n_rows:,} trades for latest-week top markets ({(time.time()-t0)/60:.1f} min)")

    # Maker + taker side, mirrored. Each match contributes 2 rows: one for the
    # maker's wallet_type and one for the taker's. Volume on each side is the
    # full usdc_amount of the match (so vw avg price weights by full match volume).
    con.execute("""
        CREATE TEMP TABLE participations AS
        SELECT t.market_id, t.token_id, COALESCE(wm.wallet_type, 'unknown') AS wallet_type,
               t.price, t.usdc, t.maker AS wallet
        FROM w_trades t
        LEFT JOIN wallet_type wm ON t.maker = wm.wallet
        UNION ALL
        SELECT t.market_id, t.token_id, COALESCE(wt.wallet_type, 'unknown') AS wallet_type,
               t.price, t.usdc, t.taker AS wallet
        FROM w_trades t
        LEFT JOIN wallet_type wt ON t.taker = wt.wallet
    """)

    # Per-market, per-wallet-type participation counts and volume (token-agnostic;
    # used for bot share and total-volume context).
    by_type = con.execute("""
        SELECT
            market_id,
            wallet_type,
            COUNT(*) AS n_participations,
            SUM(usdc) AS volume_usd
        FROM participations
        GROUP BY market_id, wallet_type
    """).fetchdf()

    # Per-market, per-token, per-wallet-type avg price (correct execution-edge
    # benchmark: comparing bot and retail prices on the SAME token.)
    by_token_type = con.execute("""
        SELECT
            market_id,
            token_id,
            wallet_type,
            COUNT(*) AS n_participations,
            SUM(usdc) AS volume_usd,
            SUM(price * usdc) / NULLIF(SUM(usdc), 0) AS avg_price_vw
        FROM participations
        GROUP BY market_id, token_id, wallet_type
    """).fetchdf()

    # Per-market totals + bot share
    totals = by_type.groupby("market_id").agg(
        total_participations=("n_participations", "sum"),
        total_volume=("volume_usd", "sum"),
    ).reset_index()
    bot_part = (
        by_type[by_type["wallet_type"] == "bot"]
        .groupby("market_id")["n_participations"].sum()
        .reset_index().rename(columns={"n_participations": "bot_participations"})
    )
    totals = totals.merge(bot_part, on="market_id", how="left").fillna({"bot_participations": 0})
    totals["bot_share_participation"] = totals["bot_participations"] / totals["total_participations"]

    # Flagged-wallet activity per market
    flagged_per_market = con.execute("""
        SELECT t.market_id, COUNT(DISTINCT wallet) AS flagged_count
        FROM (
            SELECT market_id, maker AS wallet FROM w_trades
            UNION
            SELECT market_id, taker AS wallet FROM w_trades
        ) t
        WHERE t.wallet IN (SELECT wallet FROM flagged_wallets)
        GROUP BY t.market_id
    """).fetchdf()

    # Per-market execution gap: for each token where BOTH bots and active_retail
    # have non-trivial volume (>=$1K each), compute retail_avg - bot_avg, then
    # volume-weight that gap across tokens by total bot+retail volume on the token.
    # This isolates execution edge (price paid for the same exposure) from
    # side-selection (which token each type chose to trade).
    pivot = by_token_type.pivot_table(
        index=["market_id", "token_id"],
        columns="wallet_type",
        values=["avg_price_vw", "volume_usd"],
        aggfunc="first",
    )
    pivot.columns = ["__".join(map(str, c)) for c in pivot.columns]
    pivot = pivot.reset_index()
    if "avg_price_vw__bot" not in pivot.columns: pivot["avg_price_vw__bot"] = pd.NA
    if "avg_price_vw__active_retail" not in pivot.columns: pivot["avg_price_vw__active_retail"] = pd.NA
    if "volume_usd__bot" not in pivot.columns: pivot["volume_usd__bot"] = 0
    if "volume_usd__active_retail" not in pivot.columns: pivot["volume_usd__active_retail"] = 0

    pivot["bot_vol"] = pd.to_numeric(pivot["volume_usd__bot"], errors="coerce").fillna(0)
    pivot["ret_vol"] = pd.to_numeric(pivot["volume_usd__active_retail"], errors="coerce").fillna(0)
    pivot["both_present"] = (pivot["bot_vol"] >= 1000) & (pivot["ret_vol"] >= 1000)
    pivot["bot_p"] = pd.to_numeric(pivot["avg_price_vw__bot"], errors="coerce")
    pivot["ret_p"] = pd.to_numeric(pivot["avg_price_vw__active_retail"], errors="coerce")
    pivot["gap"] = pivot["ret_p"] - pivot["bot_p"]
    pivot["weight"] = pivot["bot_vol"] + pivot["ret_vol"]

    def _market_exec_gap(mid: str) -> tuple[float | None, float | None, float | None, int]:
        """Returns (gap_in_dollars, bot_avg, retail_avg, n_tokens_compared).
        gap is volume-weighted across tokens where both types have >=$1K vol."""
        sub = pivot[(pivot["market_id"] == mid) & pivot["both_present"]]
        if sub.empty:
            return None, None, None, 0
        w = sub["weight"].sum()
        if w <= 0:
            return None, None, None, 0
        gap = float((sub["gap"] * sub["weight"]).sum() / w)
        bot_avg = float((sub["bot_p"] * sub["weight"]).sum() / w)
        ret_avg = float((sub["ret_p"] * sub["weight"]).sum() / w)
        return gap, bot_avg, ret_avg, int(len(sub))

    # Assemble per-market payload
    markets_out = []
    for m in top["markets"]:
        mid = str(m["market_id"])
        t_row = totals[totals["market_id"] == mid]
        f_row = flagged_per_market[flagged_per_market["market_id"] == mid]
        bt = by_type[by_type["market_id"] == mid].copy()
        n_part_by_type = {str(r["wallet_type"]): int(r["n_participations"]) for _, r in bt.iterrows()}
        gap, bot_avg, ret_avg, n_tok = _market_exec_gap(mid)

        markets_out.append({
            "market_id": mid,
            "rank": m["rank"],
            "question": m.get("question"),
            "category": m.get("category"),
            "n_trades": m["n_trades"],
            "usd_volume": m["usd_volume"],
            "bot_share_participation": float(t_row["bot_share_participation"].iloc[0]) if not t_row.empty else None,
            "flagged_wallets_active": int(f_row["flagged_count"].iloc[0]) if not f_row.empty else 0,
            "execution_gap_retail_minus_bot": gap,
            "avg_price_bot": bot_avg,
            "avg_price_retail": ret_avg,
            "n_tokens_compared": n_tok,
            "n_participations_by_type": n_part_by_type,
        })

    payload = {
        "as_of_week": week,
        "generated_at": utc_now(),
        "markets": markets_out,
        "n_flagged_wallets_universe": len(flagged_set),
        "wallet_type_labels": WALLET_TYPE_LABELS,
        "notes": (
            "Per-market microstructure for the top weekly markets. bot_share is "
            "share of (maker+taker) participations; avg_price is volume-weighted "
            "across both sides. flagged_wallets_active counts distinct wallets "
            "from the Paper-2 significant-at-p<0.01 set that traded the market "
            "in either role this week."
        ),
    }
    write_json(DATA_OUT / "market_microstructure_latest.json", payload)
    con.close()
    print(f"MarketMicrostructure: {len(markets_out)} markets ({(time.time()-t0)/60:.1f} min)")


if __name__ == "__main__":
    main()
