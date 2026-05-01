"""Generate a plain-English weekly narrative for the landing page.

Tone: written for a general / practitioner reader (journalist, policy analyst,
hedge-fund PM skimming on their phone). Favour concrete dollar figures, named
markets, and win/lose narratives over Greek letters and z-scores. Keep z-score
callouts only when they genuinely matter.

Reads all *_latest.json files and writes `weekly_narrative.json` with three
sentences plus a headline quote. Hand-edits are preserved unless a newer
weekly refresh has arrived.
"""
from __future__ import annotations

import json
from datetime import datetime

from common import utc_now, write_json
from config import DATA_OUT


def _read(name: str) -> dict:
    with open(DATA_OUT / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _usd(n: float) -> str:
    a = abs(n)
    sign = "-" if n < 0 else ""
    if a >= 1e9:
        return f"{sign}${a / 1e9:.2f} billion"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.0f} million"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.0f}K"
    return f"{sign}${a:.0f}"


def _commas(n: int) -> str:
    return f"{int(n):,}"


def _compact_trades(n: int) -> str:
    if n >= 1e6:
        return f"{n / 1e6:.1f} million"
    if n >= 1e3:
        return f"{n / 1e3:.0f}K"
    return str(n)


def _quote_question(q: str | None) -> str:
    """Format a market question for inline quotation. Keep verbatim and wrap
    in quotes; a journalist reading this wants the exact market name."""
    if not q:
        return '"a market created after our latest metadata snapshot"'
    return '"' + q.strip().rstrip("?") + '?"'


