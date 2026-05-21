"""Orchestrator — runs aggregations in order.

Three modes:
    python pipeline/run_all.py                 # default: weekly only (~6 min)
    python pipeline/run_all.py --mode=weekly   # same as default
    python pipeline/run_all.py --mode=full     # weekly + surveillance (~10 hrs)
    python pipeline/run_all.py --mode=surveillance-only

The Sunday cron should call this without args. The surveillance aggregators
scan the full 282 GB on-chain panel and take 1-3 hours each; run them on a
monthly cadence or on demand via --mode=full.
"""
from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

WEEKLY = [
    "aggregate_pwi",
    "aggregate_calibration",
    "aggregate_execution",
    # weekly_activity must run before bot_share (bot_share reads its output)
    "aggregate_weekly_activity",
    "aggregate_bot_share",
    "aggregate_price_gap",
    "aggregate_efficiency",
    "aggregate_pii",
    "aggregate_top_markets",
    "aggregate_market_microstructure",
    "fetch_market_snapshot",
    "aggregate_profit_split",
    "aggregate_cumulative_pnl",
]

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
        default="weekly",
        choices=["weekly", "full", "surveillance-only"],
        help="weekly (default, ~6min): skip surveillance. "
             "full (~10hrs): weekly + surveillance. "
             "surveillance-only: just the slow surveillance aggregators.",
    )
    args = ap.parse_args()

    if args.mode == "weekly":
        _run(WEEKLY)
        # Re-run the surveillance overview so it picks up any newly-refreshed
        # PII numbers even on a weekly-only refresh. It only re-reads existing
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
