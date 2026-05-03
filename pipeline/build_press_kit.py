"""build_press_kit.py — render branded 1200x675 PNGs for journalists to embed.

Outputs three social-card-ready images at site/public/press/:
  - cumulative_pnl.png        bots vs retail since 2022
  - algorithmic_share.png     bot share over time
  - top_markets.png           top weekly markets by USD volume

All three carry the same shell: title at top, chart in the middle, source +
attribution at the bottom. 1200x675 = 16:9, fits Twitter / LinkedIn / Slack
preview cards exactly. Dark palette matches the live site.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import date

from config import DATA_OUT

OUT_DIR = DATA_OUT.parent / "press"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Site palette
BG = "#0a1420"
BG_SOFT = "#0f1d30"
TEXT = "#e7edf6"
MUTED = "#8fa1b6"
GRID = "#1b2c45"
USD_BLUE = "#75bee9"
POS = "#7fc264"
NEG = "#c95757"

W, H = 1200, 675   # pixel dimensions
DPI = 100
FIGSIZE = (W / DPI, H / DPI)


def _setup_axes(ax):
    ax.set_facecolor(BG_SOFT)
    for s in ax.spines.values():
        s.set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=10)
    ax.grid(True, color=GRID, linewidth=0.8, alpha=0.7)


def _add_chrome(fig, title: str, subtitle: str, source: str):
    """Title at top, source at bottom — same shell on every PNG."""
    fig.text(0.04, 0.92, title, fontsize=22, fontweight="bold", color=TEXT,
             family="serif", ha="left")
    fig.text(0.04, 0.875, subtitle, fontsize=12, color=MUTED, ha="left")
    fig.text(0.04, 0.04, source, fontsize=11, color=MUTED, ha="left", style="italic")
    fig.text(0.96, 0.04, "jdellavedova.com", fontsize=11, color=USD_BLUE,
             ha="right", fontweight="bold")
    fig.patch.set_facecolor(BG)


def _fmt_usd(v):
    sign = "+" if v >= 0 else "-"
    a = abs(v)
    if a >= 1e9: return f"{sign}${a/1e9:.1f}B"
    if a >= 1e6: return f"{sign}${a/1e6:.0f}M"
    if a >= 1e3: return f"{sign}${a/1e3:.0f}K"
    return f"{sign}${a:.0f}"


# ------------------------------------------------------------------
# 1. Cumulative P&L
# ------------------------------------------------------------------
def render_cumulative_pnl():
    payload = json.loads((DATA_OUT / "cumulative_pnl_history.json").read_text(encoding="utf-8"))
    dates = [date.fromisoformat(d[:10]) for d in payload["dates"]]
    bot = payload["cumulative"]["bot"]
    retail = payload["cumulative"]["active_retail"]

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    _setup_axes(ax)

    ax.fill_between(dates, bot, 0, where=[v >= 0 for v in bot],
                    color=POS, alpha=0.18, linewidth=0)
    ax.plot(dates, bot, color=POS, linewidth=2.2, label="Algorithmic wallets")

    ax.fill_between(dates, retail, 0, where=[v <= 0 for v in retail],
                    color=NEG, alpha=0.18, linewidth=0)
    ax.plot(dates, retail, color=NEG, linewidth=2.2, label="Active retail")

    ax.axhline(0, color=MUTED, linewidth=1, linestyle="--", alpha=0.5)

    bot_end = bot[-1]
    retail_end = retail[-1]
    ax.annotate(f"Bots: {_fmt_usd(bot_end)}", xy=(dates[-1], bot_end),
                xytext=(8, 6), textcoords="offset points",
                color=POS, fontsize=12, fontweight="bold")
    ax.annotate(f"Active retail: {_fmt_usd(retail_end)}", xy=(dates[-1], retail_end),
                xytext=(8, -16), textcoords="offset points",
                color=NEG, fontsize=12, fontweight="bold")

    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: _fmt_usd(v)))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.legend(loc="upper left", framealpha=0, fontsize=11, labelcolor=TEXT)

    fig.subplots_adjust(left=0.08, right=0.92, top=0.78, bottom=0.13)
    _add_chrome(
        fig,
        "Cumulative P&L on Polymarket",
        f"Bots vs active retail, weekly cumulative since November 2022 ({payload['n_weeks']} weeks)",
        "Source: Della Vedova (2026), 'Who Profits from Prediction Markets?'",
    )
    fig.savefig(OUT_DIR / "cumulative_pnl.png", dpi=DPI,
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    print(f"  wrote cumulative_pnl.png")


# ------------------------------------------------------------------
# 2. Algorithmic share over time
# ------------------------------------------------------------------
def render_algo_share():
    series = json.loads((DATA_OUT / "bot_share_timeseries.json").read_text(encoding="utf-8"))
    pts = [(date.fromisoformat(p["date"]), p.get("bot_share_ma13w"))
           for p in series if p.get("bot_share_ma13w") is not None]
    dates_, vals = zip(*pts)

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    _setup_axes(ax)

    ax.fill_between(dates_, vals, 0, color=USD_BLUE, alpha=0.15, linewidth=0)
    ax.plot(dates_, vals, color=USD_BLUE, linewidth=2.4)

    last_v = vals[-1]
    ax.scatter([dates_[-1]], [last_v], s=60, color=USD_BLUE, zorder=5,
               edgecolors=BG_SOFT, linewidth=2)
    ax.annotate(f"{last_v*100:.0f}%", xy=(dates_[-1], last_v),
                xytext=(10, -4), textcoords="offset points",
                color=TEXT, fontsize=14, fontweight="bold")

    ax.set_ylim(0, 1.0)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v*100:.0f}%"))
    ax.xaxis.set_major_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))

    fig.subplots_adjust(left=0.08, right=0.92, top=0.78, bottom=0.13)
    _add_chrome(
        fig,
        "Algorithmic share of Polymarket trading",
        "Share of weekly counterparty events (maker + taker) executed by bot wallets, 13-week MA",
        "Source: Della Vedova Prediction Market Indices (DV-PMI)",
    )
    fig.savefig(OUT_DIR / "algorithmic_share.png", dpi=DPI,
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    print(f"  wrote algorithmic_share.png")


# ------------------------------------------------------------------
# 3. Top markets bar chart (Finviz heatmap doesn't render cleanly without
# a treemap library; horizontal bars are the equivalent for static export)
# ------------------------------------------------------------------
def render_top_markets():
    top = json.loads((DATA_OUT / "top_markets_latest.json").read_text(encoding="utf-8"))
    markets = top["markets"][:10]
    questions = []
    for m in markets:
        q = m.get("question") or f"Market #{m['market_id']}"
        if len(q) > 70:
            q = q[:67].rstrip() + "..."
        questions.append(q)
    vols = [m["usd_volume"] / 1e6 for m in markets]
    cats = [m.get("category") or "Other" for m in markets]

    cat_color = {
        "Politics": "#a4364c", "Macro": USD_BLUE, "Crypto": "#5e3aa3",
        "Geopolitics": "#b85c1c", "Sports": "#2e7a4a",
        "Business": "#4a5c75", "Other": "#555",
    }
    colors = [cat_color.get(c, MUTED) for c in cats]

    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)
    _setup_axes(ax)

    y_pos = list(range(len(markets)))[::-1]
    ax.barh(y_pos, vols, color=colors, edgecolor=BG_SOFT, height=0.75)
    for i, (v, q) in enumerate(zip(vols, questions)):
        ax.text(v + max(vols) * 0.01, y_pos[i], f"${v:.1f}M",
                va="center", color=TEXT, fontsize=10, fontweight="bold")

    ax.set_yticks(y_pos)
    ax.set_yticklabels(questions, color=TEXT, fontsize=10)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:.0f}M"))
    ax.set_xlim(0, max(vols) * 1.15)

    fig.subplots_adjust(left=0.32, right=0.95, top=0.78, bottom=0.13)
    _add_chrome(
        fig,
        f"Top Polymarket markets · week of {top['as_of_week']}",
        f"Top {len(markets)} markets by weekly USD volume, color-coded by category",
        "Source: Della Vedova Prediction Market Indices (DV-PMI)",
    )
    fig.savefig(OUT_DIR / "top_markets.png", dpi=DPI,
                facecolor=BG, edgecolor="none")
    plt.close(fig)
    print(f"  wrote top_markets.png")


def main() -> None:
    print(f"PressKit -> {OUT_DIR}")
    render_cumulative_pnl()
    render_algo_share()
    render_top_markets()
    print("PressKit: done")


if __name__ == "__main__":
    main()
