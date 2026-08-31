import json, sys, collections, time
sys.path.insert(0,'tools'); sys.path.insert(0,'lib')
import policy_sweep as ps
from ebay_client import api_send, EbayAPIError
WANT='296995924014'

print('re-surveying live offers (cache may be stale)...', flush=True)
rows = ps.survey('.offer_policy_survey.json')
live = [r for r in rows if r['status']=='PUBLISHED']
todo = [r for r in live if r['returnPolicyId'] != WANT]
c = collections.Counter(r['listingId'] for r in todo)
solo    = [r for r in todo if c[r['listingId']]==1]
grouped = [r for r in todo if c[r['listingId']]>1]
print(f"live={len(live)}  already-ok={len(live)-len(todo)}  todo={len(todo)}"
      f"  (standalone {len(solo)}, CHOICE {len(grouped)} across "
      f"{len({r['listingId'] for r in grouped})} listings)\n", flush=True)

out=[]
def run(batch, label):
    for i,r in enumerate(batch,1):
        try:
            res = ps.repair(r, WANT, dry=False)
        except EbayAPIError as e:
            res = f"FAIL {e}"
        try:
            o = api_send('GET', f"/sell/inventory/v1/offer/{r['offerId']}", marketplace=None)
            after = (o.get('listingPolicies') or {}).get('returnPolicyId'); st = o.get('status')
        except EbayAPIError as e:
            after, st = f'?{e}', '?'
        flag = 'OK ' if after==WANT and st=='PUBLISHED' else '!! '
        print(f"{flag}{label}[{i}/{len(batch)}] {r['listingId']} {r['sku'][:22]:<22} {res[:60]:<60} now={after} {st}", flush=True)
        out.append(dict(r, result=res, after=after, status_after=st))
        json.dump(out, open('.apply_rest_result.json','w'), indent=1)

run(solo, 'solo')
print('\n--- CHOICE / multi-variation listings ---', flush=True)
run(grouped, 'grp ')

ok = sum(1 for x in out if x['after']==WANT and x['status_after']=='PUBLISHED')
print(f"\n{ok}/{len(out)} confirmed on {WANT} and PUBLISHED")
bad = [x for x in out if not (x['after']==WANT and x['status_after']=='PUBLISHED')]
if bad:
    print(f"{len(bad)} NOT confirmed:")
    for x in bad:
        print('  ', x['listingId'], x['sku'], '|', x['result'][:120], '| after=', x['after'], x['status_after'])
