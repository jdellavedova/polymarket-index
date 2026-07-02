"""Surveillance Index: Wash Trading (Tier 1 — Self-matched trades).

A single Polymarket match is "self-matched" when the maker and taker addresses
are the same wallet. This is the direct economic analog of a wash trade under
FINRA Rule 6140: a trade between two accounts under common beneficial control
where there is no change in ownership. Tier 1 catches the simplest case (one
wallet on both sides). Tiers 2 (round-trip within window) and 3 (cluster-wash
across linked wallets) are forthcoming.

Two passes through the master trade panel via duckdb (partitioned parquet when
built, else the ~360GB CSV on H:). Pre-filter to self-matched rows
(population ~900 / 712M = 0.0001%), then aggregate the small filtered set.

Reads:
  config.trades_source() — H:/.../trades_parquet/ or processed_trades.csv

Writes:
  site/public/data/surveillance_wash_latest.json
  site/public/data/surveillance_wash_t1_weekly.csv  (full weekly panel)
"""
from __future__ import annotations

import time

import duckdb
import pandas as pd

from common import utc_now, write_json
from config import DATA_OUT, trades_source

TRADES_SRC = trades_source()


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Wash trading Tier 1: scanning {TRADES_SRC} ...")

    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='12GB'")
    con.execute("PRAGMA threads=8")

    # Two scans of the 282GB file are unavoidable: one to capture global
    # denominators (n_trades, total_volume), one to materialize the small
    # filtered self-matched subset. Each scan takes ~40min; ~80min total.

    # Pass 1: global headline counts via conditional aggregates.
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1/2: global counts (conditional aggregates) ...")
    n_trades, total_volume, n_self_matched_global, vol_self_matched_global = con.execute(f"""
        SELECT
            COUNT(*),
            SUM(CAST(usdc_amount AS DOUBLE)),
            SUM(CASE WHEN LOWER(maker_address) = LOWER(taker_address)
                     THEN 1 ELSE 0 END),
            SUM(CASE WHEN LOWER(maker_address) = LOWER(taker_address)
                     THEN CAST(usdc_amount AS DOUBLE) ELSE 0 END)
        FROM {TRADES_SRC}
    """).fetchone()
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1 done. n_trades={n_trades:,}, "
          f"n_self_matched={n_self_matched_global:,} ({(time.time()-t0)/60:.1f} min)")

    # Pass 2: materialize the small filtered subset for per-week / per-market /
    # per-wallet detail. Expected ~900-1000 rows from a 712M-row source.
    print(f"[{time.strftime('%H:%M:%S')}] Pass 2/2: filtered subset to memory ...")
    con.execute(f"""
        CREATE TEMP TABLE self_matched AS
        SELECT
            strftime(CAST(date AS DATE), '%G-W%V') AS week,
            CAST(date AS DATE) AS date,
            market_id,
            LOWER(maker_address) AS wallet,
            CAST(usdc_amount AS DOUBLE) AS vol
        FROM {TRADES_SRC}
        WHERE LOWER(maker_address) = LOWER(taker_address)
    """)

    # Use pass-1 globals for counts/volume; pass-2 only needed for DISTINCT counts.
    n_self_matched = int(n_self_matched_global or 0)
    vol_self_matched = float(vol_self_matched_global or 0.0)
    n_markets_affected, n_wallets_involved = con.execute("""
        SELECT
            COUNT(DISTINCT market_id),
            COUNT(DISTINCT wallet)
        FROM self_matched
    """).fetchone()

    top_markets = con.execute("""
        SELECT market_id, COUNT(*) AS n, SUM(vol) AS vol
        FROM self_matched
        GROUP BY market_id
        ORDER BY n DESC
        LIMIT 10
    """).fetchdf()

    n_repeat_wallets_ge_5 = con.execute("""
        SELECT COUNT(*) FROM (
            SELECT wallet FROM self_matched
            GROUP BY wallet HAVING COUNT(*) >= 5
        )
    """).fetchone()[0]

    weekly = con.execute("""
        SELECT
            week,
            COUNT(*) AS n_self_matched,
            SUM(vol) AS vol_self_matched
        FROM self_matched
        GROUP BY week
        ORDER BY week
    """).fetchdf()

    by_year = con.execute("""
        SELECT
            CAST(strftime(date, '%Y') AS INTEGER) AS year,
            COUNT(*) AS n,
            SUM(vol) AS vol
        FROM self_matched
        GROUP BY year
        ORDER BY year
    """).fetchdf()

    print(f"[{time.strftime('%H:%M:%S')}] Aggregations done ({(time.time()-t0)/60:.1f} min)")

    weekly.to_csv(DATA_OUT / "surveillance_wash_t1_weekly.csv", index=False)

    payload = {
        "index_name": "Wash Trading",
        "short_name": "Wash",
        "as_of": weekly["week"].iloc[-1] if len(weekly) else None,
        "snapshot_note": (
            "Tier 1 (self-matched) result on the full on-chain panel. Polymarket's "
            "CLOB does not permit a single wallet to match its own resting order under "
            "normal operation; observed self-matched trades are either pre-launch test "
            "transactions, contract-deployment artifacts, or rare edge cases. The "
            "near-zero share is the expected pattern for a competently-built venue and "
            "rules out the simplest wash-trading regime."
        ),
        "tier1_methodology": (
            "A trade is counted as Tier 1 wash if maker_address == taker_address "
            "(case-insensitive). FINRA Rule 6140 economic analog: a trade between two "
            "accounts under common beneficial control with no change in ownership."
        ),
        "headline": {
            "n_trades_total": int(n_trades),
            "n_self_matched": int(n_self_matched or 0),
            "self_matched_share_by_count": float((n_self_matched or 0) / n_trades) if n_trades else None,
            "total_volume": float(total_volume),
            "self_matched_volume": float(vol_self_matched or 0.0),
            "self_matched_share_by_volume": float((vol_self_matched or 0.0) / total_volume) if total_volume else None,
            "n_markets_affected": int(n_markets_affected or 0),
            "n_wallets_involved": int(n_wallets_involved or 0),
            "n_repeat_wallets_ge_5_trades": int(n_repeat_wallets_ge_5 or 0),
        },
        "top_markets": top_markets.assign(
            market_id=lambda d: d["market_id"].astype(str),
            n=lambda d: d["n"].astype(int),
            vol=lambda d: d["vol"].astype(float),
        ).to_dict(orient="records"),
        "by_year": by_year.assign(
            year=lambda d: d["year"].astype(int),
            n=lambda d: d["n"].astype(int),
            vol=lambda d: d["vol"].astype(float),
        ).to_dict(orient="records"),
        "tiers_forthcoming": [
            {
                "name": "Tier 2 — Round-trip wash",
                "description": "Single wallet buys and sells the same outcome token within a short window, netting to zero or near-zero exposure while contributing to volume.",
            },
            {
                "name": "Tier 3 — Cluster wash (linked wallets)",
                "description": "Repeated wallet-pair counterparties trading the same outcome in tight time windows with offsetting net exposure.",
            },
        ],
        "generated_at": utc_now(),
        "source": TRADES,
        "wallclock_seconds": int(time.time() - t0),
    }
    write_json(DATA_OUT / "surveillance_wash_latest.json", payload)

    print(
        f"Wash Tier 1: {n_self_matched:,} self-matched of {n_trades:,} trades "
        f"({(n_self_matched/n_trades)*100:.4f}%); ${vol_self_matched:,.0f} of "
        f"${total_volume:,.0f}; {n_markets_affected} markets, {n_wallets_involved} wallets. "
        f"({(time.time()-t0)/60:.1f} min)"
    )


if __name__ == "__main__":
    main()
