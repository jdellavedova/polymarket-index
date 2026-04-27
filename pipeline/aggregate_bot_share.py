"""Bot Share of Volume — weekly % of trades from bot wallets.

Reads `weekly_activity_history.csv` (produced by aggregate_weekly_activity),
which has TRUE per-type trade counts from processed_trades.csv joined with
wallet_statistics.csv. This fixes the earlier bug where the shares were based
on Prelec-fit-included trades and casual/one_shot types often dropped to zero.
"""
from __future__ import annotations

import pandas as pd

from common import add_rolling_stats, summary_stats, utc_now, write_json
from config import DATA_OUT


def main() -> None:
    src = DATA_OUT / "weekly_activity_history.csv"
    if not src.exists():
        raise RuntimeError(
            f"Missing {src}. Run aggregate_weekly_activity before aggregate_bot_share."
        )
    df = pd.read_csv(src)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # The activity aggregator already applies the partial-week filter with a
    # 0.5 threshold, so rows here are all complete weeks.
    df = df.rename(columns={
        "share_bot": "bot_share",
        "share_active_retail": "retail_share",
        "share_sophisticated": "sophisticated_share",
        "share_casual": "casual_share",
        "share_one_shot": "one_shot_share",
    })
    df["n_trades_total"] = df["total_trades"]
    df = add_rolling_stats(df, "bot_share")

    hist = df.copy()
    hist["date"] = hist["date"].dt.strftime("%Y-%m-%d")
    out_cols = [
        "date", "n_trades_total",
        "bot_share", "retail_share", "sophisticated_share",
        "casual_share", "one_shot_share",
        "bot_share_ma4w", "bot_share_ma13w", "bot_share_ma52w", "bot_share_z52w",
    ]
    hist[[c for c in out_cols if c in hist.columns]].to_csv(
        DATA_OUT / "bot_share_history.csv", index=False
    )

    latest = df.iloc[-1]
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
        "casual_share": float(latest["casual_share"]),
        "one_shot_share": float(latest["one_shot_share"]),
        "n_trades_week": int(latest["n_trades_total"]),
        "rolling": summary_stats(df["bot_share"]),
        "n_weeks_history": int(len(df)),
        "first_date": df.iloc[0]["date"].strftime("%Y-%m-%d"),
        "generated_at": utc_now(),
        "source": str(src),
        "methodology": (
            "Shares are fraction of actual resolved trades per week attributed "
            "to each wallet-type class (not Prelec-fit-included trades). "
            "Wallet-type assignment is static per wallet based on the full trade "
            "history thresholds defined in Paper 1."
        ),
    }
    write_json(DATA_OUT / "bot_share_latest.json", payload)

    series = df[["date", "bot_share", "bot_share_ma13w"]].copy()
    series["date"] = series["date"].dt.strftime("%Y-%m-%d")
    write_json(DATA_OUT / "bot_share_timeseries.json", series.to_dict(orient="records"))

    print(f"BotShare: {len(df)} weeks, latest={latest['date'].strftime('%Y-%m-%d')}, "
          f"bot_share={latest['bot_share']:.1%}")


if __name__ == "__main__":
    main()
