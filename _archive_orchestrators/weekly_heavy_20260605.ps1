Set-Location "C:\Users\joshd\Dev\polymarket-index"
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting --mode=weekly (heavy scans)..."
python pipeline/run_all.py --mode=weekly
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ALL DONE."