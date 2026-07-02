# Archived orchestrator scripts (July 1, 2026)

These scripts are superseded by the single canonical `weekly_refresh.ps1` in
the repo root. Each was broken in a different way; kept for reference only.
DO NOT run any of them.

| Script | Why archived |
|---|---|
| (old) `weekly_refresh.ps1` | Seeded every run's checkpoint from the frozen May 9 file (block 86,609,906), forcing an ever-growing re-download window. Rewritten in place; old version in git history. |
| `run_weekly.ps1` | No blockchain pull at all; depended on `H:\...\trades_parquet\` before it existed via `tools/incremental_reaggregate.py`. |
| `weekly_full_refresh_from_Cscripts.ps1` | Lived at `C:\scripts\`; ran the pull with no checkpoint seeding, which hit the missing `checkpoint_block_apr21_refresh.json` fallback and crashed with RuntimeError. |
| `update20260605.ps1`, `run_full_update_20260605.ps1`, `weekly_heavy_20260605.ps1`, `heavy_remainder_20260606.ps1` | One-off June 5-6 run scripts with hardcoded block ranges/checkpoints. |
| `continue_refresh_20260521.ps1`, `watch_and_continue.ps1` | One-off J:-to-H: migration helpers from May 21. |

The root causes they papered over are now fixed at the source:
- `pull_polygon_delta_fast.py` resumes from the max of all `checkpoint_block_delta_*.json` files
- `append_delta_to_master.py` guards against overlap via `master_frontier.json`
