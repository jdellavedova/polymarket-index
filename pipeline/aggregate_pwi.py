"""Probability Weighting Index (PWI).

Reads weekly_pwi.csv and emits:
  - pwi_history.csv     full weekly panel with 4/13/52-week MAs and 52w z-score
  - pwi_latest.json     current value, components, 52-week rolling stats
  - pwi_timeseries.json (date, pwi, pwi_ma13w) records for chart rendering
"""
from __future__ import annotations

import pandas as pd

from common import add_rolling_stats, summary_stats, utc_now, write_json
from config import DATA_OUT, require_source


def iso_week_to_date(week_str: str) -> pd.Timestamp:
    return pd.to_datetime(week_str + "-1", format="%G-W%V-%u")


def main() -> None:
    src = require_source("weekly_pwi")
    df = pd.read_csv(src)
    df["date"] = df["week"].map(iso_week_to_date)
    df = df.sort_values("date").reset_index(drop=True)

    # Composite PWI: z-scored mean of cal_error and longshot_fraction
    for col in ("mean_cal_error", "longshot_fraction"):
        df[f"{col}_z"] = (df[col] - df[col].mean()) / df[col].std(ddof=1)
    df["pwi"] = df[["mean_cal_error_z", "longshot_fraction_z"]].mean(axis=1)

    df = add_rolling_stats(df, "pwi")

    history = df[[
        "date", "week", "n_trades",
        "mean_cal_error", "longshot_fraction", "longshot_winrate",
        "pwi", "pwi_ma4w", "pwi_ma13w", "pwi_ma52w", "pwi_z52w",
    ]].copy()
    history["date"] = history["date"].dt.strftime("%Y-%m-%d")
    history.to_csv(DATA_OUT / "pwi_history.csv", index=False)

    latest = df.iloc[-1]
    payload = {
        "index_name": "Probability Weighting Index",
        "short_name": "PWI",
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "week": latest["week"],
        "value": float(latest["pwi"]),
        "ma4w": float(latest["pwi_ma4w"]),
        "ma13w": float(latest["pwi_ma13w"]),
        "ma52w": float(latest["pwi_ma52w"]),
        "z52w": None if pd.isna(latest["pwi_z52w"]) else float(latest["pwi_z52w"]),
        "components": {
            "mean_cal_error": float(latest["mean_cal_error"]),
            "longshot_fraction": float(latest["longshot_fraction"]),
            "longshot_winrate": float(latest["longshot_winrate"]),
        },
        "n_trades_week": int(latest["n_trades"]),
        "rolling": summary_stats(df["pwi"]),
        "n_weeks_history": int(len(df)),
        "first_week": df.iloc[0]["week"],
        "generated_at": utc_now(),
        "source": str(src),
    }
    write_json(DATA_OUT / "pwi_latest.json", payload)

    series = df[["date", "pwi", "pwi_ma13w"]].copy()
    series["date"] = series["date"].dt.strftime("%Y-%m-%d")
    write_json(DATA_OUT / "pwi_timeseries.json", series.to_dict(orient="records"))

    print(f"PWI: {len(df)} weeks, latest={latest['week']}, pwi={latest['pwi']:+.3f} "
          f"(z52w={'NA' if pd.isna(latest['pwi_z52w']) else f'{latest['pwi_z52w']:+.2f}'})")


if __name__ == "__main__":
    main()
