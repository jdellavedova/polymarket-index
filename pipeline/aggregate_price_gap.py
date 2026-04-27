"""Longshot / Favorite Price Gap — weekly longshot underpricing signal.

Derived from weekly_pwi.csv. The "longshot gap" is the realized win rate of
trades in the longshot price band minus the mid-point of that band (~5%).

Positive gap = longshots are UNDERpriced (buyer wins more than the price
implied); negative gap = favorite-longshot bias (longshots overpriced).

Favorite-side gap (top price bin) requires a separate weekly extraction and
is deferred to v1.1. For now this index tracks the longshot side only.
"""
from __future__ import annotations

import pandas as pd

from common import add_rolling_stats, summary_stats, utc_now, write_json
from config import DATA_OUT, require_source

LONGSHOT_BAND_MIDPOINT = 0.05


def iso_week_to_date(week_str: str) -> pd.Timestamp:
    return pd.to_datetime(week_str + "-1", format="%G-W%V-%u")


def main() -> None:
    src = require_source("weekly_pwi")
    df = pd.read_csv(src)
    df["date"] = df["week"].map(iso_week_to_date)
    df = df.sort_values("date").reset_index(drop=True)

    # Drop partial weeks (n_trades < 30% of prior 4-week median)
    med4 = df["n_trades"].rolling(4, min_periods=1).median().shift(1)
    mask_partial = med4.notna() & (df["n_trades"] < 0.3 * med4)
    if mask_partial.any():
        for d in df.loc[mask_partial, "week"]:
            print(f"  Dropped partial week: {d}")
        df = df[~mask_partial].reset_index(drop=True)

    df["longshot_gap"] = df["longshot_winrate"] - LONGSHOT_BAND_MIDPOINT
    df = add_rolling_stats(df, "longshot_gap")

    hist = df[[
        "date", "week", "n_trades",
        "longshot_fraction", "longshot_winrate", "longshot_gap",
        "longshot_gap_ma4w", "longshot_gap_ma13w", "longshot_gap_ma52w",
        "longshot_gap_z52w",
    ]].copy()
    hist["date"] = hist["date"].dt.strftime("%Y-%m-%d")
    hist.to_csv(DATA_OUT / "price_gap_history.csv", index=False)

    latest = df.iloc[-1]
    payload = {
        "index_name": "Longshot / Favorite Price Gap",
        "short_name": "PriceGap",
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "week": latest["week"],
        "description": (
            "Realized win rate in the longshot band minus the band midpoint "
            f"({LONGSHOT_BAND_MIDPOINT:.0%}). Positive = longshots underpriced; "
            "negative = favorite-longshot bias (longshots overpriced)."
        ),
        "value": float(latest["longshot_gap"]),
        "ma4w": float(latest["longshot_gap_ma4w"]),
        "ma13w": float(latest["longshot_gap_ma13w"]),
        "ma52w": float(latest["longshot_gap_ma52w"]),
        "z52w": None if pd.isna(latest["longshot_gap_z52w"]) else float(latest["longshot_gap_z52w"]),
        "components": {
            "longshot_winrate": float(latest["longshot_winrate"]),
            "longshot_fraction": float(latest["longshot_fraction"]),
            "band_midpoint": LONGSHOT_BAND_MIDPOINT,
        },
        "n_trades_week": int(latest["n_trades"]),
        "rolling": summary_stats(df["longshot_gap"]),
        "n_weeks_history": int(len(df)),
        "notes": "Favorite-side gap (top price bin) is a v1.1 item.",
        "generated_at": utc_now(),
        "source": str(src),
    }
    write_json(DATA_OUT / "price_gap_latest.json", payload)

    series = df[["date", "longshot_gap", "longshot_gap_ma13w"]].copy()
    series["date"] = series["date"].dt.strftime("%Y-%m-%d")
    write_json(DATA_OUT / "price_gap_timeseries.json", series.to_dict(orient="records"))

    print(f"PriceGap: {len(df)} weeks, latest={latest['week']}, "
          f"longshot_gap={latest['longshot_gap']:+.3f}")


if __name__ == "__main__":
    main()
