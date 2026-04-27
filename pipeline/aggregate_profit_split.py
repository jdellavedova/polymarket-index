"""Profit split — weekly per-type decomposition of P&L into directional
(picked the winning side) vs execution (bought cheap / sold rich vs market).

Decomposition (per Paper 1, benchmark = per-token VWAP):
  For each trade with price P, token amount Q, maker side S (+1 BUY, -1 SELL),
  with the benchmark v = VWAP of all trades in the same token (USDC / tokens):
    P&L   = S * (W - P) * Q     where W = 1 if this token side won, else 0
    dir   = S * (W - v) * Q     directional (beat the market-avg price)
    exec  = S * (v - P) * Q     execution (did better than market avg on entry)
    Sum:  dir + exec = P&L      algebraic identity

ROI fields are PnL / USD volume in basis points (×10,000).

Outputs:
  profit_split_history.csv    long weekly panel by wallet type
  profit_split_latest.json    most-recent-week snapshot
"""
from __future__ import annotations

import pickle
import time
from pathlib import Path

import duckdb
import pandas as pd

from common import utc_now, write_json
from config import DATA_OUT

DATA_DIR = Path("J:/Research/10. Prediction/data/blockchain")
TRADES = str(DATA_DIR / "processed_trades.csv")
TOKEN_PKL = DATA_DIR / "token_outcome_map.pkl"
WINNER_PKL = DATA_DIR / "market_winner_map.pkl"
WALLETS = str(DATA_DIR / "wallet_statistics.csv")


