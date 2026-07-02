# One-time continuation of the May 21 weekly refresh.
# process_delta.py ran on J: (path was loaded before we updated scripts),
# so delta CSV is on J:. This script copies it to H: then finishes the pipeline.

$ErrorActionPreference = "Stop"
$LOG    = "C:\Users\joshd\Dev\polymarket-index\refresh.log"
$DATA_J = "J:\Research\10. Prediction\data\blockchain"
$DATA_H = "H:\Research\10. Prediction\data\blockchain"
$PAPER4 = "$DATA_H\paper4"
$REPO   = "C:\Users\joshd\Dev\polymarket-index"

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
Log "CONTINUE REFRESH $week_label (J->H migration)"
Log "=========================================="

# ---- Step 1: Verify process_delta.py is done ---------------------------------

$delta_csv_j = "$DATA_J\processed_trades_delta_$date_tag.csv"
$delta_csv_h = "$DATA_H\processed_trades_delta_$date_tag.csv"

if (-not (Test-Path $delta_csv_j)) {
    Log "ERROR: $delta_csv_j not found. Is process_delta.py still running?"
    exit 1
}
$sz = [math]::Round((Get-Item $delta_csv_j).Length / 1GB, 1)
Log "Delta CSV on J: found ($sz GB): $(Split-Path $delta_csv_j -Leaf)"

# ---- Step 2: Copy delta CSV from J: to H: ------------------------------------

Log "Copying delta CSV to H:..."
Copy-Item $delta_csv_j $delta_csv_h -Force
$sz2 = [math]::Round((Get-Item $delta_csv_h).Length / 1GB, 1)
Log "Copied to H: ($sz2 GB)"

# ---- Step 3: Also copy updated wallet_statistics.csv if needed ---------------
# The wallet_statistics on H: was from Apr 23; J: has a newer copy from May 9
$ws_j = "$DATA_J\wallet_statistics.csv"
$ws_h = "$DATA_H\wallet_statistics.csv"
$ws_j_time = (Get-Item $ws_j).LastWriteTime
$ws_h_time = (Get-Item $ws_h).LastWriteTime
if ($ws_j_time -gt $ws_h_time) {
    Log "wallet_statistics.csv on J: is newer ($ws_j_time vs $ws_h_time) -- copying..."
    Copy-Item $ws_j $ws_h -Force
    Log "Copied wallet_statistics.csv to H:"
} else {
    Log "wallet_statistics.csv on H: is current"
}

# ---- Step 4: Append delta to master on H: ------------------------------------

Run "Append delta to master"  { python "$DATA_H\append_delta_to_master.py" }
Run "Expand resolutions"      { python "$DATA_H\expand_resolutions.py" }

# ---- Step 5: Rebuild paper4 source CSVs on H: --------------------------------

Run "feasibility_calibration"   { python "$PAPER4\feasibility_calibration.py" }
Run "composition_decomposition" { python "$PAPER4\composition_decomposition.py" --force }

# ---- Step 6: Dashboard pipeline ----------------------------------------------

Set-Location $REPO
Run "pipeline/run_all.py" { python pipeline/run_all.py }

# ---- Step 7: Deploy ----------------------------------------------------------

Log "START: git commit + push"
git add -A
$msg = "weekly refresh $week_label"
git commit -m $msg
git push
Log "DONE:  deployed to GitHub Pages"

Log "=========================================="
Log "ALL DONE $week_label"
Log "=========================================="
