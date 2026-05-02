"""build_briefings.py — generate the Briefings page payload from existing
indices plus the per-market microstructure file produced by
aggregate_market_microstructure.py.

Each card carries auto-pulled annotations (volume, trades, bot share for THIS
market vs Polymarket-wide, flagged-wallet count, average entry price by group)
plus an editorial news_angle that is preserved across regenerations (matched
on market_id) so the user only re-edits text for newly-appearing markets.
"""
from __future__ import annotations

import json

from common import utc_now, write_json
from config import DATA_OUT


CATEGORY_FALLBACK = "Other"


def _market_url(market_id: str, question: str | None) -> str:
    if not question:
        return f"https://polymarket.com/market/{market_id}"
    slug = (question.lower()
            .replace("?", "").replace(",", "").replace("'", "")
            .replace(":", "").replace(".", "")
            .replace("$", "").replace("%", "")
            .replace("/", "-").strip())
    slug = "-".join(slug.split())
    return f"https://polymarket.com/event/{slug}"


def _money(v: float) -> str:
    a = abs(v)
    if a >= 1e9: return f"${a/1e9:.2f}B"
    if a >= 1e6: return f"${a/1e6:.1f}M"
    if a >= 1e3: return f"${a/1e3:.0f}K"
    return f"${a:.0f}"


def _annotations(market: dict, micro: dict | None, baseline_bot_share: float, n_flagged_universe: int) -> list[dict]:
    vol = market["usd_volume"]
    n = market["n_trades"]
    a = [
        {"label": "Weekly volume", "value": _money(vol), "tone": "flat"},
        {"label": "Trades", "value": f"{n:,}", "tone": "flat"},
    ]

    if micro is None:
        # Fall back to global Polymarket-wide context if the microstructure
        # aggregator hasn't run yet.
        a.append({
            "label": "Algorithmic share (Polymarket-wide)",
            "value": f"{baseline_bot_share*100:.0f}%",
            "tone": "warn",
        })
        return a

    # Per-market bot share with delta vs Polymarket-wide baseline.
    bs = micro.get("bot_share_participation")
    if bs is not None:
        delta_pp = (bs - baseline_bot_share) * 100
        if abs(delta_pp) >= 3:
            cmp = f"{bs*100:.0f}% (vs {baseline_bot_share*100:.0f}% Polymarket-wide)"
        else:
            cmp = f"{bs*100:.0f}% (in line with the {baseline_bot_share*100:.0f}% Polymarket-wide baseline)"
        if bs >= 0.95: tone = "alert"
        elif bs >= 0.90: tone = "warn"
        elif bs >= 0.75: tone = "flat"
        else: tone = "ok"  # human-heavy markets get a positive tone
        a.append({"label": "Algorithmic share (this market)", "value": cmp, "tone": tone})

    # Flagged wallet count
    fw = micro.get("flagged_wallets_active", 0) or 0
    fw_tone = "alert" if fw >= 5 else ("warn" if fw >= 1 else "ok")
    a.append({
        "label": "Flagged wallets active",
        "value": f"{fw} of {n_flagged_universe:,}",
        "tone": fw_tone,
    })

    # Execution gap: volume-weighted across tokens where both bots and active
    # retail had >=$1K of volume. Positive number means retail paid more per
    # share than bots for the same exposure (the dashboard analog of Paper 1's
    # execution edge).
    gap = micro.get("execution_gap_retail_minus_bot")
    n_tok = micro.get("n_tokens_compared", 0) or 0
    if gap is not None and n_tok >= 1:
        gap_cents = gap * 100  # $0.0077 -> 0.77¢
        sign = "+" if gap_cents >= 0 else ""
        if gap_cents >= 0.5:
            tone = "alert"
        elif gap_cents >= 0.2:
            tone = "warn"
        else:
            tone = "flat"
        a.append({
            "label": "Execution gap (retail vs bots)",
            "value": f"{sign}{gap_cents:.2f}¢ per share",
            "tone": tone,
        })

    return a


