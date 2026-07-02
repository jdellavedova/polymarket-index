# Weekly Dashboard Refresh — THE canonical orchestrator (all others archived
# in _archive_orchestrators\). Scheduled via Windows Task Scheduler, Sunday.
#
# Flow: pull new blocks -> process delta -> append to master (dedup-guarded,
#       parquet mirror) -> run_all --mode=weekly -> git push -> cleanup
#
# Incremental by construction:
#  - pull_polygon_delta_fast.py resumes from the HIGHEST checkpoint_block_delta_*.json
#    (no manual seeding; the old May-9 seeding block that forced re-downloads is gone)
#  - append_delta_to_master.py skips rows at/below master_frontier.json, so a
#    re-pulled window can never double-append
#  - run_all --mode=weekly includes the heavy scans (~30-40 min on CSV, ~5 min
#    once trades_parquet/ is built), so nothing on the site goes stale
#
# Steady-state runtime: ~1.5 h. First run after a gap: add ~20 min per backlog week.

$ErrorActionPreference = "Stop"
$LOG      = "C:\Users\joshd\Dev\polymarket-index\refresh.log"
$DATA     = "H:\Research\10. Prediction\data\blockchain"
$REPO     = "C:\Users\joshd\Dev\polymarket-index"

# Capture the UTC date tag ONCE and pass it to every step, so a run that
# crosses UTC midnight still processes/appends the files the pull created.
$date_tag   = (Get-Date -AsUTC -Format "yyyyMMdd")
$week_label = (Get-Date -AsUTC -Format "yyyy-MM-dd")

function Log($msg) {
    $ts   = Get-Date -Format "HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Output $line
    Add-Content -Path $LOG -Value $line
}

function Run($label, $scriptBlock) {
    Log "START: $label"
    $t = [System.Diagnostics.Stopwatch]::StartNew()
    & $scriptBlock
    if ($LASTEXITCODE -ne 0) {
        Log "ERROR: $label failed (exit $LASTEXITCODE)"
        exit 1
    }
    $t.Stop()
    $mins = [math]::Round($t.Elapsed.TotalMinutes, 1)
    Log "DONE:  $label ($mins min)"
}

Add-Content -Path $LOG -Value ""
Log "=========================================="
Log "WEEKLY REFRESH $week_label"
Log "=========================================="

# Load Alchemy key from the repo .env (the pull script also self-loads it,
# this just makes it explicit in the environment for any child process)
if (Test-Path "$REPO\.env") {
    Get-Content "$REPO\.env" | ForEach-Object {
        if ($_ -match "^ALCHEMY_API_KEY=(.+)$") {
            $env:ALCHEMY_API_KEY = $Matches[1].Trim()
            Log "Alchemy key loaded"
        }
    }
}

# ---- PHASE 1: Blockchain delta (pull auto-resumes from max checkpoint) -------

Run "Pull delta events"   { python "$DATA\pull_polygon_delta_fast.py" }
Run "Process delta"       { python "$DATA\process_delta.py" $date_tag }
Run "Append to master"    { python "$DATA\append_delta_to_master.py" $date_tag }

# ---- PHASE 2: Dashboard pipeline (weekly = fast + heavy; paper4 sources ------
# refresh incrementally inside run_all via refresh_paper4_sources) ------------

Set-Location $REPO
Run "pipeline/run_all.py --mode=weekly" { python pipeline\run_all.py --mode=weekly }

# ---- PHASE 3: Deploy ----------------------------------------------------------

Log "START: git commit + push"
git add site/public/data
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "weekly refresh $week_label"
    git push
    Log "DONE:  deployed to GitHub Pages"
} else {
    Log "DONE:  no data changes to commit"
}

# ---- PHASE 4: Cleanup (raw deltas are ~40 GB/week; keep only this run's) ------

Get-ChildItem "$DATA\raw_events_delta_*.csv" | Where-Object {
    $_.Name -ne "raw_events_delta_$date_tag.csv"
} | ForEach-Object {
    Log "Cleanup: deleting $($_.Name) ($([math]::Round($_.Length/1GB,1)) GB)"
    Remove-Item $_.FullName -Force -Confirm:$false
}
# Keep the two most recent processed deltas (current + previous cycle)
Get-ChildItem "$DATA\processed_trades_delta_*.csv" | Sort-Object Name -Descending |
    Select-Object -Skip 2 | ForEach-Object {
    Log "Cleanup: deleting $($_.Name) ($([math]::Round($_.Length/1GB,1)) GB)"
    Remove-Item $_.FullName -Force -Confirm:$false
}

Log "=========================================="
Log "ALL DONE $week_label"
Log "=========================================="
