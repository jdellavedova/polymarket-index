Set-Location "C:\Users\joshd\Dev\polymarket-index"
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Running remaining heavy aggregators + publish..."
python -c "
import importlib, sys, time
sys.path.insert(0, 'pipeline')
for name in ['aggregate_top_markets', 'aggregate_market_microstructure', 'aggregate_profit_split',
             'build_master_table', 'build_weekly_narrative', 'build_commentary',
             'build_briefings', 'build_og_image', 'build_press_kit',
             'build_email_digest', 'build_social_posts']:
    t0 = time.time()
    print(f'=== {name} ===', flush=True)
    try:
        mod = importlib.import_module(name)
        mod.main()
        print(f'  done in {time.time()-t0:.0f}s', flush=True)
    except Exception as e:
        print(f'  ERROR: {e}', flush=True)
"
Write-Host "[$(Get-Date -Format 'HH:mm:ss')] ALL DONE."