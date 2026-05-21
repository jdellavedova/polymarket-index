"""build_fair_prices.py — one-time per-(market, token) buy-side VWAP cache.

Writes `cache/fair_prices.parquet` with columns:
  market_id_res, token_id, pv_sum, v_sum, fair_price, last_block

This is Paper 1's benchmark per `J:/Research/10. Prediction/data/blockchain/
analysis/revision_code/05_return_decomposition.py` lines 76-140:

    fair_price = SUM(price * usdc_amount) / SUM(usdc_amount)
                  over BUY-side trades only, grouped by (market_id, token_id)

The pv_sum / v_sum / last_block columns are kept so update_weekly.py can
extend the VWAP incrementally as new trades arrive without re-scanning the
full master CSV.

Streams the master CSV in 5M-row chunks via pandas (memory-bounded).
Runtime: ~25 min on local NVMe. Peak memory: ~3 GB.

Usage:
    python pipeline/profit_split/build_fair_prices.py
"""
from __future__ import annotations

import gc
import io
import pickle
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

DATA_DIR = Path("H:/Research/10. Prediction/data/blockchain")
TRADES = DATA_DIR / "processed_trades.csv"
TOKEN_PKL = DATA_DIR / "token_outcome_map.pkl"

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
FAIR_PRICES_OUT = CACHE_DIR / "fair_prices.parquet"

CHUNK_SIZE = 5_000_000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    log("Building per-(market_id, token_id) buy-side VWAP cache ...")

    log("Loading token -> market mapping ...")
    with open(TOKEN_PKL, "rb") as f:
        token_outcome = pickle.load(f)
    # token_outcome[token_id_str] = (market_id_str, outcome_label_str)
    token_to_market = {str(k): str(v[0]) for k, v in token_outcome.items()}
    log(f"  {len(token_to_market):,} token -> market mappings")

    # Streaming accumulators keyed by (market_id, token_id)
    pv_sum: dict[tuple[str, str], float] = defaultdict(float)
    v_sum: dict[tuple[str, str], float] = defaultdict(float)
    n_trades: dict[tuple[str, str], int] = defaultdict(int)
    last_block: dict[tuple[str, str], int] = defaultdict(int)

    t0 = time.time()
    rows_seen = 0
    rows_buy = 0

    usecols = ["blockNumber", "token_id", "maker_side", "usdc_amount", "price"]
    dtypes = {
        "blockNumber": "int64",
        "token_id": "string",
        "maker_side": "string",
        "usdc_amount": "float64",
        "price": "float64",
    }

    for chunk_idx, chunk in enumerate(
        pd.read_csv(TRADES, chunksize=CHUNK_SIZE, usecols=usecols, dtype=dtypes,
                    low_memory=False, encoding_errors='replace')
    ):
        rows_seen += len(chunk)

        # Buy-side filter (Paper 1 line 107)
        buys = chunk[chunk["maker_side"].str.upper() == "BUY"]
        if buys.empty:
            del chunk
            continue
        rows_buy += len(buys)

        # Map token -> market
        buys = buys.copy()
        buys["market_id_res"] = buys["token_id"].map(token_to_market)
        buys = buys.dropna(subset=["market_id_res"])

        # Pre-compute price * usdc per row
        buys["pv"] = buys["price"] * buys["usdc_amount"]

        # Aggregate within chunk
        agg = buys.groupby(["market_id_res", "token_id"], sort=False).agg(
            pv_sum=("pv", "sum"),
            v_sum=("usdc_amount", "sum"),
            n=("price", "size"),
            last_block=("blockNumber", "max"),
        )

        # Merge into running totals
        for (mid, tid), row in agg.iterrows():
            key = (mid, tid)
            pv_sum[key] += row.pv_sum
            v_sum[key] += row.v_sum
            n_trades[key] += int(row.n)
            if int(row.last_block) > last_block[key]:
                last_block[key] = int(row.last_block)

        del chunk, buys, agg
        gc.collect()

        elapsed = time.time() - t0
        rate = rows_seen / max(elapsed, 0.1) / 1e6
        log(
            f"  chunk {chunk_idx+1:>3} | rows seen {rows_seen/1e6:.0f}M "
            f"({rows_buy/1e6:.0f}M buys) | unique tokens {len(pv_sum):,} | "
            f"{rate:.1f} M rows/s | elapsed {elapsed/60:.1f} min"
        )

    # Materialize as DataFrame
    log("Materializing parquet output ...")
    keys = list(pv_sum.keys())
    df = pd.DataFrame({
        "market_id_res": [k[0] for k in keys],
        "token_id": [k[1] for k in keys],
        "pv_sum": [pv_sum[k] for k in keys],
        "v_sum": [v_sum[k] for k in keys],
        "n_trades": [n_trades[k] for k in keys],
        "last_block": [last_block[k] for k in keys],
    })
    # Compute fair_price now (faster lookup at update time)
    df["fair_price"] = np.where(df["v_sum"] > 0, df["pv_sum"] / df["v_sum"], np.nan)

    df.to_parquet(FAIR_PRICES_OUT, index=False, compression="zstd")
    log(
        f"Wrote {FAIR_PRICES_OUT} | rows={len(df):,} | "
        f"non-null fair_price={df['fair_price'].notna().sum():,}"
    )
    log(f"Total time: {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
