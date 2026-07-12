"""Generate the weekly email digest (HTML + plain text).

Writes two files into site/public/data/:
  weekly_email.html   — MJML-free HTML, ready to paste into Mailchimp / Buttondown /
                        Beehiiv / any ESP campaign editor
  weekly_email.txt    — plain-text equivalent for the .txt alternative part

Also prints a ready-to-send block with subject line and a suggested preview text.
This script is intentionally ESP-agnostic: the user will paste the HTML into
their chosen provider weekly (or the harness can later wire in Mailchimp's
`campaigns/send` API, Buttondown's POST /emails, etc.).

Structure (July 2026 redesign): a recurring digest lives on CHANGE, not levels.
Every headline number carries a week-over-week delta; the five indices appear
as a scoreboard ranked by how extended each is versus its own 52-week history;
the surveillance section leads with the number that moves weekly (flagged
wallets active this week) rather than the static to-date counts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from common import utc_now
from config import DATA_OUT

SITE_URL = "https://jdellavedova.com"


def _read(name: str) -> dict:
    with open(DATA_OUT / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _usd(v: float) -> str:
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
    return f"{int(n):,}"


def _pct_delta(cur: float, prev: float) -> str | None:
    """'+12.4%' style week-over-week change; None when not computable."""
    if prev is None or prev == 0 or cur is None:
        return None
    return f"{(cur - prev) / abs(prev) * 100:+.1f}%"


def _pp_delta(cur: float, prev: float) -> str | None:
    """Percentage-point change for share-type metrics."""
    if prev is None or cur is None:
        return None
    return f"{(cur - prev) * 100:+.1f}pp"


def _wow_from_history() -> dict:
    """Prior-week levels from weekly_activity_history.csv (last two rows)."""
    try:
        h = pd.read_csv(DATA_OUT / "weekly_activity_history.csv")
        if len(h) < 2:
            return {}
        prev = h.iloc[-2]
        return {
            "total_usd_volume": float(prev["total_usd_volume"]),
            "total_trades": float(prev["total_trades"]),
            "active_wallets": float(prev["active_wallets"]),
            "share_bot": float(prev["share_bot"]),
            "flagged_active": float(prev.get("flagged_active", float("nan"))),
        }
    except Exception:
        return {}


# The five weekly indices that carry ma4w/z52w context. Display value formats
# differ per index, so each entry carries its own formatter.
INDEX_FILES = [
    ("pwi_latest.json", "Probability Weighting (non-bot alpha)", "{:.3f}"),
    ("execution_latest.json", "Execution Gap (bot vs retail)", "{:+.3f}"),
    ("bot_share_latest.json", "Bot Share of Volume", "{:.1%}"),
    ("price_gap_latest.json", "Longshot/Favorite Gap", "{:+.3f}"),
    ("efficiency_latest.json", "Market Efficiency (Prelec R2)", "{:.3f}"),
]


def _index_scoreboard() -> list[dict]:
    """One row per index: value, 4-week average, z-score vs 52 weeks.
    Sorted by |z52w| descending, so the most extended index leads."""
    rows = []
    for fname, label, fmt in INDEX_FILES:
        try:
            j = _read(fname)
            val = j.get("value")
            z = j.get("z52w")
            ma4 = j.get("ma4w")
            if val is None:
                continue
            rows.append({
                "label": label,
                "value_str": fmt.format(val),
                "ma4_str": fmt.format(ma4) if ma4 is not None else "n/a",
                "z": z if z is not None else 0.0,
                "z_str": f"{z:+.2f}" if z is not None else "n/a",
            })
        except Exception:
            continue
    rows.sort(key=lambda r: abs(r["z"]), reverse=True)
    return rows


def main() -> None:
    narrative = _read("weekly_narrative.json")
    activity = _read("weekly_activity_latest.json")
    pii = _read("pii_latest.json")
    top = _read("top_markets_latest.json")
    try:
        profit = _read("profit_split_latest.json")
    except FileNotFoundError:
        profit = None

    as_of = narrative.get("as_of", activity.get("as_of", "recent"))
    prev = _wow_from_history()
    board = _index_scoreboard()

    vol = activity["total_usd_volume"]
    trades = activity["total_trades"]
    wallets = activity.get("active_wallets")
    bot_share = activity["by_type_share"]["bot"]
    flagged_now = activity.get("flagged_active_this_week")
    hl = pii["headline"]

    d_vol = _pct_delta(vol, prev.get("total_usd_volume"))
    d_trades = _pct_delta(trades, prev.get("total_trades"))
    d_wallets = _pct_delta(wallets, prev.get("active_wallets")) if wallets else None
    d_bot = _pp_delta(bot_share, prev.get("share_bot"))

    def with_delta(level: str, delta: str | None) -> str:
        return f"{level}  ({delta} WoW)" if delta else level

    # ---- Plain-text digest ----
    lines = []
    lines.append(f"DV-PMI WEEKLY DIGEST / Week of {as_of}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(narrative.get("headline_quote", "").strip('"'))
    lines.append("")
    lines.append("-" * 60)
    lines.append("THIS WEEK VS LAST")
    lines.append("-" * 60)
    lines.append(f"  Total USD volume        {with_delta(_usd(vol), d_vol)}")
    lines.append(f"  Total trades            {with_delta(_compact_trades(trades), d_trades)}")
    if wallets:
        lines.append(f"  Active wallets          {with_delta(f'{wallets:,}', d_wallets)}")
    lines.append(f"  Bot share of trades     {with_delta(f'{bot_share*100:.1f}%', d_bot)}")
    lines.append("")
    if board:
        lines.append("-" * 60)
        lines.append("INDEX SCOREBOARD (ranked by stretch vs own 52-week history)")
        lines.append("-" * 60)
        for r in board:
            lines.append(f"  {r['label']:<38s} {r['value_str']:>8s}   4w avg {r['ma4_str']:>8s}   z {r['z_str']}")
        lines.append("")
    lines.append("-" * 60)
    lines.append("THE STORY")
    lines.append("-" * 60)
    for s in narrative.get("sentences", []):
        lines.append(s)
        lines.append("")
    lines.append("-" * 60)
    lines.append("MOST-TRADED MARKETS THIS WEEK")
    lines.append("-" * 60)
    for m in (top.get("markets") or [])[:5]:
        q = m.get("question") or f"(new market #{m['market_id']})"
        cat = f" [{m['category']}]" if m.get("category") else ""
        lines.append(f"  {m['rank']}. {_usd(m['usd_volume'])}  {q}{cat}")
    lines.append("")
    lines.append("-" * 60)
    lines.append("WHO MADE MONEY (resolved trades only)")
    lines.append("-" * 60)
    if profit:
        for wt, r in profit.get("by_type", {}).items():
            pnl = r.get("pnl", 0)
            bps = r.get("pnl_roi_bps", 0)
            label = {
                "bot": "Bot",
                "active_retail": "Active Retail",
                "sophisticated": "Sophisticated",
                "casual": "Casual",
                "one_shot": "One-Shot",
            }.get(wt, wt)
            lines.append(f"  {label:<16s} {_usd(pnl):>10s}   ({bps:+.0f} bps on volume)")
    else:
        lines.append("  profit-split not available this week")
    lines.append("")
    lines.append("-" * 60)
    lines.append("SURVEILLANCE")
    lines.append("-" * 60)
    ed_surv = pii.get("event_detection", {}).get("survivors", {})
    if ed_surv:
        lines.append(f"  Event-level informed-trading test:    {ed_surv['at_5pct_dependence_adjusted']} trader-event pairs survive at 5% (0 on placebo)")
    if flagged_now is not None:
        lines.append(f"  Accuracy-outlier wallets active this week: {flagged_now:,} of {hl['total_flagged_p_lt_01']:,}")
    lines.append(f"  Sustained-accuracy outliers (skill screen): {hl['total_flagged_p_lt_01']:,} of {hl['total_wallets_tested']:,} tested ({hl['flag_rate']*100:.2f}%)")
    lines.append(f"  After strict statistical correction:  {hl['holm_bonferroni_survivors']:,}")
    lines.append("  Flags mark statistical patterns, not proof; the wallet-level screen measures sustained skill, not information.")
    lines.append("")
    lines.append("=" * 60)
    lines.append(f"Full dashboard: {SITE_URL}")
    lines.append(f"Downloads + DOI: {SITE_URL}/data")
    lines.append(f"Methodology:     {SITE_URL}/methodology")
    lines.append("")
    lines.append("To unsubscribe, reply with UNSUBSCRIBE.")
    lines.append(f"-- Joshua Della Vedova, University of San Diego ({utc_now()})")
    plain = "\n".join(lines)

    # ---- HTML digest ----
    def esc(s: str) -> str:
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def stat_row(label: str, level: str, delta: str | None) -> str:
        delta_html = (
            f" <span style='color:#5a687a;font-size:13px;'>({esc(delta)} WoW)</span>"
            if delta else ""
        )
        return f"""            <tr>
              <td style="padding:6px 0;font-family:Georgia,serif;color:#5a687a;width:52%;">{esc(label)}</td>
              <td style="padding:6px 0;font-family:'Courier New',monospace;color:#0a1420;text-align:right;"><strong>{esc(level)}</strong>{delta_html}</td>
            </tr>"""

    stat_rows = [stat_row("Total USD volume", _usd(vol), d_vol),
                 stat_row("Total trades", _compact_trades(trades), d_trades)]
    if wallets:
        stat_rows.append(stat_row("Active wallets", f"{wallets:,}", d_wallets))
    stat_rows.append(stat_row("Bot share of trades", f"{bot_share*100:.1f}%", d_bot))

    board_rows = []
    for i, r in enumerate(board):
        weight = "bold" if i == 0 else "normal"
        board_rows.append(f"""      <tr>
        <td style="padding:4px 12px 4px 0;font-family:Georgia,serif;color:#0a1420;font-weight:{weight};">{esc(r['label'])}</td>
        <td style="padding:4px 8px;font-family:'Courier New',monospace;color:#0a1420;text-align:right;white-space:nowrap;font-weight:{weight};">{esc(r['value_str'])}</td>
        <td style="padding:4px 8px;font-family:'Courier New',monospace;color:#5a687a;text-align:right;white-space:nowrap;">4w {esc(r['ma4_str'])}</td>
        <td style="padding:4px 0;font-family:'Courier New',monospace;color:#5a687a;text-align:right;white-space:nowrap;">z {esc(r['z_str'])}</td>
      </tr>""")

    top_rows = []
    for m in (top.get("markets") or [])[:5]:
        q = esc(m.get("question") or f"(new market #{m['market_id']})")
        cat = f" <span style='color:#8a8575;font-size:12px;'>[{esc(m['category'])}]</span>" if m.get("category") else ""
        top_rows.append(f"""      <tr>
        <td style="padding:6px 12px 6px 0;vertical-align:top;color:#5a687a;font-family:Georgia,serif;">{m['rank']}.</td>
        <td style="padding:6px 0;vertical-align:top;font-family:Georgia,serif;"><strong style="color:#0a1420;">{_usd(m['usd_volume'])}</strong> &middot; <span style="color:#0a1420;">{q}</span>{cat}</td>
      </tr>""")

    profit_rows = []
    if profit:
        for wt, r in profit.get("by_type", {}).items():
            pnl = r.get("pnl", 0)
            bps = r.get("pnl_roi_bps", 0)
            label = {
                "bot": "Bot",
                "active_retail": "Active Retail",
                "sophisticated": "Sophisticated",
                "casual": "Casual",
                "one_shot": "One-Shot",
            }.get(wt, wt)
            color = "#157a3a" if pnl > 0 else ("#9b1c1c" if pnl < 0 else "#0a1420")
            profit_rows.append(f"""      <tr>
        <td style="padding:4px 12px 4px 0;font-family:Georgia,serif;color:#0a1420;">{label}</td>
        <td style="padding:4px 12px;font-family:'Courier New',monospace;color:{color};text-align:right;white-space:nowrap;">{_usd(pnl)}</td>
        <td style="padding:4px 0;font-family:'Courier New',monospace;color:#5a687a;text-align:right;white-space:nowrap;">({bps:+.0f} bps)</td>
      </tr>""")

    sentences_html = "\n      ".join(
        f"<p style='margin:0 0 14px 0;font-family:Georgia,serif;color:#0a1420;line-height:1.55;'>{esc(s)}</p>"
        for s in narrative.get("sentences", [])
    )

    headline_q = narrative.get("headline_quote", "").strip('"')

    flagged_line = ""
    if flagged_now is not None:
        flagged_line = (
            f"<strong>{flagged_now:,}</strong> of the {hl['total_flagged_p_lt_01']:,} flagged wallets "
            f"traded this week. "
        )

    scoreboard_block = ""
    if board_rows:
        scoreboard_block = f"""        <tr><td style="padding:14px 28px 4px 28px;">
          <div style="font-family:'Segoe UI',Helvetica,sans-serif;font-size:11px;letter-spacing:0.09em;color:#0074c8;text-transform:uppercase;margin-bottom:8px;">Index scoreboard &middot; ranked by stretch vs 52-week history</div>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
{chr(10).join(board_rows)}
          </table>
        </td></tr>
