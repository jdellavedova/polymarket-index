"""Private Information Index (PII) — aligned with the June 2026 recast of
Paper 2, "Detecting Informed Trading in Prediction Markets: One Event at a
Time" (SSRN 6567238, revised June 2026).

PRIMARY block (event_detection): the paper's information instrument — the
per-event joint-accuracy test at the trader-event unit, multiplicity-corrected
within trader, neg-risk-excluded, with the beta-binomial dependence adjustment
at the estimated intraclass correlation (rho = 0.18). The funnel and the
survivor set are COMPUTED from the paper's committed stage outputs
(stage68_conditioned_flags.csv, stage68_investigatable_clean_core.csv), so the
dashboard reproduces Table 3 / Table 9 rather than transcribing them. Sample
constants that live only in the paper text (testable-pair counts, placebo
size, core profits) are carried with explicit provenance notes.

SECONDARY block (sustained_skill): the original wallet-level pooled
excess-accuracy screen (stage19). The June 2026 revision reclassifies this
statistic as detecting sustained skill, NOT episodic information (pooled
averages dilute episodic signal). It remains published as a skill-persistence
monitor under that reclassified label.

The legacy `headline` block mirrors sustained_skill for backward
compatibility with existing consumers (email digest, surveillance overview);
their display copy is updated to the reclassified framing.

No wallet addresses or per-pair rows are ever emitted; survivors are
aggregated by disclosure archetype, matching the paper's own anonymization.
"""
from __future__ import annotations

import pandas as pd
from scipy.stats import betabinom

from common import utc_now, write_json
from config import DATA_OUT, INSIDER_OUT

ICC_RHO = 0.18  # paper Section 7: ICC point estimate 0.18, 95% CI [0.10, 0.27]

ARCHETYPE_LABEL = {
    "speech_word_leak": "speech-content",
    "doc_decision_leak": "document-or-decision",
    "other": "enumeration",
}

# Constants stated in the paper text (not derivable from the committed stage
# CSVs). Provenance: recast Sections 3-6 / Tables 4 and 8.
PAPER_CONSTANTS = {
    "testable_pairs": 4_657_827,
    "traders_with_testable_pair": 1_020_455,
    "events_with_testable_pair": 27_361,
    "placebo_pairs": 597_146,
    "placebo_corrected_discoveries": 0,
    "core_profit_pairs_covered": 74,
    "core_profit_total_usd": 501_167,
    "core_profit_median_usd": 778,
}


def _beta_binomial_adjusted_p(n: int, k: int, q: float, rho: float = ICC_RHO) -> float:
    """One-sided tail P(X >= k) under a beta-binomial inflation of the
    independence null at intraclass correlation rho (paper Section 7)."""
    a = q * (1 - rho) / rho
    b = (1 - q) * (1 - rho) / rho
    return float(betabinom.sf(k - 1, n, a, b))


