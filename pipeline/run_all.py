"""Orchestrator — runs aggregations in order.

Four modes:
    python pipeline/run_all.py                 # default: fast (~1 min)
    python pipeline/run_all.py --mode=fast     # same as default, skips master scans
    python pipeline/run_all.py --mode=weekly   # fast + heavy scans (weekly_activity,
                                               # top_markets, market_microstructure,
                                               # profit_split); ~30-40 min on the CSV
                                               # master, ~5 min once trades_parquet/
                                               # is built (tools/convert_to_parquet.py)
    python pipeline/run_all.py --mode=full     # weekly + surveillance (hours)
    python pipeline/run_all.py --mode=surveillance-only

The Sunday refresh (weekly_refresh.ps1) calls --mode=weekly so the heavy
outputs never go stale. Surveillance runs monthly via --mode=full.

Heavy-scan scripts (scan the master trade panel via config.trades_source()):
  - aggregate_weekly_activity  (5 scans; ~20 min CSV, ~2 min parquet)
  - aggregate_top_markets      (1 scan + Gamma API; ~3 min CSV)
  - aggregate_market_microstructure (1 filtered scan; ~5-10 min CSV)
  - aggregate_profit_split     (pandas incremental over the CSV; ~5 min)

Fast mode consumes HEAVY outputs (weekly_activity_history.csv, top_markets,
profit_split); a staleness warning fires if those are >8 days old.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Journal for crash-resume: records completed scripts for the current run so a
# rerun after a mid-run failure skips work already done. Keyed to the master
# frontier timestamp, so new data invalidates the journal automatically.
STATE_FILE = HERE / ".run_state.json"

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
# build_media_scan finds NEW press coverage and queues it in media_review.txt
# (private, gitignored); nothing is auto-published to the site.
PUBLISH = [
    "build_master_table",
    "build_weekly_narrative",
    "build_commentary",
    "build_briefings",
    "build_og_image",
    "build_press_kit",
    "build_media_scan",
    "build_email_digest",
    "build_social_posts",
]


def _run(scripts: list[str], journal: dict | None = None) -> None:
    for name in scripts:
        if journal is not None and name in journal["completed"]:
            print(f"=== {name} === SKIPPED (completed earlier this run, resume)")
            continue
        start = time.time()
        print(f"=== {name} ===")
        mod = importlib.import_module(name)
        try:
            mod.main()
        except Exception:
            # Print the traceback to STDOUT before re-raising: PowerShell
            # wraps native stderr in NativeCommandError records and truncates
            # it, which cost two blind multi-hour reruns in July 2026.
            import traceback
            print(f"!!! {name} FAILED:")
            traceback.print_exc(file=sys.stdout)
            raise
        print(f"  done in {time.time() - start:.2f}s")
        if journal is not None:
            journal["completed"].append(name)
            _save_journal(journal)


def _smoke_import(scripts: list[str]) -> None:
    """Import every module in the plan BEFORE any long scan starts, so a
    module-level defect (bad import, renamed constant) fails in seconds
    instead of 40 minutes into a heavy pass."""
    print(f"Smoke-importing {len(scripts)} modules ...")
    for name in scripts:
        importlib.import_module(name)
    print("  all imports OK")
    _check_undefined_names(scripts)


def _check_undefined_names(scripts: list[str]) -> None:
    """Static undefined-name check on every planned script. Import alone
    misses NameErrors hiding inside function bodies (the July 2026 TRADES
    bug crashed 40 minutes into a heavy scan); pyflakes catches those in
    seconds. Only 'undefined name' findings are fatal; style noise passes."""
    try:
        import io
        from pyflakes.api import checkPath
        from pyflakes.reporter import Reporter
    except ImportError:
        print("  (pyflakes not installed - skipping undefined-name check)")
        return
    findings = []
    for name in scripts:
        path = HERE / f"{name}.py"
        if not path.exists():
            continue
        out, err = io.StringIO(), io.StringIO()
        checkPath(str(path), Reporter(out, err))
        findings += [ln for ln in out.getvalue().splitlines() if "undefined name" in ln]
    if findings:
        for f in findings:
            print(f"!!! {f}")
        raise SystemExit(
            f"ABORT: {len(findings)} undefined-name defect(s) found before any scan started."
        )
    print("  no undefined names")


def _run_key(mode: str) -> str:
    """Identity of 'the same run': mode + master frontier stamp. A new append
    changes the stamp, which invalidates any stale journal."""
    try:
        from config import BLOCKCHAIN
        frontier = json.loads((BLOCKCHAIN / "master_frontier.json").read_text())
        stamp = frontier.get("updated_at", "no-stamp")
    except Exception:
        stamp = "no-frontier"
    return f"{mode}:{stamp}"


def _load_journal(run_key: str) -> dict:
    try:
        state = json.loads(STATE_FILE.read_text())
        if state.get("run_key") == run_key and isinstance(state.get("completed"), list):
            done = state["completed"]
            if done:
                print(f"RESUME: journal matches this run; skipping {len(done)} "
                      f"completed step(s): {', '.join(done)}")
            return {"run_key": run_key, "completed": done}
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"WARNING: unreadable {STATE_FILE.name} ({e}) — starting fresh.")
    return {"run_key": run_key, "completed": []}


def _save_journal(journal: dict) -> None:
    payload = dict(journal, updated_at=datetime.now(timezone.utc).isoformat())
    STATE_FILE.write_text(json.dumps(payload, indent=2))


def _clear_journal() -> None:
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass


def _warn_if_heavy_outputs_stale(max_age_days: float = 8.0) -> None:
    """Fast mode restamps HEAVY outputs without recomputing them. If those
    files are old, the site silently publishes stale numbers as fresh — warn
    loudly instead of letting that drift."""
    from config import DATA_OUT
    heavy_outputs = [
        DATA_OUT / "weekly_activity_history.csv",
        DATA_OUT / "top_markets_latest.json",
        DATA_OUT / "profit_split_history.csv",
    ]
    now = time.time()
    for p in heavy_outputs:
        if not p.exists():
            print(f"!!! STALENESS: {p.name} missing — run --mode=weekly")
            continue
        age_days = (now - p.stat().st_mtime) / 86400
        if age_days > max_age_days:
            print(f"!!! STALENESS: {p.name} is {age_days:.1f} days old — "
                  f"fast mode will republish it as fresh. Run --mode=weekly.")


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
    ap.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore any resume journal and rerun every step from scratch.",
    )
    args = ap.parse_args()

    if args.mode == "fast":
        plan = FAST + SURVEILLANCE_OVERVIEW + PUBLISH
    elif args.mode == "weekly":
        plan = WEEKLY + SURVEILLANCE_OVERVIEW + PUBLISH
    elif args.mode == "full":
        plan = WEEKLY + SURVEILLANCE + SURVEILLANCE_OVERVIEW + PUBLISH
    else:  # surveillance-only
        plan = SURVEILLANCE + SURVEILLANCE_OVERVIEW

    # Fail-fast gate: catch module-level defects before any heavy scan.
    _smoke_import(plan)

    if args.mode == "fast":
        _warn_if_heavy_outputs_stale()

    # Crash-resume journal for the long modes only; fast is ~1 min, not worth it.
    journal = None
    if args.mode in ("weekly", "full", "surveillance-only"):
        if args.fresh:
            _clear_journal()
        journal = _load_journal(_run_key(args.mode))

    _run(plan, journal)
    if journal is not None:
        _clear_journal()


if __name__ == "__main__":
    main()