"""

    html = f"""<!doctype html>
<html>
<body style="margin:0;padding:0;background:#f6f4ef;">
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#f6f4ef;">
    <tr><td align="center" style="padding:24px 12px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="background:#ffffff;max-width:600px;">
        <tr><td style="padding:24px 28px 4px 28px;border-top:4px solid #0074c8;">
          <div style="font-family:'Segoe UI',Helvetica,sans-serif;font-size:12px;letter-spacing:0.08em;color:#0074c8;text-transform:uppercase;">Della Vedova Prediction Market Indices</div>
          <div style="font-family:Georgia,serif;font-size:14px;color:#5a687a;margin-top:2px;">Week of {esc(as_of)}</div>
        </td></tr>

        <tr><td style="padding:18px 28px 6px 28px;">
          <p style="margin:0;font-family:Georgia,serif;font-size:22px;line-height:1.35;color:#0a1420;">&ldquo;{esc(headline_q)}&rdquo;</p>
        </td></tr>

        <tr><td style="padding:14px 28px 6px 28px;">
          <div style="font-family:'Segoe UI',Helvetica,sans-serif;font-size:11px;letter-spacing:0.09em;color:#0074c8;text-transform:uppercase;margin-bottom:8px;">This week vs last</div>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
{chr(10).join(stat_rows)}
          </table>
        </td></tr>