def main() -> None:
    flags = INSIDER_OUT / "stage68_conditioned_flags.csv"
    core = INSIDER_OUT / "stage68_investigatable_clean_core.csv"
    stage19_wallets = INSIDER_OUT / "stage19_significant_wallets.csv"
    stage19_by_type = INSIDER_OUT / "stage19_summary_by_type.csv"
    for p in (flags, core, stage19_wallets, stage19_by_type):
        if not p.exists():
            raise FileNotFoundError(p)

    # ---- PRIMARY: trader-event detection (reproduces Tables 3 and 9) ----
    fl = pd.read_csv(flags)
    raw_flags = len(fl)
    neg_risk_excluded = int(fl["negRisk"].sum())
    defensible = int((fl["class"] == "independent_defensible").sum())
    tiers = fl.loc[fl["class"] == "independent_defensible", "dep_tier"].value_counts()
    tier_core = int(tiers.get("1_distinct_questions_clean", 0))
    tier_single_game = int(sum(v for k, v in tiers.items() if str(k).startswith("2")))
    tier_ladders = int(sum(v for k, v in tiers.items() if str(k).startswith("3")))

    co = pd.read_csv(core)
    co["p_adj"] = co.apply(
        lambda r: _beta_binomial_adjusted_p(
            int(r.n_markets_traded), int(r.n_markets_correct), float(r.mean_null_prob)
        ),
        axis=1,
    )
    surv5 = co[co["p_adj"] < 0.05]
    surv1 = co[co["p_adj"] < 0.01]
    by_arch = (
        surv5["archetype"].map(lambda a: ARCHETYPE_LABEL.get(a, a))
        .value_counts()
        .rename_axis("archetype")
        .reset_index(name="n")
        .to_dict(orient="records")
    )

    event_detection = {
        "unit": "trader-event pair (wallet x multi-market event)",
        "method": (
            "Per-event joint-accuracy test against the price-implied null "
            "(each position's success probability is its own transaction-time "
            "price), Holm-corrected across the events each trader contests, "
            "mutually-exclusive (neg-risk) events excluded, and a "
            f"beta-binomial dependence adjustment at intraclass correlation "
            f"rho = {ICC_RHO}."
        ),
        "sample": {
            "testable_pairs": PAPER_CONSTANTS["testable_pairs"],
            "traders": PAPER_CONSTANTS["traders_with_testable_pair"],
            "events": PAPER_CONSTANTS["events_with_testable_pair"],
            "provenance": "paper Section 3 (not derivable from committed stage outputs)",
        },
        "funnel": {
            "raw_flags_p_lt_01": raw_flags,
            "excluded_mutually_exclusive": neg_risk_excluded,
            "defensible": defensible,
            "distinct_question_core": tier_core,
            "single_game_multi_bet": tier_single_game,
            "cumulative_threshold_ladders": tier_ladders,
        },
        "survivors": {
            "at_5pct_dependence_adjusted": int(len(surv5)),
            "at_1pct_dependence_adjusted": int(len(surv1)),
            "adjusted_p_min": round(float(surv5["p_adj"].min()), 3),
            "adjusted_p_max": round(float(surv5["p_adj"].max()), 3),
            "icc_rho": ICC_RHO,
            "by_archetype": by_arch,
            "note": (
                "All survivors are all-correct records on disclosure events; "
                "reported by archetype only, matching the paper's anonymization."
            ),
        },
        "placebo": {
            "class": "stochastic (asset-price-direction markets)",
            "pairs": PAPER_CONSTANTS["placebo_pairs"],
            "corrected_discoveries": PAPER_CONSTANTS["placebo_corrected_discoveries"],
            "provenance": "paper Section 5",
        },
        "core_profits": {
            "pairs_covered": PAPER_CONSTANTS["core_profit_pairs_covered"],
            "total_usd": PAPER_CONSTANTS["core_profit_total_usd"],
            "median_usd_per_pair": PAPER_CONSTANTS["core_profit_median_usd"],
            "provenance": "paper Table 8",
        },
        "forward_test": (
            "The statistic flags the trader charged in CFTC v. Spagnuolo at "
            "the five percent level under the estimated within-event "
            "dependence; it was first run sixteen days before the complaint. "
            "Flags mark statistical patterns consistent with informed "
            "trading, not legal determinations."
        ),
    }

    # ---- SECONDARY: wallet-level sustained-skill screen (stage19) ----
    by_type = pd.read_csv(stage19_by_type)
    flagged = pd.read_csv(stage19_wallets)
    total_tested = int(by_type["n_wallets"].sum())
    total_flagged = int(by_type["n_significant_01"].sum())
    flag_rate = total_flagged / total_tested

    by_type_out = by_type[[
        "wallet_type", "n_wallets", "n_significant_01",
        "pct_significant_01", "mean_excess_accuracy",
    ]].copy()
    by_type_out["flag_rate"] = by_type_out["n_significant_01"] / by_type_out["n_wallets"]

    sustained_skill = {
        "reclassification_note": (
            "This wallet-level pooled excess-accuracy screen was the April "
            "2026 draft's detector. The June 2026 revision reclassifies it: "
            "a statistic that averages a trader's record measures sustained "
            "skill and is asymptotically blind to episodic information. It "
            "is published here as a skill-persistence monitor, not an "
            "informed-trading detector."
        ),
        "total_wallets_tested": total_tested,
        "total_flagged_p_lt_01": total_flagged,
        "flag_rate": flag_rate,
        "holm_bonferroni_survivors": int(flagged["significant_01_holm"].sum()),
        "bh_fdr_survivors": int(flagged["significant_05_bh"].sum()),
        "by_wallet_type": by_type_out.to_dict(orient="records"),
        "data_note": (
            "July 2026 rerun on the corrected master (trades through "
            "2026-07-03; wallets with at least 10 resolved trades)."
        ),
    }

    payload = {
        "index_name": "Private Information Index",
        "short_name": "PII",
        "snapshot_note": "Cumulative snapshot; refreshed when the paper pipeline reruns",
        "as_of": "2026-07-03",
        "source_paper": (
            "Della Vedova (2026), 'Detecting Informed Trading in Prediction "
            "Markets: One Event at a Time', SSRN 6567238 (revised June 2026)"
        ),
        "event_detection": event_detection,
        "sustained_skill": sustained_skill,
        # Legacy mirror of sustained_skill for existing consumers; display
        # copy in those consumers uses the reclassified framing.
        "headline": {
            "total_wallets_tested": total_tested,
            "total_flagged_p_lt_01": total_flagged,
            "flag_rate": flag_rate,
            "holm_bonferroni_survivors": sustained_skill["holm_bonferroni_survivors"],
            "bh_fdr_survivors": sustained_skill["bh_fdr_survivors"],
        },
        "generated_at": utc_now(),
        "sources": [str(p) for p in (flags, core, stage19_wallets, stage19_by_type)],
    }
    write_json(DATA_OUT / "pii_latest.json", payload)

    # Snapshot CSV for the /data downloads page
    pii_wide = pd.DataFrame({
        "metric": [
            "event_raw_flags_p_lt_01", "event_defensible", "event_distinct_question_core",
            "event_survivors_5pct_dep_adjusted", "event_survivors_1pct_dep_adjusted",
            "placebo_corrected_discoveries",
            "skill_wallets_tested", "skill_flagged_p_lt_01", "skill_flag_rate",
            "skill_holm_survivors", "skill_bh_fdr_survivors",
        ],
        "value": [
            raw_flags, defensible, tier_core,
            int(len(surv5)), int(len(surv1)),
            PAPER_CONSTANTS["placebo_corrected_discoveries"],
            total_tested, total_flagged, flag_rate,
            sustained_skill["holm_bonferroni_survivors"],
            sustained_skill["bh_fdr_survivors"],
        ],
    })
    pii_wide.to_csv(DATA_OUT / "pii_snapshot.csv", index=False)

    print(f"PII: event-level {raw_flags:,} raw flags -> {tier_core} core -> "
          f"{len(surv5)} survive at 5% (rho={ICC_RHO}); "
          f"skill screen {total_flagged:,} of {total_tested:,} ({flag_rate:.2%})")


if __name__ == "__main__":
    main()
