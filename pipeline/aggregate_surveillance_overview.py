"""Surveillance Overview.

Reads the four live surveillance JSONs (pii, insider_timing, spread, surprise)
and emits a single landing-page payload with 8 cards: 4 live + 4 roadmap stubs
for wash trading, matched orders, marking the close, and concentration. The
landing page reads only this file.
"""
from __future__ import annotations

import json

from common import utc_now, write_json
from config import DATA_OUT


def _read(name: str) -> dict | None:
    p = DATA_OUT / name
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _fmt_pct(x: float, dp: int = 2) -> str:
    return f"{x*100:.{dp}f}%"


def _fmt_signed(x: float, dp: int = 3) -> str:
    return f"{x:+.{dp}f}"


def main() -> None:
    pii = _read("pii_latest.json")
    timing = _read("surveillance_insider_timing_latest.json")
    spread = _read("surveillance_spread_latest.json")
    surprise = _read("surveillance_surprise_latest.json")
    wash = _read("surveillance_wash_latest.json")

    cards = []

    # --- Live cards (1-4) ---
    if pii:
        cards.append({
            "slot": 1,
            "status": "live",
            "name": "Informed Trading (PII)",
            "value": f"{_fmt_int(pii['headline']['total_flagged_p_lt_01'])} flagged",
            "secondary": f"of {_fmt_int(pii['headline']['total_wallets_tested'])} tested ({_fmt_pct(pii['headline']['flag_rate'], 2)})",
            "description": (
                "Wallets whose accuracy cannot be explained by price-following alone. "
                "Binomial orthogonality test at p<0.01 with Holm-Bonferroni and "
                "Benjamini-Hochberg corrections."
            ),
            "href": "/pii",
            "pattern_consistent_with": "private information advantage",
        })

    if timing:
        h = timing["headline"]
        cards.append({
            "slot": 2,
            "status": "live",
            "name": "Insider Timing Concentration",
            "value": f"{h['flagged_mean_hours_to_resolution']:.0f}h vs {h['nonflagged_mean_hours_to_resolution']:.0f}h",
            "secondary": f"flagged wallets enter at {h['hours_ratio_flagged_to_nonflagged']:.0%} of population time-to-resolution",
            "description": (
                "How close to market resolution flagged wallets place their trades, "
                "versus the non-flagged population. Lower ratios are consistent with "
                "pre-resolution information leakage."
            ),
            "href": "/surveillance/insider-timing",
            "pattern_consistent_with": "late-window information leakage",
        })

    if spread:
        h = spread["headline"]
        cards.append({
            "slot": 3,
            "status": "live",
            "name": "Adverse Selection",
            "value": f"{h['spread_gap']:+.3f} {h['direction']}",
            "secondary": f"in {_fmt_int(h['n_markets_with_flagged'])} markets with flagged wallets",
            "description": (
                "Effective spread differential between markets with and without "
                "flagged-wallet activity. The textbook Glosten-Milgrom prediction is "
                "wider spreads; on Polymarket the pattern is the opposite, consistent "
                "with algorithmic market makers crowding into information-rich markets."
            ),
            "href": "/surveillance/adverse-selection",
            "pattern_consistent_with": "market-maker crowding (not adverse-selection retreat)",
        })

    if surprise:
        h = surprise["headline"]
        cards.append({
            "slot": 4,
            "status": "live",
            "name": "Resolution Surprise",
            "value": _fmt_signed(h["high_surprise_ea_gap"], 3),
            "secondary": "flagged minus non-flagged excess accuracy, high-surprise markets",
            "description": (
                "Excess accuracy of flagged wallets in markets that resolved opposite "
                "to the consensus pre-resolution price. Flagged wallets are positive "
                "in every surprise tercile; the population is negative in every tercile."
            ),
            "href": "/surveillance/resolution-surprise",
            "pattern_consistent_with": "ex-ante knowledge of contrarian outcomes",
        })

    # --- Live: Wash Trading (Tiers 1 + 2 live; Tier 3 forthcoming) ---
    if wash:
        h = wash["headline"]
        wash_t2 = _read("surveillance_wash_tier2_latest.json")
        t2_strict = None
        if wash_t2:
            t2_strict = next((t for t in wash_t2.get("thresholds", []) if t.get("name") == "strict"), None)
        if t2_strict:
            secondary = (
                f"Tier 1: {h['self_matched_share_by_count']*100:.4f}% self-matched. "
                f"Tier 2 strict: {t2_strict['n_pairs']:,} pairs, "
                f"${t2_strict['round_trip_volume']/1e9:.2f}B round-trip volume "
                f"(upper bound; includes algorithmic market making)"
            )
        else:
            secondary = (
                f"Tier 1: {h['self_matched_share_by_count']*100:.4f}% of trades self-matched "
                f"(${h['self_matched_volume']:,.0f} of ${h['total_volume']/1e9:.1f}B)"
            )
        cards.append({
            "slot": 5,
            "status": "live",
            "name": "Wash Trading (Tiers 1+2)",
            "value": f"{h['n_self_matched']:,} self-matched",
            "secondary": secondary,
            "description": (
                "Tier 1: self-matched trades, FINRA Rule 6140 analog. Polymarket's CLOB does not "
                "permit self-matching at scale; Tier 1 is essentially zero. Tier 2: round-trip wash, "
                "wallets buying and selling the same token through different counterparties. The Tier 2 "
                "strict-threshold set is dominated by algorithmic market makers, not manipulators; "
                "Tier 3 (linked-wallet clusters) is forthcoming and required to isolate wash from "
                "inventory rotation."
            ),
            "href": "/surveillance/wash-trading",
            "pattern_consistent_with": "volume inflation via non-economic trading",
        })

    # --- Roadmap stubs (6-8) ---
    stubs = [
        {
            "slot": 6,
            "status": "coming_soon",
            "name": "Matched / Pre-Arranged Orders",
            "value": "in development",
            "secondary": "wallet pairs with persistent counterparty relationships",
            "description": (
                "Wallet pairs trading the same markets within short windows, "
                "repeatedly, with offsetting positions. Tighter Daubert burden "
                "than wash trading because legitimate liquidity providers create "
                "false positives; reported as 'patterns consistent with' only."
            ),
            "href": "/surveillance",
            "pattern_consistent_with": "coordinated bilateral trading",
        },
        {
            "slot": 7,
            "status": "coming_soon",
            "name": "Marking the Close",
            "value": "in development",
            "secondary": "directional concentration in final pre-resolution window",
            "description": (
                "Concentration of one-sided trading in the final hours before market "
                "resolution moving the settling price. Limited by minute-grain "
                "timestamps in on-chain data; intraday precision not possible."
            ),
            "href": "/surveillance",
            "pattern_consistent_with": "settling-price manipulation",
        },
    ]
    cards.extend(stubs)

    # --- Live: Concentration / Pump Risk (slot 8) ---
    conc = _read("surveillance_concentration_latest.json")
    if conc:
        ss = conc["population"]["summary_stats"]
        strict = next((t for t in conc.get("thresholds", []) if t.get("min_hhi") == 0.75), None)
        cards.append({
            "slot": 8,
            "status": "live",
            "name": "Concentration / Pump Risk",
            "value": f"median HHI {ss['median_hhi']:.3f}",
            "secondary": (
                f"{strict['n_markets']:,} markets with HHI >= 0.75 "
                f"({strict['share_of_eligible']*100:.1f}% of "
                f"{conc['population']['n_markets_eligible']:,} eligible)"
                if strict else "Herfindahl per market"
            ),
            "description": (
                "Per-market Herfindahl-Hirschman Index of participation. High HHI is the "
                "precondition under which one trader can move the consensus price alone, "
                "not direct evidence that they did. The index flags structure, not intent. "
                "Threshold cutpoints map onto DOJ horizontal-merger guideline categories."
            ),
            "href": "/surveillance/concentration",
            "pattern_consistent_with": "single-trader price control",
        })

    # Aggregate stats for the page header strip.
    n_live = sum(1 for c in cards if c["status"] == "live")
    n_total = len(cards)
    as_of_list = [
        timing.get("as_of") if timing else None,
        spread.get("as_of") if spread else None,
        surprise.get("as_of") if surprise else None,
        pii.get("as_of") if pii else None,
    ]
    as_of = max([d for d in as_of_list if d], default=None)

    payload = {
        "title": "DV-PMI Surveillance Indices",
        "subtitle": (
            "Eight market-integrity tests on the 670M-trade Polymarket on-chain "
            "panel. Patterns reported are statistical, not legal conclusions."
        ),
        "n_live": n_live,
        "n_total": n_total,
        "as_of": as_of,
        "generated_at": utc_now(),
        "cards": cards,
        "infeasible_tests": [
            {
                "name": "Spoofing",
                "reason": "Requires cancelled-order data, which is not on-chain.",
                "finra_rule": "Section 9(a) Securities Exchange Act; FINRA Rule 5210",
            },
            {
                "name": "Layering",
                "reason": "Requires order-book depth snapshots, which we do not collect.",
                "finra_rule": "FINRA Rule 5210",
            },
            {
                "name": "Quote Stuffing",
                "reason": "Requires sub-second order submission rates, which the on-chain trade record does not contain.",
                "finra_rule": "FINRA Rule 5210; Reg NMS Rule 605",
            },
        ],
        "methodology_note": (
            "All eight indices report patterns consistent with the named conduct "
            "rather than legal conclusions about any wallet's intent. No public "
            "page names a specific wallet address. Per-wallet detail is available "
            "in downloadable CSVs subject to the usage notice on the methodology "
            "page. We benchmark against the leading heuristic approach in the "
            "literature (Mitts and Ofir, 2026); the concordance is reported on "
            "the PII page."
        ),
    }
    write_json(DATA_OUT / "surveillance_overview_latest.json", payload)

    print(f"Surveillance Overview: {n_live}/{n_total} indices live as of {as_of}")


if __name__ == "__main__":
    main()
