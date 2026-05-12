"""Generate weekly LinkedIn and Twitter/X draft posts.

Reads the same data as build_email_digest.py and writes:
  site/public/data/social_posts_latest.json

Each refresh overwrites the file unless hand_edited is true and as_of matches.
To override, edit the file and set hand_edited to true.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from common import utc_now, write_json
from config import DATA_OUT

SITE_URL = "https://jdellavedova.com"
PRESS_KIT_URL = f"{SITE_URL}/press"


def _read(name: str) -> dict:
    with open(DATA_OUT / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _usd_short(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else "+"
    if a >= 1e9:
        return f"{sign}${a/1e9:.1f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.0f}M"
    if a >= 1e3:
        return f"{sign}${a/1e3:.0f}K"
    return f"{sign}${a:.0f}"


def _usd_plain(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${a/1e9:.1f} billion"
    if a >= 1e6:
        return f"{sign}${a/1e6:.0f} million"
    if a >= 1e3:
        return f"{sign}${a/1e3:.0f}K"
    return f"{sign}${a:.0f}"


def _compact_trades(n: int) -> str:
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    if n >= 1e3:
        return f"{n/1e3:.0f}K"
    return str(int(n))


def main() -> None:
    narrative = _read("weekly_narrative.json")
    activity = _read("weekly_activity_latest.json")
    pwi = _read("pwi_latest.json")
    top = _read("top_markets_latest.json")
    bot = _read("bot_share_latest.json")

    try:
        pnl_hist = _read("cumulative_pnl_history.json")
        cum_bot = pnl_hist["endpoints"]["bot"]
        cum_retail = pnl_hist["endpoints"]["active_retail"]
        has_cumulative = True
    except (FileNotFoundError, KeyError):
        has_cumulative = False

    try:
        profit = _read("profit_split_latest.json")
    except FileNotFoundError:
        profit = None

    as_of = narrative.get("as_of", activity.get("as_of", "this week"))
    vol = activity.get("total_usd_volume", 0)
    trades = activity.get("total_trades", 0)
    bot_share = activity["by_type_share"]["bot"]
    alpha = pwi["value"]
    top5 = top.get("markets", [])
    top_q = top5[0].get("question") if top5 else None
    top_vol = top5[0].get("usd_volume", 0) if top5 else 0

    # Weekly P&L line (from profit_split if available)
    weekly_pnl_line = ""
    if profit:
        pb = profit.get("by_type", {})
        bot_row = pb.get("bot")
        retail_row = pb.get("active_retail")
        if bot_row and retail_row:
            bot_pnl = bot_row.get("pnl", 0)
            retail_pnl = retail_row.get("pnl", 0)
            weekly_pnl_line = (
                f"On resolved trades: bots {_usd_short(bot_pnl)}, "
                f"active retail {_usd_short(retail_pnl)}."
            )

    # Cumulative line
    cum_line = ""
    if has_cumulative:
        cum_line = (
            f"Cumulative since Nov 2022: bots {_usd_plain(cum_bot)}, "
            f"active retail {_usd_plain(cum_retail)}."
        )

    # ---- LinkedIn post ----
    # ~200 words. Hook number, 4 stats, top market, cumulative, link.
    li_lines = [
        f"DV-PMI Weekly Update | Week of {as_of}",
        "",
        f"Polymarket traders moved {_usd_plain(vol)} this week across "
        f"{_compact_trades(trades)} individual trades.",
        "",
        f"Key numbers:",
        f"  Bot share of counterparty events: {bot_share*100:.0f}%",
        f"  Non-bot probability-weighting index (Prelec alpha): {alpha:.3f} "
        f"(Kahneman-Tversky benchmark: 0.65)",
    ]

    if top_q:
        li_lines.append(
            f"  Highest-volume market: \"{top_q}\" ({_usd_plain(top_vol)})"
        )

    if weekly_pnl_line:
        li_lines.append(f"  {weekly_pnl_line}")

    if cum_line:
        li_lines += ["", cum_line]

    li_lines += [
        "",
        "These indices are updated every Monday from on-chain Polymarket data "
        "(312 GB, 274 million resolved trades). Full methodology and downloads at "
        f"{SITE_URL}",
        "",
        "#PredictionMarkets #Finance #Polymarket #BehavioralFinance",
    ]
    linkedin = "\n".join(li_lines)

    # ---- Twitter/X post ----
    # 280 chars max for the text, then one chart image suggestion and a link.
    # Build the core stat line first, then trim if needed.
    try:
        iso_week = date.fromisoformat(as_of).isocalendar().week
        week_tag = f"W{iso_week:02d}"
    except ValueError:
        week_tag = as_of  # fall back to whatever as_of is if it isn't ISO date

    if has_cumulative:
        tw_core = (
            f"Polymarket {week_tag} update: "
            f"{_usd_plain(vol)} traded, bots {bot_share*100:.0f}% of counterparty events. "
            f"Cumulative since 2022: bots {_usd_short(cum_bot)}, "
            f"retail {_usd_short(cum_retail)}."
        )
    else:
        tw_core = (
            f"Polymarket {week_tag} update: "
            f"{_usd_plain(vol)} traded, bots {bot_share*100:.0f}% of counterparty events. "
            f"Prelec alpha {alpha:.2f} (prob. weighting unchanged from K-T 1992)."
        )

    twitter = f"{tw_core}\n\n{SITE_URL}"
    suggested_image = "press/cumulative_pnl.png"

    # Char count warning
    char_count = len(twitter)
    twitter_note = (
        f"({char_count} chars incl. URL. Attach {suggested_image} from the press kit.)"
    )

    out_path = DATA_OUT / "social_posts_latest.json"

    # Preserve hand-edited version if as_of matches
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            if existing.get("hand_edited") and existing.get("as_of") == as_of:
                print(f"Social posts: preserving hand-edited version for {as_of}")
                return
        except Exception:
            pass

    write_json(out_path, {
        "as_of": as_of,
        "generated_at": utc_now(),
        "hand_edited": False,
        "linkedin": linkedin,
        "twitter": twitter,
        "twitter_note": twitter_note,
        "suggested_image": suggested_image,
        "notes": (
            "Set hand_edited to true and edit linkedin/twitter to preserve your edits "
            "across future pipeline runs (as long as as_of matches)."
        ),
    })
    print(f"Social posts: generated for {as_of}")
    print(f"  LinkedIn: {len(linkedin)} chars")
    print(f"  Twitter:  {char_count} chars  {twitter_note}")
