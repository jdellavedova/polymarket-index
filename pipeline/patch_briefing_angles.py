import json, sys
sys.stdout.reconfigure(encoding='utf-8')

path = 'C:/Users/joshd/Dev/polymarket-index/site/public/data/briefings_latest.json'
with open(path, encoding='utf-8') as f:
    d = json.load(f)

angles = {}

# Rank 4: Epstein — 7% YES, $1.1M
for b in d['briefings']:
    if 'Epstein' in b['question']:
        angles[b['rank']] = (
            "A congressional push in May 2026 to release unredacted Epstein documents "
            "and formally reopen the investigation into his 2019 prison death drove a new "
            "surge of volume. At 7%, traders assign meaningful tail-risk probability to a "
            "formal DOJ or congressional declaration overturning the official suicide ruling, "
            "though no new forensic evidence has emerged and the official medical examiner's "
            "finding remains unchanged."
        )
    if 'no change in Fed' in b['question'] or 'no change in fed' in b['question'].lower():
        angles[b['rank']] = (
            "The June FOMC meeting is fully priced as a hold at 98% YES; Fed officials' "
            "recent guidance and sticky services inflation leave no credible case for a "
            "near-term cut. Volume here is largely arbitrage flow locking in the near-certain "
            "outcome. The live debate has shifted to September, where cut probability sits "
            "around 65-70% on longer-dated contracts."
        )
    if 'Lai Ching-te' in b['question'] or 'impeached' in b['question']:
        angles[b['rank']] = (
            "Taiwan's opposition-controlled Legislative Yuan advanced impeachment proceedings "
            "against President Lai Ching-te in May, citing constitutional overreach in budget "
            "vetoes — driving a spike in trading volume. At 2% with fewer than 40 days to the "
            "June 30 deadline, markets price this as a tail risk: the DPP retains enough "
            "allied votes to block the two-thirds supermajority required for a successful "
            "impeachment in the current session."
        )

for b in d['briefings']:
    if b['rank'] in angles:
        b['news_angle'] = angles[b['rank']]
        print(f"Patched rank {b['rank']}: {b['question'][:50]}")

with open(path, 'w', encoding='utf-8') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
print("Done")
