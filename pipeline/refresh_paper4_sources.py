"""
refresh_paper4_sources.py -- Keeps paper4 source CSVs current.

Two steps, in order:
  1. CLOB resolution map refresh (incremental, ~30s for a normal week; ~27min first run)
  2. paper4 incremental update: Prelec fits for any new complete weeks in the latest delta

Both scripts live on H: (next to the data). This module is a thin wrapper so
run_all.py can call main() like any other pipeline module.

Why this is automatic now:
  - The old expand_resolutions.py used the Gamma bulk endpoint, which caps at ~250K
    offset and silently returned stale data (0 new markets on the May 22 run).
  - The CLOB endpoint has no effective cap and returns winner flags directly.
  - Cursor is saved so only new markets are fetched each week.
  - A freshness check skips the CLOB step if maps were updated within the last hour,
    preventing double-runs when the pipeline is invoked repeatedly.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

BLOCKCHAIN   = Path(r"H:\Research\10. Prediction\data\blockchain")
CLOB_SCRIPT  = BLOCKCHAIN / "refresh_resolution_maps_clob.py"
PAPER4_SCRIPT = BLOCKCHAIN / "paper4" / "update_paper4_incremental.py"


def _find_latest_delta() -> Path | None:
    """Return the most recently modified processed_trades_delta_*.csv on H:."""
    deltas = sorted(BLOCKCHAIN.glob("processed_trades_delta_*.csv"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True)
    return deltas[0] if deltas else None


def main() -> None:
    # ---- Step 1: CLOB resolution map refresh (incremental) ----
    if not CLOB_SCRIPT.exists():
        print(f"  [skip] CLOB refresh script not found: {CLOB_SCRIPT}")
    else:
        # Skip if the token map was updated within the last hour (prevents double-runs)
        token_map = BLOCKCHAIN / "token_outcome_map.pkl"
        age_hours = (time.time() - token_map.stat().st_mtime) / 3600 if token_map.exists() else 999
        if age_hours < 1.0:
            print(f"  [skip] Resolution maps refreshed {age_hours:.1f}h ago — skipping CLOB fetch")
        else:
            print(f"Running CLOB resolution map refresh (incremental, maps are {age_hours:.1f}h old)...")
            result = subprocess.run(
                [sys.executable, str(CLOB_SCRIPT)],
                capture_output=False,
            )
            if result.returncode != 0:
                print(f"  WARNING: CLOB refresh exited {result.returncode}")

    # ---- Step 2: paper4 incremental update ----
    if not PAPER4_SCRIPT.exists():
        print(f"  [skip] paper4 incremental script not found: {PAPER4_SCRIPT}")
        return

    delta = _find_latest_delta()
    if delta is None:
        print("  [skip] no processed_trades_delta_*.csv found on H: — blockchain pull needed")
        return

    # Extract date tag from filename (e.g. processed_trades_delta_20260522.csv -> 20260522)
    date_tag = delta.stem.replace("processed_trades_delta_", "")
    print(f"Running paper4 incremental update (delta: {delta.name})...")
    result = subprocess.run(
        [sys.executable, str(PAPER4_SCRIPT), date_tag],
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"  WARNING: paper4 incremental exited {result.returncode}")