def main() -> None:
    top = json.loads((DATA_OUT / "top_markets_latest.json").read_text(encoding="utf-8"))
    activity = json.loads((DATA_OUT / "weekly_activity_latest.json").read_text(encoding="utf-8"))
    calib = json.loads((DATA_OUT / "calibration_latest.json").read_text(encoding="utf-8"))
    pii = json.loads((DATA_OUT / "pii_latest.json").read_text(encoding="utf-8"))

    micro_path = DATA_OUT / "market_microstructure_latest.json"
    if micro_path.exists():
        micro_payload = json.loads(micro_path.read_text(encoding="utf-8"))
        micro_by_id = {m["market_id"]: m for m in micro_payload.get("markets", [])}
        n_flagged_universe = micro_payload.get("n_flagged_wallets_universe", pii["headline"]["total_flagged_p_lt_01"])
    else:
        print("WARN: market_microstructure_latest.json not found; cards will use Polymarket-wide annotations only.")
        micro_by_id = {}
        n_flagged_universe = pii["headline"]["total_flagged_p_lt_01"]

    # Preserve hand-edited news_angle / research_link / research_label
    existing_path = DATA_OUT / "briefings_latest.json"
    overrides: dict[str, dict] = {}
    if existing_path.exists():
        try:
            old = json.loads(existing_path.read_text(encoding="utf-8"))
            for b in old.get("briefings", []):
                overrides[b["market_id"]] = {
                    "news_angle": b.get("news_angle", ""),
                    "research_link": b.get("research_link", "/research"),
                    "research_label": b.get("research_label", "Research"),
                }
        except Exception:
            pass

    baseline_bot = activity["by_type_share"]["bot"]
    briefings = []
    for m in top["markets"][:6]:
        mid = str(m["market_id"])
        cat = m.get("category") or CATEGORY_FALLBACK
        ovr = overrides.get(mid, {})
        briefings.append({
            "rank": m["rank"],
            "category": cat,
            "question": m.get("question") or f"(market #{mid})",
            "market_id": mid,
            "market_url": _market_url(mid, m.get("question")),
            "volume_usd": m["usd_volume"],
            "n_trades": m["n_trades"],
            "annotations": _annotations(m, micro_by_id.get(mid), baseline_bot, n_flagged_universe),
            "news_angle": ovr.get("news_angle", "(EDITORIAL: 1-2 sentences on the news context here)"),
            "research_link": ovr.get("research_link", "/research"),
            "research_label": ovr.get("research_label", "Research"),
        })

    payload = {
        "as_of_week": top["as_of_week"],
        "generated_at": utc_now(),
        "lede": (
            f"Top {len(briefings)} markets by USD volume from the week of {top['as_of_week']}, "
            "annotated with per-market microstructure from the DV-PMI weekly indices."
        ),
        "briefings": briefings,
        "global_context": {
            "polymarket_wide_bot_share": baseline_bot,
            "polymarket_wide_volume_usd": activity["total_usd_volume"],
            "polymarket_wide_trades": activity["total_trades"],
            "calibration_alpha": calib["prelec_alpha"],
            "calibration_r2": calib["prelec_r2"],
        },
        "notes": (
            "Per-market microstructure annotations come from aggregate_market_microstructure.py "
            "(per-market bot share, flagged-wallet activity, average entry price by wallet type). "
            "News-angle text is editorial. Briefings are a dashboard product, not investment advice."
        ),
    }
    write_json(existing_path, payload)
    print(f"Briefings: {len(briefings)} cards for {top['as_of_week']}")
    if any("(EDITORIAL:" in b["news_angle"] for b in briefings):
        print("  WARNING: some news_angle slots are still placeholders. Hand-edit before publishing.")


if __name__ == "__main__":
    main()
