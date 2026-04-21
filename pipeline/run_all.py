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
    "aggregate_bot_share",
    "aggregate_price_gap",
    "aggregate_efficiency",
    "aggregate_pii",
    "build_master_table",
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
