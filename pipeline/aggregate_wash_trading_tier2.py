"""Surveillance Index: Wash Trading (Tier 2 — Round-trip wash).

A single wallet's activity in a single token is "round-trip wash" candidate
when the wallet both buys and sells the token, the two sides are approximately
balanced (churn ratio = 2*min(buy,sell) / (buy+sell), range 0..1), volume is
material, and the wallet engaged in repeated round-trips (not a one-off close).

This screen catches the canonical economic form of wash trading that Tier 1
(self-matched) misses: a wallet rotating in and out of the same token across
many counterparties, inflating volume without genuine ownership change.

Caveat: legitimate algorithmic market makers naturally cycle inventory and
will appear above churn thresholds. We report multiple thresholds and the
full distribution rather than a single binary "wash" flag. Use is "patterns
consistent with round-trip wash" per the site's posture rules.

Reads:
  J:/Research/10. Prediction/data/blockchain/processed_trades.csv (282 GB)

Writes:
  site/public/data/surveillance_wash_tier2_latest.json
  site/public/data/surveillance_wash_tier2_top_wallets.csv
"""
from __future__ import annotations

import time

import duckdb

from common import utc_now, write_json
from config import DATA_OUT

TRADES = "J:/Research/10. Prediction/data/blockchain/processed_trades.csv"

