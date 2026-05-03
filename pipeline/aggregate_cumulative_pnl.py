"""aggregate_cumulative_pnl.py — derive cumulative-by-week P&L per wallet
type from profit_split_history.csv. No CSV scan; just a running sum.

Powers the CumulativePnlChart on the homepage and /research:
  - Bots end at ~+$133M cumulative since Nov 2022
  - Active retail ends at ~-$79M
  - The single most-quotable visualization on the site

Outputs:
  cumulative_pnl_history.json   { weeks: [...], series: {bot: [...], active_retail: [...], ...} }
  cumulative_pnl_history.csv    long format, for download
"""
from __future__ import annotations

import pandas as pd

from common import utc_now, write_json
from config import DATA_OUT


WALLET_TYPES = ["bot", "active_retail", "sophisticated", "casual", "one_shot"]


def main() -> None:
    src = DATA_OUT / "profit_split_history.csv"
    if not src.exists():
        raise FileNotFoundError(src)

    df = pd.read_csv(src)
    if df.empty:
        print("CumulativePnL: profit_split_history is empty; skipping.")
        return

    weeks = sorted(df["week"].unique())
    full = pd.MultiIndex.from_product([weeks, WALLET_TYPES], names=["week", "wallet_type"]).to_frame(index=False)
    df = full.merge(df, on=["week", "wallet_type"], how="left")
    df["pnl"] = df["pnl"].fillna(0.0)
    df["date"] = df.groupby("week")["date"].transform(lambda s: s.ffill().bfill())
    df = df.sort_values(["wallet_type", "week"])
    df["cumulative_pnl"] = df.groupby("wallet_type")["pnl"].cumsum()

    series: dict[str, list[float]] = {}
    dates: list[str] = []
    for wt in WALLET_TYPES:
        sub = df[df["wallet_type"] == wt].sort_values("week")
        if not dates:
            dates = sub["date"].astype(str).tolist()
        series[wt] = [round(float(v), 2) for v in sub["cumulative_pnl"].tolist()]

    payload = {
        "as_of": dates[-1] if dates else None,
        "n_weeks": len(weeks),
        "wallet_types": WALLET_TYPES,
        "weeks": weeks,
        "dates": dates,
        "cumulative": series,
        "endpoints": {wt: series[wt][-1] if series[wt] else 0 for wt in WALLET_TYPES},
        "generated_at": utc_now(),
        "notes": (
            "Cumulative weekly P&L by wallet type since the start of profit_split_history. "
            "Endpoints should match Paper 1 (bot ~ +$133M, active_retail ~ -$79M) within "
            "the dashboard's both-side mirror attribution convention."
        ),
    }
    write_json(DATA_OUT / "cumulative_pnl_history.json", payload)

    out_long = df[["date", "week", "wallet_type", "pnl", "cumulative_pnl"]].copy()
    out_long.to_csv(DATA_OUT / "cumulative_pnl_history.csv", index=False)

    print(f"CumulativePnL: {len(weeks)} weeks; bot={payload['endpoints']['bot']/1e6:+.1f}M, "
          f"retail={payload['endpoints']['active_retail']/1e6:+.1f}M")


if __name__ == "__main__":
    main()
