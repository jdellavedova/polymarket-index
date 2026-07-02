# weekly_full_refresh.ps1
#
# End-to-end Polymarket dashboard refresh pipeline.
# Steps:
#   1. Pull new blockchain events (delta from last checkpoint)
#   2. Process delta into processed_trades_delta_{date}.csv
#   3. Append delta to master processed_trades.csv
#   4. Update paper4 weekly CSVs (incremental Prelec fit — minutes, not hours)
#   5. Regenerate dashboard JSON/CSV outputs (--mode=fast: no full master-CSV scan)
#   6. Commit and push to GitHub
#
# Schedule: Windows Task Scheduler, weekly, Sunday 10 PM PT
# Log: C:\scripts\dashboard_refresh.log

param(
    [string]$DateTag = (Get-Date -Format "yyyyMMdd"),
    [switch]$SkipPull,    # skip step 1 if pull already ran
    [switch]$NoPush       # skip git push (for local testing)
)

$ErrorActionPreference = "Stop"
$LogFile = "C:\scripts\dashboard_refresh.log"
$BlockchainDir = "H:\Research\10. Prediction\data\blockchain"
$Paper4Dir = "$BlockchainDir\paper4"
$DashboardDir = "C:\Users\joshd\Dev\polymarket-index"

function Write-Log {
    param([string]$Msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

function Check-Exit {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        Write-Log "ERROR: $Step failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
}

Write-Log "========================================================"
Write-Log "WEEKLY DASHBOARD REFRESH — $DateTag"
Write-Log "========================================================"

# ---- Step 1: Pull blockchain delta ----
if (-not $SkipPull) {
    Write-Log "Step 1: Pulling blockchain delta..."
    Push-Location $BlockchainDir
    python pull_polygon_delta_fast.py 2>&1 | Tee-Object -FilePath "$BlockchainDir\delta_run_$DateTag.log"
    Check-Exit "pull_polygon_delta_fast.py"
    Pop-Location
    Write-Log "Step 1 complete."
} else {
    Write-Log "Step 1: Skipped (--SkipPull)"
}

# ---- Step 2: Process delta ----
Write-Log "Step 2: Processing delta..."
Push-Location $BlockchainDir
python process_delta.py $DateTag 2>&1 | Tee-Object -FilePath "$BlockchainDir\process_delta_$DateTag.log"
Check-Exit "process_delta.py"
Pop-Location
Write-Log "Step 2 complete."

# ---- Step 3: Append to master ----
Write-Log "Step 3: Appending delta to master..."
Push-Location $BlockchainDir
python append_delta_to_master.py $DateTag 2>&1 | Tee-Object -FilePath "$BlockchainDir\append_delta_$DateTag.log"
Check-Exit "append_delta_to_master.py"
Pop-Location
Write-Log "Step 3 complete."

# ---- Step 4: Incremental paper4 update ----
Write-Log "Step 4: Updating paper4 weekly CSVs (incremental)..."
Push-Location $Paper4Dir
python update_paper4_incremental.py $DateTag 2>&1 | Tee-Object -FilePath "$Paper4Dir\incremental_$DateTag.log"
Check-Exit "update_paper4_incremental.py"
Pop-Location
Write-Log "Step 4 complete."

# ---- Step 5: Dashboard pipeline ----
Write-Log "Step 5: Regenerating dashboard outputs..."
Push-Location $DashboardDir
python pipeline/run_all.py --mode=fast 2>&1 | Tee-Object -FilePath "$DashboardDir\pipeline_$DateTag.log"
Check-Exit "pipeline/run_all.py"
Pop-Location
Write-Log "Step 5 complete."

# ---- Step 6: Commit and push ----
if (-not $NoPush) {
    Write-Log "Step 6: Committing and pushing to GitHub..."
    Push-Location $DashboardDir
    git add site/public/data/
    git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Log "  No data changes to commit."
    } else {
        $commitMsg = "Weekly dashboard refresh $DateTag"
        git commit -m $commitMsg
        Check-Exit "git commit"
        git push
        Check-Exit "git push"
        Write-Log "  Pushed to GitHub."
    }
    Pop-Location
} else {
    Write-Log "Step 6: Skipped (--NoPush)"
}

Write-Log "========================================================"
Write-Log "REFRESH COMPLETE"
Write-Log "========================================================"
