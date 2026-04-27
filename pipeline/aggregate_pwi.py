"""Probability Weighting Index (PWI) — non-bot weighted Prelec alpha.

The PWI is the trade-count-weighted mean Prelec alpha each week across all
non-bot wallet classes (active retail, sophisticated, casual, one-shot).

Interpretation:
  alpha = 1   rational pricing (no probability distortion)
  alpha < 1   classical inverse-S weighting; tails over-weighted
  alpha > 1   rare; tails under-weighted

Emits:
  - pwi_history.csv     weekly panel with MAs and z52w
  - pwi_latest.json     current value, components, 52w rolling stats
  - pwi_timeseries.json chart-ready records (date, pwi_alpha, pwi_alpha_ma13w)
"""
from __future__ import annotations

import pandas as pd

from common import add_rolling_stats, drop_partial_weeks, summary_stats, utc_now, write_json
from config import DATA_OUT, require_source


def _weighted(g: pd.DataFrame, col: str) -> float:
    w = g["n_trades"].astype(float)
    if w.sum() == 0:
        return float("nan")
    return float((g[col] * w).sum() / w.sum())


def main() -> None:
    src = require_source("weekly_alpha_by_type")
    df = pd.read_csv(src)
    df["date"] = pd.to_datetime(df["date"])
    df = drop_partial_weeks(df)

    non_bot = df[df["wallet_type"] != "bot"].copy()

    weekly = (
        non_bot.groupby("date", as_index=False)
        .apply(
            lambda g: pd.Series({
                "pwi_alpha": _weighted(g, "alpha"),
                "mean_cal_error": _weighted(g, "mean_cal_error"),
                "longshot_fraction": _weighted(g, "longshot_fraction"),
                "n_trades_nonbot": int(g["n_trades"].sum()),
            }),
            include_groups=False,
        )
        .reset_index(drop=True)
        .sort_values("date")
        .reset_index(drop=True)
    )

    weekly = add_rolling_stats(weekly, "pwi_alpha")

    history = weekly.copy()
    history["date"] = history["date"].dt.strftime("%Y-%m-%d")
    history[[
        "date", "pwi_alpha", "mean_cal_error", "longshot_fraction", "n_trades_nonbot",
        "pwi_alpha_ma4w", "pwi_alpha_ma13w", "pwi_alpha_ma52w", "pwi_alpha_z52w",
    ]].to_csv(DATA_OUT / "pwi_history.csv", index=False)

    latest = weekly.iloc[-1]
    payload = {
        "index_name": "Probability Weighting Index",
        "short_name": "PWI",
        "definition": "Trade-count-weighted non-bot Prelec alpha, weekly",
        "interpretation": (
            "alpha = 1 is rational pricing; alpha below 1 is the classical "
            "inverse-S weighting (tails over-weighted); alpha above 1 means "
            "tails under-weighted."
        ),
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "value": float(latest["pwi_alpha"]),
        "ma4w": float(latest["pwi_alpha_ma4w"]),
        "ma13w": float(latest["pwi_alpha_ma13w"]),
        "ma52w": float(latest["pwi_alpha_ma52w"]),
        "z52w": None if pd.isna(latest["pwi_alpha_z52w"]) else float(latest["pwi_alpha_z52w"]),
        "components": {
            "weighted_mean_cal_error": float(latest["mean_cal_error"]),
            "weighted_longshot_fraction": float(latest["longshot_fraction"]),
        },
        "n_trades_nonbot_week": int(latest["n_trades_nonbot"]),
        "rolling": summary_stats(weekly["pwi_alpha"]),
        "n_weeks_history": int(len(weekly)),
        "first_date": weekly.iloc[0]["date"].strftime("%Y-%m-%d"),
        "bot_exclusion_note": (
            "Bot wallets are excluded (trades_per_day > 50 OR n_trades > 1000) "
            "so the index reflects human probability weighting only."
        ),
        "generated_at": utc_now(),
        "source": str(src),
    }
    write_json(DATA_OUT / "pwi_latest.json", payload)

    series = weekly[["date", "pwi_alpha", "pwi_alpha_ma13w"]].copy()
    series["date"] = series["date"].dt.strftime("%Y-%m-%d")
    write_json(DATA_OUT / "pwi_timeseries.json", series.to_dict(orient="records"))

    print(
        f"PWI (non-bot): {len(weekly)} weeks, latest={latest['date'].strftime('%Y-%m-%d')}, "
        f"alpha={latest['pwi_alpha']:.3f} "
        f"(z52w={'NA' if pd.isna(latest['pwi_alpha_z52w']) else f'{latest['pwi_alpha_z52w']:+.2f}'})"
    )


if __name__ == "__main__":
    main()
