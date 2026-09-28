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
                         load_credentials, get_return_policies, EbayAPIError)
from config import get_storefront, ConfigError
import list_edit as le

# Offer fields eBay accepts back on an updateOffer PUT. Anything else in the
# GET payload (listing, status, ...) is read-only and 400s if echoed.
_PUT_KEYS = ("availableQuantity", "categoryId", "listingDescription",
             "listingDuration", "listingPolicies", "pricingSummary",
             "quantityLimitPerBuyer", "merchantLocationKey", "tax",
             "storeCategoryNames", "secondaryCategoryId", "lotSize",
             "includeCatalogProductDetails", "charity", "extendedProducerResponsibility")


def survey(cache=None, creds=None):
    rows = []
    for it in iter_inventory_items(creds=creds):
        sku = it.get("sku")
        if not sku:
            continue
        try:
            offers = get_offers_for_sku(sku, creds=creds)
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


def repair(row, want_return, dry=True, creds=None):
    """PUT the offer with the target return policy, then republish if it was live."""
    oid = row["offerId"]
    offer = api_send("GET", f"/sell/inventory/v1/offer/{oid}", marketplace=None, creds=creds)
    lp = offer.get("listingPolicies") or {}
    if lp.get("returnPolicyId") == want_return:
        return "already"
    if dry:
        return "would-update"
    body = {k: offer[k] for k in _PUT_KEYS if k in offer}
    body.setdefault("listingPolicies", {})
    body["listingPolicies"] = dict(lp, returnPolicyId=want_return)
    api_send("PUT", f"/sell/inventory/v1/offer/{oid}", body=body, marketplace=None, creds=creds)
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


def _returns_accepted(policy_id, creds):
    """Does this eBay return policy actually accept returns?

    Read from eBay, not from config: the storefront profile says what we
    INTEND and the policy ID says what eBay ENFORCES. This check exists for
    exactly the case where those two disagree.
    """
    for pol in get_return_policies(creds=creds):
        if str(pol.get("returnPolicyId")) == str(policy_id):
            return bool(pol.get("returnsAccepted"))
    return None          # unknown to this account


def _guard(args, creds):
    """Refuse the two ways --apply can silently destroy a store's terms.

    This is the most destructive tool in the repo: it rewrites the return
    policy on every offer and REPUBLISHES the live ones, in bulk, with no
    per-item confirmation. Two failures are possible and neither announces
    itself (GH #156 section 2).

    1. NO EXPLICIT STORE. Without --store the account comes from
       $EBAYBIZ_STORE or ebay.active_store, so which store gets bulk
       rewritten depends on ambient state — a shell variable set an hour ago
       for something else. Every other command here is per-item and
       recoverable; this one is neither.

    2. RETURNS FORCED ONTO A NO-RETURNS STORE. An as-is storefront exists to
       refuse returns. If its configured return_policy_id is ever a
       returns-accepted policy — pasted from the main store, or created
       before the no-returns one existed — this would apply it to every as-is
       lot and republish them live, deleting the reason the store exists.
    """
    if not args.apply:
        return                                   # dry runs read only
    if not args.store:
        raise SystemExit(
            "[X] --apply needs an explicit --store.\n"
            "    This rewrites the return policy on EVERY offer and republishes\n"
            "    the live ones. Without --store the account comes from\n"
            "    $EBAYBIZ_STORE or ebay.active_store — ambient state deciding\n"
            "    which store gets bulk rewritten. Name it: --store junk, ...")
    try:
        sf = get_storefront(creds.store)
    except ConfigError:
        sf = {}
    if sf.get("returns") == "none_as_is":
        want = le._resolve_policies_and_location(creds)[0]["return"]
        accepts = _returns_accepted(want, creds)
        if accepts:
            raise SystemExit(
                f"[X] refusing: storefront {creds.store!r} is returns: none_as_is,\n"
                f"    but its return_policy_id {want} ACCEPTS returns. Applying it\n"
                f"    would put returns on every as-is lot and republish them live —\n"
                f"    the inverse of why this store exists. Fix\n"
                f"    ebay.stores.{creds.store}.return_policy_id, then re-run.")
        if accepts is None:
            raise SystemExit(
                f"[X] refusing: return policy {want} is not one of store\n"
                f"    {creds.store!r}'s own policies. Policy IDs are per-account, so\n"
                f"    this is very likely another store's — applying it would fail\n"
                f"    or mis-set every offer.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually rewrite offers (default is a dry report)")
    ap.add_argument("--store", metavar="NAME",
                    help="which eBay seller account to sweep. REQUIRED with "
                         "--apply: this rewrites and republishes every live "
                         "offer, so the target is named, never inferred.")
    ap.add_argument("--cache", default=None,
                    help="survey cache (default: per-store, so one account's "
                         "survey is never replayed against another)")
    ap.add_argument("--reuse", action="store_true",
                    help="read the survey from --cache instead of re-walking the API")
    ap.add_argument("--status", default="PUBLISHED",
                    help="offer status to repair; ALL for every status")
    args = ap.parse_args()

    creds = load_credentials(store=args.store)
    _guard(args, creds)
    if not args.cache:
        args.cache = f".offer_policy_survey-{creds.store}.json"

    policies, _ = le._resolve_policies_and_location(creds)
    want = policies["return"]
    print(f"store: {creds.store}   environment: {creds.environment}")
    print(f"target return policy: {want}\n")

    if args.reuse:
        rows = json.load(open(args.cache))
    else:
        rows = survey(args.cache, creds=creds)

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
            out = repair(r, want, dry=not args.apply, creds=creds)
        except EbayAPIError as e:
            out = f"FAIL {e}"
        done[out.split()[0]] += 1
        print(f"  [{i}/{len(targets)}] {r['sku']} {r['offerId']} -> {out}")
    print("\n", dict(done))


if __name__ == "__main__":
    main()
