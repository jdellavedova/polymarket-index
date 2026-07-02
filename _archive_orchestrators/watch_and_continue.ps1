# Watches process_delta_20260521.log and runs continuation when done.
$log      = "J:\Research\10. Prediction\data\blockchain\process_delta_20260521.log"
$continue = "C:\Users\joshd\Dev\polymarket-index\continue_refresh_20260521.ps1"

Write-Output "Watching $log for completion..."

$max_wait = 120  # max minutes to wait
$elapsed  = 0

while ($elapsed -lt $max_wait) {
    Start-Sleep -Seconds 60
    $elapsed++

    $tail = Get-Content $log -Tail 2 -ErrorAction SilentlyContinue
    $tail | ForEach-Object { Write-Output "[watch] $_" }

    $done = $tail | Where-Object { $_ -match "Done in|Next: run append_delta" }
    $err  = $tail | Where-Object { $_ -match "ERROR|Traceback|Exception" }

    if ($err) {
        Write-Output "ERROR detected in process_delta.py -- aborting watch."
        exit 1
    }
    if ($done) {
        Write-Output "process_delta.py finished. Launching continuation..."
        & $continue
        exit 0
    }
}
Write-Output "Timeout after $max_wait minutes."
exit 1
