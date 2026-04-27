"""Generate a 1200x630 social-share image (PNG) for the weekly refresh.

Reads weekly_narrative.json + weekly_activity_latest.json + profit_split + top_markets
and composes a branded OG image that Twitter/LinkedIn/Bluesky/Slack show as a
preview card when anyone pastes a dv-pmi URL.

Output: site/public/og.png  (referenced by og:image in Base.astro)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from config import DATA_OUT

SITE_DIR = Path(__file__).resolve().parent.parent / "site"
PUBLIC = SITE_DIR / "public"

W, H = 1200, 630
BG = (10, 20, 32)          # --bg  #0a1420
FG = (196, 210, 228)       # --chart-text #c4d2e4
ACCENT = (117, 190, 233)   # --chart-primary #75bee9
MUTED = (90, 104, 122)
POSITIVE = (127, 216, 152)
NEGATIVE = (236, 118, 118)


def _read(name: str) -> dict:
    with open(DATA_OUT / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Windows fallback chain: try a serif face for the big number (academic feel),
    # then fall back to system default.
    candidates = []
    if bold:
        candidates = [
            r"C:\Windows\Fonts\georgiab.ttf",
            r"C:\Windows\Fonts\seguisb.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
        ]
    else:
        candidates = [
            r"C:\Windows\Fonts\georgia.ttf",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\arial.ttf",
        ]
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _usd_compact(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e9:
        return f"{sign}${a/1e9:.2f}B"
    if a >= 1e6:
        return f"{sign}${a/1e6:.0f}M"
    if a >= 1e3:
        return f"{sign}${a/1e3:.0f}K"
    return f"{sign}${a:.0f}"


def _compact_trades(n: int) -> str:
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    if n >= 1e3:
        return f"{n/1e3:.0f}K"
    return str(int(n))


def _textsize(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if _textsize(draw, test, font)[0] <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def main() -> None:
    narrative = _read("weekly_narrative.json")
    activity = _read("weekly_activity_latest.json")
    try:
        profit = _read("profit_split_latest.json")
    except FileNotFoundError:
        profit = None

    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Top strip: small-caps attribution
    draw.rectangle((0, 0, W, 8), fill=ACCENT)
    attrib_font = _font(20, bold=True)
    attrib = "DELLA VEDOVA PREDICTION MARKET INDICES"
    draw.text((48, 34), attrib, font=attrib_font, fill=ACCENT)

    as_of = narrative.get("as_of", activity.get("as_of", "recent"))
    week_font = _font(22)
    draw.text((48, 64), f"Week of {as_of}", font=week_font, fill=FG)

    # Big dollar headline
    total_vol = activity.get("total_usd_volume", 0.0)
    big_font = _font(110, bold=True)
    big_text = _usd_compact(total_vol)
    draw.text((48, 110), big_text, font=big_font, fill=FG)

    # Sub-caption under the big number
    sub_font = _font(26)
    total_trades = activity.get("total_trades", 0)
    new_wallets = activity.get("new_wallets", 0)
    bot_share = activity.get("by_type_share", {}).get("bot", 0)
    caption = (
        f"in trading across {_compact_trades(total_trades)} trades this week"
    )
    draw.text((48, 240), caption, font=sub_font, fill=MUTED)

    # Three-box KPI strip
    box_top = 310
    box_h = 130
    gap = 18
    box_w = (W - 48 * 2 - gap * 2) // 3
    boxes = [
        {
            "label": "BOT SHARE OF TRADES",
            "value": f"{bot_share * 100:.0f}%",
            "sub": "the rest are human traders",
        },
        {
            "label": "NEW PARTICIPANTS",
            "value": f"{new_wallets:,}",
            "sub": "first-ever trade this week",
        },
    ]
    # Third box: who won this week (from profit_split)
    if profit:
        bot_row = profit.get("by_type", {}).get("bot", {})
        retail_row = profit.get("by_type", {}).get("active_retail", {})
        bot_pnl = bot_row.get("pnl", 0)
        retail_pnl = retail_row.get("pnl", 0)
        if bot_pnl > 0 and retail_pnl < 0:
            third = {
                "label": "WHO WON THIS WEEK",
                "value": "BOTS",
                "sub": f"{_usd_compact(bot_pnl)} to algos / {_usd_compact(abs(retail_pnl))} from retail",
                "color": ACCENT,
            }
        elif bot_pnl < 0 and retail_pnl > 0:
            third = {
                "label": "WHO WON THIS WEEK",
                "value": "RETAIL",
                "sub": f"{_usd_compact(retail_pnl)} to human traders; rare week",
                "color": POSITIVE,
            }
        elif bot_pnl < 0 and retail_pnl < 0:
            third = {
                "label": "WHO WON THIS WEEK",
                "value": "NEITHER",
                "sub": "both sides lost on resolved trades",
                "color": NEGATIVE,
            }
        else:
            third = {
                "label": "WHO WON THIS WEEK",
                "value": "BOTH",
                "sub": "bots and retail both profitable",
                "color": POSITIVE,
            }
    else:
        third = {
            "label": "BOT EDGE",
            "value": "—",
            "sub": "profit-split pending",
        }
    boxes.append(third)

    for i, b in enumerate(boxes):
        x = 48 + i * (box_w + gap)
        # subtle box outline
        draw.rectangle((x, box_top, x + box_w, box_top + box_h), outline=(31, 49, 82), width=2)
        label_font = _font(16, bold=True)
        draw.text((x + 18, box_top + 14), b["label"], font=label_font, fill=ACCENT)
        val_font = _font(52, bold=True)
        val_color = b.get("color", FG)
        draw.text((x + 18, box_top + 36), b["value"], font=val_font, fill=val_color)
        sub_small = _font(18)
        # wrap long sub-captions inside the box width
        lines = _wrap(draw, b["sub"], sub_small, box_w - 36)
        for li, line in enumerate(lines[:2]):
            draw.text((x + 18, box_top + 94 + li * 20), line, font=sub_small, fill=MUTED)

    # Headline quote (narrative hook) — bottom band
    quote_font = _font(24)
    quote = narrative.get("headline_quote") or ""
    quote_lines = _wrap(draw, quote, quote_font, W - 96)
    y = 475
    for line in quote_lines[:3]:
        draw.text((48, y), line, font=quote_font, fill=FG)
        y += 32

    # Footer attribution
    foot_font = _font(16)
    draw.text((48, H - 36), "jdellavedova.com", font=foot_font, fill=ACCENT)
    right_text = "Built from every on-chain Polymarket trade since 2022"
    rtw = _textsize(draw, right_text, foot_font)[0]
    draw.text((W - 48 - rtw, H - 36), right_text, font=foot_font, fill=MUTED)

    out = PUBLIC / "og.png"
    img.save(out, "PNG", optimize=True)
    print(f"OG image: wrote {out} ({os.path.getsize(out) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
