"""Execution Edge Monitor + Market Efficiency Trend.

Reads weekly_alpha_by_type.csv (per-week Prelec alpha by wallet type) and
emits:
  - execution_history.csv         long-format weekly panel: week, type, alpha,
                                  r2, n_trades, mean_cal_error,
                                  longshot_fraction, plus MAs and z-scores for
                                  the bot-retail gap
  - execution_latest.json         latest snapshot with gap + per-type values
  - execution_timeseries.json     chart-ready (date, gap, gap_ma13w) records

The "execution gap" index is alpha_bot - alpha_active_retail: a behavioral
proxy that reflects how differently bots weight tail probabilities relative
to active retail. Paper 1 documents bots are +0.5% ROI liquidity providers
and retail are -1.99% ROI takers; the alpha gap tracks the behavioral
signature of that divergence over time.
"""
from __future__ import annotations

import pandas as pd

from common import add_rolling_stats, summary_stats, utc_now, write_json
from config import DATA_OUT, require_source


def main() -> None:
    src = require_source("weekly_alpha_by_type")
    df = pd.read_csv(src)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "wallet_type"]).reset_index(drop=True)

    # Save the long-format per-type panel with MAs on alpha
    long = df.copy()
    long["date"] = long["date"].dt.strftime("%Y-%m-%d")
    long.to_csv(DATA_OUT / "execution_by_type_history.csv", index=False)

    # Wide pivot on alpha for the gap index
    wide = df.pivot_table(index="date", columns="wallet_type", values="alpha").reset_index()
    wide.columns.name = None
    # Map names defensively — observed values: bot, active_retail, sophisticated, casual, one_shot
    type_cols = [c for c in wide.columns if c != "date"]
    required = {"bot", "active_retail"}
    missing = required - set(type_cols)
    if missing:
        raise RuntimeError(f"Missing wallet types in source: {missing}. Found: {type_cols}")

    wide["alpha_gap"] = wide["bot"] - wide["active_retail"]
    wide = add_rolling_stats(wide, "alpha_gap")

    # Attach weekly n_trades by summing across types
    n_trades_week = df.groupby("date")["n_trades"].sum().reset_index().rename(
        columns={"n_trades": "n_trades_total"}
    )
    wide = wide.merge(n_trades_week, on="date", how="left")
    # Also keep r2 per type (efficiency by type)
    r2_wide = df.pivot_table(index="date", columns="wallet_type", values="r2").reset_index()
    r2_wide.columns = [
        c if c == "date" else f"r2_{c}" for c in r2_wide.columns
    ]
    wide = wide.merge(r2_wide, on="date", how="left")

    # Emit history CSV (wide, one row per week)
    hist = wide.copy()
    hist["date"] = hist["date"].dt.strftime("%Y-%m-%d")
    out_cols = [
        "date",
        "bot", "active_retail", "sophisticated", "casual", "one_shot",
        "alpha_gap", "alpha_gap_ma4w", "alpha_gap_ma13w", "alpha_gap_ma52w", "alpha_gap_z52w",
        "r2_bot", "r2_active_retail",
        "n_trades_total",
    ]
    out_cols = [c for c in out_cols if c in hist.columns]
    hist[out_cols].to_csv(DATA_OUT / "execution_history.csv", index=False)

    latest = wide.iloc[-1]
    per_type_latest = {
        t: {
            "alpha": None if pd.isna(latest.get(t, None)) else float(latest[t]),
        }
        for t in ("bot", "active_retail", "sophisticated", "casual", "one_shot")
        if t in wide.columns
    }

    payload = {
        "index_name": "Execution Edge Monitor",
        "short_name": "Execution",
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "description": "Bot vs active-retail Prelec alpha gap (behavioral execution proxy)",
        "value": float(latest["alpha_gap"]),
        "ma4w": float(latest["alpha_gap_ma4w"]),
        "ma13w": float(latest["alpha_gap_ma13w"]),
        "ma52w": float(latest["alpha_gap_ma52w"]),
        "z52w": None if pd.isna(latest["alpha_gap_z52w"]) else float(latest["alpha_gap_z52w"]),
        "per_type_alpha": per_type_latest,
        "r2_bot": None if "r2_bot" not in wide.columns or pd.isna(latest["r2_bot"]) else float(latest["r2_bot"]),
        "r2_active_retail": None if "r2_active_retail" not in wide.columns or pd.isna(latest["r2_active_retail"]) else float(latest["r2_active_retail"]),
        "n_trades_week": int(latest["n_trades_total"]) if not pd.isna(latest["n_trades_total"]) else None,
        "rolling": summary_stats(wide["alpha_gap"]),
        "n_weeks_history": int(len(wide)),
        "first_date": wide.iloc[0]["date"].strftime("%Y-%m-%d"),
        "generated_at": utc_now(),
        "source": str(src),
    }
    write_json(DATA_OUT / "execution_latest.json", payload)

    series = wide[["date", "alpha_gap", "alpha_gap_ma13w"]].copy()
    series["date"] = series["date"].dt.strftime("%Y-%m-%d")
    write_json(DATA_OUT / "execution_timeseries.json", series.to_dict(orient="records"))

    print(f"Execution: {len(wide)} weeks, latest={latest['date'].strftime('%Y-%m-%d')}, "
          f"gap={latest['alpha_gap']:+.3f} "
          f"(z52w={'NA' if pd.isna(latest['alpha_gap_z52w']) else f'{latest['alpha_gap_z52w']:+.2f}'})")


if __name__ == "__main__":
    main()
