Set-Location "H:\Research\10. Prediction\data\blockchain"
$env:ALCHEMY_API_KEY = "G_nt50leDhp-dugPj7A3a"
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Starting blockchain pull (block 87271027 to current)..."
python pull_polygon_delta_fast.py
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Pull done. Starting process_delta..."
python process_delta.py 20260605
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Processing done. Starting append_delta_to_master..."
python append_delta_to_master.py 20260605
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Append done. Running dashboard pipeline..."
Set-Location "C:\Users\joshd\Dev\polymarket-index"
python pipeline/run_all.py
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ALL DONE."