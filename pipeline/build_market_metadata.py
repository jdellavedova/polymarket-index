"""build_market_metadata.py — unified market_metadata.csv for all paper sessions.

The static polymarket_markets.csv covers only ~200K markets with max market_id
794,558 and has category populated for only 2% of rows. The actual trade panel
has market_ids up to ~1.3M. Paper sessions joining trades against the static
snapshot get nulls for most recent markets and almost all categories.

This script merges three sources to produce a single authoritative file:
  1. Static polymarket_markets.csv (question + category where present)
  2. market_winner_map.pkl (universe of resolved markets)
  3. token_outcome_map.pkl (token -> market mapping; gives us the full id range)

Then enriches in two passes:
  Pass A: heuristic categorization (pipeline/categorize.py) for any market with a
          question but no category. Fast, runs in seconds.
  Pass B: Gamma API fallback for any market with no question. Slower (~5 calls/sec
          with rate limiting). Optional — only runs if --enrich-gamma is passed.

Output: J:/Research/10. Prediction/data/blockchain/market_metadata.csv
  Columns: market_id, question, category, source, resolved, winning_outcome, last_updated

Usage:
  python pipeline/build_market_metadata.py            # Phase 1 only (fast)
  python pipeline/build_market_metadata.py --enrich-gamma  # Phase 2 (slow)
"""
from __future__ import annotations

import argparse
import json
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from categorize import categorize as heuristic_categorize

STATIC_CSV = "J:/Research/10. Prediction/data/polymarket_markets.csv"
MARKET_WINNER = "J:/Research/10. Prediction/data/blockchain/market_winner_map.pkl"
TOKEN_OUTCOME = "J:/Research/10. Prediction/data/blockchain/token_outcome_map.pkl"
OUT_PATH = Path("J:/Research/10. Prediction/data/blockchain/market_metadata.csv")