{scoreboard_block}
        <tr><td style="padding:18px 28px 4px 28px;">
          <div style="font-family:'Segoe UI',Helvetica,sans-serif;font-size:11px;letter-spacing:0.09em;color:#0074c8;text-transform:uppercase;margin-bottom:8px;">The story</div>
          {sentences_html}
        </td></tr>

        <tr><td style="padding:14px 28px 4px 28px;">
          <div style="font-family:'Segoe UI',Helvetica,sans-serif;font-size:11px;letter-spacing:0.09em;color:#0074c8;text-transform:uppercase;margin-bottom:8px;">Most-traded markets this week</div>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
{chr(10).join(top_rows)}
          </table>
        </td></tr>

        <tr><td style="padding:18px 28px 4px 28px;">
          <div style="font-family:'Segoe UI',Helvetica,sans-serif;font-size:11px;letter-spacing:0.09em;color:#0074c8;text-transform:uppercase;margin-bottom:8px;">Who made money (resolved trades)</div>
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
{chr(10).join(profit_rows) if profit_rows else '<tr><td style="color:#5a687a;font-family:Georgia,serif;">profit-split not available this week</td></tr>'}
          </table>
        </td></tr>

        <tr><td style="padding:18px 28px 4px 28px;">
          <div style="font-family:'Segoe UI',Helvetica,sans-serif;font-size:11px;letter-spacing:0.09em;color:#0074c8;text-transform:uppercase;margin-bottom:8px;">Surveillance</div>
          <p style="margin:0;font-family:Georgia,serif;color:#0a1420;line-height:1.5;">
            {f"<strong>{ed_surv['at_5pct_dependence_adjusted']}</strong> trader-event pairs survive the per-event informed-trading test at the 5% level (zero on the placebo class). " if ed_surv else ""}{flagged_line}<strong>{hl['total_flagged_p_lt_01']:,}</strong> of {hl['total_wallets_tested']:,} tested wallets show sustained excess accuracy ({hl['flag_rate']*100:.2f}%), a skill signature rather than evidence of informed trading; {hl['holm_bonferroni_survivors']:,} survive strict statistical correction.
          </p>
        </td></tr>

        <tr><td style="padding:24px 28px;text-align:center;">
          <a href="{SITE_URL}" style="display:inline-block;background:#0074c8;color:#ffffff;padding:12px 26px;text-decoration:none;font-family:'Segoe UI',Helvetica,sans-serif;font-size:14px;letter-spacing:0.03em;">Open the dashboard</a>
        </td></tr>

        <tr><td style="padding:14px 28px 24px 28px;border-top:1px solid #e8e4d9;">
          <p style="margin:0;font-family:Georgia,serif;font-size:12px;color:#5a687a;line-height:1.5;">
            Joshua Della Vedova &middot; Knauss School of Business, University of San Diego<br>
            <a href="{SITE_URL}/methodology" style="color:#0074c8;text-decoration:none;">Methodology</a> &middot;
            <a href="{SITE_URL}/data" style="color:#0074c8;text-decoration:none;">Full downloads</a> &middot;
            <a href="https://orcid.org/0000-0003-3371-9735" style="color:#0074c8;text-decoration:none;">ORCID</a><br>
            <em style="color:#8a8575;">This dashboard is a research dataset. Nothing here is investment advice.</em>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

    html_out = DATA_OUT / "weekly_email.html"
    txt_out = DATA_OUT / "weekly_email.txt"
    html_out.write_text(html, encoding="utf-8")
    txt_out.write_text(plain, encoding="utf-8")

    subj_delta = f" ({d_vol} WoW)" if d_vol else ""
    subject = f"DV-PMI / Week of {as_of} / {_usd(vol)} volume{subj_delta}"
    preview = headline_q[:120]
    print(f"Email digest written:")
    print(f"  HTML:    {html_out}")
    print(f"  Plain:   {txt_out}")
    print(f"  Subject: {subject}")
    print(f"  Preview: {preview}")


if __name__ == "__main__":
    main()
