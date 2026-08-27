import json, sys
sys.path.insert(0,'tools'); sys.path.insert(0,'lib')
import policy_sweep as ps
from ebay_client import api_send, EbayAPIError
WANT='296995924014'
rows=json.load(open('.live_null_return_policy.json'))
out=[]
for i,r in enumerate(rows,1):
    try:
        res=ps.repair(r, WANT, dry=False)
    except EbayAPIError as e:
        res=f"FAIL {e}"
    # read back the truth rather than trusting the write
    try:
        o=api_send('GET', f"/sell/inventory/v1/offer/{r['offerId']}", marketplace=None)
        after=(o.get('listingPolicies') or {}).get('returnPolicyId')
        st=o.get('status')
    except EbayAPIError as e:
        after, st = f'?{e}', '?'
    print(f"[{i}/{len(rows)}] {r['listingId']} {r['sku']:<12} {res:<22} now={after} {st}", flush=True)
    out.append(dict(r, result=res, after=after, status_after=st))
json.dump(out, open('.apply_null25_result.json','w'), indent=1)
ok=sum(1 for x in out if x['after']==WANT)
print(f"\n{ok}/{len(out)} now on {WANT}")