GAMMA = "https://gamma-api.polymarket.com/markets/{mid}"
GAMMA_TIMEOUT = 12
GAMMA_SLEEP = 0.18  # ~5 calls/sec, polite


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_gamma(market_id: str) -> dict | None:
    try:
        r = requests.get(GAMMA.format(mid=market_id), timeout=GAMMA_TIMEOUT)
        if r.status_code != 200:
            return None
        d = r.json()
        return {"question": d.get("question"), "category": d.get("category")}
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enrich-gamma", action="store_true",
                        help="Phase 2: hit Gamma API for missing questions (slow, ~hours)")
    parser.add_argument("--gamma-cap", type=int, default=None,
                        help="Optional cap on number of Gamma calls for testing")
    args = parser.parse_args()

    print(f"[{time.strftime('%H:%M:%S')}] Loading static polymarket_markets.csv ...")
    static_df = pd.read_csv(
        STATIC_CSV,
        usecols=["market_id", "question", "category"],
        low_memory=False,
        dtype={"market_id": str},
    )
    # Dedup: keep the first non-null question per market_id
    static_df = (
        static_df.sort_values("question", na_position="last")
        .drop_duplicates(subset=["market_id"], keep="first")
        .reset_index(drop=True)
    )
    print(f"  static covers {len(static_df):,} unique market_ids "
          f"({static_df['question'].notna().sum():,} with question, "
          f"{static_df['category'].notna().sum():,} with category)")

    print(f"[{time.strftime('%H:%M:%S')}] Loading resolution maps ...")
    with open(MARKET_WINNER, "rb") as f:
        mwm = pickle.load(f)  # {market_id_str: winning_label}
    with open(TOKEN_OUTCOME, "rb") as f:
        tom = pickle.load(f)  # {token_id_int: (market_id_str, outcome_label)}
    market_ids_from_tom = {str(market_id) for _, (market_id, _) in tom.items()}
    print(f"  market_winner_map: {len(mwm):,} resolved markets")
    print(f"  token_outcome_map: {len(market_ids_from_tom):,} unique markets (across all tokens)")

    # Universe of all market_ids we know about
    all_ids: set[str] = set()
    all_ids.update(static_df["market_id"].astype(str))
    all_ids.update(str(k) for k in mwm.keys())
    all_ids.update(market_ids_from_tom)
    print(f"  union: {len(all_ids):,} unique market_ids across all sources")

    # Build the working dataframe
    out = pd.DataFrame({"market_id": sorted(all_ids, key=lambda x: (len(x), x))})
    out["market_id"] = out["market_id"].astype(str)
    out = out.merge(static_df, on="market_id", how="left")
    out["resolved"] = out["market_id"].isin({str(k) for k in mwm.keys()})
    out["winning_outcome"] = out["market_id"].map({str(k): str(v) for k, v in mwm.items()})

    # Track the source of each (question, category) pair
    out["source"] = None
    out.loc[out["question"].notna(), "source"] = "static"

    # ========================================================================
    # Phase A: heuristic categorization for rows with question but no category
    # ========================================================================
    print(f"[{time.strftime('%H:%M:%S')}] Phase A: heuristic categorization ...")
    needs_cat = out["category"].isna() & out["question"].notna()
    print(f"  {needs_cat.sum():,} markets need heuristic category")
    out.loc[needs_cat, "category"] = out.loc[needs_cat, "question"].map(heuristic_categorize)
    out.loc[needs_cat & out["category"].notna(), "source"] = "static+heuristic"

    # ========================================================================
    # Phase B: Gamma API fallback for markets with no question
    # ========================================================================
    if args.enrich_gamma:
        missing = out[out["question"].isna()].copy()
        if args.gamma_cap:
            missing = missing.head(args.gamma_cap)
        print(f"[{time.strftime('%H:%M:%S')}] Phase B: Gamma API enrichment for {len(missing):,} markets "
              f"(~{len(missing) * GAMMA_SLEEP / 60:.0f} min at {1/GAMMA_SLEEP:.0f}/s) ...")
        n_filled = 0
        for i, row in enumerate(missing.itertuples(), 1):
            res = fetch_gamma(row.market_id)
            if res and res.get("question"):
                idx = out.index[out["market_id"] == row.market_id][0]
                out.at[idx, "question"] = res["question"]
                out.at[idx, "category"] = res.get("category") or heuristic_categorize(res["question"])
                out.at[idx, "source"] = "gamma"
                n_filled += 1
            if i % 500 == 0:
                pct = i / len(missing) * 100
                print(f"    {i:,}/{len(missing):,} ({pct:.0f}%) — {n_filled:,} filled")
                # Periodic checkpoint write so a crash doesn't lose progress
                out_partial = out[out["question"].notna() | out["resolved"]].copy()
                out_partial["last_updated"] = _now()
                out_partial.to_csv(OUT_PATH, index=False)
            time.sleep(GAMMA_SLEEP)
        print(f"  Gamma enrichment: {n_filled:,} of {len(missing):,} filled")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] Skipping Phase B (Gamma). Use --enrich-gamma to run.")

    out["last_updated"] = _now()

    # Reorder columns
    out = out[["market_id", "question", "category", "resolved", "winning_outcome", "source", "last_updated"]]
    out.to_csv(OUT_PATH, index=False)

    print()
    print(f"=== market_metadata.csv ===")
    print(f"  rows:               {len(out):,}")
    print(f"  with question:      {out['question'].notna().sum():,}")
    print(f"  with category:      {out['category'].notna().sum():,}")
    print(f"  resolved (winner):  {out['resolved'].sum():,}")
    print(f"  source breakdown:")
    print(out["source"].fillna("(none)").value_counts().to_string())
    print(f"  category breakdown:")
    print(out["category"].fillna("(none)").value_counts().head(15).to_string())
    print()
    print(f"Output: {OUT_PATH}")


if __name__ == "__main__":
    main()
