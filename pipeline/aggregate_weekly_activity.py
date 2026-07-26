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

INCREMENTAL BY DEFAULT (July 2026): settled weeks never change, so each run
recomputes only the trailing weeks (last history week minus REFRESH_WEEKS,
onward) from the week-partitioned parquet store and splices them onto the
existing history. A full rescan of the 1B-row master (which OOM'd/thrashed
repeatedly in July 2026) happens only when the history CSV is absent or
AWA_FULL_REBUILD=1 is set. Two caveats of the frozen-history design:
  - wallet TYPES are joined as of each computation, so if wallet_statistics
    is rebuilt and a wallet's type changes, weeks before the refresh window
    keep the old attribution. Set AWA_FULL_REBUILD=1 after any major
    wallet_statistics rebuild.
  - new_wallets IS recomputed for all weeks each run (it comes from the
    small wallet_statistics table, not from a trade scan).

Outputs:
  weekly_activity_history.csv  full weekly panel
  weekly_activity_latest.json  current week snapshot
  weekly_volume_timeseries.json  chart series for the landing page
"""
from __future__ import annotations

import os
import time
from datetime import timedelta

import duckdb
import pandas as pd

from common import add_rolling_stats, summary_stats, utc_now, write_json
from config import DATA_OUT, trades_source, tune_duckdb

TRADES_SRC = trades_source()
WALLETS = "H:/Research/10. Prediction/data/blockchain/wallet_statistics.csv"
FLAGGED = "G:/My Drive/1. Research/1. Polymarket/2. Insider/output/stage19_significant_wallets.csv"
HIST_PATH = DATA_OUT / "weekly_activity_history.csv"

# Recompute this many weeks back from the last week in history. Covers the
# trailing partial week, late-arriving backfills, and the partial-week filter
# having dropped the last row.
REFRESH_WEEKS = 4

TYPES = ("bot", "active_retail", "sophisticated", "casual", "one_shot")

RAW_COLS = (
    ["week", "total_trades", "total_usd_volume"]
    + [f"participations_{t}" for t in TYPES]
    + [f"participation_volume_{t}" for t in TYPES]
    + ["participations_unclassified", "participation_volume_unclassified",
       "active_wallets", "flagged_active"]
)


def _week_filter(cutoff_week: str) -> str:
    """SQL predicate limiting the trade scan to weeks >= cutoff_week.

    On the parquet store the hive partition column part_week gives file-level
    pruning ('YYYY-Www' compares lexicographically = chronologically). The
    CSV fallback has no such column, so filter on the date."""
    if "read_parquet" in TRADES_SRC:
        return f"part_week >= '{cutoff_week}'"
    return f"strftime(CAST(date AS DATE), '%G-W%V') >= '{cutoff_week}'"


def _scan_weeks(con, where: str) -> pd.DataFrame:
    """The three trade-scan passes, restricted by `where`, returned as one
    wide raw frame (one row per week, RAW_COLS)."""
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1: match-level totals ({where}) ...")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE weekly_totals AS
        SELECT
            strftime(CAST(date AS DATE), '%G-W%V') AS week,
            COUNT(*) AS total_trades,
            SUM(CAST(usdc_amount AS DOUBLE)) AS total_usd_volume
        FROM {TRADES_SRC}
        WHERE {where}
        GROUP BY 1
    """)

    # Pre-aggregate each side by week x wallet BEFORE the type join: shrinks
    # the join input from 2 rows per match to one row per active wallet-week,
    # the restructure that ended the July 2026 OOM/spill-thrash failures.
    print(f"[{time.strftime('%H:%M:%S')}] Pass 2: participation by type ...")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE weekly_by_type AS
        WITH side_agg AS (
            SELECT week, wallet, SUM(n) AS n, SUM(vol) AS vol
            FROM (
                SELECT strftime(CAST(date AS DATE), '%G-W%V') AS week,
                       LOWER(maker_address) AS wallet,
                       COUNT(*) AS n,
                       SUM(CAST(usdc_amount AS DOUBLE)) AS vol
                FROM {TRADES_SRC}
                WHERE {where}
                GROUP BY 1, 2
                UNION ALL
                SELECT strftime(CAST(date AS DATE), '%G-W%V') AS week,
                       LOWER(taker_address) AS wallet,
                       COUNT(*) AS n,
                       SUM(CAST(usdc_amount AS DOUBLE)) AS vol
                FROM {TRADES_SRC}
                WHERE {where}
                GROUP BY 1, 2
            )
            GROUP BY 1, 2
        )
        SELECT
            s.week,
            COALESCE(w.wallet_type, 'unclassified') AS wallet_type,
            SUM(s.n) AS participations,
            SUM(s.vol) AS participation_volume
        FROM side_agg s
        LEFT JOIN wallets w ON s.wallet = w.wallet
        GROUP BY 1, 2
    """)

    print(f"[{time.strftime('%H:%M:%S')}] Pass 3: active-wallet counts ...")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE weekly_wallet_counts AS
        WITH sides AS (
            SELECT strftime(CAST(date AS DATE), '%G-W%V') AS week,
                   LOWER(maker_address) AS wallet
            FROM {TRADES_SRC}
            WHERE {where}
            UNION
            SELECT strftime(CAST(date AS DATE), '%G-W%V') AS week,
                   LOWER(taker_address) AS wallet
            FROM {TRADES_SRC}
            WHERE {where}
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
    for c in RAW_COLS:
        if c not in wide.columns:
            wide[c] = 0
    return wide[RAW_COLS]


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Weekly activity (both-side attribution) ...")

    con = duckdb.connect(":memory:")
    con.execute("PRAGMA memory_limit='40GB'")
    con.execute("PRAGMA threads=8")
    tune_duckdb(con)

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

    full_rebuild = os.environ.get("AWA_FULL_REBUILD") == "1" or not HIST_PATH.exists()
    cutoff_week = None
    if full_rebuild:
        print("FULL REBUILD: scanning the entire master (history absent or AWA_FULL_REBUILD=1)")
        wide = _scan_weeks(con, "1=1")
    else:
        hist = pd.read_csv(HIST_PATH)
        last_date = pd.to_datetime(hist["date"]).max()
        cutoff_week = (last_date - timedelta(weeks=REFRESH_WEEKS)).strftime("%G-W%V")
        print(f"INCREMENTAL: history through {last_date.date()}; "
              f"recomputing weeks >= {cutoff_week}")
        fresh = _scan_weeks(con, _week_filter(cutoff_week))
        keep = hist[hist["week"] < cutoff_week]
        missing = [c for c in RAW_COLS if c not in keep.columns]
        for c in missing:
            keep = keep.assign(**{c: 0})
        wide = pd.concat([keep[RAW_COLS], fresh], ignore_index=True)

    # ---- Derived columns, recomputed over the whole frame every run ----
    wide = wide.sort_values("week").reset_index(drop=True)
    wide["total_participations"] = wide["total_trades"] * 2
    for wt in TYPES:
        col = f"participations_{wt}"
        wide[f"share_{wt}"] = (wide[col] / wide["total_participations"]).where(
            wide["total_participations"] > 0, 0.0
        )

    # New wallets per ISO week from first_trade_date (small table, all weeks)
    print(f"[{time.strftime('%H:%M:%S')}] Pass 4: new participants per week ...")
    new_wallets = con.execute("""
        SELECT strftime(CAST(first_trade_date AS DATE), '%G-W%V') AS week,
               COUNT(*) AS new_wallets
        FROM wallets
        WHERE first_trade_date IS NOT NULL AND first_trade_date != ''
        GROUP BY 1
        ORDER BY 1
    """).fetchdf()
    wide = wide.drop(columns=["new_wallets"], errors="ignore").merge(
        new_wallets, on="week", how="left")
    wide["new_wallets"] = wide["new_wallets"].fillna(0).astype(int)

    wide["date"] = pd.to_datetime(wide["week"] + "-1", format="%G-W%V-%u", errors="coerce")
    wide = wide.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # Partial-week filter: drop weeks whose total_trades is < 50% of prior 4wk
    # median. In incremental mode it applies ONLY to the recomputed weeks:
    # settled history must not be re-litigated (the rolling context shifts as
    # previously-dropped weeks vanish from the frame, which silently removed
    # three 2023 weeks on the first incremental run).
    med4 = wide["total_trades"].rolling(4, min_periods=1).median().shift(1)
    mask = med4.notna() & (wide["total_trades"] < 0.5 * med4)
    if cutoff_week is not None:
        mask &= wide["week"] >= cutoff_week
    if mask.any():
        for w in wide.loc[mask, "week"]:
            print(f"  Dropped partial week: {w}")
        wide = wide[~mask].reset_index(drop=True)

    wide = add_rolling_stats(wide, "total_usd_volume")
    wide = add_rolling_stats(wide, "new_wallets")

    hist_out = wide.copy()
    hist_out["date"] = hist_out["date"].dt.strftime("%Y-%m-%d")
    hist_out[[c for c in hist_out.columns if c != "week"]].assign(week=hist_out["week"]).to_csv(
        HIST_PATH, index=False
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
            wt: int(latest.get(f"participations_{wt}", 0)) for wt in TYPES
        },
        "by_type_volume": {
            wt: float(latest.get(f"participation_volume_{wt}", 0.0)) for wt in TYPES
        },
        "by_type_share": {
            wt: float(latest.get(f"share_{wt}", 0.0)) for wt in TYPES
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
            "flagged_active_this_week is the count of Paper 2-flagged wallets that participated. "
            "Settled weeks are computed once and kept; each refresh recomputes the trailing "
            f"{REFRESH_WEEKS} weeks."
        ),
        "generated_at": utc_now(),
        "source": TRADES_SRC,
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

    print(f"[{time.strftime('%H:%M:%S')}] Done in {time.time() - t0:.1f}s "
          f"({'full rebuild' if full_rebuild else 'incremental'})")


if __name__ == "__main__":
    main()
