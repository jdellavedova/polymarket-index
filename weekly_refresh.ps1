# Weekly Dashboard Refresh - THE canonical orchestrator (all others archived
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
$LOCK     = "C:\Users\joshd\Dev\polymarket-index\refresh.lock"
$STATUS   = "C:\Users\joshd\Dev\polymarket-index\refresh_status.json"

# Capture the ACTUAL current UTC date at run time, ONCE, and pass it to every
# step - so a run that crosses UTC midnight still processes/appends the files
# the pull created, and a catch-up run on a different weekday tags correctly.
$date_tag   = [datetime]::UtcNow.ToString("yyyyMMdd")
$week_label = [datetime]::UtcNow.ToString("yyyy-MM-dd")

function Log($msg) {
    $ts   = Get-Date -Format "HH:mm:ss"
    $line = "[$ts] $msg"
    Write-Output $line
    # A transient lock on the log file (editor, AV scan, sync client) must never
    # kill the pipeline: retry briefly, then give up on the file write only.
    for ($i = 0; $i -lt 3; $i++) {
        try { Add-Content -Path $LOG -Value $line -ErrorAction Stop; return }
        catch { Start-Sleep -Milliseconds 500 }
    }
    Write-Output "[$ts] (log file locked - line not persisted)"
}

# Terminal status file: the single source of truth for "did the last refresh
# actually finish?". Written at every phase transition, so a killed process
# leaves status=running at the dead phase instead of a silent green.
function Set-Status($phase, $state) {
    $payload = @{
        run_date   = $week_label
        phase      = $phase
        status     = $state
        updated_at = [datetime]::UtcNow.ToString("o")
    } | ConvertTo-Json
    try { Set-Content -Path $STATUS -Value $payload -Encoding utf8 -ErrorAction Stop }
    catch { Log "WARNING: could not write status file ($phase/$state)" }
}

function Fail($label) {
    Log "ERROR: $label failed (exit $LASTEXITCODE)"
    Set-Status $label "failed"
    Remove-Item $LOCK -Force -Confirm:$false -ErrorAction SilentlyContinue
    exit 1
}

# ---- PRE-RUN GUARDS -----------------------------------------------------------
# 1. Overlap lock: never let two refreshes run at once (a stale lock older
#    than 12h is assumed dead and replaced).
if (Test-Path $LOCK) {
    $lockAge = (Get-Date) - (Get-Item $LOCK).LastWriteTime
    if ($lockAge.TotalHours -lt 12) {
        Log "SKIP: another refresh appears to be running (lock is $([math]::Round($lockAge.TotalMinutes)) min old). Delete $LOCK to override."
        exit 0
    }
    Log "Stale lock ($([math]::Round($lockAge.TotalHours,1)) h old) - replacing."
}
Set-Content -Path $LOCK -Value "$date_tag $(Get-Date -Format o)"

# 2. Already-ran check — keyed to COMPLETION (refresh_status.json), never to
#    the master append alone. The old frontier-based guard turned a manual
#    mid-week append into a silent skip of the aggregate+publish phases (the
#    July 2026 false-green: task result 0, site never updated).
$skipPull = $false
if (Test-Path $STATUS) {
    try {
        $prev = Get-Content $STATUS -Raw | ConvertFrom-Json
        $prevAge = ([datetime]::UtcNow - [datetime]::Parse($prev.updated_at).ToUniversalTime()).TotalDays
        if ($prev.status -eq "success" -and $prevAge -lt 3) {
            Log "SKIP: last refresh completed successfully $([math]::Round($prevAge,1)) days ago. Next refresh is due Sunday."
            Remove-Item $LOCK -Force -Confirm:$false
            exit 0
        }
        if ($prev.status -ne "success") {
            Log "WARNING: previous refresh did NOT complete (phase '$($prev.phase)', status '$($prev.status)', $([math]::Round($prevAge,1)) days ago). Running catch-up."
        }
    } catch {
        Log "WARNING: could not parse $STATUS - proceeding anyway."
    }
}

# 3. Fresh-append check: if the master was already appended in the last 3 days
#    (e.g. a manual catch-up), skip ONLY the pull/process/append phases and
#    still run the aggregation + publish, which is what the site needs.
$frontierFile = "$DATA\master_frontier.json"
if (Test-Path $frontierFile) {
    try {
        $frontier = Get-Content $frontierFile -Raw | ConvertFrom-Json
        $lastAppend = [datetime]::Parse($frontier.updated_at).ToUniversalTime()
        $daysSince = ([datetime]::UtcNow - $lastAppend).TotalDays
        if ($daysSince -lt 3 -and $frontier.rows_appended -gt 0) {
            $skipPull = $true
            Log "Master already appended $([math]::Round($daysSince,1)) days ago ($($frontier.last_appended_delta)) - skipping pull phase, running aggregation + publish."
        }
    } catch {
        Log "WARNING: could not parse $frontierFile - proceeding with full run."
    }
}

function Run($label, $scriptBlock) {
    Log "START: $label"
    Set-Status $label "running"
    $t = [System.Diagnostics.Stopwatch]::StartNew()
    & $scriptBlock
    if ($LASTEXITCODE -ne 0) { Fail $label }
    $t.Stop()
    $mins = [math]::Round($t.Elapsed.TotalMinutes, 1)
    Log "DONE:  $label ($mins min)"
}

try { Add-Content -Path $LOG -Value "" -ErrorAction Stop } catch {}
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

if (-not $skipPull) {
    Run "Pull delta events"   { python "$DATA\pull_polygon_delta_fast.py" }
    Run "Process delta"       { python "$DATA\process_delta.py" $date_tag }
    Run "Append to master"    { python "$DATA\append_delta_to_master.py" $date_tag }
} else {
    Log "PHASE 1 skipped (master already current)."
}

# ---- PHASE 2: Dashboard pipeline (weekly = fast + heavy; paper4 sources ------
# refresh incrementally inside run_all via refresh_paper4_sources). run_all ----
# smoke-imports every module before scanning and keeps a resume journal, so ----
# a rerun after a crash skips the stages that already completed. --------------

Set-Location $REPO
Run "pipeline/run_all.py --mode=weekly" { python pipeline\run_all.py --mode=weekly }

# ---- PHASE 3: Deploy ----------------------------------------------------------

Log "START: git commit + push"
Set-Status "git push" "running"
git add site/public/data
if ($LASTEXITCODE -ne 0) { Fail "git add" }
git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    git commit -m "weekly refresh $week_label"
    if ($LASTEXITCODE -ne 0) { Fail "git commit" }
    git push
    if ($LASTEXITCODE -ne 0) { Fail "git push" }
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

Remove-Item $LOCK -Force -Confirm:$false -ErrorAction SilentlyContinue
Set-Status "done" "success"

Log "=========================================="
Log "ALL DONE $week_label"
Log "=========================================="
