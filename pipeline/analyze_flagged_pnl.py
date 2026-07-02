"""analyze_flagged_pnl.py — one-off analysis: compute dollar P&L for the 6,291
wallets flagged by the insider screen at p<0.01.

Approach: filter the master trades CSV to trades by flagged wallets only,
join with token outcome map, compute per-trade P&L = S * (W - P) * Q,
aggregate by wallet, then by wallet_type. Both maker- and taker-side are
counted (mirror-signed) for the wallet whose address appears, consistent
with the dashboard convention.

Outputs:
  flagged_wallet_pnl.csv     per-wallet PnL with wallet_type
  flagged_pnl_summary.json   aggregated by wallet_type for the press card
"""
from __future__ import annotations

import json
import time
import pickle
from pathlib import Path

import duckdb
import pandas as pd

from common import utc_now, write_json
from config import DATA_OUT, trades_source

TRADES_SRC = trades_source()
TOKEN_OUTCOME = "H:/Research/10. Prediction/data/blockchain/token_outcome_map.pkl"
MARKET_WINNER = "H:/Research/10. Prediction/data/blockchain/market_winner_map.pkl"
FLAGGED = "G:/My Drive/1. Research/1. Polymarket/2. Insider/output/stage19_significant_wallets.csv"


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Loading flagged-wallet list ...")
    flagged_df = pd.read_csv(FLAGGED, usecols=["wallet", "wallet_type", "significant_01"])
    flagged_df = flagged_df[flagged_df["significant_01"] == 1]
    flagged_df["wallet"] = flagged_df["wallet"].str.lower()
    print(f"  loaded {len(flagged_df):,} flagged wallets")

    print(f"[{time.strftime('%H:%M:%S')}] Loading token-outcome map ...")
    # token_outcome_map: {token_id_int: (market_id_str, outcome_label)}
    # market_winner_map: {market_id_str: winning_label}
    # Build per-token "is winner" by joining the two.
    with open(TOKEN_OUTCOME, "rb") as f:
        tom = pickle.load(f)
    with open(MARKET_WINNER, "rb") as f:
        mwm = pickle.load(f)
    rows = []
    for token_id, (market_id, outcome_label) in tom.items():
        winner = mwm.get(str(market_id))
        if winner is None:
            continue  # market not yet resolved
        rows.append((str(token_id), 1 if str(outcome_label) == str(winner) else 0))
    outcome_df = pd.DataFrame(rows, columns=["token_id", "outcome"])
    print(f"  loaded {len(outcome_df):,} resolved token outcomes")

    con = duckdb.connect(":memory:")
    con.execute("PRAGMA memory_limit='12GB'")
    con.execute("PRAGMA threads=8")
    con.register("flagged", flagged_df[["wallet", "wallet_type"]])
    con.register("outcomes", outcome_df)

    print(f"[{time.strftime('%H:%M:%S')}] Scanning master trades for flagged wallets (both sides) ...")
    con.execute(f"""
        CREATE TEMP TABLE flagged_trades AS
        SELECT
            CASE side WHEN 'maker' THEN t.maker_address ELSE t.taker_address END AS wallet,
            t.token_id,
            CAST(t.price AS DOUBLE) AS price,
            CAST(t.token_amount AS DOUBLE) AS qty,
            CASE LOWER(t.maker_side)
                WHEN 'buy' THEN 1
                ELSE -1
            END * CASE side WHEN 'maker' THEN 1 ELSE -1 END AS sign
        FROM (
            SELECT 'maker' AS side, * FROM {TRADES_SRC}
            UNION ALL
            SELECT 'taker' AS side, * FROM {TRADES_SRC}
        ) t
        WHERE LOWER(CASE side WHEN 'maker' THEN t.maker_address ELSE t.taker_address END)
              IN (SELECT wallet FROM flagged)
    """)
    n_rows = con.execute("SELECT COUNT(*) FROM flagged_trades").fetchone()[0]
    print(f"  scanned to {n_rows:,} flagged-wallet participations ({(time.time()-t0)/60:.1f} min)")

    # Per-trade PnL = sign * (outcome - price) * qty.
    # Both sides of every match get mirror-signed entries via the sign column above.
    print(f"[{time.strftime('%H:%M:%S')}] Computing per-wallet PnL ...")
    con.execute("""
        CREATE TEMP TABLE wallet_pnl AS
        SELECT
            ft.wallet,
            COUNT(*) AS n_participations,
            SUM(CAST(ft.sign AS DOUBLE) * (CAST(o.outcome AS DOUBLE) - ft.price) * ft.qty) AS pnl
        FROM flagged_trades ft
        INNER JOIN outcomes o ON CAST(ft.token_id AS VARCHAR) = o.token_id
        GROUP BY ft.wallet
    """)

    # Join with wallet_type
    df = con.execute("""
        SELECT
            wp.wallet,
            f.wallet_type,
            wp.n_participations,
            wp.pnl
        FROM wallet_pnl wp
        INNER JOIN flagged f ON wp.wallet = LOWER(f.wallet)
    """).fetchdf()

    df.to_csv(DATA_OUT / "flagged_wallet_pnl.csv", index=False)

    by_type = df.groupby("wallet_type").agg(
        n_wallets=("wallet", "count"),
        total_pnl=("pnl", "sum"),
        mean_pnl=("pnl", "mean"),
        median_pnl=("pnl", "median"),
        n_profitable=("pnl", lambda s: int((s > 0).sum())),
    ).round(2)

    print()
    print("--- Flagged-wallet P&L by wallet_type ---")
    print(by_type)
    print()
    total_n = len(df)
    total_profitable = int((df["pnl"] > 0).sum())
    total_pnl = float(df["pnl"].sum())
    print(f"Overall: {total_n:,} flagged wallets, "
          f"{total_profitable:,} profitable ({total_profitable/total_n:.1%}), "
          f"aggregate PnL = ${total_pnl/1e6:+.1f}M")

    payload = {
        "as_of": utc_now(),
        "n_flagged_total": total_n,
        "n_profitable_total": total_profitable,
        "pct_profitable_total": total_profitable / total_n if total_n else 0,
        "aggregate_pnl_usd": total_pnl,
        "by_wallet_type": [
            {
                "wallet_type": idx,
                "n_wallets": int(row["n_wallets"]),
                "n_profitable": int(row["n_profitable"]),
                "pct_profitable": row["n_profitable"] / row["n_wallets"] if row["n_wallets"] else 0,
                "total_pnl_usd": float(row["total_pnl"]),
                "mean_pnl_usd": float(row["mean_pnl"]),
                "median_pnl_usd": float(row["median_pnl"]),
            }
            for idx, row in by_type.iterrows()
        ],
        "notes": "Per-wallet PnL computed as Σ S × (W − P) × Q across all "
                 "resolved trades by the wallet, where S is +1 for buys / −1 for sells, "
                 "W is the binary outcome (1 winner, 0 loser), P is the entry price, "
                 "and Q is token quantity. Both maker and taker sides counted with mirror signs.",
    }
    write_json(DATA_OUT / "flagged_pnl_summary.json", payload)
    con.close()
    print(f"FlaggedPnL: {(time.time()-t0)/60:.1f} min total")


if __name__ == "__main__":
    main()
