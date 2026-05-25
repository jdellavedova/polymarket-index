import json, sys
sys.stdout.reconfigure(encoding='utf-8')
with open('C:/Users/joshd/Dev/polymarket-index/site/public/data/briefings_latest.json') as f:
    d = json.load(f)
print('Week:', d['as_of_week'])
for b in d['briefings']:
    prob = next((a['value'] for a in b['annotations'] if a['label']=='Implied probability'), 'N/A')
    vol  = next((a['value'] for a in b['annotations'] if a['label']=='Weekly volume'), 'N/A')
    na   = b.get('news_angle', '')
    flag = 'PLACEHOLDER' if 'EDITORIAL' in na else 'ok'
    print(f"  [{flag}] {b['rank']}: {b['question'][:55]} | {prob} | {vol}")
