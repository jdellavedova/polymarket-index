"""Merge every weekly history CSV into polymarket_indices_weekly.csv.

Date-indexed wide panel, one column per index. The canonical citation file,
analog to Baker-Wurgler's downloadable Excel.
"""
from __future__ import annotations

from functools import reduce

import pandas as pd

from common import utc_now, write_json
from config import DATA_OUT


INDICES = [
    ("pwi_history.csv", ["pwi", "pwi_ma13w"]),
    ("execution_history.csv", ["alpha_gap", "alpha_gap_ma13w"]),
    ("bot_share_history.csv", ["bot_share", "bot_share_ma13w"]),
    ("price_gap_history.csv", ["longshot_gap", "longshot_gap_ma13w"]),
    ("efficiency_history.csv", ["efficiency", "efficiency_ma13w"]),
]


def main() -> None:
    frames = []
    for csv_name, cols in INDICES:
        path = DATA_OUT / csv_name
        if not path.exists():
            print(f"  skip {csv_name} (not built yet)")
            continue
        df = pd.read_csv(path, parse_dates=["date"])
        keep = ["date"] + [c for c in cols if c in df.columns]
        frames.append(df[keep])

    if not frames:
        raise RuntimeError("No index history files found. Run aggregators first.")

    master = reduce(lambda a, b: a.merge(b, on="date", how="outer"), frames)
    master = master.sort_values("date").reset_index(drop=True)
    master["date"] = master["date"].dt.strftime("%Y-%m-%d")
    master.to_csv(DATA_OUT / "polymarket_indices_weekly.csv", index=False)

    summary = {
        "generated_at": utc_now(),
        "n_weeks": int(len(master)),
        "first_date": master.iloc[0]["date"],
        "last_date": master.iloc[-1]["date"],
        "columns": list(master.columns),
    }
    write_json(DATA_OUT / "master_table_summary.json", summary)

    print(f"Master table: {len(master)} weeks, {len(master.columns) - 1} index columns, "
          f"{master.iloc[0]['date']} -> {master.iloc[-1]['date']}")


if __name__ == "__main__":
    main()
