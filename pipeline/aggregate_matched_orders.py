"""Surveillance Index: Matched / Pre-Arranged Orders (Tier 3 cluster wash).

For each unordered pair of distinct wallets (a, b), count how often they
ended up on opposite sides of the same Polymarket match, across how many
markets, and at what total volume. Persistent counterparty pairs (many
matches together over a long period and across many markets) are the
signature that distinguishes coordinated wash trading from inventory
rotation by an unrelated market maker — the same MM rotates against
*everyone* (so no single counterparty dominates its activity), while a
linked-wallet wash pair trades primarily against each other.

This is Tier 3 of the wash-trading framework. Tier 1 (self-matched) is
near-zero on Polymarket. Tier 2 (round-trip within a single wallet) is
dominated by algorithmic MMs. Tier 3 is the only of the three that can
isolate coordinated manipulative activity from legitimate MM rotation.

Read once; pair-level groupby; HAVING-clause-filter to persistent pairs;
report distribution + top pairs (without naming any specific wallet).

Reads:
  J:/Research/10. Prediction/data/blockchain/processed_trades.csv (282 GB)

Writes:
  site/public/data/surveillance_matched_latest.json
  site/public/data/surveillance_matched_top_pairs.csv
"""
from __future__ import annotations

import time

import duckdb

from common import utc_now, write_json
from config import DATA_OUT

TRADES = "J:/Research/10. Prediction/data/blockchain/processed_trades.csv"

# Persistence threshold: minimum number of trades the same pair must have
# against each other to be considered. Single coincidental matches in big
# markets are not persistence; this filter prunes the long tail aggressively
# so the materialized result is small enough to aggregate in memory.
MIN_TRADES_PER_PAIR = 10