def main() -> None:
    t0 = time.time()
    print(f"[{time.strftime('%H:%M:%S')}] Loading resolution maps ...")
    with open(TOKEN_PKL, "rb") as f:
        token_outcome = pickle.load(f)
    with open(WINNER_PKL, "rb") as f:
        market_winner = pickle.load(f)
    tok = pd.DataFrame(
        [(str(t), str(m), str(o)) for t, (m, o) in token_outcome.items()],
        columns=["token_id", "resolve_market_id", "outcome_label"],
    )
    win = pd.DataFrame(
        [(str(m), str(w)) for m, w in market_winner.items()],
        columns=["resolve_market_id", "winner"],
    )

    con = duckdb.connect(":memory:")
    con.execute("PRAGMA memory_limit='16GB'")
    con.execute("PRAGMA threads=8")
    con.register("tok", tok)
    con.register("win", win)
    con.execute(f"""
        CREATE TABLE wallets AS
        SELECT LOWER(wallet) AS wallet, wallet_type
        FROM read_csv_auto('{WALLETS}', all_varchar=TRUE)
    """)

    # Pass 1: per-token VWAP (one scan, produces a small ~800K-row table)
    print(f"[{time.strftime('%H:%M:%S')}] Pass 1: per-token VWAP ...")
    con.execute(f"""
        CREATE TABLE vwap AS
        SELECT token_id,
               SUM(CAST(usdc_amount AS DOUBLE)) /
               NULLIF(SUM(CAST(token_amount AS DOUBLE)), 0) AS v
        FROM read_csv_auto('{TRADES}', all_varchar=TRUE, parallel=TRUE)
        GROUP BY token_id
    """)
    n_vwap = con.execute("SELECT COUNT(*) FROM vwap").fetchone()[0]
    print(f"  VWAP computed for {n_vwap:,} tokens")

    # Pass 2: stream trades again, join resolution + wallet + vwap, aggregate
    print(f"[{time.strftime('%H:%M:%S')}] Pass 2: aggregating by (week, wallet_type) ...")
    con.execute(f"""
        CREATE TEMP TABLE agg AS
        SELECT
            strftime(CAST(t.date AS DATE), '%G-W%V') AS week,
            COALESCE(w.wallet_type, 'unclassified') AS wallet_type,
            COUNT(*) AS n_trades,
            SUM(CAST(t.usdc_amount AS DOUBLE)) AS usd_volume,
            SUM(CASE WHEN ((t.maker_side = 'BUY' AND tok.outcome_label = win.winner)
                        OR (t.maker_side = 'SELL' AND tok.outcome_label != win.winner))
                     THEN CAST(t.token_amount AS DOUBLE) ELSE 0 END)
              / NULLIF(SUM(CAST(t.token_amount AS DOUBLE)), 0) AS accuracy,
            SUM((CASE WHEN t.maker_side = 'BUY' THEN 1 ELSE -1 END)
                * ((CASE WHEN tok.outcome_label = win.winner THEN 1.0 ELSE 0.0 END)
                    - CAST(t.price AS DOUBLE))
                * CAST(t.token_amount AS DOUBLE)) AS pnl,
            SUM((CASE WHEN t.maker_side = 'BUY' THEN 1 ELSE -1 END)
                * ((CASE WHEN tok.outcome_label = win.winner THEN 1.0 ELSE 0.0 END) - vwap.v)
                * CAST(t.token_amount AS DOUBLE)) AS directional,
            SUM((CASE WHEN t.maker_side = 'BUY' THEN 1 ELSE -1 END)
                * (vwap.v - CAST(t.price AS DOUBLE))
                * CAST(t.token_amount AS DOUBLE)) AS execution
        FROM read_csv_auto('{TRADES}', all_varchar=TRUE, parallel=TRUE) t
        INNER JOIN tok ON t.token_id = tok.token_id
        INNER JOIN win ON tok.resolve_market_id = win.resolve_market_id
        INNER JOIN vwap ON t.token_id = vwap.token_id
        LEFT JOIN wallets w ON LOWER(t.maker_address) = w.wallet
        WHERE vwap.v IS NOT NULL
        GROUP BY 1, 2
    """)

    long = con.execute("SELECT * FROM agg ORDER BY week, wallet_type").fetchdf()
    print(f"[{time.strftime('%H:%M:%S')}] Scan complete: {len(long):,} (week, type) cells")

    long["date"] = pd.to_datetime(long["week"] + "-1", format="%G-W%V-%u", errors="coerce")
    long = long.dropna(subset=["date"]).sort_values(["date", "wallet_type"]).reset_index(drop=True)

    # Drop current/future weeks (block-timestamp extrapolation bug produces
    # phantom future weeks) and truly sparse weeks (too few resolved trades
    # for per-type numbers to be reliable).
    today_week = pd.Timestamp.utcnow().strftime("%G-W%V")
    future_or_current = set(long.loc[long["week"] >= today_week, "week"].unique())
    wk_totals = long.groupby("week")["n_trades"].sum()
    too_sparse = set(wk_totals[wk_totals < 10_000].index)
    drop = too_sparse | future_or_current
    if drop:
        for w in sorted(drop):
            reason = ("current / future week" if w in future_or_current
                      else "<10K resolved trades")
            print(f"  Dropped {w}: {reason}")
        long = long[~long["week"].isin(drop)].reset_index(drop=True)

    for col in ("pnl", "directional", "execution"):
        long[f"{col}_roi"] = long.apply(
            lambda r: (r[col] / r["usd_volume"]) if r["usd_volume"] > 0 else 0.0, axis=1
        )

    hist = long.copy()
    hist["date"] = hist["date"].dt.strftime("%Y-%m-%d")
    cols = ["date", "week", "wallet_type", "n_trades", "usd_volume", "accuracy",
            "pnl", "directional", "execution",
            "pnl_roi", "directional_roi", "execution_roi"]
    hist[cols].to_csv(DATA_OUT / "profit_split_history.csv", index=False)

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
        "benchmark": "per-token VWAP (USDC / token_amount)",
        "by_type": latest_rows,
        "methodology": (
            "For each trade: sign S = +1 for BUY / -1 for SELL; W = 1 if this "
            "token side won, else 0; v = volume-weighted average price across "
            "ALL trades in the same token; P = entry price, Q = token amount. "
            "Directional = S*(W-v)*Q (picked the winner); Execution = S*(v-P)*Q "
            "(beat the market avg). Sum = realized P&L exactly. ROI is P&L / USD "
            "volume in basis points (x10000)."
        ),
        "generated_at": utc_now(),
        "source": TRADES,
    }
    write_json(DATA_OUT / "profit_split_latest.json", payload)

    con.close()
    bot_row = latest[latest["wallet_type"] == "bot"]
    ret_row = latest[latest["wallet_type"] == "active_retail"]
    bot_str = (f"bot: ${bot_row.iloc[0]['pnl']:,.0f} ({bot_row.iloc[0]['pnl_roi']*10000:+.0f}bps)"
               if len(bot_row) else "")
    ret_str = (f"retail: ${ret_row.iloc[0]['pnl']:,.0f} ({ret_row.iloc[0]['pnl_roi']*10000:+.0f}bps)"
               if len(ret_row) else "")
    print(f"ProfitSplit: latest={latest_week} {bot_str}  {ret_str} "
          f"({(time.time()-t0)/60:.1f} min)")


if __name__ == "__main__":
    main()
