"""Surveillance Index: Concentration / Pump Risk (Herfindahl-Hirschman Index).

For each market, compute the HHI of participation across wallets:
   HHI = sum_i (s_i^2)
where s_i is wallet i's share of total participation events in that market.
Each match contributes one participation event for the maker and one for the
taker (matching the convention used elsewhere on the site). A market HHI close
to 1.0 means a single wallet drove most of the activity; close to 0 means the
participation was spread across many wallets.

Economic interpretation: markets with very high HHI are markets where one or
two wallets had enough share of activity to move the consensus price alone.
This is not direct evidence of manipulation; it is the precondition under
which price manipulation by a single trader becomes mechanically feasible.

Reads:
  J:/Research/10. Prediction/data/blockchain/processed_trades.csv (282 GB)

Writes:
  site/public/data/surveillance_concentration_latest.json
  site/public/data/surveillance_concentration_top_markets.csv
"""
from __future__ import annotations

import time

import duckdb

from common import utc_now, write_json
from config import DATA_OUT, trades_source, tune_duckdb

TRADES_SRC = trades_source()

# HHI thresholds. The 0.25 / 0.50 / 0.75 cutpoints map roughly onto the
# DOJ horizontal-merger guidelines (unconcentrated / moderately / highly).
THRESHOLDS = [0.25, 0.50, 0.75]

