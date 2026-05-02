"""Orchestrator — runs every aggregation in order.

Usage:
    python pipeline/run_all.py
"""
from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SCRIPTS = [
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
    "aggregate_profit_split",
    "build_master_table",
    "build_weekly_narrative",
    "build_briefings",
    "build_og_image",
    "build_email_digest",
]


def main() -> None:
    for name in SCRIPTS:
        start = time.time()
        print(f"=== {name} ===")
        mod = importlib.import_module(name)
        mod.main()
        print(f"  done in {time.time() - start:.2f}s")


if __name__ == "__main__":
    main()
