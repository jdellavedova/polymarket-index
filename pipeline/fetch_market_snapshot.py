"""fetch_market_snapshot.py — pull current odds + order-book depth for the
top-volume Polymarket markets.

For each market in top_markets_latest.json, hits Gamma API for current
implied probabilities and CLOB API for full order books on the YES and NO
tokens. Computes:
  - yes_price (current implied probability of YES)
  - top-of-book bid/ask (price + dollar size) for YES
  - bid-ask spread (in cents and bps of mid)
  - depth within +/-5% of mid (dollars on each side)
  - top-5 levels of the YES book (for the inline depth ladder)
  - end_date (resolution time)
  - 7-day change in yes_price (against market_snapshot_history.csv if present)

Outputs:
  market_snapshot_latest.json   (current snapshot, one entry per market)
  market_snapshot_history.csv   (long format, one row per market per week)
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

from common import utc_now, write_json
from config import DATA_OUT

GAMMA = "https://gamma-api.polymarket.com/markets/{mid}"
CLOB_BOOK = "https://clob.polymarket.com/book?token_id={tid}"
TIMEOUT = 12


def _safe_get(url: str) -> dict | None:
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _parse_list(raw) -> list:
    """Gamma returns lists as JSON-encoded strings ('["Yes","No"]')."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return []


def _book_summary(book: dict | None) -> dict:
    """Parse a CLOB book response into top-of-book + depth metrics."""
    if not book:
        return {}
    bids_raw = book.get("bids") or []
    asks_raw = book.get("asks") or []
    bids = sorted(((float(b["price"]), float(b["size"])) for b in bids_raw), reverse=True)
    asks = sorted((float(a["price"]), float(a["size"])) for a in asks_raw)
    out: dict = {
        "n_bid_levels": len(bids),
        "n_ask_levels": len(asks),
        "top5_bids": [{"price": p, "size": s, "dollars": p * s} for p, s in bids[:5]],
        "top5_asks": [{"price": p, "size": s, "dollars": p * s} for p, s in asks[:5]],
    }
    if bids:
        bp, bs = bids[0]
        out["top_bid_price"] = bp
        out["top_bid_dollars"] = bp * bs
    if asks:
        ap, asz = asks[0]
        out["top_ask_price"] = ap
        out["top_ask_dollars"] = ap * asz
    if bids and asks:
        mid = (bids[0][0] + asks[0][0]) / 2
        out["mid"] = mid
        out["spread_cents"] = (asks[0][0] - bids[0][0]) * 100
        if mid > 0:
            out["spread_bps"] = (asks[0][0] - bids[0][0]) / mid * 10000
        # Depth within +/-5% of mid, in dollars
        lo = mid * 0.95
        hi = mid * 1.05
        out["depth_5pct_bid_dollars"] = sum(p * s for p, s in bids if p >= lo)
        out["depth_5pct_ask_dollars"] = sum(p * s for p, s in asks if p <= hi)
    return out


def fetch_one(market_id: str) -> dict | None:
    g = _safe_get(GAMMA.format(mid=market_id))
    if not g:
        return None
    outcomes = _parse_list(g.get("outcomes"))
    prices = _parse_list(g.get("outcomePrices"))
    token_ids = _parse_list(g.get("clobTokenIds"))
    if not token_ids:
        return None

    # Convention: index 0 is YES (matches Polymarket's own UI). Some markets
    # use multi-outcome but the top-volume binary markets use [Yes, No].
    yes_idx = 0
    if outcomes and len(outcomes) >= 2 and str(outcomes[0]).lower() in ("no",):
        yes_idx = 1
    yes_token = token_ids[yes_idx] if yes_idx < len(token_ids) else token_ids[0]

    out: dict = {
        "market_id": market_id,
        "question": g.get("question"),
        "yes_price": float(prices[yes_idx]) if yes_idx < len(prices) and prices else None,
        "outcomes": outcomes,
        "outcome_prices": [float(p) for p in prices] if prices else None,
        "yes_token_id": str(yes_token),
        "end_date": g.get("endDate") or g.get("end_date_iso"),
        "active": g.get("active"),
        "closed": g.get("closed"),
        "volume_total_num": float(g.get("volumeNum") or g.get("volume") or 0) if (g.get("volumeNum") or g.get("volume")) else None,
        "fetched_at": utc_now(),
    }

    book = _safe_get(CLOB_BOOK.format(tid=yes_token))
    out["yes_book"] = _book_summary(book)
    return out