def main() -> None:
    pwi = _read("pwi_latest.json")
    bot = _read("bot_share_latest.json")
    exe = _read("execution_latest.json")
    pg = _read("price_gap_latest.json")
    eff = _read("efficiency_latest.json")
    pii = _read("pii_latest.json")
    activity = _read("weekly_activity_latest.json")
    top = _read("top_markets_latest.json")
    try:
        profit = _read("profit_split_latest.json")
    except FileNotFoundError:
        profit = None

    as_of = activity.get("as_of") or pwi["as_of"]
    total_vol = activity.get("total_usd_volume", 0.0)
    total_trades = activity.get("total_trades", 0)
    new_participants = activity.get("new_wallets", 0)
    new_ma13w = activity.get("new_wallets_ma13w")
    vol_z = activity.get("volume_z52w")
    bot_share_now = activity["by_type_share"]["bot"]

    # Sentence 1: headline activity (dollars, trades, new users)
    new_ctx = ""
    if new_ma13w is not None:
        if new_participants > new_ma13w * 1.2:
            new_ctx = " (above the recent trend)"
        elif new_participants < new_ma13w * 0.8:
            new_ctx = " (below the recent trend)"
    s1 = (
        f"Traders moved {_usd(total_vol)} through Polymarket in the week of {as_of}, "
        f"across {_compact_trades(total_trades)} individual trades, "
        f"with {_commas(new_participants)} wallets placing their first-ever bet{new_ctx}. "
        f"Algorithmic wallets accounted for {bot_share_now * 100:.0f}% of weekly counterparty events "
        f"(both maker and taker sides counted); everyone else split the remaining "
        f"{((1 - bot_share_now) * 100):.0f}%."
    )

    # Sentence 2: most-watched markets (narrative hook)
    top5 = top.get("markets", [])
    if top5:
        leader = top5[0]
        q = _quote_question(leader.get("question"))
        lead_vol = _usd(leader.get("usd_volume", 0))
        # Count election markets in top 5 as a proxy for "politics was the story"
        topics = []
        for m in top5[:3]:
            cq = (m.get("question") or "").lower()
            if "election" in cq or "prime minister" in cq or "president" in cq:
                topics.append("politics")
            elif "fed" in cq or "interest rate" in cq or "inflation" in cq:
                topics.append("macro")
            elif "bitcoin" in cq or "crypto" in cq or "eth" in cq:
                topics.append("crypto")
        theme = ""
        if topics.count("politics") >= 2:
            theme = " Politics dominated the week's attention."
        elif topics.count("macro") >= 2:
            theme = " Macro calls drove the action."
        elif topics.count("crypto") >= 2:
            theme = " Crypto-linked contracts led the action."
        s2 = (
            f"The most-traded market was {q}, pulling in {lead_vol} of volume.{theme}"
        )
    else:
        s2 = "Top-market data not available this week."

    # Sentence 3: who won, who lost (from profit_split if available; else fallback
    # to Prelec alpha interpretation)
    s3 = None
    if profit:
        pb = profit.get("by_type", {})
        bot_row = pb.get("bot")
        retail_row = pb.get("active_retail")
        if bot_row and retail_row:
            bot_pnl = bot_row.get("pnl", 0)
            retail_pnl = retail_row.get("pnl", 0)
            bot_roi = bot_row.get("pnl_roi_bps", 0)
            retail_roi = retail_row.get("pnl_roi_bps", 0)
            if bot_pnl > 0 and retail_pnl < 0:
                s3 = (
                    f"The usual pattern held: algorithmic traders made {_usd(bot_pnl)} "
                    f"({bot_roi:+.0f} bps on volume) while retail traders lost "
                    f"{_usd(abs(retail_pnl))} ({retail_roi:+.0f} bps)."
                )
            elif bot_pnl < 0 and retail_pnl > 0:
                s3 = (
                    f"An unusual reversal: retail traders outperformed bots this week. "
                    f"Active retail earned {_usd(retail_pnl)} "
                    f"({retail_roi:+.0f} bps on volume) while algorithms lost "
                    f"{_usd(abs(bot_pnl))} ({bot_roi:+.0f} bps). Weeks like this are rare "
                    f"(bots are typically on top by the execution channel)."
                )
            elif bot_pnl > 0 and retail_pnl > 0:
                s3 = (
                    f"Both sides made money this week. Bots collected {_usd(bot_pnl)} "
                    f"({bot_roi:+.0f} bps); retail collected {_usd(retail_pnl)} "
                    f"({retail_roi:+.0f} bps)."
                )
            else:
                s3 = (
                    f"Both sides lost money on resolved trades this week. Bots dropped "
                    f"{_usd(abs(bot_pnl))} ({bot_roi:+.0f} bps); retail dropped "
                    f"{_usd(abs(retail_pnl))} ({retail_roi:+.0f} bps). Much of the "
                    f"activity was in markets that have not yet resolved."
                )
    if s3 is None:
        alpha = pwi["value"]
        if alpha < 0.85:
            s3 = (
                f"Human traders continued to over-weight long shots "
                f"(probability-weighting parameter of {alpha:.2f}, below the rational 1.0 "
                f"and close to the 0.65 Kahneman-Tversky experimental value)."
            )
        else:
            s3 = (
                f"Probability weighting among human traders was near rational this week "
                f"(parameter {alpha:.2f})."
            )

    # Headline quote (one sentence, journalist-quotable). Volume trend and
    # new-participant trend are SEPARATE facts; do not weld them.
    if vol_z is not None and vol_z > 1.5:
        headline_quote = (
            f"Polymarket saw {_usd(total_vol)} of trading this week, the busiest week since late "
            f"last year. {_commas(new_participants)} wallets placed their first bet."
        )
    elif vol_z is not None and vol_z > 0.5:
        headline_quote = (
            f"Polymarket traders moved {_usd(total_vol)} this week, above the 52-week trend, "
            f"on {_compact_trades(total_trades)} individual trades."
        )
    elif top5 and any(t in (top5[0].get("question") or "").lower()
                       for t in ("election", "prime minister", "president")):
        leader_q = _quote_question(top5[0].get("question"))
        headline_quote = (
            f"Polymarket traders moved {_usd(total_vol)} this week, with the biggest action on {leader_q}."
        )
    else:
        headline_quote = (
            f"Polymarket traders moved {_usd(total_vol)} this week across "
            f"{_compact_trades(total_trades)} individual trades."
        )

    payload = {
        "as_of": as_of,
        "generated_at": utc_now(),
        "sentences": [s1, s2, s3],
        "headline_quote": headline_quote,
        "author": "Joshua Della Vedova, University of San Diego",
        "attribution_short": "Della Vedova (2026), Della Vedova Prediction Market Indices",
        "hand_edited": False,
        "notes": (
            "This file is regenerated by build_weekly_narrative.py each refresh. "
            "To override the auto-generated prose, edit the sentences + headline_quote "
            "and set hand_edited to true; the generator will skip future auto-regeneration "
            "as long as as_of matches."
        ),
    }

    existing_path = DATA_OUT / "weekly_narrative.json"
    if existing_path.exists():
        try:
            with open(existing_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("hand_edited") and existing.get("as_of") == as_of:
                print(f"Narrative: preserving hand-edited version for {as_of}")
                return
        except Exception:
            pass

    write_json(existing_path, payload)
    print(f"Narrative: generated default prose for {as_of}")


if __name__ == "__main__":
    main()
