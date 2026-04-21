"""Private Information Index (PII).

Snapshot-only for v1. Reads Paper 2 stage outputs from 2. Insider/output/ and
emits the flagged-wallet breakdown by MNPI taxonomy and by wallet type.

Weekly PII time series is a v1.1 item; it requires merging the flagged wallet
set with per-wallet trade timestamps from processed_trades.csv (233M rows),
which is a dedicated ETL job.
"""
from __future__ import annotations

import pandas as pd

from common import utc_now, write_json
from config import DATA_OUT, INSIDER_OUT


def main() -> None:
    stage19_wallets = INSIDER_OUT / "stage19_significant_wallets.csv"
    stage19_by_type = INSIDER_OUT / "stage19_summary_by_type.csv"
    stage22_mnpi = INSIDER_OUT / "stage22_mnpi_placebo.csv"
    stage22_cat = INSIDER_OUT / "stage22_significant_by_category.csv"

    for p in (stage19_wallets, stage19_by_type, stage22_mnpi, stage22_cat):
        if not p.exists():
            raise FileNotFoundError(p)

    flagged = pd.read_csv(stage19_wallets)
    by_type = pd.read_csv(stage19_by_type)
    mnpi = pd.read_csv(stage22_mnpi)
    cat = pd.read_csv(stage22_cat)

    total_wallets_tested = int(by_type["n_wallets"].sum())
    total_flagged_01 = int(by_type["n_significant_01"].sum())
    flag_rate_total = total_flagged_01 / total_wallets_tested

    by_type_out = by_type[[
        "wallet_type", "n_wallets", "n_significant_01",
        "pct_significant_01", "mean_excess_accuracy",
    ]].copy()
    by_type_out["flag_rate"] = by_type_out["n_significant_01"] / by_type_out["n_wallets"]

    mnpi_out = mnpi[[
        "mnpi_type", "n_wallets_tested", "n_flagged_01",
        "flag_rate_01", "z_above_1pct",
    ]].copy()

    cat_out = cat[[
        "category", "n_wallets_tested", "n_significant_01", "frac_significant_01",
    ]].copy()

    snapshot_csv = DATA_OUT / "pii_snapshot.csv"
    pii_wide = pd.DataFrame({
        "metric": [
            "total_wallets_tested",
            "total_flagged_p_lt_01",
            "flag_rate_overall",
            "flag_rate_vote",
            "flag_rate_action",
            "flag_rate_performance",
            "flag_rate_stochastic",
            "holm_bonferroni_survivors",
            "bh_fdr_survivors",
        ],
        "value": [
            total_wallets_tested,
            total_flagged_01,
            flag_rate_total,
            float(mnpi.loc[mnpi["mnpi_type"] == "vote", "flag_rate_01"].iloc[0]),
            float(mnpi.loc[mnpi["mnpi_type"] == "action", "flag_rate_01"].iloc[0]),
            float(mnpi.loc[mnpi["mnpi_type"] == "performance", "flag_rate_01"].iloc[0]),
            float(mnpi.loc[mnpi["mnpi_type"] == "stochastic", "flag_rate_01"].iloc[0]),
            int(flagged["significant_01_holm"].sum()),
            int(flagged["significant_05_bh"].sum()),
        ],
    })
    pii_wide.to_csv(snapshot_csv, index=False)

    payload = {
        "index_name": "Private Information Index",
        "short_name": "PII",
        "snapshot_note": "Cumulative snapshot; weekly time series requires trade-timestamp join (v1.1)",
        "as_of": "2026-02-28",
        "source_paper": "Della Vedova (2026), 'Detecting Informed Trading in Prediction Markets'",
        "headline": {
            "total_wallets_tested": total_wallets_tested,
            "total_flagged_p_lt_01": total_flagged_01,
            "flag_rate": flag_rate_total,
            "holm_bonferroni_survivors": int(flagged["significant_01_holm"].sum()),
            "bh_fdr_survivors": int(flagged["significant_05_bh"].sum()),
        },
        "mnpi_taxonomy": mnpi_out.to_dict(orient="records"),
        "by_wallet_type": by_type_out.to_dict(orient="records"),
        "by_raw_category": cat_out.to_dict(orient="records"),
        "vs_mitts_ofir": {
            "their_flag_rate": 0.38,
            "our_flag_rate": flag_rate_total,
            "cohens_kappa": 0.007,
            "note": "Near-zero concordance between structural (orthogonality) and heuristic (5-signal) approaches.",
        },
        "generated_at": utc_now(),
        "sources": [str(p) for p in (stage19_wallets, stage19_by_type, stage22_mnpi, stage22_cat)],
    }
    write_json(DATA_OUT / "pii_latest.json", payload)

    print(f"PII: {total_flagged_01:,} of {total_wallets_tested:,} wallets flagged "
          f"({flag_rate_total:.2%}); vote={mnpi_out.iloc[0]['flag_rate_01']:.2%}, "
          f"stochastic={mnpi_out.iloc[3]['flag_rate_01']:.2%}")


if __name__ == "__main__":
    main()
