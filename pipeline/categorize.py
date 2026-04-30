"""categorize.py — heuristic question-text classifier for prediction markets.

Polymarket and Gamma metadata frequently leave the `category` field null,
especially for markets created after the static snapshot. This module
classifies a market by keyword-matching against the question text. Used by
aggregate_top_markets.py to populate the Category column on the dashboard.

Returns one of:
  Politics, Macro, Crypto, Sports, Geopolitics, Business, Entertainment, Other
"""
from __future__ import annotations

import re

# Order matters: more-specific patterns first.
RULES: list[tuple[str, list[str]]] = [
    ("Crypto", [
        r"\bbitcoin\b", r"\bbtc\b", r"\bether(eum)?\b", r"\beth\b",
        r"\bsolana\b", r"\bsol\b", r"\bdogecoin\b", r"\bxrp\b",
        r"\bcrypto\b", r"\bstablecoin\b", r"\busd[ct]\b", r"\bbinance\b",
        r"\bcoinbase\b", r"\bblockchain\b", r"\bnft\b",
    ]),
    ("Macro", [
        r"\bfed\b", r"\bfederal reserve\b", r"\bfomc\b", r"\binterest rate", r"\brate cut",
        r"\brate hike", r"\bbasis points?\b", r"\bcpi\b", r"\binflation\b", r"\bppi\b",
        r"\bgdp\b", r"\brecession\b", r"\bunemployment\b", r"\bnonfarm\b", r"\bjobs report\b",
        r"\btreasury yield\b", r"\b10[- ]?year\b", r"\bs&p 500\b", r"\bnasdaq\b",
        r"\bdow jones\b", r"\bstock market\b", r"\bequity index\b",
    ]),
    ("Geopolitics", [
        r"\binvade\b", r"\binvasion\b", r"\bwar\b", r"\bmilitary\b",
        r"\bnato\b", r"\barmed forces\b", r"\bnuclear weapon",
        r"\bukraine\b", r"\brussia\b", r"\bisrael\b", r"\bgaza\b",
        r"\bhamas\b", r"\bhezbollah\b", r"\biran\b", r"\bnorth korea\b",
        r"\btaiwan\b", r"\bsouth china sea\b",
        r"\bcease[- ]?fire\b", r"\bpeace deal\b", r"\bhostage", r"\bstrike on\b",
    ]),
    ("Politics", [
        r"\belection\b", r"\bprimary\b", r"\bcaucus\b",
        r"\bpresident", r"\bprime minister\b", r"\bchancellor\b",
        r"\bsenate\b", r"\bcongress\b", r"\bhouse of representatives\b",
        r"\bparliament\b", r"\bbundestag\b", r"\bdiet\b",
        r"\bvote\b", r"\bvoter\b", r"\belectoral\b",
        r"\btrump\b", r"\bbiden\b", r"\bharris\b", r"\bdesantis\b",
        r"\bmusk\b.*\b(senate|run|office)\b",
        r"\bdemocrat", r"\brepublican", r"\bgop\b",
        r"\bsupreme court\b", r"\bjustice\b", r"\bimpeach",
        r"\bcabinet\b", r"\bsecretary of\b", r"\battorney general\b",
    ]),
    ("Sports", [
        r"\bnfl\b", r"\bnba\b", r"\bnhl\b", r"\bmlb\b", r"\bmls\b", r"\bfifa\b",
        r"\bworld cup\b", r"\bsuper bowl\b", r"\bstanley cup\b", r"\bworld series\b",
        r"\bnba finals\b", r"\bchampions league\b", r"\bpremier league\b",
        r"\bla liga\b", r"\bbundesliga\b", r"\bserie a\b", r"\bligue 1\b",
        r"\bufc\b", r"\bboxing\b", r"\bfootball\b", r"\bsoccer\b",
        r"\bbasketball\b", r"\bbaseball\b", r"\bhockey\b", r"\btennis\b",
        r"\bgolf\b", r"\bf1\b", r"\bformula 1\b", r"\bgrand prix\b",
        r"\bolympic", r"\bmasters\b.*\b(golf|tournament)\b",
        r"\bplayoff", r"\bchampions(hip)?\b", r"\b(win|score) the\b.*\b(game|match)\b",
    ]),
    ("Business", [
        r"\bipo\b", r"\bmerger\b", r"\bacquisition\b", r"\bbankrupt",
        r"\bceo\b", r"\bcfo\b", r"\bearnings\b", r"\brevenue\b", r"\bgo public\b",
        r"\b(apple|amazon|tesla|google|microsoft|meta|nvidia|alphabet)\b",
        r"\bopenai\b", r"\banthropic\b", r"\bsam altman\b", r"\belon musk\b.*\b(ceo|tesla|spacex|x\b|twitter)\b",
        r"\bspacex\b", r"\bstarship\b", r"\b(layoffs|hiring|fire) at\b",
    ]),
    ("Entertainment", [
        r"\boscar(s)?\b", r"\bgrammy", r"\bemmy", r"\bgolden globe",
        r"\b(taylor swift|beyonc[eé]|drake|kanye)\b",
        r"\bbillboard\b", r"\b(album|song|single).*\b(release|drop)\b",
        r"\bbox office\b", r"\b(film|movie).*\b(gross|box)\b",
        r"\bnetflix\b.*\b(release|series|show)\b",
    ]),
]

COMPILED = [(cat, [re.compile(p, re.IGNORECASE) for p in pats]) for cat, pats in RULES]


def categorize(question: str | None) -> str | None:
    """Return a category label for a question, or None if no rule matches.

    Rules are tried in order (more specific first); first match wins.
    """
    if not question:
        return None
    q = question.strip()
    for cat, patterns in COMPILED:
        if any(p.search(q) for p in patterns):
            return cat
    return "Other"


if __name__ == "__main__":
    samples = [
        "Will there be no change in Fed interest rates after the April 2026 meeting?",
        "Will Bitcoin hit $150k by June 30, 2026?",
        "Will the U.S. invade Iran before 2027?",
        "Will Roberto Sánchez Palomino win the 2026 Peruvian presidential election?",
        "Will Trump be re-elected in 2028?",
        "Will the Lakers win the NBA Finals?",
        "Will Apple acquire Anthropic by Q4 2026?",
        "Will Taylor Swift release a new album?",
        "Random question about something unusual",
    ]
    for q in samples:
        print(f"  {categorize(q):<15} | {q}")
