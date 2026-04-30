# `profit_split/` — chunked, incremental profit decomposition

This module computes the per-(week, wallet_type) profit decomposition that
feeds the dashboard's "Who profits" section. Replaces the legacy
`pipeline/aggregate_profit_split.py` (now a thin wrapper).

## Methodology

**Benchmark:** per-(market_id, token_id) buy-side VWAP. Identical to Paper 1
main analysis (`J:/Research/10. Prediction/data/blockchain/analysis/
revision_code/05_return_decomposition.py` lines 76-140).

```
fair_price[m, t] = SUM(price * usdc_amount) / SUM(usdc_amount)
                    over BUY-side trades only, grouped by (market m, token t)
```

**Decomposition:** for each match,
```
sign       = +1 if maker_side='BUY' else -1
maker_pnl  = sign * (W - P) * Q     where W = 1 if token side won, else 0
maker_dir  = sign * (W - fair) * Q
maker_exec = sign * (fair - P) * Q
sum: maker_dir + maker_exec = maker_pnl  (algebraic identity)
```

**Attribution:** both maker AND taker, with mirror signs.
- Maker row: (pnl, dir, exec) attributed to wallet_type[maker_address]
- Taker row: (-pnl, -dir, -exec) attributed to wallet_type[taker_address]
- System total per week sums to zero across all wallet types.

This departs from Paper 1's maker-only attribution (Paper 1 reports academic-
standard maker-only stats; the dashboard prioritizes a public-readable
zero-sum display).

**ROI bps:** `pnl / usd_volume * 10000`. Volume-weighted ROI, NOT Paper 1's
per-trade edge measure. The two are different denominators and not directly
comparable.

## Files

- `build_fair_prices.py` — one-time. Streams the master CSV, computes
  per-(market, token) buy-side VWAP, writes `cache/fair_prices.parquet`.
  Runtime ~25 min. Run once at module setup or after major data refresh.

- `rebuild_history.py` — one-time. After `build_fair_prices.py`, re-streams
  the master CSV with both-side decomposition, produces full
  `profit_split_history.csv` and `profit_split_latest.json`. Runtime ~30-45
  min. Run after methodology changes.

- `update_weekly.py` — weekly. Reads `cache/weekly_state.json` for the last
  processed block, filters master CSV to new trades only, updates fair-prices
  cache incrementally, decomposes new trades, merges into history CSV,
  rebuilds latest JSON. Runtime 1-3 min. This is the script the weekly
  refresh job runs.

- `cache/fair_prices.parquet` — per-(market, token) buy-side VWAP cache with
  `pv_sum`, `v_sum`, `fair_price`, `last_block` columns.

- `cache/weekly_state.json` — `{last_processed_week, last_processed_block,
  generated_at}`.

## Operational sequence

### Initial deployment (one-time)
```
python pipeline/profit_split/build_fair_prices.py
python pipeline/profit_split/rebuild_history.py
```

### Weekly refresh (every Sunday after Polygon delta pull)
```
python pipeline/profit_split/update_weekly.py
```

### Backward compatibility
`pipeline/aggregate_profit_split.py` is a thin wrapper that calls
`update_weekly.main()`. The orchestrator at `pipeline/run_all.py` still
invokes `aggregate_profit_split` and gets the new behavior transparently.
