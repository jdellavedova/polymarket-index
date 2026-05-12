"""Surveillance Index: Adverse Selection / Spread Pattern.

Surfaces Paper 2 Stage 23 outputs: do markets with flagged-wallet activity
show measurable differences in effective spreads? The naive Glosten-Milgrom
prediction is widening; the realized pattern on Polymarket is the opposite
(spreads tighten in markets where flagged wallets trade). The likely
explanation is the Paper 1 finding that bot market makers crowd into markets
with high information flow to capture order flow rather than retreat from it.

Reads:
  stage23_spread_regression.csv  (formal regression of spread on flag indicator)
  stage23_market_spreads.csv     (per-market spread + flagged-wallet count)

Writes:
  surveillance_spread_latest.json
"""
from __future__ import annotations

import pandas as pd

from common import utc_now, write_json
from config import DATA_OUT, INSIDER_OUT


def main() -> None:
    reg = pd.read_csv(INSIDER_OUT / "stage23_spread_regression.csv")
    markets = pd.read_csv(INSIDER_OUT / "stage23_market_spreads.csv")

    # Headline numbers: split markets into two populations, compute mean spread
    # in each, and report the gap.
    sig_mask = markets["has_sig_wallet"] == 1
    mean_spread_sig_markets = float(markets.loc[sig_mask, "mean_spread"].mean())
    mean_spread_clean_markets = float(markets.loc[~sig_mask, "mean_spread"].mean())
    spread_gap_cents = mean_spread_sig_markets - mean_spread_clean_markets

    headline = {
        "n_markets_total": int(len(markets)),
        "n_markets_with_flagged": int(sig_mask.sum()),
        "share_markets_with_flagged": float(sig_mask.mean()),
        "mean_spread_in_flagged_markets": mean_spread_sig_markets,
        "mean_spread_in_clean_markets": mean_spread_clean_markets,
        "spread_gap": spread_gap_cents,
        "regression_beta_has_sig_wallet": float(reg.loc[0, "beta_sig"]),
        "regression_t_has_sig_wallet": float(reg.loc[0, "t_sig"]),
    }

    # Direction of the effect for the front-end label.
    headline["direction"] = "tighter" if spread_gap_cents < 0 else "wider"

    payload = {
        "index_name": "Adverse Selection / Spread Pattern",
        "short_name": "Spreads",
        "as_of": "2026-02-28",
        "snapshot_note": (
            "Cumulative snapshot from Paper 2. The negative coefficient indicates "
            "tighter, not wider, spreads in markets with flagged-wallet activity. "
            "This inverts the textbook Glosten-Milgrom prediction and is consistent "
            "with the Paper 1 finding that algorithmic market makers crowd into "
            "high-information-flow markets rather than retreating."
        ),
        "source_paper": "Della Vedova (2026), 'Detecting Informed Trading in Prediction Markets'",
        "headline": headline,
        "regression_summary": reg.to_dict(orient="records"),
        "generated_at": utc_now(),
        "sources": [
            str(INSIDER_OUT / "stage23_spread_regression.csv"),
            str(INSIDER_OUT / "stage23_market_spreads.csv"),
        ],
    }
    write_json(DATA_OUT / "surveillance_spread_latest.json", payload)

    print(
        f"Adverse Selection: flagged-market spread {mean_spread_sig_markets:.3f}, "
        f"clean-market spread {mean_spread_clean_markets:.3f}, "
        f"gap {spread_gap_cents:+.3f} ({headline['direction']})"
    )


if __name__ == "__main__":
    main()
