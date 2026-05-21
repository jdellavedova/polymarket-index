"""update_weekly.py — incremental weekly update of profit_split outputs.

Reads cache/weekly_state.json for last_processed_block, filters the master
CSV to trades with blockNumber > last_processed_block, updates the
fair_prices cache for any new (market, token) pairs or new buy-side volume,
computes per-trade decomposition for the new trades, aggregates by
(week, wallet_type), merges into profit_split_history.csv, rebuilds
profit_split_latest.json from the most recent fully-resolved week.

Designed to run weekly after a fresh Polygon delta pull. Runtime: ~1-3 min
when only ~30M new trades. Memory: ~2-4 GB peak.

Usage:
    python pipeline/profit_split/update_weekly.py

If cache/weekly_state.json doesn't exist, falls back to running
rebuild_history.py logic from scratch.
"""
from __future__ import annotations

import gc
import io
import json
import pickle
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import utc_now, write_json
from config import DATA_OUT

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)

DATA_DIR = Path("H:/Research/10. Prediction/data/blockchain")
TRADES = DATA_DIR / "processed_trades.csv"
TOKEN_PKL = DATA_DIR / "token_outcome_map.pkl"
WINNER_PKL = DATA_DIR / "market_winner_map.pkl"
WALLETS = DATA_DIR / "wallet_statistics.csv"

CACHE_DIR = Path(__file__).resolve().parent / "cache"
FAIR_PRICES = CACHE_DIR / "fair_prices.parquet"
WEEKLY_STATE = CACHE_DIR / "weekly_state.json"
HIST_CSV = DATA_OUT / "profit_split_history.csv"

