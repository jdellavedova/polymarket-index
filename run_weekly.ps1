# run_weekly.ps1 — Master weekly dashboard refresh
#
# Designed for Windows Task Scheduler (Sunday 10 PM PT).
# Prerequisites:
#   - H: drive mounted with trades_parquet/ populated by convert_to_parquet.py
#   - Python env with pandas, pyarrow, scipy in PATH
#   - git remote configured and authenticated
#
# Runtime: ~20-30 min (incremental reaggregation + dashboard pipeline)
# First run after convert_to_parquet.py: longer (fits all historical weeks)

$ErrorActionPreference = "Stop"
$RepoRoot = $PSScriptRoot
$LogFile  = Join-Path $RepoRoot "run_weekly.log"

function Log($msg) {
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    "$ts  $msg" | Tee-Object -FilePath $LogFile -Append
}

Set-Location $RepoRoot
Log "=== Weekly refresh starting ==="

# 1. Incremental paper4 reaggregation (reads only new Parquet weeks)
Log "Step 1: incremental_reaggregate.py"
python tools/incremental_reaggregate.py
if (-not $?) { Log "ERROR: incremental_reaggregate.py failed"; exit 1 }
Log "Step 1 complete"

# 2. Dashboard pipeline (all aggregators → JSON/CSV/PNG)
Log "Step 2: pipeline/run_all.py"
python pipeline/run_all.py
if (-not $?) { Log "ERROR: run_all.py failed"; exit 1 }
Log "Step 2 complete"

# 3. Commit updated data outputs and push to trigger GitHub Pages rebuild
Log "Step 3: git commit + push"
git add site/public/data/
git add site/public/press/
# og.png is regenerated each run if present
if (Test-Path "site/public/og.png") { git add site/public/og.png }

$changed = git diff --cached --name-only
if ($changed) {
    $date = Get-Date -Format "yyyy-MM-dd"
    git commit -m "Weekly refresh $date"
    git push
    Log "Pushed: $($changed -join ', ')"
} else {
    Log "No data changes — nothing to push"
}

Log "=== Weekly refresh complete ==="
