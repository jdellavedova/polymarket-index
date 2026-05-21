"""rebuild_history.py — full rebuild of profit_split_history.csv and profit_split_latest.json.

For initial deployment of the new methodology (and any future major change).
Streams the master CSV in chunks, joins resolution + wallet + cached
fair_prices, attributes P&L to BOTH maker AND taker (mirror signs) so
type-level totals sum to zero. Aggregates by (week, wallet_type) and
writes the same outputs as the old aggregate_profit_split.py.

Methodology summary (mirrors Paper 1's 05_return_decomposition.py with the
deliberate dashboard departure of both-side attribution for zero-sum totals):

  fair_price = SUM(price * usdc) / SUM(usdc) over BUY-side trades, by (market, token)
  is_buy = (maker_side = 'BUY')
  token_won = 1 if outcome_label = winner else 0

  maker_pnl   = is_buy ? (token_won * Q - usdc) : (usdc - token_won * Q)
  maker_dir   = is_buy ? (token_won - fair) * Q  : (fair - token_won) * Q
  maker_exec  = is_buy ? (fair - price) * Q       : (price - fair) * Q

  Maker row attributes (pnl, dir, exec) to wallet_type[maker_address]
  Taker row attributes (-pnl, -dir, -exec) to wallet_type[taker_address]
  Total system pnl = 0 by construction.

Runtime: ~30-45 min on local NVMe. Memory: ~6-8 GB peak.

Usage:
    python pipeline/profit_split/rebuild_history.py
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

CHUNK_SIZE = 5_000_000


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> None:
    if not FAIR_PRICES.exists():
        log(f"ERROR: {FAIR_PRICES} not found. Run build_fair_prices.py first.")
        sys.exit(1)

    t0 = time.time()
    log("Loading caches and lookups ...")

    fair_df = pd.read_parquet(FAIR_PRICES, columns=["market_id_res", "token_id", "fair_price"])
    fair_lookup = dict(zip(fair_df["token_id"].astype(str), fair_df["fair_price"]))
    log(f"  fair_prices: {len(fair_lookup):,} tokens (non-null: {fair_df['fair_price'].notna().sum():,})")
    del fair_df
    gc.collect()

    with open(TOKEN_PKL, "rb") as f:
        token_outcome = pickle.load(f)
    token_to_outcome = {str(k): str(v[1]) for k, v in token_outcome.items()}
    token_to_market = {str(k): str(v[0]) for k, v in token_outcome.items()}
    log(f"  token outcomes: {len(token_to_outcome):,}")

    with open(WINNER_PKL, "rb") as f:
        market_winner = pickle.load(f)
    market_winner = {str(k): str(v) for k, v in market_winner.items()}
    log(f"  market winners: {len(market_winner):,}")

    wallet_df = pd.read_csv(WALLETS, dtype=str, usecols=["wallet", "wallet_type"])
    wallet_df["wallet"] = wallet_df["wallet"].str.lower()
    wallet_lookup = dict(zip(wallet_df["wallet"], wallet_df["wallet_type"]))
    log(f"  wallets: {len(wallet_lookup):,}")
    del wallet_df
    gc.collect()

    # Aggregator keyed by (week, wallet_type)
    agg: dict[tuple[str, str], dict[str, float]] = defaultdict(lambda: {
        "n_trades": 0,
        "usd_volume": 0.0,
        "won_tokens": 0.0,
        "tot_tokens": 0.0,
        "pnl": 0.0,
        "directional": 0.0,
        "execution": 0.0,
    })

    usecols = ["blockNumber", "date", "maker_address", "taker_address",
               "token_id", "maker_side", "usdc_amount", "token_amount", "price"]
    dtypes = {
        "blockNumber": "int64",
        "date": "string",
        "maker_address": "string",
        "taker_address": "string",
        "token_id": "string",
        "maker_side": "string",
        "usdc_amount": "float64",
        "token_amount": "float64",
        "price": "float64",
    }

    rows_seen = 0
    last_block = 0

    for chunk_idx, chunk in enumerate(
        pd.read_csv(TRADES, chunksize=CHUNK_SIZE, usecols=usecols, dtype=dtypes,
                    low_memory=False, encoding_errors='replace')
    ):
        rows_seen += len(chunk)
        last_block = max(last_block, int(chunk["blockNumber"].max()))

        # Vectorized lookups
        chunk["market_id_res"] = chunk["token_id"].map(token_to_market)
        chunk = chunk.dropna(subset=["market_id_res"])
        if chunk.empty:
            continue

        chunk["outcome_label"] = chunk["token_id"].map(token_to_outcome)
        chunk["winner"] = chunk["market_id_res"].map(market_winner)
        chunk = chunk.dropna(subset=["winner"])
        if chunk.empty:
            continue

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

        # Per-trade primitives, maker side (signed for buyer convention)
        maker_pnl = np.where(is_buy, W * Q - usdc, usdc - W * Q)
        maker_dir = np.where(is_buy, (W - F) * Q, (F - W) * Q)
        maker_exec = np.where(is_buy, (F - P) * Q, (P - F) * Q)
        # Won-token attribution: maker holds bought tokens
        maker_won = np.where(((is_buy) & (W == 1.0)) | ((~is_buy) & (W == 0.0)), Q, 0.0)

        # ISO week from date
        weeks = pd.to_datetime(chunk["date"], errors="coerce").dt.strftime("%G-W%V")

        # Wallet types
        maker_types = chunk["maker_address"].map(wallet_lookup).fillna("unclassified")
        taker_types = chunk["taker_address"].map(wallet_lookup).fillna("unclassified")

        # Build maker and taker frames in one go, vectorized groupby
        maker_df = pd.DataFrame({
            "week": weeks.values,
            "wallet_type": maker_types.values,
            "n": 1,
            "usdc": usdc,
            "Q": Q,
            "won": maker_won,
            "pnl": maker_pnl,
            "directional": maker_dir,
            "execution": maker_exec,
        })
        taker_df = pd.DataFrame({
            "week": weeks.values,
            "wallet_type": taker_types.values,
            "n": 1,
            "usdc": usdc,
            "Q": Q,
            "won": Q - maker_won,
            "pnl": -maker_pnl,
            "directional": -maker_dir,
            "execution": -maker_exec,
        })
        sides = pd.concat([maker_df, taker_df], ignore_index=True)
        sides = sides.dropna(subset=["week"])
        chunk_agg = sides.groupby(["week", "wallet_type"], sort=False, observed=True).agg(
            n_trades=("n", "sum"),
            usd_volume=("usdc", "sum"),
            won_tokens=("won", "sum"),
            tot_tokens=("Q", "sum"),
            pnl=("pnl", "sum"),
            directional=("directional", "sum"),
            execution=("execution", "sum"),
        )
        # Merge into running totals
        for (wk, wt), row in chunk_agg.iterrows():
            r = agg[(wk, wt)]
            r["n_trades"] += int(row["n_trades"])
            r["usd_volume"] += float(row["usd_volume"])
            r["won_tokens"] += float(row["won_tokens"])
            r["tot_tokens"] += float(row["tot_tokens"])
            r["pnl"] += float(row["pnl"])
            r["directional"] += float(row["directional"])
            r["execution"] += float(row["execution"])

        del chunk, Q, P, usdc, W, F, is_buy, maker_pnl, maker_dir, maker_exec, maker_won
        del weeks, maker_types, taker_types, maker_df, taker_df, sides, chunk_agg
        gc.collect()

        elapsed = time.time() - t0
        log(f"  chunk {chunk_idx+1:>3} | rows {rows_seen/1e6:.0f}M | weeks×types {len(agg):,} | elapsed {elapsed/60:.1f} min")

    log(f"Scan complete in {(time.time()-t0)/60:.1f} min. Building outputs ...")

    # Materialize to DataFrame
    rows = []
    for (week, wt), r in agg.items():
        rows.append({
            "week": week,
            "wallet_type": wt,
            "n_trades": int(r["n_trades"]),
            "usd_volume": float(r["usd_volume"]),
            "accuracy": float(r["won_tokens"] / r["tot_tokens"]) if r["tot_tokens"] > 0 else None,
            "pnl": float(r["pnl"]),
            "directional": float(r["directional"]),
            "execution": float(r["execution"]),
        })
    long = pd.DataFrame(rows)
    long["date"] = pd.to_datetime(long["week"] + "-1", format="%G-W%V-%u", errors="coerce")
    long = long.dropna(subset=["date"]).sort_values(["date", "wallet_type"]).reset_index(drop=True)

    # Drop current/future weeks (block-extrapolation phantom dates)
    today_week = pd.Timestamp.utcnow().strftime("%G-W%V")
    future_or_current = set(long.loc[long["week"] >= today_week, "week"].unique())
    wk_totals = long.groupby("week")["n_trades"].sum()
    too_sparse = set(wk_totals[wk_totals < 10_000].index)
    drop = too_sparse | future_or_current
    if drop:
        for w in sorted(drop):
            reason = "current/future" if w in future_or_current else "<10K trades"
            log(f"  drop {w}: {reason}")
        long = long[~long["week"].isin(drop)].reset_index(drop=True)

    # ROI bps
    for col in ("pnl", "directional", "execution"):
        long[f"{col}_roi"] = np.where(long["usd_volume"] > 0, long[col] / long["usd_volume"], 0.0)

    # Write history CSV
    hist = long.copy()
    hist["date"] = hist["date"].dt.strftime("%Y-%m-%d")
    cols = ["date", "week", "wallet_type", "n_trades", "usd_volume", "accuracy",
            "pnl", "directional", "execution",
            "pnl_roi", "directional_roi", "execution_roi"]
    hist[cols].to_csv(DATA_OUT / "profit_split_history.csv", index=False)
    log(f"  wrote profit_split_history.csv ({len(hist):,} rows)")

    # Latest-week JSON
    latest_week = long["week"].max()
    latest = long[long["week"] == latest_week].copy()
    latest_rows = {}
    for _, r in latest.iterrows():
        latest_rows[r["wallet_type"]] = {
            "n_trades": int(r["n_trades"]),
            "usd_volume": float(r["usd_volume"]),
            "accuracy": float(r["accuracy"]) if pd.notna(r["accuracy"]) else None,
            "pnl": float(r["pnl"]),
            "directional": float(r["directional"]),
            "execution": float(r["execution"]),
            "pnl_roi_bps": float(r["pnl_roi"]) * 10000,
            "directional_roi_bps": float(r["directional_roi"]) * 10000,
            "execution_roi_bps": float(r["execution_roi"]) * 10000,
        }

    payload = {
        "index_name": "Profit Split",
        "short_name": "ProfitSplit",
        "as_of_week": latest_week,
        "as_of": latest.iloc[0]["date"].strftime("%Y-%m-%d"),
        "benchmark": "per-(market_id, token_id) buy-side VWAP (Paper 1 main analysis benchmark)",
        "by_type": latest_rows,
        "methodology": (
            "Fair-price benchmark: per-(market_id, token_id) volume-weighted average "
            "price across BUY-side trades only (Paper 1 Section 4 main analysis). "
            "For each match: maker_pnl = sign * (W - P) * Q; maker_directional = sign * (W - fair) * Q; "
            "maker_execution = sign * (fair - P) * Q (sign = +1 if maker_side=BUY else -1). "
            "P&L is attributed to BOTH maker AND taker with mirror signs so dollar totals "
            "sum to zero across all wallet types per week. Bps fields are ROI = pnl / "
            "usd_volume in basis points (x10000). The dashboard departs from Paper 1's "
            "maker-only attribution to support the zero-sum public display; the benchmark "
            "and per-trade decomposition are unchanged."
        ),
        "generated_at": utc_now(),
        "source": str(TRADES),
    }
    write_json(DATA_OUT / "profit_split_latest.json", payload)
    log(f"  wrote profit_split_latest.json (latest week: {latest_week})")

    # Update weekly state
    WEEKLY_STATE.write_text(json.dumps({
        "last_processed_week": latest_week,
        "last_processed_block": int(last_block),
        "generated_at": utc_now(),
    }, indent=2))
    log(f"  wrote weekly_state.json (last_block={last_block:,})")

    # Print zero-sum check
    log("")
    log("Zero-sum check (latest week):")
    total_pnl = sum(r["pnl"] for r in latest_rows.values())
    log(f"  TOTAL pnl across all types: ${total_pnl:+,.2f}  (should be ~0)")
    log("")
    log(f"Per-type latest week (W={latest_week}):")
    for wt, r in latest_rows.items():
        log(f"  {wt:<18} pnl=${r['pnl']:>+12,.0f}  vol=${r['usd_volume']:>14,.0f}  "
            f"dir={r['directional_roi_bps']:>+8.0f}bps  exec={r['execution_roi_bps']:>+8.0f}bps  "
            f"tot={r['pnl_roi_bps']:>+8.0f}bps")

    log(f"\nTotal rebuild time: {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