# Thresholds to report. Each pair (churn, min_vol, min_trips) becomes a row in
# the result table. Strictest threshold is the headline.
THRESHOLDS = [
    {"name": "loose",   "min_churn": 0.80, "min_vol":  1000, "min_trips":  5},
    {"name": "medium",  "min_churn": 0.90, "min_vol":  5000, "min_trips":  8},
    {"name": "strict",  "min_churn": 0.95, "min_vol": 10000, "min_trips": 10},
]


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Wash Tier 2: scanning {TRADES} ...")

    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='14GB'")
    con.execute("PRAGMA threads=8")

    # One pass through the file, expanding each trade row into two
    # (wallet, side) records (one for the maker, one for the taker) and
    # grouping by (wallet, token_id) to capture buys and sells per pair.
    #
    # Direction logic for a CTF outcome token:
    #   - maker is the resting limit order, taker is the marketable order
    #   - maker_side = 'BUY'  => maker bought, taker sold
    #   - maker_side = 'SELL' => maker sold,   taker bought
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1/2: per-wallet per-token buy/sell aggregation ...")
    con.execute(f"""
        CREATE TEMP TABLE pair_stats AS
        SELECT
            wallet,
            token_id,
            SUM(buy_vol)  AS buy_vol,
            SUM(sell_vol) AS sell_vol,
            SUM(n_buy)    AS n_buy,
            SUM(n_sell)   AS n_sell
        FROM (
            -- Maker side
            SELECT
                LOWER(maker_address) AS wallet,
                token_id,
                CASE WHEN UPPER(maker_side) = 'BUY'  THEN CAST(usdc_amount AS DOUBLE) ELSE 0 END AS buy_vol,
                CASE WHEN UPPER(maker_side) = 'SELL' THEN CAST(usdc_amount AS DOUBLE) ELSE 0 END AS sell_vol,
                CASE WHEN UPPER(maker_side) = 'BUY'  THEN 1 ELSE 0 END AS n_buy,
                CASE WHEN UPPER(maker_side) = 'SELL' THEN 1 ELSE 0 END AS n_sell
            FROM read_csv_auto('{TRADES}', sample_size=-1)
            UNION ALL
            -- Taker side (opposite direction)
            SELECT
                LOWER(taker_address) AS wallet,
                token_id,
                CASE WHEN UPPER(maker_side) = 'SELL' THEN CAST(usdc_amount AS DOUBLE) ELSE 0 END AS buy_vol,
                CASE WHEN UPPER(maker_side) = 'BUY'  THEN CAST(usdc_amount AS DOUBLE) ELSE 0 END AS sell_vol,
                CASE WHEN UPPER(maker_side) = 'SELL' THEN 1 ELSE 0 END AS n_buy,
                CASE WHEN UPPER(maker_side) = 'BUY'  THEN 1 ELSE 0 END AS n_sell
            FROM read_csv_auto('{TRADES}', sample_size=-1)
        )
        GROUP BY wallet, token_id
        HAVING SUM(buy_vol) > 0 AND SUM(sell_vol) > 0
    """)
    n_pairs = con.execute("SELECT COUNT(*) FROM pair_stats").fetchone()[0]
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1 done. {n_pairs:,} two-sided (wallet, token) pairs "
          f"({(time.time()-t0)/60:.1f} min)")

    # Compute churn ratio + round-trip count once.
    con.execute("""
        CREATE TEMP TABLE pair_metrics AS
        SELECT
            wallet,
            token_id,
            buy_vol,
            sell_vol,
            buy_vol + sell_vol AS total_vol,
            (2.0 * LEAST(buy_vol, sell_vol)) / NULLIF(buy_vol + sell_vol, 0) AS churn_ratio,
            LEAST(n_buy, n_sell) AS n_round_trips,
            n_buy,
            n_sell
        FROM pair_stats
    """)

    # Counts at each threshold.
    threshold_results = []
    for t in THRESHOLDS:
        row = con.execute(f"""
            SELECT
                COUNT(*) AS n_pairs,
                COUNT(DISTINCT wallet) AS n_wallets,
                SUM(total_vol) AS total_vol,
                SUM(LEAST(buy_vol, sell_vol)) AS round_trip_vol
            FROM pair_metrics
            WHERE churn_ratio >= {t['min_churn']}
              AND total_vol >= {t['min_vol']}
              AND n_round_trips >= {t['min_trips']}
        """).fetchone()
        threshold_results.append({
            **t,
            "n_pairs": int(row[0] or 0),
            "n_wallets": int(row[1] or 0),
            "total_volume": float(row[2] or 0.0),
            "round_trip_volume": float(row[3] or 0.0),
        })

    # Population denominators against pair_metrics
    pop = con.execute("""
        SELECT
            COUNT(*) AS n_pairs,
            COUNT(DISTINCT wallet) AS n_wallets,
            SUM(total_vol) AS total_vol
        FROM pair_metrics
    """).fetchone()

    # Histogram of churn_ratio across all two-sided pairs (10 equal-width bins)
    histogram = con.execute("""
        SELECT
            FLOOR(churn_ratio * 10) / 10 AS bin_low,
            COUNT(*) AS n_pairs,
            SUM(total_vol) AS total_vol
        FROM pair_metrics
        GROUP BY bin_low
        ORDER BY bin_low
    """).fetchdf()

    # Top wallets at the strict threshold by total round-trip volume
    strict = THRESHOLDS[-1]
    top_wallets = con.execute(f"""
        WITH flagged_pairs AS (
            SELECT wallet, token_id, total_vol,
                   LEAST(buy_vol, sell_vol) AS rt_vol,
                   churn_ratio, n_round_trips
            FROM pair_metrics
            WHERE churn_ratio >= {strict['min_churn']}
              AND total_vol >= {strict['min_vol']}
              AND n_round_trips >= {strict['min_trips']}
        )
        SELECT
            wallet,
            COUNT(*) AS n_tokens_flagged,
            SUM(rt_vol) AS total_round_trip_vol,
            AVG(churn_ratio) AS avg_churn,
            SUM(n_round_trips) AS total_round_trips
        FROM flagged_pairs
        GROUP BY wallet
        ORDER BY total_round_trip_vol DESC
        LIMIT 20
    """).fetchdf()
    top_wallets.to_csv(DATA_OUT / "surveillance_wash_tier2_top_wallets.csv", index=False)

    # Per-year detected pairs at the strict threshold. Requires joining back
    # to trade dates; expensive on the full file, so derive from the temp
    # table's first-trade year via a lightweight second scan.
    print(f"[{time.strftime('%H:%M:%S')}] Pass 2/2: per-year first-activity year for flagged pairs ...")
    con.execute(f"""
        CREATE TEMP TABLE flagged_wallets AS
        SELECT DISTINCT wallet
        FROM pair_metrics
        WHERE churn_ratio >= {strict['min_churn']}
          AND total_vol >= {strict['min_vol']}
          AND n_round_trips >= {strict['min_trips']}
    """)
    by_year = con.execute(f"""
        SELECT
            CAST(strftime(CAST(date AS DATE), '%Y') AS INTEGER) AS year,
            COUNT(*) AS n_trades_by_flagged,
            SUM(CAST(usdc_amount AS DOUBLE)) AS vol_by_flagged
        FROM read_csv_auto('{TRADES}', sample_size=-1)
        WHERE LOWER(maker_address) IN (SELECT wallet FROM flagged_wallets)
           OR LOWER(taker_address) IN (SELECT wallet FROM flagged_wallets)
        GROUP BY year
        ORDER BY year
    """).fetchdf()

    print(f"[{time.strftime('%H:%M:%S')}] Aggregations done ({(time.time()-t0)/60:.1f} min)")

    payload = {
        "index_name": "Wash Trading (Tier 2 — Round-trip)",
        "short_name": "Wash T2",
        "as_of": utc_now()[:10],
        "snapshot_note": (
            "Cumulative through the most recent refresh. We report at three churn-ratio "
            "thresholds rather than a single binary flag. The loose threshold (0.80 churn) "
            "catches legitimate algorithmic market making in addition to wash trading; the "
            "strict threshold (0.95 churn, 10+ round-trips, $10K+) is the headline number."
        ),
        "tier2_methodology": (
            "For each (wallet, token_id) pair where the wallet has at least one buy and one "
            "sell, compute churn_ratio = 2 * min(buy_vol, sell_vol) / (buy_vol + sell_vol). "
            "Range 0..1, where 1 is a perfectly-balanced position. A pair is flagged at a "
            "given threshold if churn_ratio >= T_churn AND total_vol >= T_vol AND "
            "min(n_buys, n_sells) >= T_trips. Direction logic per trade: maker = trader who "
            "placed the resting limit order; maker_side BUY means the maker acquired the "
            "outcome token, the taker disposed. We expand each match into two (wallet, side) "
            "records (maker and taker) and group by (wallet, token_id)."
        ),
        "population": {
            "n_two_sided_pairs": int(pop[0] or 0),
            "n_wallets_with_two_sided_activity": int(pop[1] or 0),
            "total_volume_in_two_sided_pairs": float(pop[2] or 0.0),
        },
        "thresholds": threshold_results,
        "churn_histogram": histogram.assign(
            bin_low=lambda d: d["bin_low"].astype(float),
            n_pairs=lambda d: d["n_pairs"].astype(int),
            total_vol=lambda d: d["total_vol"].astype(float),
        ).to_dict(orient="records"),
        "top_wallets_strict": top_wallets.assign(
            wallet=lambda d: d["wallet"].astype(str),
            n_tokens_flagged=lambda d: d["n_tokens_flagged"].astype(int),
            total_round_trip_vol=lambda d: d["total_round_trip_vol"].astype(float),
            avg_churn=lambda d: d["avg_churn"].astype(float),
            total_round_trips=lambda d: d["total_round_trips"].astype(int),
        ).to_dict(orient="records"),
        "by_year_strict_flagged_wallets": by_year.assign(
            year=lambda d: d["year"].astype(int),
            n_trades_by_flagged=lambda d: d["n_trades_by_flagged"].astype(int),
            vol_by_flagged=lambda d: d["vol_by_flagged"].astype(float),
        ).to_dict(orient="records"),
        "interpretation_caveats": [
            "The loose threshold (0.80 churn) is intentionally inclusive and captures "
            "legitimate algorithmic market making as well as wash trading.",
            "The strict threshold (0.95 churn, 10+ round-trips, $10K+) is intended for "
            "the headline 'patterns consistent with wash trading' count.",
            "No temporal-window constraint is applied; a wallet that buys and sells the "
            "same token across months is included if the balance and volume thresholds "
            "are met. A within-window variant (e.g. all round-trips within 24h) is a "
            "candidate Tier 2b refinement.",
            "No public page names a specific wallet address; the top-wallets CSV is "
            "available for download under the methodology page's usage notice.",
        ],
        "generated_at": utc_now(),
        "source": TRADES,
        "wallclock_seconds": int(time.time() - t0),
    }
    write_json(DATA_OUT / "surveillance_wash_tier2_latest.json", payload)

    strict_result = threshold_results[-1]
    print(
        f"Wash Tier 2 (strict): {strict_result['n_pairs']:,} (wallet, token) pairs flagged, "
        f"{strict_result['n_wallets']:,} unique wallets, "
        f"${strict_result['round_trip_volume']:,.0f} of round-trip volume. "
        f"Total two-sided pairs in population: {pop[0]:,}. "
        f"({(time.time()-t0)/60:.1f} min)"
    )


if __name__ == "__main__":
    main()
