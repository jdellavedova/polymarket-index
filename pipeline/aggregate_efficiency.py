"""Market Efficiency Trend — weekly R-squared of the Prelec fit.

Uses active_retail as the primary series: active retail dominates non-bot
volume and the r2 captures how well a single behavioral parameter describes
the market's calibration gap this week. Bot r2 is reported for comparison.

Higher R2 = the market's pricing gap is well-described by one parameter
(i.e., the calibration curve is smooth and predictable). Lower R2 = gap is
noisy, suggesting idiosyncratic shocks rather than a stable behavioral bias.
"""
from __future__ import annotations

import pandas as pd

from common import add_rolling_stats, drop_partial_weeks, summary_stats, utc_now, write_json
from config import DATA_OUT, require_source


def main() -> None:
    src = require_source("weekly_alpha_by_type")
    df = pd.read_csv(src)
    df["date"] = pd.to_datetime(df["date"], format="mixed").dt.normalize()
    df = drop_partial_weeks(df)

    wide = (
        df.pivot_table(index="date", columns="wallet_type", values="r2")
          .reset_index()
          .sort_values("date")
    )
    wide.columns.name = None
    wide = wide.rename(columns={
        c: f"r2_{c}" for c in wide.columns if c != "date"
    })

    if "r2_active_retail" not in wide.columns:
        raise RuntimeError(f"No r2_active_retail column. Got: {list(wide.columns)}")

    wide["efficiency"] = wide["r2_active_retail"]
    wide = add_rolling_stats(wide, "efficiency")

    hist = wide.copy()
    hist["date"] = hist["date"].dt.strftime("%Y-%m-%d")
    out_cols = [
        "date", "efficiency",
        "efficiency_ma4w", "efficiency_ma13w", "efficiency_ma52w", "efficiency_z52w",
        "r2_bot", "r2_active_retail", "r2_sophisticated", "r2_casual", "r2_one_shot",
    ]
    hist[[c for c in out_cols if c in hist.columns]].to_csv(
        DATA_OUT / "efficiency_history.csv", index=False
    )

    latest = wide.iloc[-1]
    payload = {
        "index_name": "Market Efficiency Trend",
        "short_name": "Efficiency",
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "description": "R-squared of the weekly one-parameter Prelec fit to active retail trades.",
        "value": float(latest["efficiency"]),
        "ma4w": float(latest["efficiency_ma4w"]),
        "ma13w": float(latest["efficiency_ma13w"]),
        "ma52w": float(latest["efficiency_ma52w"]),
        "z52w": None if pd.isna(latest["efficiency_z52w"]) else float(latest["efficiency_z52w"]),
        "per_type_r2": {
            t: (None if pd.isna(latest.get(f"r2_{t}", None)) else float(latest[f"r2_{t}"]))
            for t in ("bot", "active_retail", "sophisticated", "casual", "one_shot")
            if f"r2_{t}" in wide.columns
        },
        "rolling": summary_stats(wide["efficiency"]),
        "n_weeks_history": int(len(wide)),
        "first_date": wide.iloc[0]["date"].strftime("%Y-%m-%d"),
        "generated_at": utc_now(),
        "source": str(src),
    }
    write_json(DATA_OUT / "efficiency_latest.json", payload)

    series = wide[["date", "efficiency", "efficiency_ma13w"]].copy()
    series["date"] = series["date"].dt.strftime("%Y-%m-%d")
    write_json(DATA_OUT / "efficiency_timeseries.json", series.to_dict(orient="records"))

    print(f"Efficiency: {len(wide)} weeks, latest={latest['date'].strftime('%Y-%m-%d')}, "
          f"R2={latest['efficiency']:.3f}")


if __name__ == "__main__":
    main()
