# Weekly Dashboard Refresh
# Runs overnight: delta pull -> process -> append -> paper4 rebuild -> dashboard -> deploy
# Total: ~4-6 hours

$ErrorActionPreference = "Stop"
$LOG      = "C:\Users\joshd\Dev\polymarket-index\refresh.log"
$DATA     = "J:\Research\10. Prediction\data\blockchain"
$PAPER4   = "$DATA\paper4"
$REPO     = "C:\Users\joshd\Dev\polymarket-index"
$date_tag = (Get-Date -Format "yyyyMMdd")
$week_label = (Get-Date -Format "yyyy-MM-dd")

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
    if ($LASTEXITCODE -and $LASTEXITCODE -gt 1) {
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

# Load Alchemy key from .env
if (Test-Path "$DATA\.env") {
    Get-Content "$DATA\.env" | ForEach-Object {
        if ($_ -match "^ALCHEMY_API_KEY=(.+)$") {
            $env:ALCHEMY_API_KEY = $Matches[1].Trim()
            Log "Alchemy key loaded"
        }
    }
}

# ---- PHASE 1: Blockchain delta -----------------------------------------------

# Seed today's checkpoint from May 9 so pull resumes from block 86,609,906
$today_ckpt = "$DATA\checkpoint_block_delta_$date_tag.json"
$may9_ckpt  = "$DATA\checkpoint_block_delta_20260509.json"
if (-not (Test-Path $today_ckpt)) {
    Copy-Item $may9_ckpt $today_ckpt
    Log "Checkpoint seeded from 20260509 (block 86609906)"
}

Run "Pull delta events"   { python "$DATA\pull_polygon_delta_fast.py" }
Run "Process delta"       { python "$DATA\process_delta.py" }
Run "Append to master"    { python "$DATA\append_delta_to_master.py" }
Run "Expand resolutions"  { python "$DATA\expand_resolutions.py" }

# ---- PHASE 2: Rebuild paper4 source CSVs ------------------------------------

Run "feasibility_calibration"   { python "$PAPER4\feasibility_calibration.py" }
Run "composition_decomposition" { python "$PAPER4\composition_decomposition.py" --force }

# ---- PHASE 3: Dashboard pipeline --------------------------------------------

Set-Location $REPO
Run "pipeline/run_all.py" { python pipeline/run_all.py }

# ---- PHASE 4: Deploy --------------------------------------------------------

Log "START: git commit + push"
git add -A
$msg = "weekly refresh $week_label"
git commit -m $msg
git push
Log "DONE:  deployed to GitHub Pages"

Log "=========================================="
Log "ALL DONE $week_label"
Log "=========================================="