CHUNK_SIZE = 5_000_000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    if not WEEKLY_STATE.exists() or not FAIR_PRICES.exists() or not HIST_CSV.exists():
        log("Cache or history missing; falling back to full rebuild.")
        from rebuild_history import main as rebuild
        rebuild()
        return

    state = json.loads(WEEKLY_STATE.read_text())
    last_block = int(state.get("last_processed_block", 0))
    log(f"Last processed block: {last_block:,}")

    t0 = time.time()
    log("Loading caches and lookups ...")

    fair_df = pd.read_parquet(FAIR_PRICES)
    fair_lookup = dict(zip(fair_df["token_id"].astype(str), fair_df["fair_price"]))
    pv_lookup = dict(zip(fair_df["token_id"].astype(str), fair_df["pv_sum"]))
    v_lookup = dict(zip(fair_df["token_id"].astype(str), fair_df["v_sum"]))
    log(f"  fair_prices cache: {len(fair_df):,} tokens")

    with open(TOKEN_PKL, "rb") as f:
        token_outcome = pickle.load(f)
    token_to_outcome = {str(k): str(v[1]) for k, v in token_outcome.items()}
    token_to_market = {str(k): str(v[0]) for k, v in token_outcome.items()}

    with open(WINNER_PKL, "rb") as f:
        market_winner = pickle.load(f)
    market_winner = {str(k): str(v) for k, v in market_winner.items()}

    wallet_df = pd.read_csv(WALLETS, dtype=str, usecols=["wallet", "wallet_type"])
    wallet_df["wallet"] = wallet_df["wallet"].str.lower()
    wallet_lookup = dict(zip(wallet_df["wallet"], wallet_df["wallet_type"]))
    del wallet_df
    gc.collect()

    log(f"Streaming master CSV; filtering to blockNumber > {last_block:,} ...")
    usecols = ["blockNumber", "date", "maker_address", "taker_address",
               "token_id", "maker_side", "usdc_amount", "token_amount", "price"]
    dtypes = {
        "blockNumber": "int64", "date": "string",
        "maker_address": "string", "taker_address": "string",
        "token_id": "string", "maker_side": "string",
        "usdc_amount": "float64", "token_amount": "float64", "price": "float64",
    }

    new_max_block = last_block
    agg: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {
        "n_trades": 0, "usd_volume": 0.0, "won_tokens": 0.0, "tot_tokens": 0.0,
        "pnl": 0.0, "directional": 0.0, "execution": 0.0,
    })
    n_new = 0

    for chunk_idx, chunk in enumerate(
        pd.read_csv(TRADES, chunksize=CHUNK_SIZE, usecols=usecols, dtype=dtypes,
                    low_memory=False, encoding_errors='replace')
    ):
        chunk = chunk[chunk["blockNumber"] > last_block]
        if chunk.empty:
            continue
        n_new += len(chunk)
        new_max_block = max(new_max_block, int(chunk["blockNumber"].max()))

        # Update fair_prices for buys in this chunk
        buys = chunk[chunk["maker_side"].str.upper() == "BUY"].copy()
        if not buys.empty:
            buys["pv"] = buys["price"] * buys["usdc_amount"]
            buy_agg = buys.groupby("token_id", sort=False).agg(pv=("pv", "sum"), v=("usdc_amount", "sum"))
            for tid, row in buy_agg.iterrows():
                tid_s = str(tid)
                pv_lookup[tid_s] = pv_lookup.get(tid_s, 0.0) + float(row["pv"])
                v_lookup[tid_s] = v_lookup.get(tid_s, 0.0) + float(row["v"])
                if v_lookup[tid_s] > 0:
                    fair_lookup[tid_s] = pv_lookup[tid_s] / v_lookup[tid_s]
            del buys, buy_agg

        # Decompose
        chunk["market_id_res"] = chunk["token_id"].map(token_to_market)
        chunk = chunk.dropna(subset=["market_id_res"])
        chunk["outcome_label"] = chunk["token_id"].map(token_to_outcome)
        chunk["winner"] = chunk["market_id_res"].map(market_winner)
        chunk = chunk.dropna(subset=["winner"])
        chunk["fair_price"] = chunk["token_id"].map(fair_lookup)
        chunk = chunk.dropna(subset=["fair_price"])
        if chunk.empty:
            continue

        chunk["maker_address"] = chunk["maker_address"].str.lower()
        chunk["taker_address"] = chunk["taker_address"].str.lower()
        chunk["is_buy"] = chunk["maker_side"].str.upper() == "BUY"
        chunk["token_won"] = (chunk["outcome_label"] == chunk["winner"]).astype("float64")

        Q = chunk["token_amount"].values
        P = chunk["price"].values
        usdc = chunk["usdc_amount"].values
        W = chunk["token_won"].values
        F = chunk["fair_price"].values
        is_buy = chunk["is_buy"].values

        maker_pnl = np.where(is_buy, W * Q - usdc, usdc - W * Q)
        maker_dir = np.where(is_buy, (W - F) * Q, (F - W) * Q)
        maker_exec = np.where(is_buy, (F - P) * Q, (P - F) * Q)
        maker_won = np.where(((is_buy) & (W == 1.0)) | ((~is_buy) & (W == 0.0)), Q, 0.0)

        weeks = pd.to_datetime(chunk["date"], errors="coerce").dt.strftime("%G-W%V")
        maker_types = chunk["maker_address"].map(wallet_lookup).fillna("unclassified")
        taker_types = chunk["taker_address"].map(wallet_lookup).fillna("unclassified")

        maker_df = pd.DataFrame({
            "week": weeks.values, "wallet_type": maker_types.values,
            "n": 1, "usdc": usdc, "Q": Q, "won": maker_won,
            "pnl": maker_pnl, "directional": maker_dir, "execution": maker_exec,
        })
        taker_df = pd.DataFrame({
            "week": weeks.values, "wallet_type": taker_types.values,
            "n": 1, "usdc": usdc, "Q": Q, "won": Q - maker_won,
            "pnl": -maker_pnl, "directional": -maker_dir, "execution": -maker_exec,
        })
        sides = pd.concat([maker_df, taker_df], ignore_index=True).dropna(subset=["week"])
        chunk_agg = sides.groupby(["week", "wallet_type"], sort=False, observed=True).agg(
            n_trades=("n", "sum"), usd_volume=("usdc", "sum"),
            won_tokens=("won", "sum"), tot_tokens=("Q", "sum"),
            pnl=("pnl", "sum"), directional=("directional", "sum"), execution=("execution", "sum"),
        )
        for (wk, wt), row in chunk_agg.iterrows():
            r = agg[(wk, wt)]
            r["n_trades"] += int(row["n_trades"])
            r["usd_volume"] += float(row["usd_volume"])
            r["won_tokens"] += float(row["won_tokens"])
            r["tot_tokens"] += float(row["tot_tokens"])
            r["pnl"] += float(row["pnl"])
            r["directional"] += float(row["directional"])
            r["execution"] += float(row["execution"])

        del chunk, Q, P, usdc, W, F, is_buy
        del maker_pnl, maker_dir, maker_exec, maker_won
        del weeks, maker_types, taker_types, maker_df, taker_df, sides, chunk_agg
        gc.collect()

        log(f"  chunk {chunk_idx+1}: +{len(chunk) if False else n_new:,} new trades cumulative")

    if n_new == 0:
        log("No new trades since last update; nothing to do.")
        return

    log(f"New trades processed: {n_new:,}; new max block {new_max_block:,}")

    # Save updated fair_prices
    log("Persisting updated fair_prices.parquet ...")
    new_keys = sorted(set(pv_lookup.keys()))
    out_df = pd.DataFrame({
        "token_id": new_keys,
        "market_id_res": [token_to_market.get(k, "") for k in new_keys],
        "pv_sum": [pv_lookup[k] for k in new_keys],
        "v_sum": [v_lookup[k] for k in new_keys],
    })
    out_df["fair_price"] = np.where(out_df["v_sum"] > 0, out_df["pv_sum"] / out_df["v_sum"], np.nan)
    out_df.to_parquet(FAIR_PRICES, index=False, compression="zstd")

    # Merge new agg rows into history CSV
    log("Merging into profit_split_history.csv ...")
    hist = pd.read_csv(HIST_CSV)

    new_rows = []
    for (wk, wt), r in agg.items():
        new_rows.append({
            "week": wk, "wallet_type": wt,
            "n_trades": int(r["n_trades"]), "usd_volume": float(r["usd_volume"]),
            "accuracy": (r["won_tokens"] / r["tot_tokens"]) if r["tot_tokens"] > 0 else None,
            "pnl": float(r["pnl"]), "directional": float(r["directional"]), "execution": float(r["execution"]),
        })
    new_df = pd.DataFrame(new_rows)
    new_df["date"] = pd.to_datetime(new_df["week"] + "-1", format="%G-W%V-%u", errors="coerce")
    new_df = new_df.dropna(subset=["date"])

    # If a (week, wallet_type) already exists, sum into it; else append
    hist_idx = hist.set_index(["week", "wallet_type"])
    new_idx = new_df.set_index(["week", "wallet_type"])
    for key, row in new_idx.iterrows():
        if key in hist_idx.index:
            for col in ("n_trades", "usd_volume", "pnl", "directional", "execution"):
                hist_idx.loc[key, col] = hist_idx.loc[key, col] + row[col]
        else:
            hist_idx = pd.concat([hist_idx, pd.DataFrame([row], index=pd.MultiIndex.from_tuples([key], names=hist_idx.index.names))])
    hist = hist_idx.reset_index()
    hist["date"] = pd.to_datetime(hist["week"] + "-1", format="%G-W%V-%u", errors="coerce")
    hist = hist.sort_values(["date", "wallet_type"])

    for col in ("pnl", "directional", "execution"):
        hist[f"{col}_roi"] = np.where(hist["usd_volume"] > 0, hist[col] / hist["usd_volume"], 0.0)
    hist["accuracy"] = pd.to_numeric(hist["accuracy"], errors="coerce")

    # Drop current/future
    today_week = pd.Timestamp.utcnow().strftime("%G-W%V")
    hist = hist[hist["week"] < today_week]

    hist["date"] = hist["date"].dt.strftime("%Y-%m-%d")
    cols = ["date", "week", "wallet_type", "n_trades", "usd_volume", "accuracy",
            "pnl", "directional", "execution",
            "pnl_roi", "directional_roi", "execution_roi"]
    hist[cols].to_csv(HIST_CSV, index=False)
    log(f"  history rows: {len(hist):,}")

    # Latest JSON
    latest_week = hist["week"].max()
    latest = hist[hist["week"] == latest_week].copy()
    latest_rows = {}
    for _, r in latest.iterrows():
        latest_rows[r["wallet_type"]] = {
            "n_trades": int(r["n_trades"]),
            "usd_volume": float(r["usd_volume"]),
            "accuracy": float(r["accuracy"]) if pd.notna(r["accuracy"]) else None,
            "pnl": float(r["pnl"]), "directional": float(r["directional"]), "execution": float(r["execution"]),
            "pnl_roi_bps": float(r["pnl_roi"]) * 10000,
            "directional_roi_bps": float(r["directional_roi"]) * 10000,
            "execution_roi_bps": float(r["execution_roi"]) * 10000,
        }
    payload = {
        "index_name": "Profit Split", "short_name": "ProfitSplit",
        "as_of_week": latest_week,
        "as_of": pd.to_datetime(latest_week + "-1", format="%G-W%V-%u").strftime("%Y-%m-%d"),
        "benchmark": "per-(market_id, token_id) buy-side VWAP (Paper 1 main analysis benchmark)",
        "by_type": latest_rows,
        "methodology": (
            "Fair-price benchmark: per-(market_id, token_id) volume-weighted average "
            "price across BUY-side trades only (Paper 1 main analysis). P&L attributed "
            "to BOTH maker AND taker with mirror signs so dollar totals sum to zero "
            "across all wallet types per week. Bps fields are ROI = pnl / usd_volume "
            "in basis points (x10000)."
        ),
        "generated_at": utc_now(),
        "source": str(TRADES),
    }
    write_json(DATA_OUT / "profit_split_latest.json", payload)

    WEEKLY_STATE.write_text(json.dumps({
        "last_processed_week": latest_week,
        "last_processed_block": int(new_max_block),
        "generated_at": utc_now(),
    }, indent=2))

    total_pnl = sum(r["pnl"] for r in latest_rows.values())
    log("")
    log(f"Latest week {latest_week} | TOTAL pnl = ${total_pnl:+,.2f} (should be ~0)")
    log(f"Total time: {(time.time() - t0)/60:.1f} min")


if __name__ == "__main__":
    main()
