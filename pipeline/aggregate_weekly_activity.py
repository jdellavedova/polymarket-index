"""Weekly activity panel: total USD volume, per-type participation shares
(both maker and taker counted), new participants, and flagged-wallets-active.

Every Polymarket match has a maker and a taker. Attributing each match only
to the maker (the earlier version of this aggregator) massively over-counts
bot participation because bots provide ~95% of makes. This version counts
each match once for the maker AND once for the taker, so "trades by type" is
really "counterparty events by type" — a more honest measure of who is
actually participating.

Total trades on the "what happened this week" card remains the count of
matches (not participations), so: total_participations = 2 * total_trades.

Outputs:
  weekly_activity_history.csv  full weekly panel
  weekly_activity_latest.json  current week snapshot + new fields:
    - flagged_active_this_week  count of Paper 2-flagged wallets that
                                participated in W17 (maker or taker)
"""
from __future__ import annotations

import time
from pathlib import Path

import duckdb
import pandas as pd

from common import add_rolling_stats, summary_stats, utc_now, write_json
from config import DATA_OUT

TRADES = "H:/Research/10. Prediction/data/blockchain/processed_trades.csv"
WALLETS = "H:/Research/10. Prediction/data/blockchain/wallet_statistics.csv"
FLAGGED = "G:/My Drive/1. Research/1. Polymarket/2. Insider/output/stage19_significant_wallets.csv"


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Weekly activity (both-side attribution) ...")

    con = duckdb.connect(":memory:")
    con.execute("PRAGMA memory_limit='16GB'")
    con.execute("PRAGMA threads=8")

    print(f"[{time.strftime('%H:%M:%S')}] Loading wallet types + flagged set ...")
    con.execute(f"""
        CREATE TABLE wallets AS
        SELECT LOWER(wallet) AS wallet, wallet_type, first_trade_date
        FROM read_csv_auto('{WALLETS}', all_varchar=TRUE)
    """)
    n_wallets = con.execute("SELECT COUNT(*) FROM wallets").fetchone()[0]
    print(f"  {n_wallets:,} wallets classified")

    con.execute(f"""
        CREATE TABLE flagged AS
        SELECT DISTINCT LOWER(wallet) AS wallet
        FROM read_csv_auto('{FLAGGED}', all_varchar=TRUE)
    """)
    n_flagged = con.execute("SELECT COUNT(*) FROM flagged").fetchone()[0]
    print(f"  {n_flagged:,} flagged wallets loaded")

    # Match-level totals (one row per match = true trade count and volume)
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1: match-level totals per week ...")
    con.execute(f"""
        CREATE TEMP TABLE weekly_totals AS
        SELECT
            strftime(CAST(date AS DATE), '%G-W%V') AS week,
            COUNT(*) AS total_trades,
            SUM(CAST(usdc_amount AS DOUBLE)) AS total_usd_volume
        FROM read_csv_auto('{TRADES}', all_varchar=TRUE, parallel=TRUE)
        GROUP BY 1
    """)

    # Participation-level per type (each match counted twice: once as maker, once as taker).
    # This is the honest "who is participating" measure.
    print(f"[{time.strftime('%H:%M:%S')}] Pass 2: participation by type (maker + taker) ...")
    con.execute(f"""
        CREATE TEMP TABLE weekly_by_type AS
        WITH sides AS (
            SELECT strftime(CAST(date AS DATE), '%G-W%V') AS week,
                   LOWER(maker_address) AS wallet,
                   CAST(usdc_amount AS DOUBLE) AS vol
            FROM read_csv_auto('{TRADES}', all_varchar=TRUE, parallel=TRUE)
            UNION ALL
            SELECT strftime(CAST(date AS DATE), '%G-W%V') AS week,
                   LOWER(taker_address) AS wallet,
                   CAST(usdc_amount AS DOUBLE) AS vol
            FROM read_csv_auto('{TRADES}', all_varchar=TRUE, parallel=TRUE)
        )
        SELECT
            s.week,
            COALESCE(w.wallet_type, 'unclassified') AS wallet_type,
            COUNT(*) AS participations,
            SUM(s.vol) AS participation_volume
        FROM sides s
        LEFT JOIN wallets w ON s.wallet = w.wallet
        GROUP BY 1, 2
    """)

    # Distinct-wallet counts (active + flagged-active) per week
    print(f"[{time.strftime('%H:%M:%S')}] Pass 3: active-wallet counts per week ...")
    con.execute(f"""
        CREATE TEMP TABLE weekly_wallet_counts AS
        WITH sides AS (
            SELECT strftime(CAST(date AS DATE), '%G-W%V') AS week,
                   LOWER(maker_address) AS wallet
            FROM read_csv_auto('{TRADES}', all_varchar=TRUE, parallel=TRUE)
            UNION
            SELECT strftime(CAST(date AS DATE), '%G-W%V') AS week,
                   LOWER(taker_address) AS wallet
            FROM read_csv_auto('{TRADES}', all_varchar=TRUE, parallel=TRUE)
        )
        SELECT
            s.week,
            COUNT(DISTINCT s.wallet) AS active_wallets,
            COUNT(DISTINCT CASE WHEN f.wallet IS NOT NULL THEN s.wallet END) AS flagged_active
        FROM sides s
        LEFT JOIN flagged f ON s.wallet = f.wallet
        GROUP BY 1
    """)

    long = con.execute("SELECT * FROM weekly_by_type ORDER BY week, wallet_type").fetchdf()
    totals = con.execute("SELECT * FROM weekly_totals ORDER BY week").fetchdf()
    wcounts = con.execute("SELECT * FROM weekly_wallet_counts ORDER BY week").fetchdf()
    print(f"[{time.strftime('%H:%M:%S')}] Scans complete: {len(totals):,} weeks")

    # Pivot participations by type to wide
    part_wide = long.pivot_table(
        index="week", columns="wallet_type", values="participations",
        aggfunc="sum", fill_value=0
    ).reset_index()
    part_wide.columns = ["week"] + [f"participations_{c}" for c in part_wide.columns if c != "week"]

    vol_wide = long.pivot_table(
        index="week", columns="wallet_type", values="participation_volume",
        aggfunc="sum", fill_value=0
    ).reset_index()
    vol_wide.columns = ["week"] + [f"participation_volume_{c}" for c in vol_wide.columns if c != "week"]

    wide = totals.merge(part_wide, on="week").merge(vol_wide, on="week").merge(wcounts, on="week")

    # Participations should equal 2 * total_trades; define it explicitly
    wide["total_participations"] = wide["total_trades"] * 2
    # Sanity (sum of per-type participations should equal total_participations within rounding)

    for wt in ("bot", "active_retail", "sophisticated", "casual", "one_shot"):
        col = f"participations_{wt}"
        if col in wide.columns:
            wide[f"share_{wt}"] = wide[col] / wide["total_participations"]
        else:
            wide[f"share_{wt}"] = 0.0

    # New wallets per ISO week from first_trade_date (unchanged)
    print(f"[{time.strftime('%H:%M:%S')}] Pass 4: new participants per week ...")
    new_wallets = con.execute(f"""
        SELECT strftime(CAST(first_trade_date AS DATE), '%G-W%V') AS week,
               COUNT(*) AS new_wallets
        FROM wallets
        WHERE first_trade_date IS NOT NULL AND first_trade_date != ''
        GROUP BY 1
        ORDER BY 1
    """).fetchdf()
    wide = wide.merge(new_wallets, on="week", how="left")
    wide["new_wallets"] = wide["new_wallets"].fillna(0).astype(int)

    wide["date"] = pd.to_datetime(wide["week"] + "-1", format="%G-W%V-%u", errors="coerce")
    wide = wide.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # Partial-week filter: drop weeks whose total_trades is < 50% of prior 4wk median
    med4 = wide["total_trades"].rolling(4, min_periods=1).median().shift(1)
    mask = med4.notna() & (wide["total_trades"] < 0.5 * med4)
    if mask.any():
        for w in wide.loc[mask, "week"]:
            print(f"  Dropped partial week: {w}")
        wide = wide[~mask].reset_index(drop=True)

    wide = add_rolling_stats(wide, "total_usd_volume")
    wide = add_rolling_stats(wide, "new_wallets")

    hist = wide.copy()
    hist["date"] = hist["date"].dt.strftime("%Y-%m-%d")
    hist[[c for c in hist.columns if c != "week"]].assign(week=hist["week"]).to_csv(
        DATA_OUT / "weekly_activity_history.csv", index=False
    )

    latest = wide.iloc[-1]
    payload = {
        "index_name": "Weekly Activity",
        "short_name": "Activity",
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "week": latest["week"],
        "total_usd_volume": float(latest["total_usd_volume"]),
        "total_trades": int(latest["total_trades"]),
        "total_participations": int(latest["total_participations"]),
        "active_wallets": int(latest["active_wallets"]),
        "new_wallets": int(latest["new_wallets"]),
        "flagged_active_this_week": int(latest["flagged_active"]),
        "by_type_participations": {
            wt: int(latest.get(f"participations_{wt}", 0))
            for wt in ("bot", "active_retail", "sophisticated", "casual", "one_shot")
        },
        "by_type_volume": {
            wt: float(latest.get(f"participation_volume_{wt}", 0.0))
            for wt in ("bot", "active_retail", "sophisticated", "casual", "one_shot")
        },
        "by_type_share": {
            wt: float(latest.get(f"share_{wt}", 0.0))
            for wt in ("bot", "active_retail", "sophisticated", "casual", "one_shot")
        },
        "volume_ma4w": float(latest["total_usd_volume_ma4w"]),
        "volume_ma13w": float(latest["total_usd_volume_ma13w"]),
        "volume_z52w": (None if pd.isna(latest["total_usd_volume_z52w"])
                         else float(latest["total_usd_volume_z52w"])),
        "new_wallets_ma4w": float(latest["new_wallets_ma4w"]),
        "new_wallets_ma13w": float(latest["new_wallets_ma13w"]),
        "rolling_volume": summary_stats(wide["total_usd_volume"]),
        "rolling_new_wallets": summary_stats(wide["new_wallets"]),
        "n_weeks_history": int(len(wide)),
        "methodology": (
            "Every Polymarket match has a maker (limit order) and a taker (market order). "
            "Per-type shares count each match twice, once for the maker's type and once for "
            "the taker's type — an honest participation measure. `total_trades` is the match "
            "count (single-count), so total_participations = 2 * total_trades by construction. "
            "new_wallets is the count of wallets whose first-ever trade fell in the week. "
            "flagged_active_this_week is the count of Paper 2-flagged wallets that participated."
        ),
        "generated_at": utc_now(),
        "source": TRADES,
    }
    write_json(DATA_OUT / "weekly_activity_latest.json", payload)

    # Chart-ready timeseries for the landing-page weekly-volume chart
    series = wide[["date", "total_usd_volume", "total_usd_volume_ma13w"]].copy()
    series["date"] = series["date"].dt.strftime("%Y-%m-%d")
    series = series.rename(columns={
        "total_usd_volume": "weekly_volume",
        "total_usd_volume_ma13w": "weekly_volume_ma13w",
    })
    write_json(DATA_OUT / "weekly_volume_timeseries.json", series.to_dict(orient="records"))

    con.close()
    print(
        f"Activity: {len(wide)} weeks, latest={latest['week']}, "
        f"trades={int(latest['total_trades']):,}, total=${latest['total_usd_volume']:,.0f}, "
        f"active={int(latest['active_wallets']):,}, new={int(latest['new_wallets']):,}, "
        f"flagged_active={int(latest['flagged_active']):,}, "
        f"bot_share={latest.get('share_bot', 0)*100:.1f}% ({(time.time()-t0)/60:.1f} min)"
    )


if __name__ == "__main__":
    main()
