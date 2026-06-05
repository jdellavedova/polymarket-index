"""Orchestrator — runs aggregations in order.

Four modes:
    python pipeline/run_all.py                 # default: fast-weekly (~1 min)
    python pipeline/run_all.py --mode=fast     # same as default, skips full-CSV scans
    python pipeline/run_all.py --mode=weekly   # fast + heavy scans (weekly_activity,
                                               # top_markets, market_microstructure,
                                               # profit_split); can take hours on H: drive
    python pipeline/run_all.py --mode=full     # weekly + surveillance (~10+ hrs)
    python pipeline/run_all.py --mode=surveillance-only

Sunday cron calls --mode=fast (default). Heavy-scan scripts run monthly or on demand
via --mode=weekly. Surveillance runs monthly via --mode=full.

Heavy-scan scripts (scan the 335 GB master CSV on H: drive; hours each):
  - aggregate_weekly_activity  (5 full scans)
  - aggregate_top_markets      (1 full scan + Gamma API)
  - aggregate_market_microstructure (DuckDB scan)
  - aggregate_profit_split     (incremental but still full-scan to find new rows)
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Fast: reads pre-computed CSVs from paper4 pipeline; no master-CSV scan. ~1 min.
# refresh_paper4_sources runs first: CLOB resolution map refresh (incremental,
# ~5 s for a normal week) + paper4 Prelec fits for any new weeks in the latest delta.
FAST = [
    "refresh_paper4_sources",
    "aggregate_pwi",
    "aggregate_calibration",
    "aggregate_execution",
    "aggregate_bot_share",       # reads weekly_activity_history.csv (produced by HEAVY)
    "aggregate_price_gap",
    "aggregate_efficiency",
    "aggregate_pii",
    "fetch_market_snapshot",     # API calls, reads top_markets_latest.json (from HEAVY)
    "aggregate_cumulative_pnl",  # reads profit_split_history.csv (from HEAVY)
]

# Heavy: scan the 335 GB master CSV on H: drive. Hours each. Run monthly or on demand.
# weekly_activity must run before bot_share; top_markets before fetch_market_snapshot.
HEAVY = [
    "aggregate_weekly_activity",
    "aggregate_top_markets",
    "aggregate_market_microstructure",
    "aggregate_profit_split",
]

WEEKLY = HEAVY + FAST  # legacy alias: full weekly with heavy scans

# Surveillance suite: full-panel scans, 1-3 hours each. Run monthly or on demand.
SURVEILLANCE = [
    "aggregate_insider_timing",
    "aggregate_adverse_selection",
    "aggregate_resolution_surprise",
    "aggregate_wash_trading",
    "aggregate_wash_trading_tier2",
    "aggregate_concentration",
    "aggregate_matched_orders",
]

# Overview reads PII + every surveillance JSON. Run after both blocks complete.
SURVEILLANCE_OVERVIEW = ["aggregate_surveillance_overview"]

# Publishing artifacts: master table, narrative, briefings, OG image, press kit, email.
PUBLISH = [
    "build_master_table",
    "build_weekly_narrative",
    "build_commentary",
    "build_briefings",
    "build_og_image",
    "build_press_kit",
    "build_email_digest",
    "build_social_posts",
]


def _run(scripts: list[str]) -> None:
    for name in scripts:
        start = time.time()
        print(f"=== {name} ===")
        mod = importlib.import_module(name)
        mod.main()
        print(f"  done in {time.time() - start:.2f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--mode",
        default="fast",
        choices=["fast", "weekly", "full", "surveillance-only"],
        help="fast (default, ~1min): no master-CSV scans, reads pre-computed CSVs. "
             "weekly (~hours): fast + heavy master-CSV scans. "
             "full (~10hrs): weekly + surveillance. "
             "surveillance-only: just the slow surveillance aggregators.",
    )
    args = ap.parse_args()

    if args.mode in ("fast", "weekly"):
        scripts = FAST if args.mode == "fast" else WEEKLY
        _run(scripts)
        # Re-run the surveillance overview so it picks up any newly-refreshed
        # PII numbers even on a fast-only refresh. It only re-reads existing
        # JSONs and is cheap.
        _run(SURVEILLANCE_OVERVIEW)
        _run(PUBLISH)
    elif args.mode == "full":
        _run(WEEKLY)
        _run(SURVEILLANCE)
        _run(SURVEILLANCE_OVERVIEW)
        _run(PUBLISH)
    elif args.mode == "surveillance-only":
        _run(SURVEILLANCE)
        _run(SURVEILLANCE_OVERVIEW)


if __name__ == "__main__":
    main()
