"""build_commentary.py — auto-append a weekly commentary entry to commentary.json.

Reads the already-generated weekly narrative and top-markets payload; if no
entry for the current as_of week exists in commentary.json, prepends one.
Hand-edited entries are never modified: the generator only touches
auto_generated=true entries it created in prior runs for the same week.
"""
from __future__ import annotations

import json
from pathlib import Path

from common import utc_now, write_json
from config import DATA_OUT

COMMENTARY_FILE = DATA_OUT / "commentary.json"


def _read(name: str) -> dict:
    with open(DATA_OUT / name, "r", encoding="utf-8") as f:
        return json.load(f)


def _classify_tag(top_markets: list[dict]) -> str:
    if not top_markets:
        return "MARKETS"
    q = (top_markets[0].get("question") or "").lower()
    if any(t in q for t in ("election", "president", "prime minister", "parliament", "senate", "congress")):
        return "POLITICS"
    if any(t in q for t in ("fed", "rate", "inflation", "gdp", "employment", "fomc")):
        return "MACRO"
    if any(t in q for t in ("bitcoin", "crypto", "eth", "btc", "sol")):
        return "CRYPTO"
    if any(t in q for t in ("world cup", "super bowl", "nfl", "nba", "mlb", "premier league")):
        return "SPORTS"
    return "MARKETS"


def _truncate(s: str, max_chars: int = 280) -> str:
    if len(s) <= max_chars:
        return s
    return s[:max_chars - 1].rsplit(" ", 1)[0] + "..."


def main() -> None:
    narrative = _read("weekly_narrative.json")
    top = _read("top_markets_latest.json")

    as_of = narrative.get("as_of", "")
    sentences = narrative.get("sentences", [])
    top_markets = top.get("markets", [])

    # Build auto title from the top market question.
    top_q = (top_markets[0].get("question") or "").strip() if top_markets else ""
    if top_q:
        title = f"Week of {as_of}: {top_q[:80]}"
    else:
        title = f"Week of {as_of}: Polymarket weekly summary"

    # Body: headline quote + first two sentences of the narrative.
    headline = narrative.get("headline_quote", "")
    body_parts = [p for p in ([headline] + sentences) if p]
    body = " ".join(body_parts[:3])
    body = _truncate(body, 400)

    tag = _classify_tag(top_markets)

    new_entry = {
        "date": as_of,
        "tag": tag,
        "title": title,
        "body": body,
        "href": "/briefings",
        "href_label": "See this week's briefings",
        "auto_generated": True,
    }

    # Load existing commentary.json (create empty shell if missing).
    if COMMENTARY_FILE.exists():
        try:
            existing = json.loads(COMMENTARY_FILE.read_text(encoding="utf-8"))
        except Exception:
            existing = {"notes": []}
    else:
        existing = {"notes": []}

    notes: list[dict] = existing.get("notes", [])

    # Check if an entry for this week already exists.
    for i, note in enumerate(notes):
        if note.get("date") == as_of:
            if note.get("auto_generated"):
                # Replace the stale auto entry with the freshly generated one.
                notes[i] = new_entry
                print(f"Commentary: replaced auto entry for {as_of}")
            else:
                print(f"Commentary: preserving hand-edited entry for {as_of}")
            break
    else:
        # No entry for this week; prepend a new one.
        notes.insert(0, new_entry)
        print(f"Commentary: added new auto entry for {as_of} (total {len(notes)} notes)")

    payload = {
        "notes": notes,
        "notes_url": "/notes",
        "generated_at": utc_now(),
    }
    write_json(COMMENTARY_FILE, payload)


if __name__ == "__main__":
    main()