def main() -> None:
    t0 = time.time()
    top = json.loads((DATA_OUT / "top_markets_latest.json").read_text(encoding="utf-8"))
    snapshots: list[dict] = []
    for m in top["markets"]:
        mid = str(m["market_id"])
        snap = fetch_one(mid)
        if snap is None:
            print(f"  SKIP market_id={mid} (Gamma/CLOB fetch failed)")
            continue
        snap["rank"] = m["rank"]
        snap["category"] = m.get("category")
        snap["weekly_volume_usd"] = m["usd_volume"]
        snap["weekly_trades"] = m["n_trades"]
        snapshots.append(snap)
        time.sleep(0.15)  # be polite to the public API

    # Append to history (long format) for week-over-week diffs
    hist_path = DATA_OUT / "market_snapshot_history.csv"
    today_iso = date.today().isoformat()
    new_rows = []
    for s in snapshots:
        new_rows.append({
            "snapshot_date": today_iso,
            "as_of_week": top["as_of_week"],
            "market_id": s["market_id"],
            "yes_price": s.get("yes_price"),
            "spread_cents": (s.get("yes_book") or {}).get("spread_cents"),
            "depth_5pct_bid_dollars": (s.get("yes_book") or {}).get("depth_5pct_bid_dollars"),
            "depth_5pct_ask_dollars": (s.get("yes_book") or {}).get("depth_5pct_ask_dollars"),
        })
    new_df = pd.DataFrame(new_rows)
    if hist_path.exists():
        old = pd.read_csv(hist_path, dtype={"market_id": str})
        # de-dup on (snapshot_date, market_id) so reruns don't double up
        combined = pd.concat([old, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["snapshot_date", "market_id"], keep="last")
    else:
        combined = new_df
    combined.to_csv(hist_path, index=False)

    # Compute 7-day price change vs the most recent prior snapshot for the same market
    hist = combined.copy()
    hist["snapshot_date"] = pd.to_datetime(hist["snapshot_date"])
    today_dt = pd.Timestamp(today_iso)
    target = today_dt - timedelta(days=7)
    for s in snapshots:
        prior = hist[(hist["market_id"] == s["market_id"]) & (hist["snapshot_date"] <= target)]
        if not prior.empty and s.get("yes_price") is not None:
            prior = prior.sort_values("snapshot_date").iloc[-1]
            if pd.notna(prior["yes_price"]):
                s["yes_price_7d_ago"] = float(prior["yes_price"])
                s["yes_price_change_pp"] = (s["yes_price"] - float(prior["yes_price"])) * 100
            else:
                s["yes_price_7d_ago"] = None
                s["yes_price_change_pp"] = None
        else:
            s["yes_price_7d_ago"] = None
            s["yes_price_change_pp"] = None

    payload = {
        "as_of_week": top["as_of_week"],
        "generated_at": utc_now(),
        "markets": snapshots,
        "notes": (
            "Live odds + order-book depth from Polymarket Gamma + CLOB APIs. "
            "yes_price is the implied probability of the YES outcome (0..1). "
            "Spread is on the YES token; depth is dollars within +/-5% of mid. "
            "yes_price_change_pp diffs against a snapshot taken ~7 days prior."
        ),
    }
    write_json(DATA_OUT / "market_snapshot_latest.json", payload)
    print(f"MarketSnapshot: {len(snapshots)} markets in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
