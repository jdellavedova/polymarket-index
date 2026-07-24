"""Shared paths and configuration for the dashboard pipeline."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

DATA_OUT = REPO_ROOT / "site" / "public" / "data"
DATA_OUT.mkdir(parents=True, exist_ok=True)

BLOCKCHAIN = Path("H:/Research/10. Prediction/data/blockchain")
PAPER4 = BLOCKCHAIN / "paper4"
INSIDER_OUT = Path("G:/My Drive/1. Research/1. Polymarket/2. Insider/output")

TRADES_CSV = BLOCKCHAIN / "processed_trades.csv"
TRADES_PARQUET_DIR = BLOCKCHAIN / "trades_parquet"


def trades_source() -> str:
    """DuckDB FROM-expression for the master trade panel.

    Prefers the partitioned Parquet store (built once by
    tools/convert_to_parquet.py, kept current by append_delta_to_master.py on
    H:), which scans in minutes instead of the ~20+ min per pass the 360 GB
    CSV takes. Falls back to the CSV if parquet hasn't been built yet. Both
    expose identical all-VARCHAR columns, so queries are source-agnostic.
    """
    if TRADES_PARQUET_DIR.exists() and any(TRADES_PARQUET_DIR.rglob("*.parquet")):
        # A _mirror_pending_* marker means append_delta_to_master.py crashed
        # mid-mirror: the CSV has rows the parquet store is missing. Fall back
        # to the (slower but complete) CSV until the mirror is repaired.
        pending = sorted(p.name for p in TRADES_PARQUET_DIR.glob("_mirror_pending_*"))
        if pending:
            print(f"WARNING: incomplete parquet mirror ({', '.join(pending)}) — "
                  f"falling back to the master CSV. Re-run the parquet mirror "
                  f"for those deltas, then delete the marker(s).")
        else:
            return (f"read_parquet('{TRADES_PARQUET_DIR.as_posix()}/**/*.parquet', "
                    f"hive_partitioning=1)")
    return f"read_csv_auto('{TRADES_CSV.as_posix()}', all_varchar=TRUE, parallel=TRUE)"

SOURCES = {
    "weekly_pwi": PAPER4 / "weekly_pwi.csv",
    "calibration_nonbot": PAPER4 / "calibration_nonbot_market.csv",
    "weekly_alpha_by_type": PAPER4 / "weekly_alpha_by_type.csv",
    "stage19_significant_wallets": INSIDER_OUT / "stage19_significant_wallets.csv",
}


def require_source(key: str) -> Path:
    path = SOURCES[key]
    if not path.exists():
        raise FileNotFoundError(f"Source file missing: {path}")
    return path


def alchemy_key() -> str:
    key = os.getenv("ALCHEMY_API_KEY")
    if not key:
        raise RuntimeError("ALCHEMY_API_KEY not set. Copy .env.example to .env and fill it in.")
    return key
