"""Surveillance Index: Resolution Surprise.

Surfaces Paper 2 Stage 31: when market resolutions surprise the pre-resolution
price (winner was not the consensus), do flagged wallets show systematically
higher excess accuracy? Yes. Flagged-wallet excess accuracy is positive across
all surprise terciles; non-flagged is negative across all terciles.

Reads:
  stage31_surprise_regression.csv  (formal regression)
  stage31_flag_rate_by_surprise.csv (tercile-level excess accuracy + edge)

Writes:
  surveillance_surprise_latest.json
"""
from __future__ import annotations

import pandas as pd

from common import utc_now, write_json
from config import DATA_OUT, INSIDER_OUT


def main() -> None:
    reg = pd.read_csv(INSIDER_OUT / "stage31_surprise_regression.csv")
    terciles = pd.read_csv(INSIDER_OUT / "stage31_flag_rate_by_surprise.csv")

    by_tercile = []
    for t in ["low", "medium", "high"]:
        flag_row = terciles[(terciles["surprise_tercile"] == t) & (terciles["is_flagged"] == 1)].iloc[0]
        nonflag_row = terciles[(terciles["surprise_tercile"] == t) & (terciles["is_flagged"] == 0)].iloc[0]
        by_tercile.append({
            "surprise_tercile": t,
            "flagged_excess_accuracy": float(flag_row["excess_accuracy"]),
            "flagged_mean_edge": float(flag_row["mean_edge"]),
            "flagged_n_trades": int(flag_row["n_trades"]),
            "flagged_volume": float(flag_row["volume"]),
            "nonflagged_excess_accuracy": float(nonflag_row["excess_accuracy"]),
            "nonflagged_mean_edge": float(nonflag_row["mean_edge"]),
            "nonflagged_n_trades": int(nonflag_row["n_trades"]),
            "nonflagged_volume": float(nonflag_row["volume"]),
            "ea_gap": float(flag_row["excess_accuracy"] - nonflag_row["excess_accuracy"]),
        })

    headline = {
        "regression_beta_flagged": float(reg.loc[0, "b_flagged"]),
        "regression_t_flagged": float(reg.loc[0, "t_flagged"]),
        "regression_beta_interaction": float(reg.loc[0, "b_interaction"]),
        "regression_t_interaction": float(reg.loc[0, "t_interaction"]),
        "regression_n": int(reg.loc[0, "n"]),
        # The most digestible single number for the card.
        "high_surprise_ea_gap": next(
            t["ea_gap"] for t in by_tercile if t["surprise_tercile"] == "high"
        ),
    }

    payload = {
        "index_name": "Resolution Surprise",
        "short_name": "Surprise",
        "as_of": "2026-02-28",
        "snapshot_note": (
            "Cumulative snapshot from Paper 2. Excess accuracy in markets that "
            "resolved opposite to the consensus pre-resolution price. Flagged "
            "wallets show positive excess accuracy in every surprise tercile, "
            "including in markets where the population was systematically wrong."
        ),
        "source_paper": "Della Vedova (2026), 'Detecting Informed Trading in Prediction Markets'",
        "headline": headline,
        "by_tercile": by_tercile,
        "regression_summary": reg.to_dict(orient="records"),
        "generated_at": utc_now(),
        "sources": [
            str(INSIDER_OUT / "stage31_surprise_regression.csv"),
            str(INSIDER_OUT / "stage31_flag_rate_by_surprise.csv"),
        ],
    }
    write_json(DATA_OUT / "surveillance_surprise_latest.json", payload)

    print(
        f"Resolution Surprise: flagged EA gap in high-surprise tercile "
        f"{headline['high_surprise_ea_gap']:+.3f}; flagged beta={headline['regression_beta_flagged']:.3f} "
        f"(t={headline['regression_t_flagged']:.1f})"
    )


if __name__ == "__main__":
    main()
