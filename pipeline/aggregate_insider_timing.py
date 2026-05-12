"""Surveillance Index: Insider Timing Concentration.

Surfaces Paper 2 Stage 21 outputs: when do flagged wallets trade relative to
market resolution? Headline finding is that significant (p<0.01) wallets enter
markets at roughly half the time-to-resolution of the population, consistent
with information leakage near the event.

Reads:
  stage21_timing_by_significance.csv  (mean hours, fractions in final windows)
  stage21_ea_by_timing_bucket.csv      (excess accuracy by days-to-resolution bucket)
  stage21_timing_regression.csv        (formal regression of timing on significance)

Writes:
  surveillance_insider_timing_latest.json
"""
from __future__ import annotations

import pandas as pd

from common import utc_now, write_json
from config import DATA_OUT, INSIDER_OUT


def main() -> None:
    by_sig = pd.read_csv(INSIDER_OUT / "stage21_timing_by_significance.csv")
    by_bucket = pd.read_csv(INSIDER_OUT / "stage21_ea_by_timing_bucket.csv")
    reg = pd.read_csv(INSIDER_OUT / "stage21_timing_regression.csv")

    sig = by_sig[by_sig["group"] == "significant"].iloc[0]
    nonsig = by_sig[by_sig["group"] == "non_significant"].iloc[0]

    headline = {
        "flagged_mean_hours_to_resolution": float(sig["mean_hours"]),
        "flagged_median_hours_to_resolution": float(sig["median_hours"]),
        "nonflagged_mean_hours_to_resolution": float(nonsig["mean_hours"]),
        "nonflagged_median_hours_to_resolution": float(nonsig["median_hours"]),
        "flagged_frac_final_72h": float(sig["mean_frac_72h"]),
        "nonflagged_frac_final_72h": float(nonsig["mean_frac_72h"]),
        "flagged_n_wallets": int(sig["n_wallets"]),
        "nonflagged_n_wallets": int(nonsig["n_wallets"]),
    }

    # Concentration ratio: how much earlier do flagged wallets enter relative
    # to the population? Lower ratio = stronger pre-resolution clustering.
    headline["hours_ratio_flagged_to_nonflagged"] = (
        headline["flagged_mean_hours_to_resolution"]
        / headline["nonflagged_mean_hours_to_resolution"]
    )

    # Chart data: excess accuracy by days-to-resolution bucket, split by flag.
    buckets = []
    for bucket in by_bucket["dtr_bucket"].unique():
        row_flag = by_bucket[
            (by_bucket["dtr_bucket"] == bucket) & (by_bucket["is_significant"] == 1)
        ]
        row_nonflag = by_bucket[
            (by_bucket["dtr_bucket"] == bucket) & (by_bucket["is_significant"] == 0)
        ]
        buckets.append({
            "dtr_bucket": bucket,
            "flagged_ea": float(row_flag["excess_accuracy"].iloc[0]) if len(row_flag) else None,
            "flagged_n_trades": int(row_flag["n_trades"].iloc[0]) if len(row_flag) else 0,
            "nonflagged_ea": float(row_nonflag["excess_accuracy"].iloc[0]) if len(row_nonflag) else None,
            "nonflagged_n_trades": int(row_nonflag["n_trades"].iloc[0]) if len(row_nonflag) else 0,
        })

    payload = {
        "index_name": "Insider Timing Concentration",
        "short_name": "Timing",
        "as_of": "2026-02-28",  # Paper 2 sample end
        "snapshot_note": "Cumulative snapshot from Paper 2; weekly time series is a future release item.",
        "source_paper": "Della Vedova (2026), 'Detecting Informed Trading in Prediction Markets'",
        "headline": headline,
        "by_bucket": buckets,
        "regression_summary": reg.to_dict(orient="records"),
        "generated_at": utc_now(),
        "sources": [
            str(INSIDER_OUT / "stage21_timing_by_significance.csv"),
            str(INSIDER_OUT / "stage21_ea_by_timing_bucket.csv"),
            str(INSIDER_OUT / "stage21_timing_regression.csv"),
        ],
    }
    write_json(DATA_OUT / "surveillance_insider_timing_latest.json", payload)

    print(
        f"Insider Timing: flagged mean {headline['flagged_mean_hours_to_resolution']:.0f}h, "
        f"non-flagged {headline['nonflagged_mean_hours_to_resolution']:.0f}h "
        f"(ratio {headline['hours_ratio_flagged_to_nonflagged']:.2f})"
    )


if __name__ == "__main__":
    main()