# Minimum trade count for a market to be eligible. Filters out markets with
# only 1-2 trades where HHI is trivially 1.0.
MIN_TRADES = 20


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Concentration: scanning {TRADES_SRC} ...")

    con = duckdb.connect()
    con.execute("PRAGMA memory_limit='14GB'")
    con.execute("PRAGMA threads=8")
    tune_duckdb(con)

    # Single pass: compute per-(market, wallet) participation counts via
    # UNION ALL of maker and taker sides. Group up to per-market HHI.
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1/1: per-(market, wallet) groupby ...")
    con.execute(f"""
        CREATE TEMP TABLE pair_counts AS
        SELECT market_id, wallet, COUNT(*) AS n_participations
        FROM (
            SELECT market_id, LOWER(maker_address) AS wallet
            FROM {TRADES_SRC}
            UNION ALL
            SELECT market_id, LOWER(taker_address) AS wallet
            FROM {TRADES_SRC}
        )
        GROUP BY market_id, wallet
    """)
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1 done ({(time.time()-t0)/60:.1f} min); aggregating ...")

    # Per-market HHI + total participation + top wallet share.
    con.execute(f"""
        CREATE TEMP TABLE market_concentration AS
        WITH market_totals AS (
            SELECT market_id, SUM(n_participations) AS total_participations
            FROM pair_counts GROUP BY market_id
        ),
        market_shares AS (
            SELECT
                pc.market_id,
                pc.wallet,
                pc.n_participations,
                pc.n_participations * 1.0 / mt.total_participations AS share
            FROM pair_counts pc
            JOIN market_totals mt USING (market_id)
        ),
        market_top_wallet AS (
            SELECT market_id, MAX(share) AS top_wallet_share
            FROM market_shares GROUP BY market_id
        )
        SELECT
            mt.market_id,
            mt.total_participations,
            SUM(ms.share * ms.share) AS hhi,
            mtw.top_wallet_share,
            COUNT(DISTINCT ms.wallet) AS n_unique_wallets
        FROM market_totals mt
        JOIN market_shares ms USING (market_id)
        JOIN market_top_wallet mtw USING (market_id)
        GROUP BY mt.market_id, mt.total_participations, mtw.top_wallet_share
    """)

    # Eligible-market subset (>= MIN_TRADES participations; total = 2 * matches)
    eligible_min = 2 * MIN_TRADES
    pop = con.execute(f"""
        SELECT
            COUNT(*) AS n_markets_eligible,
            SUM(total_participations) AS total_participations
        FROM market_concentration
        WHERE total_participations >= {eligible_min}
    """).fetchone()
    n_markets_eligible, total_participations = int(pop[0] or 0), float(pop[1] or 0)

    # Counts at each threshold
    threshold_rows = []
    for hhi_cut in THRESHOLDS:
        row = con.execute(f"""
            SELECT
                COUNT(*) AS n_markets,
                SUM(total_participations) AS participations
            FROM market_concentration
            WHERE total_participations >= {eligible_min}
              AND hhi >= {hhi_cut}
        """).fetchone()
        threshold_rows.append({
            "min_hhi": hhi_cut,
            "n_markets": int(row[0] or 0),
            "share_of_eligible": (int(row[0] or 0) / n_markets_eligible) if n_markets_eligible else None,
            "participations_in_flagged_markets": int(row[1] or 0),
        })

    # HHI distribution (10 bins on [0,1])
    histogram = con.execute(f"""
        SELECT
            FLOOR(hhi * 10) / 10 AS bin_low,
            COUNT(*) AS n_markets,
            SUM(total_participations) AS participations
        FROM market_concentration
        WHERE total_participations >= {eligible_min}
        GROUP BY bin_low
        ORDER BY bin_low
    """).fetchdf()

    # Top markets by HHI (highest concentration), filtered to material activity.
    # Material activity threshold: at least 200 participations (100 matches).
    top_markets = con.execute("""
        SELECT
            market_id,
            total_participations,
            hhi,
            top_wallet_share,
            n_unique_wallets
        FROM market_concentration
        WHERE total_participations >= 200
        ORDER BY hhi DESC
        LIMIT 20
    """).fetchdf()
    top_markets.to_csv(DATA_OUT / "surveillance_concentration_top_markets.csv", index=False)

    # Distributional summary stats
    summary = con.execute(f"""
        SELECT
            AVG(hhi) AS mean_hhi,
            MEDIAN(hhi) AS median_hhi,
            QUANTILE_CONT(hhi, 0.90) AS p90_hhi,
            QUANTILE_CONT(hhi, 0.99) AS p99_hhi,
            AVG(top_wallet_share) AS mean_top_share,
            MEDIAN(top_wallet_share) AS median_top_share
        FROM market_concentration
        WHERE total_participations >= {eligible_min}
    """).fetchone()

    print(f"[{time.strftime('%H:%M:%S')}] Aggregations done ({(time.time()-t0)/60:.1f} min)")

    payload = {
        "index_name": "Concentration / Pump Risk",
        "short_name": "HHI",
        "as_of": utc_now()[:10],
        "snapshot_note": (
            f"Per-market Herfindahl-Hirschman Index across all resolved Polymarket markets "
            f"with at least {MIN_TRADES} matches. HHI close to 1 means a single wallet drove "
            f"most participation; close to 0 means activity was diffuse. High HHI is the "
            f"precondition under which one trader can mechanically move the consensus price, "
            f"not direct evidence that they did."
        ),
        "methodology": (
            f"For each market, compute each wallet's share of total participation events "
            f"(each match contributes one event for the maker and one for the taker). HHI = "
            f"sum(share^2). Threshold cutpoints 0.25 / 0.50 / 0.75 map onto the DOJ "
            f"horizontal-merger-guideline categories (unconcentrated, moderately concentrated, "
            f"highly concentrated). Markets with fewer than {MIN_TRADES} matches "
            f"(={eligible_min} participations) are excluded to avoid trivial HHI=1 from "
            f"sparse activity. Material-activity top-markets table requires >=100 matches."
        ),
        "population": {
            "n_markets_eligible": n_markets_eligible,
            "min_matches_per_eligible_market": MIN_TRADES,
            "total_participations_in_eligible_markets": total_participations,
            "summary_stats": {
                "mean_hhi": float(summary[0] or 0),
                "median_hhi": float(summary[1] or 0),
                "p90_hhi": float(summary[2] or 0),
                "p99_hhi": float(summary[3] or 0),
                "mean_top_wallet_share": float(summary[4] or 0),
                "median_top_wallet_share": float(summary[5] or 0),
            },
        },
        "thresholds": threshold_rows,
        "hhi_histogram": histogram.assign(
            bin_low=lambda d: d["bin_low"].astype(float),
            n_markets=lambda d: d["n_markets"].astype(int),
            participations=lambda d: d["participations"].astype(int),
        ).to_dict(orient="records"),
        "top_markets": top_markets.assign(
            market_id=lambda d: d["market_id"].astype(str),
            total_participations=lambda d: d["total_participations"].astype(int),
            hhi=lambda d: d["hhi"].astype(float),
            top_wallet_share=lambda d: d["top_wallet_share"].astype(float),
            n_unique_wallets=lambda d: d["n_unique_wallets"].astype(int),
        ).to_dict(orient="records"),
        "interpretation_caveats": [
            "High HHI is necessary but not sufficient for single-trader price manipulation. "
            "A market with HHI = 1.0 (one wallet on both sides of every match) might be a "
            "venue-operator test market, an early-launch bootstrap, a tiny niche market with "
            "one dedicated trader, or actual manipulation.",
            "Algorithmic market makers can drive a single market's HHI above 0.5 simply by "
            "providing most of the liquidity. The flag is on the market structure, not on the "
            "intent of the dominant wallet.",
            "The HHI here is on participation count, not dollar volume. A wallet that places "
            "many small trades will weigh more in HHI than a wallet that places a few large "
            "trades. A volume-weighted variant is a candidate v2 refinement.",
        ],
        "generated_at": utc_now(),
        "source": TRADES_SRC,
        "wallclock_seconds": int(time.time() - t0),
    }
    write_json(DATA_OUT / "surveillance_concentration_latest.json", payload)

    strict = threshold_rows[-1]  # HHI >= 0.75
    print(
        f"Concentration: {n_markets_eligible:,} eligible markets. "
        f"Mean HHI {summary[0]:.3f}, median {summary[1]:.3f}, p99 {summary[3]:.3f}. "
        f"Highly concentrated (HHI >= 0.75): {strict['n_markets']:,} markets "
        f"({strict['share_of_eligible']*100:.1f}%). "
        f"({(time.time()-t0)/60:.1f} min)"
    )


if __name__ == "__main__":
    main()
