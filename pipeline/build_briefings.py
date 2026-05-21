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


def _annotations(market: dict, micro: dict | None, snap: dict | None, baseline_bot_share: float, n_flagged_universe: int) -> list[dict]:
    vol = market["usd_volume"]
    n = market["n_trades"]
    a: list[dict] = []

    # Lead with live odds when we have them. The implied probability is the
    # most-asked-for number on a prediction-market briefing.
    if snap and snap.get("yes_price") is not None:
        yp = snap["yes_price"]
        # Markets that have effectively resolved (price=1.0 or 0.0, no live book)
        # get a clean "resolved" label instead of a misleading "100%" annotation.
        yb = snap.get("yes_book") or {}
        if (yp >= 0.999 or yp <= 0.001) and not yb.get("top_bid_price"):
            a.append({"label": "Status", "value": "Resolved (book closed)", "tone": "ok"})
        else:
            chg = snap.get("yes_price_change_pp")
            if chg is None:
                val = f"{yp*100:.0f}% YES"
            else:
                arrow = "▲" if chg > 0.5 else ("▼" if chg < -0.5 else "•")
                sign = "+" if chg >= 0 else ""
                val = f"{yp*100:.0f}% YES  {arrow} {sign}{chg:.0f}pp 7d"
            a.append({"label": "Implied probability", "value": val, "tone": "flat"})

    a.append({"label": "Weekly volume", "value": _money(vol), "tone": "flat"})
    a.append({"label": "Trades", "value": f"{n:,}", "tone": "flat"})

    # 24h volume (Gamma) gives a more recent activity pulse than the weekly bucket.
    if snap and snap.get("volume_24hr") is not None:
        a.append({"label": "24h volume (Gamma)", "value": _money(snap["volume_24hr"]), "tone": "flat"})

    # Time to resolution: days until end_date_iso. Sub-7-day markets get a warn
    # tone (close to settling, prices may be locked in by news rather than markets).
    if snap and snap.get("end_date_iso"):
        try:
            end_iso = snap["end_date_iso"][:10]  # YYYY-MM-DD
            from datetime import date as _date
            end = _date.fromisoformat(end_iso)
            days_left = (end - _date.today()).days
            if days_left >= 0:
                if days_left <= 7:
                    val = f"{days_left} day{'s' if days_left != 1 else ''} ({end_iso})"
                    tone = "warn"
                elif days_left <= 30:
                    val = f"{days_left} days ({end_iso})"
                    tone = "flat"
                else:
                    val = f"{days_left} days ({end_iso})"
                    tone = "flat"
                a.append({"label": "Time to resolution", "value": val, "tone": tone})
        except Exception:
            pass

    # Top-of-book + spread + depth (only if we have a live YES book)
    if snap:
        yb = snap.get("yes_book") or {}
        if yb.get("top_bid_price") is not None and yb.get("top_ask_price") is not None:
            a.append({
                "label": "Top of book (YES)",
                "value": f"Bid {yb['top_bid_price']:.2f} (${yb['top_bid_dollars']:,.0f}) / Ask {yb['top_ask_price']:.2f} (${yb['top_ask_dollars']:,.0f})",
                "tone": "flat",
            })
            sp_c = yb.get("spread_cents")
            sp_bp = yb.get("spread_bps")
            if sp_c is not None and sp_bp is not None and sp_c > 0 and sp_bp > 0:
                # Wide spread (>5%) = thin book, journalist warning
                tone = "alert" if sp_bp > 500 else ("warn" if sp_bp > 200 else "flat")
                a.append({
                    "label": "Spread",
                    "value": f"{sp_c:.1f}¢ ({sp_bp:.0f} bps)",
                    "tone": tone,
                })
            db = yb.get("depth_5pct_bid_dollars")
            da = yb.get("depth_5pct_ask_dollars")
            if db is not None and da is not None:
                a.append({
                    "label": "Depth within ±5% of mid",
                    "value": f"${db:,.0f} bid  /  ${da:,.0f} ask",
                    "tone": "flat",
                })

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

    # Live odds + depth (Gamma + CLOB APIs)
    snap_path = DATA_OUT / "market_snapshot_latest.json"
    snap_by_id: dict[str, dict] = {}
    if snap_path.exists():
        snap_payload = json.loads(snap_path.read_text(encoding="utf-8"))
        snap_by_id = {m["market_id"]: m for m in snap_payload.get("markets", [])}
    else:
        print("WARN: market_snapshot_latest.json not found; cards will lack live odds + depth.")

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

    def _is_resolved(market_id: str) -> bool:
        snap = snap_by_id.get(str(market_id))
        if not snap:
            return False
        yp = snap.get("yes_price")
        if yp is None:
            return False
        if yp >= 0.999 or yp <= 0.001:
            yb = snap.get("yes_book") or {}
            if not yb.get("top_bid_price"):
                return True
        return False

    # Prefer live (unresolved) markets; fall back to resolved ones only if needed.
    candidates = top["markets"]
    live = [m for m in candidates if not _is_resolved(m["market_id"])]
    resolved_fill = [m for m in candidates if _is_resolved(m["market_id"])]
    ordered = (live + resolved_fill)[:6]

    briefings = []
    for m in ordered:
        mid = str(m["market_id"])
        cat = m.get("category") or CATEGORY_FALLBACK
        ovr = overrides.get(mid, {})
        snap = snap_by_id.get(mid)
        # Pull a compact slice of the YES book for the inline depth ladder.
        depth_ladder = None
        if snap:
            yb = snap.get("yes_book") or {}
            if yb.get("top5_bids") and yb.get("top5_asks"):
                depth_ladder = {
                    "bids": yb["top5_bids"],
                    "asks": yb["top5_asks"],
                }

        briefings.append({
            "rank": m["rank"],
            "category": cat,
            "question": m.get("question") or f"(market #{mid})",
            "market_id": mid,
            "market_url": _market_url(mid, m.get("question")),
            "volume_usd": m["usd_volume"],
            "n_trades": m["n_trades"],
            "annotations": _annotations(m, micro_by_id.get(mid), snap, baseline_bot, n_flagged_universe),
            "depth_ladder": depth_ladder,
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
