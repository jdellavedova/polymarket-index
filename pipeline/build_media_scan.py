"""Weekly media scan — finds NEW press coverage mentioning Josh / the research
and queues it for human review in the weekly email digest.

Sources: Google News RSS (no API key). Candidates are deduped against
(a) mentions already listed in site/public/data/press_quotes.json and
(b) previously seen items in pipeline/media_scan_state.json (machine-local,
gitignored).

Nothing is auto-published: media items only reach the site when Josh adds
them to press_quotes.json / MentionedIn.astro by hand. Quotes are forever;
the scanner only shortens the time-to-notice. Network failures degrade to a
warning — this step must never fail the pipeline.
"""
from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests

from config import DATA_OUT

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "media_scan_state.json"
PRESS_QUOTES = DATA_OUT / "press_quotes.json"

QUERIES = [
    '"Della Vedova" Polymarket',
    '"Della Vedova" "prediction market"',
    '"Della Vedova" "University of San Diego" trading',
]
RSS_URL = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
TIMEOUT = 20


def _norm_title(t: str) -> str:
    """Lowercased alphanumeric words, outlet suffix stripped, for fuzzy dedup."""
    t = re.sub(r"\s+-\s+[^-]+$", "", t)  # Google News appends " - Outlet"
    return " ".join(re.findall(r"[a-z0-9]+", t.lower()))


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"items": []}


def _listed_titles() -> set[str]:
    try:
        pq = json.loads(PRESS_QUOTES.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()
    return {_norm_title(m.get("headline", "")) for m in pq.get("media_mentions", [])}


def _titles_overlap(a: str, b: str) -> bool:
    """Loose containment: the shorter title's words mostly inside the longer's."""
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return False
    small, big = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return len(small & big) / len(small) >= 0.7


def fetch_candidates() -> list[dict]:
    out, seen_links = [], set()
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (DV-PMI media scan)"
    for q in QUERIES:
        try:
            r = session.get(RSS_URL.format(q=quote_plus(q)), timeout=TIMEOUT)
            r.raise_for_status()
            root = ET.fromstring(r.content)
        except Exception as e:
            print(f"  WARNING: query {q!r} failed ({e}) - skipping")
            continue
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            src = item.find("source")
            outlet = (src.text or "").strip() if src is not None else ""
            if not title or not link or link in seen_links:
                continue
            seen_links.add(link)
            out.append({"title": title, "link": link, "pubDate": pub, "outlet": outlet})
        time.sleep(1)
    return out


def main() -> None:
    print(f"[{time.strftime('%H:%M:%S')}] Media scan (Google News RSS) ...")
    state = _load_state()
    known_titles = {i["norm_title"] for i in state["items"]}
    listed = _listed_titles()

    candidates = fetch_candidates()
    now = datetime.now(timezone.utc).isoformat()
    n_new = 0
    for c in candidates:
        nt = _norm_title(c["title"])
        if nt in known_titles:
            continue
        already_listed = any(_titles_overlap(nt, lt) for lt in listed)
        state["items"].append({
            "title": c["title"],
            "outlet": c["outlet"],
            "link": c["link"],
            "pubDate": c["pubDate"],
            "norm_title": nt,
            "first_seen": now,
            "status": "listed" if already_listed else "new",
        })
        known_titles.add(nt)
        if not already_listed:
            n_new += 1
            print(f"  NEW CANDIDATE: [{c['outlet']}] {c['title']}")

    # Reconcile: anything Josh has since added to press_quotes flips to listed.
    for item in state["items"]:
        if item["status"] == "new" and any(
            _titles_overlap(item["norm_title"], lt) for lt in listed
        ):
            item["status"] = "listed"

    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    pending = [i for i in state["items"] if i["status"] == "new"]

    # Private review file (gitignored, NOT in site/public/data — the digest
    # goes to subscribers, this list is for Josh only).
    review = HERE.parent / "media_review.txt"
    if pending:
        body = ["POSSIBLE NEW MEDIA MENTIONS - review, then add real ones to",
                "site/public/data/press_quotes.json (+ MentionedIn.astro for top outlets).",
                "Items clear automatically once listed there.", ""]
        for m in sorted(pending, key=lambda x: x.get("first_seen", ""), reverse=True):
            outlet = f"[{m['outlet']}] " if m.get("outlet") else ""
            body.append(f"* {outlet}{m['title']}")
            body.append(f"  {m['link']}")
            body.append(f"  first seen {m['first_seen'][:10]}")
            body.append("")
        review.write_text("\n".join(body), encoding="utf-8")
        print(f"!!! {len(pending)} media mention(s) pending review -> {review}")
    elif review.exists():
        review.unlink()
    print(f"MediaScan: {len(candidates)} results, {n_new} new this run, "
          f"{len(pending)} pending review")


if __name__ == "__main__":
    main()
