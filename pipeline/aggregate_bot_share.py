"""Bot Share of Volume — weekly % of trades attributed to bot wallets.

Reads weekly_alpha_by_type.csv and computes bot_share = bot_n_trades / total
per week. Emits history CSV with MAs and z-score, plus latest JSON and
chart-ready timeseries.
"""
from __future__ import annotations

import pandas as pd

from common import add_rolling_stats, summary_stats, utc_now, write_json
from config import DATA_OUT, require_source


def main() -> None:
    src = require_source("weekly_alpha_by_type")
    df = pd.read_csv(src)
    df["date"] = pd.to_datetime(df["date"])

    weekly = (
        df.pivot_table(index="date", columns="wallet_type", values="n_trades",
                       aggfunc="sum", fill_value=0)
          .reset_index()
          .sort_values("date")
    )
    weekly.columns.name = None

    type_cols = [c for c in weekly.columns if c != "date"]
    weekly["n_trades_total"] = weekly[type_cols].sum(axis=1)
    weekly["bot_share"] = weekly.get("bot", 0) / weekly["n_trades_total"]
    weekly["retail_share"] = weekly.get("active_retail", 0) / weekly["n_trades_total"]
    weekly["sophisticated_share"] = weekly.get("sophisticated", 0) / weekly["n_trades_total"]

    weekly = add_rolling_stats(weekly, "bot_share")

    hist = weekly.copy()
    hist["date"] = hist["date"].dt.strftime("%Y-%m-%d")
    out_cols = [
        "date", "n_trades_total",
        "bot_share", "retail_share", "sophisticated_share",
        "bot_share_ma4w", "bot_share_ma13w", "bot_share_ma52w", "bot_share_z52w",
    ]
    hist[[c for c in out_cols if c in hist.columns]].to_csv(
        DATA_OUT / "bot_share_history.csv", index=False
    )

    latest = weekly.iloc[-1]
    payload = {
        "index_name": "Bot Share of Volume",
        "short_name": "BotShare",
        "as_of": latest["date"].strftime("%Y-%m-%d"),
        "value": float(latest["bot_share"]),
        "ma4w": float(latest["bot_share_ma4w"]),
        "ma13w": float(latest["bot_share_ma13w"]),
        "ma52w": float(latest["bot_share_ma52w"]),
        "z52w": None if pd.isna(latest["bot_share_z52w"]) else float(latest["bot_share_z52w"]),
        "retail_share": float(latest["retail_share"]),
        "sophisticated_share": float(latest["sophisticated_share"]),
        "n_trades_week": int(latest["n_trades_total"]),
        "rolling": summary_stats(weekly["bot_share"]),
        "n_weeks_history": int(len(weekly)),
        "first_date": weekly.iloc[0]["date"].strftime("%Y-%m-%d"),
        "generated_at": utc_now(),
        "source": str(src),
    }
    write_json(DATA_OUT / "bot_share_latest.json", payload)

    series = weekly[["date", "bot_share", "bot_share_ma13w"]].copy()
    series["date"] = series["date"].dt.strftime("%Y-%m-%d")
    write_json(DATA_OUT / "bot_share_timeseries.json", series.to_dict(orient="records"))

    print(f"BotShare: {len(weekly)} weeks, latest={latest['date'].strftime('%Y-%m-%d')}, "
          f"bot_share={latest['bot_share']:.1%}")


if __name__ == "__main__":
    main()
