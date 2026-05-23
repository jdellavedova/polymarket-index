"""patch_w21.py — append partial W21 (May 18-22) data from the delta CSV
to weekly_activity_history.csv, then regenerate downstream JSON outputs.

Run after append_delta_to_master.py when you want the dashboard to show
today's date rather than the last complete week.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import add_rolling_stats, summary_stats, utc_now, write_json
from config import DATA_OUT

DELTA   = Path("H:/Research/10. Prediction/data/blockchain/processed_trades_delta_20260522.csv")
WALLETS = Path("H:/Research/10. Prediction/data/blockchain/wallet_statistics.csv")
FLAGGED = Path("G:/My Drive/1. Research/1. Polymarket/2. Insider/output/stage19_significant_wallets.csv")
HIST    = DATA_OUT / "weekly_activity_history.csv"

W21_WEEK  = "2026-W21"
W21_START = "2026-05-18"
W21_THRU  = "2026-05-22"   # today


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Patching W21 from delta ({DELTA.name}) ...")

    con = duckdb.connect(":memory:")
    con.execute("PRAGMA memory_limit='8GB'")
    con.execute("PRAGMA threads=8")

    # Load small lookup tables
    con.execute(
        f"CREATE TABLE wallets AS "
        f"SELECT LOWER(wallet) AS wallet, wallet_type, first_trade_date "
        f"FROM read_csv_auto('{WALLETS.as_posix()}', all_varchar=TRUE)"
    )
    con.execute(
        f"CREATE TABLE flagged AS "
        f"SELECT DISTINCT LOWER(wallet) AS wallet "
        f"FROM read_csv_auto('{FLAGGED.as_posix()}', all_varchar=TRUE)"
    )
    print(f"  Wallet tables loaded", flush=True)

    delta_path = DELTA.as_posix()

    # Pass 1: match-level totals
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1: match totals ...", flush=True)
    totals = con.execute(
        f"SELECT COUNT(*) AS total_trades, SUM(CAST(usdc_amount AS DOUBLE)) AS total_usd_volume "
        f"FROM read_csv_auto('{delta_path}', all_varchar=TRUE) "
        f"WHERE CAST(date AS DATE) >= '{W21_START}' AND CAST(date AS DATE) <= '{W21_THRU}'"
    ).fetchdf()
    n_trades = int(totals["total_trades"].iloc[0])
    usd_vol  = float(totals["total_usd_volume"].iloc[0])
    print(f"  trades={n_trades:,}  vol=${usd_vol:,.0f}", flush=True)

    # Pass 2: per-type participations (both sides)
    print(f"[{time.strftime('%H:%M:%S')}] Pass 2: per-type participations ...", flush=True)
    by_type = con.execute(
        f"WITH sides AS ("
        f"  SELECT LOWER(maker_address) AS wallet, CAST(usdc_amount AS DOUBLE) AS vol "
        f"  FROM read_csv_auto('{delta_path}', all_varchar=TRUE) "
        f"  WHERE CAST(date AS DATE) >= '{W21_START}' AND CAST(date AS DATE) <= '{W21_THRU}' "
        f"  UNION ALL "
        f"  SELECT LOWER(taker_address) AS wallet, CAST(usdc_amount AS DOUBLE) AS vol "
        f"  FROM read_csv_auto('{delta_path}', all_varchar=TRUE) "
        f"  WHERE CAST(date AS DATE) >= '{W21_START}' AND CAST(date AS DATE) <= '{W21_THRU}'"
        f") "
        f"SELECT COALESCE(w.wallet_type, 'unclassified') AS wallet_type, "
        f"       COUNT(*) AS participations, SUM(s.vol) AS participation_volume "
        f"FROM sides s LEFT JOIN wallets w ON s.wallet = w.wallet "
        f"GROUP BY 1"
    ).fetchdf()
    print(by_type.to_string(), flush=True)

    # Pass 3: active + flagged wallet counts
    print(f"[{time.strftime('%H:%M:%S')}] Pass 3: wallet counts ...", flush=True)
    wcounts = con.execute(
        f"WITH sides AS ("
        f"  SELECT LOWER(maker_address) AS wallet "
        f"  FROM read_csv_auto('{delta_path}', all_varchar=TRUE) "
        f"  WHERE CAST(date AS DATE) >= '{W21_START}' AND CAST(date AS DATE) <= '{W21_THRU}' "
        f"  UNION "
        f"  SELECT LOWER(taker_address) AS wallet "
        f"  FROM read_csv_auto('{delta_path}', all_varchar=TRUE) "
        f"  WHERE CAST(date AS DATE) >= '{W21_START}' AND CAST(date AS DATE) <= '{W21_THRU}'"
        f") "
        f"SELECT COUNT(DISTINCT s.wallet) AS active_wallets, "
        f"       COUNT(DISTINCT CASE WHEN f.wallet IS NOT NULL THEN s.wallet END) AS flagged_active "
        f"FROM sides s LEFT JOIN flagged f ON s.wallet = f.wallet"
    ).fetchdf()
    active_wallets = int(wcounts["active_wallets"].iloc[0])
    flagged_active = int(wcounts["flagged_active"].iloc[0])
    print(f"  active={active_wallets:,}  flagged={flagged_active:,}", flush=True)

    # New wallets: first_trade_date in this partial week
    new_w = con.execute(
        f"SELECT COUNT(*) FROM wallets "
        f"WHERE CAST(first_trade_date AS DATE) >= '{W21_START}' "
        f"  AND CAST(first_trade_date AS DATE) <= '{W21_THRU}'"
    ).fetchone()[0]
    print(f"  new_wallets={new_w:,}", flush=True)
    con.close()

    # Build the W21 row matching the history schema
    type_map = dict(zip(by_type["wallet_type"], by_type["participations"]))
    vol_map  = dict(zip(by_type["wallet_type"], by_type["participation_volume"]))
    types = ("bot", "active_retail", "sophisticated", "casual", "one_shot", "unclassified")
    total_participations = n_trades * 2

    row: dict = {
        "total_trades":                 n_trades,
        "total_usd_volume":             usd_vol,
        "participations_active_retail": type_map.get("active_retail", 0),
        "participations_bot":           type_map.get("bot", 0),
        "participations_casual":        type_map.get("casual", 0),
        "participations_one_shot":      type_map.get("one_shot", 0),
        "participations_sophisticated": type_map.get("sophisticated", 0),
        "participations_unclassified":  type_map.get("unclassified", 0),
        "participation_volume_active_retail": vol_map.get("active_retail", 0.0),
        "participation_volume_bot":           vol_map.get("bot", 0.0),
        "participation_volume_casual":        vol_map.get("casual", 0.0),
        "participation_volume_one_shot":      vol_map.get("one_shot", 0.0),
        "participation_volume_sophisticated": vol_map.get("sophisticated", 0.0),
        "participation_volume_unclassified":  vol_map.get("unclassified", 0.0),
        "active_wallets":           active_wallets,
        "flagged_active":           flagged_active,
        "total_participations":     total_participations,
        "share_bot":                type_map.get("bot", 0) / total_participations if total_participations else 0,
        "share_active_retail":      type_map.get("active_retail", 0) / total_participations if total_participations else 0,
        "share_sophisticated":      type_map.get("sophisticated", 0) / total_participations if total_participations else 0,
        "share_casual":             type_map.get("casual", 0) / total_participations if total_participations else 0,
        "share_one_shot":           type_map.get("one_shot", 0) / total_participations if total_participations else 0,
        "new_wallets":              new_w,
        "date":                     W21_THRU,   # use thru-date so "as_of" shows May 22
        "week":                     W21_WEEK,
    }

    # Load history, drop any existing W21 row, append
    hist = pd.read_csv(HIST)
    hist = hist[hist["week"] != W21_WEEK].copy()

    w21_df = pd.DataFrame([row])
    # Add rolling-stat columns that may be missing (filled NaN for new partial row)
    for col in hist.columns:
        if col not in w21_df.columns:
            w21_df[col] = np.nan

    hist = pd.concat([hist, w21_df[hist.columns]], ignore_index=True)

    # Recompute rolling stats over full series including W21
    hist["date_dt"] = pd.to_datetime(hist["date"], errors="coerce")
    hist = hist.sort_values("date_dt").reset_index(drop=True)
    hist = add_rolling_stats(hist, "total_usd_volume")
    hist = add_rolling_stats(hist, "new_wallets")
    hist["date"] = hist["date_dt"].dt.strftime("%Y-%m-%d")
    hist = hist.drop(columns=["date_dt"])

    hist.to_csv(HIST, index=False)
    print(f"  Wrote {HIST} ({len(hist)} weeks, latest={hist['week'].iloc[-1]})", flush=True)

    # Regenerate weekly_activity_latest.json and timeseries JSON
    latest = hist.iloc[-1]
    hist["date_dt"] = pd.to_datetime(hist["date"])

    payload = {
        "index_name": "Weekly Activity",
        "short_name": "Activity",
        "as_of":    W21_THRU,
        "week":     W21_WEEK,
        "partial_week": True,
        "partial_week_note": f"Partial week through {W21_THRU}; full week ends 2026-05-24.",
        "total_usd_volume":     float(latest["total_usd_volume"]),
        "total_trades":         int(latest["total_trades"]),
        "total_participations": int(latest["total_participations"]),
        "active_wallets":       int(latest["active_wallets"]),
        "new_wallets":          int(latest["new_wallets"]),
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
        "volume_ma4w":  float(latest["total_usd_volume_ma4w"]),
        "volume_ma13w": float(latest["total_usd_volume_ma13w"]),
        "volume_z52w":  (None if pd.isna(latest["total_usd_volume_z52w"])
                          else float(latest["total_usd_volume_z52w"])),
        "new_wallets_ma4w":  float(latest["new_wallets_ma4w"]),
        "new_wallets_ma13w": float(latest["new_wallets_ma13w"]),
        "rolling_volume":     summary_stats(hist["total_usd_volume"]),
        "rolling_new_wallets": summary_stats(hist["new_wallets"]),
        "n_weeks_history":    int(len(hist)),
        "generated_at":       utc_now(),
        "source": str(DELTA),
    }
    write_json(DATA_OUT / "weekly_activity_latest.json", payload)

    series = hist[["date", "total_usd_volume", "total_usd_volume_ma13w"]].copy()
    series = series.rename(columns={
        "total_usd_volume": "weekly_volume",
        "total_usd_volume_ma13w": "weekly_volume_ma13w",
    })
    write_json(DATA_OUT / "weekly_volume_timeseries.json", series.to_dict(orient="records"))

    elapsed = time.time() - t0
    print(
        f"W21 patch done in {elapsed:.1f}s — "
        f"trades={n_trades:,}, vol=${usd_vol/1e6:.1f}M, "
        f"bot_share={row['share_bot']*100:.1f}%"
    )


if __name__ == "__main__":
    main()
