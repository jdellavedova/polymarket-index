"""build_briefings.py — generate the Briefings page payload from existing indices.

Pulls the top markets list and the global weekly indices, computes a per-market
microstructure annotation set, and writes briefings_latest.json. The news-angle
text is editorial and must be hand-written each week; this script preserves any
existing news_angle when regenerating (matched by market_id).

Run weekly after aggregate_top_markets.py and aggregate_pii.py have produced
fresh _latest.json files.
"""
from __future__ import annotations

import json
from pathlib import Path

from common import utc_now, write_json
from config import DATA_OUT


CATEGORY_FALLBACK = "Other"

# Polymarket event-page URL pattern. Slug fallback: market_id.
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


def _calibration_tag(category: str) -> str:
    if category in ("Crypto",):
        return "longshot zone (overpriced)"
    if category in ("Macro", "Politics"):
        return "well-calibrated near 0.50"
    if category in ("Geopolitics",):
        return "longshot zone (overpriced)"
    return "mid-range"


def _flag_rate_note(category: str, pii: dict) -> tuple[str, str] | None:
    """Map question category -> MNPI category -> flag rate note. Returns
    (annotation_value, tone) or None. Heuristic mapping; refine as we learn
    which categories carry which informational profiles."""
    rows = {r["mnpi_type"]: r for r in pii.get("mnpi_taxonomy", [])}
    if category in ("Politics", "Macro"):
        r = rows.get("vote") or rows.get("action")
        if r:
            return (
                f"{r['mnpi_type'].capitalize()} category ({r['flag_rate_01']*100:.2f}% flag rate)",
                "alert" if r["flag_rate_01"] > 0.015 else "warn",
            )
    if category in ("Geopolitics",):
        r = rows.get("action")
        if r:
            return (
                f"Action category ({r['flag_rate_01']*100:.2f}% flag rate)",
                "alert" if r["flag_rate_01"] > 0.015 else "warn",
            )
    if category in ("Crypto",):
        r = rows.get("stochastic")
        if r:
            return (
                f"Stochastic category ({r['flag_rate_01']*100:.2f}% flag rate)",
                "ok",
            )
    return None


def _annotations(market: dict, bot_share: float, pii: dict) -> list[dict]:
    vol = market["usd_volume"]
    n = market["n_trades"]
    cat = market.get("category") or CATEGORY_FALLBACK
    vpt = vol / n if n else 0.0
    a = [
        {"label": "Weekly volume", "value": f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K", "tone": "flat"},
        {"label": "Trades", "value": f"{n:,}", "tone": "flat"},
    ]
    # Volume per trade is informative: high $/trade signals larger sophisticated
    # bets (whales, funds); low $/trade signals retail engagement.
    if vpt >= 1000:
        a.append({"label": "Volume per trade", "value": f"${vpt:,.0f}", "tone": "warn"})
    else:
        a.append({"label": "Volume per trade", "value": f"${vpt:,.0f}", "tone": "ok"})

    fr = _flag_rate_note(cat, pii)
    if fr:
        a.append({"label": "Insider-trading hotspot", "value": fr[0], "tone": fr[1]})
    else:
        a.append({"label": "Calibration regime", "value": _calibration_tag(cat), "tone": "warn" if "longshot" in _calibration_tag(cat) else "ok"})
    return a


def main() -> None:
    top = json.loads((DATA_OUT / "top_markets_latest.json").read_text(encoding="utf-8"))
    activity = json.loads((DATA_OUT / "weekly_activity_latest.json").read_text(encoding="utf-8"))
    calib = json.loads((DATA_OUT / "calibration_latest.json").read_text(encoding="utf-8"))
    pii = json.loads((DATA_OUT / "pii_latest.json").read_text(encoding="utf-8"))

    # Preserve hand-edited news_angle / research_link / research_label across
    # regenerations. Keyed on market_id.
    existing_path = DATA_OUT / "briefings_latest.json"
    existing_overrides: dict[str, dict] = {}
    if existing_path.exists():
        try:
            old = json.loads(existing_path.read_text(encoding="utf-8"))
            for b in old.get("briefings", []):
                existing_overrides[b["market_id"]] = {
                    "news_angle": b.get("news_angle", ""),
                    "research_link": b.get("research_link", "/research"),
                    "research_label": b.get("research_label", "Research"),
                }
        except Exception:
            pass

    bot_share = activity["by_type_share"]["bot"]
    briefings = []
    for m in top["markets"][:6]:
        mid = str(m["market_id"])
        cat = m.get("category") or CATEGORY_FALLBACK
        ovr = existing_overrides.get(mid, {})
        briefings.append({
            "rank": m["rank"],
            "category": cat,
            "question": m.get("question") or f"(market #{mid})",
            "market_id": mid,
            "market_url": _market_url(mid, m.get("question")),
            "volume_usd": m["usd_volume"],
            "n_trades": m["n_trades"],
            "annotations": _annotations(m, bot_share, pii),
            "news_angle": ovr.get("news_angle", "(EDITORIAL: 1-2 sentences on the news context here)"),
            "research_link": ovr.get("research_link", "/research"),
            "research_label": ovr.get("research_label", "Research"),
        })

    payload = {
        "as_of_week": top["as_of_week"],
        "generated_at": utc_now(),
        "lede": (
            f"Top {len(briefings)} markets by USD volume from the week of {top['as_of_week']}, "
            "with microstructure context from the DV-PMI weekly indices."
        ),
        "briefings": briefings,
        "global_context": {
            "polymarket_wide_bot_share": bot_share,
            "polymarket_wide_volume_usd": activity["total_usd_volume"],
            "polymarket_wide_trades": activity["total_trades"],
            "calibration_alpha": calib["prelec_alpha"],
            "calibration_r2": calib["prelec_r2"],
        },
        "notes": (
            "Per-market microstructure annotations draw on the DV-PMI weekly indices. "
            "News-angle text is editorial. Briefings are a dashboard product, not investment advice."
        ),
    }
    write_json(existing_path, payload)
    print(f"Briefings: {len(briefings)} cards for {top['as_of_week']}")
    if any("(EDITORIAL:" in b["news_angle"] for b in briefings):
        print("  WARNING: some news_angle slots are still placeholders. Hand-edit before publishing.")


if __name__ == "__main__":
    main()
