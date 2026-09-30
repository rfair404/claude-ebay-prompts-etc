#!/usr/bin/env python3
"""promote_seasoned — cost-per-sale promotion that waits until a listing is N days old.

The house rule (2026-09-29): a new listing gets its first 15 days to sell on its
own, unpromoted. Only after that does it join cost-per-sale promotion.

WHY THIS IS A TOOL AND NOT A CAMPAIGN SETTING

eBay's rules-based campaigns (`campaignCriterion` + `autoSelectFutureInventory`)
filter on brand, category, condition and min/max price only. There is NO
listing-age rule, so an auto-add campaign promotes a listing the day it goes
live, and removing the young ones by hand does not stick: the rule adds them
straight back. The age gate therefore has to live here, on a key-based
(no-criterion) cost-per-sale campaign that we fill by listing id, once a day.

WHERE THE LISTINGS AND THEIR AGES COME FROM

A Browse `item_summary/search` sweep, `filter=sellers:{seller}`, across every
top-level category. The Inventory-API sheet (tools/ebay_sheet.py) is blind to
listings created by hand on eBay.com — measured 2026-09-29: 11 of Revenue
Auto's 202 ads were not in it. The sweep found 253 live listings, including
the hand-listed ones, and every summary carries `itemCreationDate`, so the age
costs no extra call.

The age is the CURRENT listing's age: a relist gets a new id and a new
creation date, so it waits its 15 days again. That is intended.

WHAT IT DOES

    python tools/promote_seasoned.py --store default                 # plan, no writes
    python tools/promote_seasoned.py --store default --create --confirm
    python tools/promote_seasoned.py --store default --confirm       # add what has aged in
    python tools/promote_seasoned.py --store default --end 166478204014 --confirm

Every write is a dry run without --confirm, same as promote.py and list_edit.
The daily scheduled run is the plain `--confirm` form: it finds the campaign by
name, and adds every live listing at or above --min-price that is at least
--min-age-days old and is not already in it. It never removes an ad; eBay drops
an ad by itself when its listing ends.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "tools"))

import stores  # noqa: E402
import promote  # noqa: E402

CAMPAIGN_NAME = "Revenue Seasoned 15d"
MIN_AGE_DAYS = 15
MIN_PRICE = 25.0
AD_RATE_CAP = 10.0
BROWSE_SEARCH = "/buy/browse/v1/item_summary/search"


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def top_categories(api_get) -> list[str]:
    tree = api_get("/commerce/taxonomy/v1/category_tree/0")
    return [n["category"]["categoryId"]
            for n in tree["rootCategoryNode"]["childCategoryTreeNodes"]]


def sweep(seller: str, api_get) -> dict:
    """listing_id -> {created, price, title} for every live listing of `seller`."""
    found: dict[str, dict] = {}
    for cid in top_categories(api_get):
        off = 0
        while True:
            r = api_get(BROWSE_SEARCH, query={"category_ids": cid,
                                              "filter": f"sellers:{{{seller}}}",
                                              "limit": 200, "offset": off},
                        marketplace="EBAY_US")
            items = r.get("itemSummaries") or []
            for it in items:
                lid = str(it.get("legacyItemId") or it["itemId"].split("|")[1])
                found[lid] = {"created": it.get("itemCreationDate"),
                              "price": promote._f((it.get("price") or {}).get("value")),
                              "title": it.get("title", "")}
            off += len(items)
            if not items or off >= int(r.get("total") or 0):
                break
    return found


def split_by_age(listings: dict, now: datetime, min_age_days: int = MIN_AGE_DAYS,
                 min_price: float = MIN_PRICE) -> tuple[list, list]:
    """(seasoned, too_young) listing ids among those at or above min_price.

    A listing with no creation date is treated as too young: promoting on a
    guess is the thing this tool exists to stop.
    """
    cut = now - timedelta(days=min_age_days)
    seasoned, young = [], []
    for lid, v in listings.items():
        if v.get("price", 0) < min_price:
            continue
        created = v.get("created")
        if created and _ts(created) <= cut:
            seasoned.append(lid)
        else:
            young.append(lid)
    return sorted(seasoned), sorted(young)


def campaign_body(name: str = CAMPAIGN_NAME, cap: float = AD_RATE_CAP,
                  start: datetime | None = None) -> dict:
    """Key-based cost-per-sale: no campaignCriterion, so eBay never auto-adds.

    Dynamic ad rate capped at `cap`, the same terms Revenue Auto ran on.
    """
    start = start or datetime.now(timezone.utc) + timedelta(minutes=2)
    return {
        "campaignName": name,
        "marketplaceId": "EBAY_US",
        "channels": ["ON_SITE"],
        "startDate": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "fundingStrategy": {
            "fundingModel": "COST_PER_SALE",
            "adRateStrategy": "DYNAMIC",
            "dynamicAdRatePreferences": [{"adRateAdjustmentPercent": "0.0",
                                          "adRateCapPercent": f"{cap:.1f}"}],
        },
    }


def find_campaign(api_send, name: str) -> dict | None:
    try:
        return api_send("GET", "/sell/marketing/v1/ad_campaign/get_campaign_by_name"
                               f"?campaign_name={quote(name)}") or None
    except Exception:                                               # noqa: BLE001
        return None


def campaign_listing_ids(api_send, campaign_id: str) -> set[str]:
    ids, off = set(), 0
    while True:
        p = api_send("GET", f"/sell/marketing/v1/ad_campaign/{campaign_id}/ad"
                            f"?limit=200&offset={off}")
        got = p.get("ads") or []
        ids |= {str(a.get("listingId")) for a in got if a.get("listingId")}
        off += len(got)
        if len(got) < 200 or off >= int(p.get("total") or 0):
            return ids


def to_add(seasoned: list, already: set) -> list:
    return [l for l in seasoned if l not in already]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seller", default="popsgames",
                    help="eBay seller username to sweep (default popsgames)")
    ap.add_argument("--name", default=CAMPAIGN_NAME, help="campaign name")
    ap.add_argument("--min-age-days", type=int, default=MIN_AGE_DAYS)
    ap.add_argument("--min-price", type=float, default=MIN_PRICE)
    ap.add_argument("--ad-rate-cap", type=float, default=AD_RATE_CAP)
    ap.add_argument("--create", action="store_true",
                    help="create the campaign if it does not exist (needs --confirm)")
    ap.add_argument("--end", metavar="ID",
                    help="END another campaign (kept for its reports, never deleted)")
    ap.add_argument("--json", help="also write the plan here")
    ap.add_argument("--confirm", action="store_true",
                    help="actually write to eBay. Without it every write is a dry run.")
    stores.add_store_args(ap, help_extra="With --confirm and more than one store "
                                         "configured, --store must be given explicitly.")
    a = ap.parse_args()

    if a.confirm and len(stores.configured_stores()) > 1:
        store = stores.require_explicit_store(a, "promote_seasoned --confirm")
    else:
        store = stores.resolve_store_name(a.store)
    from ebay_client import api_get as _get, load_credentials
    creds = load_credentials(store=store)
    api_send = promote._api(creds)
    api_get = lambda path, **k: _get(path, creds=creds, **k)        # noqa: E731

    now = datetime.now(timezone.utc)
    listings = sweep(a.seller, api_get)
    seasoned, young = split_by_age(listings, now, a.min_age_days, a.min_price)
    print(f"sweep: {len(listings)} live · >=${a.min_price:.0f}: "
          f"{len(seasoned) + len(young)} · seasoned (>={a.min_age_days}d): "
          f"{len(seasoned)} · too young: {len(young)}")

    camp = find_campaign(api_send, a.name)
    cid = str((camp or {}).get("campaignId") or "")
    if not cid:
        body = campaign_body(a.name, a.ad_rate_cap)
        if not (a.create and a.confirm):
            print(f"campaign '{a.name}' not found — "
                  + ("DRY — would POST /sell/marketing/v1/ad_campaign\n"
                     + json.dumps(body, indent=1) if a.create
                     else "run with --create --confirm to make it"))
        else:
            api_send("POST", "/sell/marketing/v1/ad_campaign", body)
            cid = str((find_campaign(api_send, a.name) or {}).get("campaignId") or "")
            print(f"created campaign {cid}")
    else:
        print(f"campaign {cid} '{a.name}' {camp.get('campaignStatus')}")

    # END BEFORE ADD. A listing can sit in only ONE cost-per-sale campaign: with
    # the old campaign still running, every add came back "An ad for listing Id
    # … already exists" (149/149 refused, 2026-09-29). So the old campaign is
    # ended first and we wait for eBay to report it ENDED before adding.
    if a.end:
        if a.confirm:
            api_send("POST", f"/sell/marketing/v1/ad_campaign/{a.end}/end")
            status = ""
            for _ in range(24):
                status = api_send("GET", f"/sell/marketing/v1/ad_campaign/{a.end}"
                                  ).get("campaignStatus", "")
                if status == "ENDED":
                    break
                time.sleep(5)
            print(f"ended campaign {a.end} (status {status})")
        else:
            print(f"DRY — would POST /sell/marketing/v1/ad_campaign/{a.end}/end "
                  "(before adding)")

    already = campaign_listing_ids(api_send, cid) if cid else set()
    adds = to_add(seasoned, already)
    print(f"already in campaign: {len(already)} · to add: {len(adds)}")
    for lid in adds[:10]:
        v = listings[lid]
        print(f"  + {lid}  ${v['price']:.2f}  {v['created'][:10]}  {v['title'][:50]}")
    if len(adds) > 10:
        print(f"  … {len(adds) - 10} more")

    if adds:
        if a.confirm and cid:
            fails = 0
            for i in range(0, len(adds), 500):
                r = promote.add_ads(cid, adds[i:i + 500], True, creds=creds) or {}
                for x in r.get("responses") or []:
                    if int(x.get("statusCode") or 200) >= 300:
                        fails += 1
                        print(f"   FAILED {x.get('listingId')}: "
                              f"{(x.get('errors') or [{}])[0].get('message', '')[:80]}")
            print(f"added {len(adds) - fails}/{len(adds)}")
        else:
            print(f"DRY — would add {len(adds)} ad(s)"
                  + ("" if cid else " once the campaign exists"))

    if a.json:
        Path(a.json).write_text(json.dumps({
            "at": now.isoformat(), "campaign_id": cid, "seasoned": seasoned,
            "young": young, "added": adds if a.confirm else []}, indent=1),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
