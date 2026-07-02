cd "H:\Research\10. Prediction\data\blockchain"
$env:ALCHEMY_API_KEY = "G_nt50leDhp-dugPj7A3a"
Write-Output "[$(Get-Date -Format 'HH:mm:ss')] Starting blockchain pull (block 87271027 → current)..."
python pull_polygon_delta_fast.py 2>&1
Write-Output "[$(Get-Date -Format 'HH:mm:ss')] Pull complete. Starting process_delta..."
python process_delta.py 20260605 2>&1
Write-Output "[$(Get-Date -Format 'HH:mm:ss')] Processing complete. Starting append_delta_to_master..."
python append_delta_to_master.py 20260605 2>&1
Write-Output "[$(Get-Date -Format 'HH:mm:ss')] Append complete. Running dashboard pipeline..."
cd "C:\Users\joshd\Dev\polymarket-index"
python pipeline/run_all.py 2>&1
Write-Output "[$(Get-Date -Format 'HH:mm:ss')] ALL DONE."