# Reporting thresholds on top of MIN_TRADES_PER_PAIR. Each row in the
# threshold table reflects pairs meeting all three: min n_trades, min
# n_markets, min total volume.
THRESHOLDS = [
    {"name": "loose",  "min_trades":    10, "min_markets":  3, "min_vol":   1000},
    {"name": "medium", "min_trades":   100, "min_markets": 10, "min_vol":  10000},
    {"name": "strict", "min_trades":  1000, "min_markets": 30, "min_vol": 100000},
]


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Matched orders / Tier 3: scanning {TRADES} ...")

    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='14GB'")
    con.execute("PRAGMA threads=8")

    # Single pass: GROUP BY unordered (wallet_a, wallet_b). The HAVING clause
    # prunes the long tail of one-shot pairings before we materialize the
    # result. Pair direction is sorted so (A,B) and (B,A) collapse.
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1/1: pair groupby with HAVING n_trades >= "
          f"{MIN_TRADES_PER_PAIR} ...")
    con.execute(f"""
        CREATE TEMP TABLE pair_stats AS
        SELECT
            LEAST(LOWER(maker_address), LOWER(taker_address))    AS wallet_a,
            GREATEST(LOWER(maker_address), LOWER(taker_address)) AS wallet_b,
            COUNT(*) AS n_trades,
            COUNT(DISTINCT market_id) AS n_markets,
            SUM(CAST(usdc_amount AS DOUBLE)) AS total_volume,
            MIN(CAST(date AS DATE)) AS first_trade_date,
            MAX(CAST(date AS DATE)) AS last_trade_date
        FROM read_csv_auto('{TRADES}', sample_size=-1)
        WHERE LOWER(maker_address) != LOWER(taker_address)
        GROUP BY wallet_a, wallet_b
        HAVING COUNT(*) >= {MIN_TRADES_PER_PAIR}
    """)
    n_persistent = con.execute("SELECT COUNT(*) FROM pair_stats").fetchone()[0]
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1 done ({(time.time()-t0)/60:.1f} min). "
          f"{n_persistent:,} pairs with >= {MIN_TRADES_PER_PAIR} shared trades.")

    # Add per-pair span / per-pair trades-per-market.
    con.execute("""
        CREATE TEMP TABLE pair_metrics AS
        SELECT
            *,
            (last_trade_date - first_trade_date) AS span_days,
            n_trades * 1.0 / NULLIF(n_markets, 0) AS trades_per_market
        FROM pair_stats
    """)

    # Population aggregate (within the persistent-pair set).
    pop = con.execute("""
        SELECT
            COUNT(*) AS n_pairs,
            COUNT(DISTINCT wallet_a) + COUNT(DISTINCT wallet_b) AS approx_n_wallets,
            SUM(n_trades) AS total_pair_trades,
            SUM(total_volume) AS total_pair_volume,
            AVG(n_trades) AS mean_trades,
            QUANTILE_CONT(n_trades, 0.5) AS median_trades,
            AVG(n_markets) AS mean_markets,
            QUANTILE_CONT(n_markets, 0.5) AS median_markets,
            AVG(trades_per_market) AS mean_tpm,
            QUANTILE_CONT(trades_per_market, 0.5) AS median_tpm
        FROM pair_metrics
    """).fetchone()

    # Counts at each threshold.
    threshold_rows = []
    for t in THRESHOLDS:
        row = con.execute(f"""
            SELECT
                COUNT(*) AS n_pairs,
                SUM(n_trades) AS shared_trades,
                SUM(total_volume) AS shared_volume
            FROM pair_metrics
            WHERE n_trades >= {t['min_trades']}
              AND n_markets >= {t['min_markets']}
              AND total_volume >= {t['min_vol']}
        """).fetchone()
        threshold_rows.append({
            **t,
            "n_pairs": int(row[0] or 0),
            "shared_trades": int(row[1] or 0),
            "shared_volume": float(row[2] or 0.0),
        })

    # Distribution of pairs by number of shared trades.
    trade_dist = con.execute("""
        SELECT bucket, COUNT(*) AS n_pairs, SUM(total_volume) AS total_volume
        FROM (
            SELECT CASE
                WHEN n_trades < 25 THEN '10-24'
                WHEN n_trades < 100 THEN '25-99'
                WHEN n_trades < 500 THEN '100-499'
                WHEN n_trades < 2500 THEN '500-2,499'
                WHEN n_trades < 10000 THEN '2,500-9,999'
                ELSE '10,000+'
            END AS bucket,
            total_volume
            FROM pair_metrics
        )
        GROUP BY bucket
        ORDER BY MIN(CASE
            WHEN bucket = '10-24' THEN 0
            WHEN bucket = '25-99' THEN 1
            WHEN bucket = '100-499' THEN 2
            WHEN bucket = '500-2,499' THEN 3
            WHEN bucket = '2,500-9,999' THEN 4
            ELSE 5 END)
    """).fetchdf()

    # Top 20 pairs by total shared volume.
    top_pairs = con.execute("""
        SELECT
            wallet_a, wallet_b,
            n_trades, n_markets, total_volume,
            first_trade_date, last_trade_date, span_days
        FROM pair_metrics
        ORDER BY total_volume DESC
        LIMIT 20
    """).fetchdf()
    top_pairs.to_csv(DATA_OUT / "surveillance_matched_top_pairs.csv", index=False)

    print(f"[{time.strftime('%H:%M:%S')}] Aggregations done ({(time.time()-t0)/60:.1f} min)")

    # Aggregate stats over top pairs (no wallet addresses on the public page).
    tp_summary = {
        "median_n_trades": float(top_pairs["n_trades"].median()),
        "median_n_markets": float(top_pairs["n_markets"].median()),
        "median_total_volume": float(top_pairs["total_volume"].median()),
        "median_span_days": float(top_pairs["span_days"].dt.days.median()
                                  if hasattr(top_pairs["span_days"], "dt")
                                  else top_pairs["span_days"].apply(lambda x: x.days if hasattr(x, "days") else x).median()),
        "max_n_trades": int(top_pairs["n_trades"].max()),
        "max_total_volume": float(top_pairs["total_volume"].max()),
    }

    payload = {
        "index_name": "Matched / Pre-Arranged Orders",
        "short_name": "Matched",
        "as_of": utc_now()[:10],
        "snapshot_note": (
            f"Wallet pairs that have been counterparties on at least "
            f"{MIN_TRADES_PER_PAIR} matches across the on-chain panel. Persistent "
            f"counterparty pairs that trade across many markets over a long time "
            f"span are the signature of coordinated wash trading or pre-arranged "
            f"order flow; algorithmic market makers, by contrast, rotate against "
            f"many distinct counterparties and concentrate their pair-counts on no "
            f"single wallet. The strict threshold ({THRESHOLDS[-1]['min_trades']}+ "
            f"shared trades, {THRESHOLDS[-1]['min_markets']}+ shared markets, "
            f"${THRESHOLDS[-1]['min_vol']:,}+ shared volume) is the headline; loose "
            f"and medium thresholds are also reported."
        ),
        "methodology": (
            f"For each match, compute the unordered pair "
            f"(LEAST(maker, taker), GREATEST(maker, taker)). Group by pair and count "
            f"matches, distinct markets, total volume, first/last trade dates. Pairs "
            f"with fewer than {MIN_TRADES_PER_PAIR} shared trades are dropped before "
            f"materialization. Self-matched trades (maker = taker) are excluded; "
            f"those are Tier 1 of the wash-trading framework."
        ),
        "population": {
            "n_persistent_pairs": int(pop[0] or 0),
            "approx_n_wallets_involved": int(pop[1] or 0),
            "total_trades_within_persistent_pairs": int(pop[2] or 0),
            "total_volume_within_persistent_pairs": float(pop[3] or 0.0),
            "summary_stats": {
                "mean_n_trades": float(pop[4] or 0),
                "median_n_trades": float(pop[5] or 0),
                "mean_n_markets": float(pop[6] or 0),
                "median_n_markets": float(pop[7] or 0),
                "mean_trades_per_market": float(pop[8] or 0),
                "median_trades_per_market": float(pop[9] or 0),
            },
        },
        "thresholds": threshold_rows,
        "trade_count_distribution": trade_dist.assign(
            bucket=lambda d: d["bucket"].astype(str),
            n_pairs=lambda d: d["n_pairs"].astype(int),
            total_volume=lambda d: d["total_volume"].astype(float),
        ).to_dict(orient="records"),
        "top_pair_summary": tp_summary,
        "interpretation_caveats": [
            "Persistent counterparty relationships are necessary but not sufficient "
            "for coordinated wash trading. Two algorithmic market makers that compete "
            "for liquidity in the same niche markets will appear as a persistent pair "
            "without any manipulative intent.",
            "The flag is reported as 'patterns consistent with coordinated bilateral "
            "trading,' not 'wash trading.' Per the site's posture rules.",
            "No public page names a specific wallet address. The top-pairs CSV with "
            "wallet addresses is available under the methodology page's usage notice.",
            "Tier 3 v1 is a structural test; it does not directly measure offsetting "
            "net exposure between pair members. A direction-weighted refinement (Tier "
            "3b) is the natural next iteration.",
        ],
        "generated_at": utc_now(),
        "source": TRADES,
        "min_trades_per_pair": MIN_TRADES_PER_PAIR,
        "wallclock_seconds": int(time.time() - t0),
    }
    write_json(DATA_OUT / "surveillance_matched_latest.json", payload)

    strict = threshold_rows[-1]
    print(
        f"Matched (Tier 3): {n_persistent:,} persistent pairs total. "
        f"Strict ({strict['min_trades']}+ trades / {strict['min_markets']}+ markets / "
        f"${strict['min_vol']:,}+): {strict['n_pairs']:,} pairs, "
        f"${strict['shared_volume']:,.0f} shared volume. "
        f"({(time.time()-t0)/60:.1f} min)"
    )


if __name__ == "__main__":
    main()
