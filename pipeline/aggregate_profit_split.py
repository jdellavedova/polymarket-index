"""aggregate_profit_split.py — thin wrapper around pipeline.profit_split.update_weekly.

The actual implementation lives in `pipeline/profit_split/`. This file is
kept so `pipeline/run_all.py` continues to import `aggregate_profit_split`
without modification.

Methodology summary (see `pipeline/profit_split/README.md` for details):
  - benchmark: per-(market_id, token_id) buy-side VWAP (Paper 1)
  - attribution: both maker AND taker with mirror signs (zero-sum totals)
  - bps: volume-weighted ROI = pnl / usd_volume * 10000

For initial deployment / methodology changes, run instead:
    python pipeline/profit_split/build_fair_prices.py
    python pipeline/profit_split/rebuild_history.py

For weekly refresh:
    python pipeline/profit_split/update_weekly.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make the profit_split package importable when this file is invoked directly.
sys.path.insert(0, str(Path(__file__).resolve().parent / "profit_split"))


def main() -> None:
    from update_weekly import main as run_weekly_update
    run_weekly_update()


if __name__ == "__main__":
    main()
