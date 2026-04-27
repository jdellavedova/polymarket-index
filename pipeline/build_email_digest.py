"""Generate the weekly email digest (HTML + plain text).

Writes two files into site/public/data/:
  weekly_email.html   — MJML-free HTML, ready to paste into Mailchimp / Buttondown /
                        Beehiiv / any ESP campaign editor
  weekly_email.txt    — plain-text equivalent for the .txt alternative part

Also prints a ready-to-send block with subject line and a suggested preview text.
This script is intentionally ESP-agnostic: the user will paste the HTML into
their chosen provider weekly (or the harness can later wire in Mailchimp's
`campaigns/send` API, Buttondown's POST /emails, etc.).
"""
from __future__ import annotations

import json
from pathlib import Path

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


def main() -> None:
    narrative = _read("weekly_narrative.json")
    activity = _read("weekly_activity_latest.json")
    pwi = _read("pwi_latest.json")
    pii = _read("pii_latest.json")
    top = _read("top_markets_latest.json")
    try:
        profit = _read("profit_split_latest.json")
    except FileNotFoundError:
        profit = None

    as_of = narrative.get("as_of", activity.get("as_of", "recent"))

    # ---- Plain-text digest ----
    lines = []
    lines.append(f"DV-PMI WEEKLY DIGEST / Week of {as_of}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(narrative.get("headline_quote", "").strip('"'))
    lines.append("")
    lines.append("-" * 60)
    lines.append("THIS WEEK BY THE NUMBERS")
    lines.append("-" * 60)
    lines.append(f"  Total USD volume        {_usd(activity['total_usd_volume'])}")
    lines.append(f"  Total trades            {_compact_trades(activity['total_trades'])}")
    lines.append(f"  New participants        {activity['new_wallets']:,}")
    lines.append(f"  Bot share of trades     {activity['by_type_share']['bot']*100:.1f}%")
    lines.append(f"  Non-bot Prelec alpha    {pwi['value']:.3f} (Kahneman-Tversky: 0.65)")
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
        lines.append(f"  {m['rank']}. {_usd(m['usd_volume'])}  {q}")
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
    lines.append("INSIDER-TRADING FLAG COUNT (to date)")
    lines.append("-" * 60)
    hl = pii["headline"]
    lines.append(f"  Wallets flagged as likely informed:   {hl['total_flagged_p_lt_01']:,}")
    lines.append(f"  Out of wallets tested:                {hl['total_wallets_tested']:,} ({hl['flag_rate']*100:.2f}%)")
    lines.append(f"  After strict statistical correction:  {hl['holm_bonferroni_survivors']:,}")
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

    bot_share_pct = activity["by_type_share"]["bot"] * 100
    top_rows = []
    for m in (top.get("markets") or [])[:5]:
        q = esc(m.get("question") or f"(new market #{m['market_id']})")
        top_rows.append(f"""      <tr>
        <td style="padding:6px 12px 6px 0;vertical-align:top;color:#5a687a;font-family:Georgia,serif;">{m['rank']}.</td>
        <td style="padding:6px 0;vertical-align:top;font-family:Georgia,serif;"><strong style="color:#0a1420;">{_usd(m['usd_volume'])}</strong> &middot; <span style="color:#0a1420;">{q}</span></td>
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
    hl = pii["headline"]

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
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%">
            <tr>
              <td style="padding:6px 0;font-family:Georgia,serif;color:#5a687a;width:55%;">Total USD volume</td>
              <td style="padding:6px 0;font-family:'Courier New',monospace;color:#0a1420;text-align:right;"><strong>{_usd(activity['total_usd_volume'])}</strong></td>
            </tr>
            <tr>
              <td style="padding:6px 0;font-family:Georgia,serif;color:#5a687a;">Total trades</td>
              <td style="padding:6px 0;font-family:'Courier New',monospace;color:#0a1420;text-align:right;">{_compact_trades(activity['total_trades'])}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;font-family:Georgia,serif;color:#5a687a;">New participants</td>
              <td style="padding:6px 0;font-family:'Courier New',monospace;color:#0a1420;text-align:right;">{activity['new_wallets']:,}</td>
            </tr>
            <tr>
              <td style="padding:6px 0;font-family:Georgia,serif;color:#5a687a;">Bot share of trades</td>
              <td style="padding:6px 0;font-family:'Courier New',monospace;color:#0a1420;text-align:right;">{bot_share_pct:.1f}%</td>
            </tr>
            <tr>
              <td style="padding:6px 0;font-family:Georgia,serif;color:#5a687a;">Non-bot Prelec &alpha;</td>
              <td style="padding:6px 0;font-family:'Courier New',monospace;color:#0a1420;text-align:right;">{pwi['value']:.3f} <span style='color:#5a687a;'>(K-T: 0.65)</span></td>
            </tr>
          </table>
        </td></tr>

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
          <div style="font-family:'Segoe UI',Helvetica,sans-serif;font-size:11px;letter-spacing:0.09em;color:#0074c8;text-transform:uppercase;margin-bottom:8px;">Insider-trading flags (to date)</div>
          <p style="margin:0;font-family:Georgia,serif;color:#0a1420;line-height:1.5;">
            <strong>{hl['total_flagged_p_lt_01']:,}</strong> of {hl['total_wallets_tested']:,} tested wallets flagged as likely informed ({hl['flag_rate']*100:.2f}%). {hl['holm_bonferroni_survivors']:,} survive strict statistical correction.
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

    subject = f"DV-PMI / Week of {as_of} / {_usd(activity['total_usd_volume'])} volume"
    preview = headline_q[:120]
    print(f"Email digest written:")
    print(f"  HTML:    {html_out}")
    print(f"  Plain:   {txt_out}")
    print(f"  Subject: {subject}")
    print(f"  Preview: {preview}")


if __name__ == "__main__":
    main()
