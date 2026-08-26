#!/usr/bin/env python3
"""Read the shipping terms eBay is ACTUALLY serving on our live listings.

Why this exists: 66 PUBLISHED offers were built against fulfillment policies
that have since been deleted from the account (292380047014, 295948332014,
292427280014, ...). The Account API returns 404 for those ids, and the Inventory
API only ever echoes the id back — so the real terms those listings show buyers
are not readable through either. The Browse API would carry them, but our seller
keyset returns HTTP 403 on /buy/browse.

The Trading API does carry them, on the same user token and the same transport
already used for EPS photo upload. GetItem returns the SNAPSHOT of the terms the
listing was published with, which is exactly what we need before overwriting.

    python tools/live_shipping_survey.py                 # every published offer
    python tools/live_shipping_survey.py --dead-only     # only deleted-policy ones
    python tools/live_shipping_survey.py --csv out.csv
"""
from __future__ import annotations

import argparse, csv, json, sys, urllib.request, urllib.error
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))

import ebay_client as ec  # noqa: E402

NS = "urn:ebay:apis:eBLBaseComponents"
LIVE_POLICIES = {"296458692014", "296996597014"}


def get_item(listing_id: str, creds) -> dict:
    """Trading GetItem -> the shipping/return terms the listing actually serves."""
    xml = ('<?xml version="1.0" encoding="utf-8"?>'
           '<GetItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
           f"<ItemID>{listing_id}</ItemID>"
           "<DetailLevel>ReturnAll</DetailLevel>"
           "<IncludeItemSpecifics>false</IncludeItemSpecifics>"
           "</GetItemRequest>")
    headers = {
        "X-EBAY-API-COMPATIBILITY-LEVEL": ec._TRADING_COMPAT_LEVEL,
        "X-EBAY-API-CALL-NAME": "GetItem",
        "X-EBAY-API-SITEID": ec._TRADING_SITE_ID,
        "X-EBAY-API-DEV-NAME": creds.dev_id,
        "X-EBAY-API-APP-NAME": creds.app_id,
        "X-EBAY-API-CERT-NAME": creds.cert_id,
        "X-EBAY-API-IAF-TOKEN": ec.get_user_access_token(creds),
        "Content-Type": "text/xml",
    }
    req = urllib.request.Request(ec._trading_endpoint(creds),
                                 data=xml.encode("utf-8"), method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        root = ET.fromstring(resp.read().decode("utf-8", errors="replace"))

    def find(path):
        el = root.find(path)
        return el.text if el is not None else None

    ack = find(f"{{{NS}}}Ack")
    if ack not in ("Success", "Warning"):
        msg = find(f"{{{NS}}}Errors/{{{NS}}}LongMessage") or "unknown Trading error"
        raise RuntimeError(f"{listing_id}: {msg}")

    item = f"{{{NS}}}Item/"
    svcs = []
    for so in root.findall(f"{item}{{{NS}}}ShippingDetails/{{{NS}}}ShippingServiceOptions"):
        def sub(tag):
            el = so.find(f"{{{NS}}}{tag}")
            return el.text if el is not None else None
        svcs.append({"service": sub("ShippingService"), "cost": sub("ShippingServiceCost"),
                     "free": sub("FreeShipping")})
    return {
        "handlingTime": find(f"{item}{{{NS}}}DispatchTimeMax"),
        "shippingType": find(f"{item}{{{NS}}}ShippingDetails/{{{NS}}}ShippingType"),
        "services": svcs,
        "returnsAccepted": find(f"{item}{{{NS}}}ReturnPolicy/{{{NS}}}ReturnsAcceptedOption"),
        "returnPeriod": find(f"{item}{{{NS}}}ReturnPolicy/{{{NS}}}ReturnsWithinOption"),
        "returnPayer": find(f"{item}{{{NS}}}ReturnPolicy/{{{NS}}}ShippingCostPaidByOption"),
        "site": find(f"{item}{{{NS}}}Site"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--survey", default=".offer_policy_survey2.json")
    ap.add_argument("--dead-only", action="store_true",
                    help="only offers whose fulfillment policy no longer exists")
    ap.add_argument("--csv")
    args = ap.parse_args()

    creds = ec.load_credentials()
    rows = [r for r in json.load(open(args.survey))
            if r["status"] == "PUBLISHED" and r.get("listingId")]
    if args.dead_only:
        rows = [r for r in rows if r["fulfillmentPolicyId"] not in LIVE_POLICIES]

    # One listing can back many SKUs (multi-variation) — ask eBay once each.
    seen, out = {}, []
    for i, r in enumerate(rows, 1):
        lid = r["listingId"]
        if lid not in seen:
            try:
                seen[lid] = get_item(lid, creds)
            except Exception as e:                       # noqa: BLE001
                seen[lid] = {"error": str(e)}
            print(f"  [{i}/{len(rows)}] {lid} {r['sku']}", file=sys.stderr)
        out.append({**r, **seen[lid]})

    for o in out:
        svc = "; ".join(f"{s['service']}={'FREE' if s['free']=='true' else s['cost']}"
                        for s in o.get("services") or []) or o.get("error", "-")
        print(f"{o['sku']:32} pol={o['fulfillmentPolicyId']} handling={o.get('handlingTime')}d  {svc}")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["sku", "offerId", "listingId", "fulfillmentPolicyId",
                        "handlingTime", "shippingType", "services",
                        "returnPeriod", "returnPayer", "error"])
            for o in out:
                w.writerow([o["sku"], o["offerId"], o["listingId"], o["fulfillmentPolicyId"],
                            o.get("handlingTime"), o.get("shippingType"),
                            json.dumps(o.get("services")), o.get("returnPeriod"),
                            o.get("returnPayer"), o.get("error", "")])
        print(f"\nwrote {args.csv}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
