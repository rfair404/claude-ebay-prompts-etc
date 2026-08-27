#!/usr/bin/env python3
"""Survey — and optionally repair — the return/fulfillment policy on every offer.

Aligning the store to Top Rated Plus is not just a config change: offers already
on eBay keep whatever policy IDs they were published with. This walks every SKU's
offers, reports which policy each carries, and with --apply rewrites the return
policy (and optionally the handling-time-failing fulfillment policy) to the
configured default, republishing PUBLISHED offers so the change goes live.

See docs/top-rated-plus.md.
"""
import argparse, json, sys, collections, time
sys.path.insert(0, "lib")

from ebay_client import (iter_inventory_items, get_offers_for_sku, api_send,
                         load_credentials, EbayAPIError)
import list_edit as le

# Offer fields eBay accepts back on an updateOffer PUT. Anything else in the
# GET payload (listing, status, ...) is read-only and 400s if echoed.
_PUT_KEYS = ("availableQuantity", "categoryId", "listingDescription",
             "listingDuration", "listingPolicies", "pricingSummary",
             "quantityLimitPerBuyer", "merchantLocationKey", "tax",
             "storeCategoryNames", "secondaryCategoryId", "lotSize",
             "includeCatalogProductDetails", "charity", "extendedProducerResponsibility")


def survey(cache=None):
    rows = []
    for it in iter_inventory_items():
        sku = it.get("sku")
        if not sku:
            continue
        try:
            offers = get_offers_for_sku(sku)
        except EbayAPIError as e:
            print(f"  ! {sku}: {e}", file=sys.stderr)
            continue
        for o in offers:
            lp = o.get("listingPolicies") or {}
            rows.append({
                "sku": sku,
                "offerId": o.get("offerId"),
                "status": o.get("status"),
                "listingId": o.get("listing", {}).get("listingId"),
                "returnPolicyId": lp.get("returnPolicyId"),
                "fulfillmentPolicyId": lp.get("fulfillmentPolicyId"),
            })
        if cache and len(rows) % 25 == 0:
            json.dump(rows, open(cache, "w"), indent=1)
    if cache:
        json.dump(rows, open(cache, "w"), indent=1)
    return rows


def repair(row, want_return, dry=True):
    """PUT the offer with the target return policy, then republish if it was live."""
    oid = row["offerId"]
    offer = api_send("GET", f"/sell/inventory/v1/offer/{oid}", marketplace=None)
    lp = offer.get("listingPolicies") or {}
    if lp.get("returnPolicyId") == want_return:
        return "already"
    if dry:
        return "would-update"
    body = {k: offer[k] for k in _PUT_KEYS if k in offer}
    body.setdefault("listingPolicies", {})
    body["listingPolicies"] = dict(lp, returnPolicyId=want_return)
    api_send("PUT", f"/sell/inventory/v1/offer/{oid}", body=body, marketplace=None)
    if row["status"] == "PUBLISHED":
        # A published offer needs republishing for the new terms to take effect.
        # Transient 400s on republish are a known eBay flake — retry once.
        for attempt in range(2):
            try:
                api_send("POST", f"/sell/inventory/v1/offer/{oid}/publish", body={},
                         marketplace=None)
                break
            except EbayAPIError:
                if attempt:
                    raise
                time.sleep(2)
        return "updated+republished"
    return "updated"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite offers (default is a dry report)")
    ap.add_argument("--cache", default=".offer_policy_survey.json")
    ap.add_argument("--reuse", action="store_true",
                    help="read the survey from --cache instead of re-walking the API")
    ap.add_argument("--status", default="PUBLISHED",
                    help="offer status to repair; ALL for every status")
    args = ap.parse_args()

    policies, _ = le._resolve_policies_and_location(load_credentials())
    want = policies["return"]
    print(f"target return policy: {want}\n")

    if args.reuse:
        rows = json.load(open(args.cache))
    else:
        rows = survey(args.cache)

    print(f"offers: {len(rows)}")
    for label, key in (("status", "status"), ("returnPolicyId", "returnPolicyId")):
        c = collections.Counter(r[key] for r in rows)
        print(f"  by {label}: {dict(c)}")

    targets = [r for r in rows
               if r["returnPolicyId"] != want
               and (args.status == "ALL" or r["status"] == args.status)]
    print(f"\n{len(targets)} offer(s) to repair"
          f"{'' if args.apply else ' (dry run — pass --apply)'}")

    done = collections.Counter()
    for i, r in enumerate(targets, 1):
        try:
            out = repair(r, want, dry=not args.apply)
        except EbayAPIError as e:
            out = f"FAIL {e}"
        done[out.split()[0]] += 1
        print(f"  [{i}/{len(targets)}] {r['sku']} {r['offerId']} -> {out}")
    print("\n", dict(done))


if __name__ == "__main__":
    main()
