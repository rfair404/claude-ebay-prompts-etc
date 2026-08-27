#!/usr/bin/env python3
"""One-off: put every MPN-format (multi-variation) offer on the free/seller-paid
+ eBay International Shipping policy, and raise any variation priced below the
$5-profit floor. Backs up the prior offer bodies before writing."""
import sys, json, argparse
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "lib"))
import list_edit as L

POLICY = "296458692014"
FLOOR = "14.99"

ap = argparse.ArgumentParser()
ap.add_argument("--apply", action="store_true")
ap.add_argument("--policy-only", action="store_true")
a = ap.parse_args()

rows = json.load(open(ROOT / ".mpn_reprice.json"))
creds = L.load_credentials()
backup, changed = [], []
for lid, sku, old, new, net, qty in rows:
    oid = L._find_offer_id_for_sku(sku, creds)
    cur = L.api_send("GET", f"/sell/inventory/v1/offer/{oid}", creds=creds)
    backup.append(cur)
    offer = {k: cur[k] for k in L._WRITABLE_OFFER_KEYS if k in cur}
    what = []
    lp = offer.setdefault("listingPolicies", {})
    if lp.get("fulfillmentPolicyId") != POLICY:
        what.append(f"policy {lp.get('fulfillmentPolicyId')}->{POLICY}")
        lp["fulfillmentPolicyId"] = POLICY
    if not a.policy_only and float(new) != float(old):
        what.append(f"price {old}->{new}")
        offer["pricingSummary"] = {"price": {"value": f"{new:.2f}", "currency": "USD"}}
    if not what:
        print(f"  {sku:28s} unchanged"); continue
    print(f"  {sku:28s} {'; '.join(what)}")
    if a.apply:
        L.api_send("PUT", f"/sell/inventory/v1/offer/{oid}", offer, creds=creds)
        changed.append((sku, what))
(ROOT / ".mpn_offer_backup.json").write_text(json.dumps(backup, indent=1), encoding="utf-8")
print(f"\n{'APPLIED' if a.apply else 'DRY RUN'} — {len(changed) if a.apply else 0} offers written; backup in .mpn_offer_backup.json")
