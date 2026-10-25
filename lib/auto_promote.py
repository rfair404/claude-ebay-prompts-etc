"""auto_promote — put a listing into the cost-per-sale campaign the moment it
goes live.

`list_edit.publish_offer()` calls `promote_new_listing()` right after a
successful publish, so promotion is part of LIST rather than a separate job.
Measured 2026-10-07: the campaign that promoted every listing from day one
(Revenue Auto) sold 12 through ads in its last six days, and its replacement,
which waited 15 days per listing, sold one in eight. New listings are what
sell, so a new listing is promoted with no wait.

WHAT IT WILL AND WON'T DO

* Adds ONE ad (the new listing) to ONE named cost-per-sale campaign. It never
  creates, ends, pauses or re-rates a campaign, and never touches a
  cost-per-click campaign. A cost-per-sale ad costs nothing unless the item
  sells through it, which is why this may run unattended inside publish.
* Never fails a publish. The listing is already live when this runs; every
  problem (campaign missing or not RUNNING, eBay refusing the ad) is
  returned as a message for the caller to print, not raised.
* A listing priced below `min_price` is skipped.
* A consignment item whose lot is named in `exclude_consignment` is never
  promoted (Russell's rule, 2026-10-25: nothing from K01's lot). The lot is
  found the way tools/consign.py keeps it: a client_ledger.csv row with the
  SKU, listing id or inventory folder, or a folder inside a CONSIGN-* intake.
  If the consignment ledgers can't be found at all, nothing is promoted.
  tools/promote.py's `add_ads()` applies the same check to bulk adds.
  A campaign that picks listings by itself (a Smart cost-per-click campaign,
  an auto-select rule) can still promote it, and the API can't take it back
  out, so the outcome then names the campaign and the Seller Hub step.

CONFIG (per store, #156)

    store:                       # the default store
      auto_promote:
        campaign: "Revenue Seasoned 15d"
        min_price: 25
        enabled: true
        exclude_consignment: [K01]   # client codes or CONSIGN-* folder names

    storefronts:
      junk:
        auto_promote: {campaign: "...", min_price: 10}

The default store works with no config: it uses the DEFAULTS below. A named
store is OFF until its own `auto_promote` block names a campaign. Campaigns
belong to one eBay account, so the default store's campaign name means
nothing on another account, and this setting deliberately does not fall
through from `store:` the way storefront policy keys do.

Listings created by hand on eBay.com never pass through publish_offer.
`tools/promote_seasoned.py --min-age-days 0 --confirm` is the catch-up for
those: it adds every live listing at or above the floor that has no ad yet.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote

REPO = Path(__file__).resolve().parent.parent

# Named lots only: other consignment agreements may allow ads, and the ad fee
# comes out of the client's net (tools/consign.py), so excluding a lot is a
# per-client decision. Replacing the list in config replaces this default.
DEFAULTS = {"enabled": True, "campaign": "Revenue Seasoned 15d", "min_price": 25.0,
            "exclude_consignment": ["K01"]}

API = "/sell/marketing/v1"


def settings(store: Optional[str] = None, config: Optional[dict] = None) -> dict:
    """{enabled, campaign, min_price} for `store`. See the module docstring
    for why a named store never inherits the default store's block."""
    if config is None:
        from config import load_config
        config = load_config()
    from stores import resolve_store_name, is_default
    name = resolve_store_name(store)
    if is_default(name):
        block = ((config.get("store") or {}).get("auto_promote")) or {}
        out = {**DEFAULTS, **block}
    else:
        block = (((config.get("storefronts") or {}).get(name) or {})
                 .get("auto_promote")) or {}
        out = {**DEFAULTS, "enabled": bool(block.get("campaign")), **block}
    out["min_price"] = float(out.get("min_price") or 0)
    ex = out.get("exclude_consignment") or []
    out["exclude_consignment"] = [str(x).strip() for x in
                                  ([ex] if isinstance(ex, str) else ex) if str(x).strip()]
    return out


def _consign():
    tools = str(REPO / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import consign
    return consign


def _inventory_folder(folder) -> str:
    """`.../inventory/CONSIGN-x/bin-01` -> `inventory/CONSIGN-x/bin-01`, the
    shape client_ledger.csv's inventory_folder uses; '' outside inventory/."""
    parts = Path(folder).parts if folder else ()
    if "inventory" not in parts:
        return ""
    i = len(parts) - 1 - parts[::-1].index("inventory")
    return "/".join(parts[i:])


def consignment_excluded(s: dict, *, sku: str = "", listing_id: str = "",
                         folder=None) -> Optional[str]:
    """'consignment K01 excluded' when the item is in a lot that `s`'s
    `exclude_consignment` names (client code or CONSIGN-* folder), else None.
    Fails closed: with lots excluded and no consignment ledgers to read, it
    can't tell, so it says so instead of letting the ad through."""
    names = {n.upper() for n in s.get("exclude_consignment") or []}
    if not names:
        return None
    c = _consign()
    if not c.INVENTORY.is_dir():
        return f"consignment ledgers not found ({c.INVENTORY})"
    hit = c.find_item(sku=str(sku or ""), listing_id=str(listing_id or ""),
                      folder=_inventory_folder(folder))
    if hit and {hit[0].upper(), hit[1].upper()} & names:
        return f"consignment {hit[0] or hit[1]} excluded"
    return None


def excluded_listings(listing_ids: Iterable, store: Optional[str] = None,
                      config: Optional[dict] = None) -> dict[str, str]:
    """{listing_id: reason} for the ids that must not get an ad, for a bulk
    add (tools/promote.add_ads). A listing id is matched in the consignment
    ledgers directly and through its SKU in `store`'s listings ledger."""
    s = settings(store, config)
    if not s["exclude_consignment"]:
        return {}
    import sync_actuals
    sku_of = {str(r.get("listing_id") or ""): str(r.get("sku") or "")
              for r in sync_actuals.load_listings_ledger(store)}
    out = {}
    for lid in map(str, listing_ids):
        why = consignment_excluded(s, sku=sku_of.get(lid, ""), listing_id=lid)
        if why:
            out[lid] = why
    return out


def _price(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# eBay's API can't take an ad out of a Smart campaign (delete 400/36101,
# archive 409/35064, 2026-10-25), and the campaign's own rules are Russell's
# to change, so a listing one of them picks up is stopped by hand.
SELLER_HUB_STOP = ("Seller Hub > Active Listings > Actions > Edit promoted listing > "
                   "turn off the cost-per-click toggle > Stop promoting > Apply")


def _covers(rule: dict, price: Optional[float]) -> bool:
    lo = _price((rule.get("minPrice") or {}).get("value"))
    hi = _price((rule.get("maxPrice") or {}).get("value"))
    return price is None or ((lo is None or price >= lo) and (hi is None or price <= hi))


def auto_campaigns(listing_id: str, price, api_send, creds=None) -> list[str]:
    """What still promotes `listing_id` without us: each campaign that already
    has an ad for it, and each RUNNING campaign that picks listings by rule
    (Smart targeting or auto-select) at `price`. Read-only."""
    out, named = [], set()
    try:
        r = api_send("GET", f"{API}/ad_campaign/find_campaign_by_ad_reference"
                            f"?listing_id={quote(str(listing_id))}", creds=creds) or {}
    except Exception:                                                # noqa: BLE001
        r = {}                       # eBay answers 404 when no campaign has it
    for c in r.get("campaigns") or []:
        named.add(c.get("campaignName"))
        out.append(f"'{c.get('campaignName')}' has an ad for it")
    p = _price(price)
    r = api_send("GET", f"{API}/ad_campaign?campaign_status=RUNNING&limit=100",
                 creds=creds) or {}
    for c in r.get("campaigns") or []:
        crit = c.get("campaignCriterion") or {}
        if c.get("campaignName") in named or not (
                c.get("campaignTargetingType") == "SMART"
                or crit.get("autoSelectFutureInventory")):
            continue
        if any(_covers(x, p) for x in crit.get("selectionRules") or [{}]):
            out.append(f"'{c.get('campaignName')}' auto-selects listings at this price")
    return out


def _still_promoted(listing_id, price, api_send, creds) -> str:
    """The operator step to append to an excluded listing's outcome when a
    campaign we don't fill by id still promotes it; '' when none does."""
    try:
        found = auto_campaigns(listing_id, price, api_send, creds)
    except Exception as e:                                           # noqa: BLE001
        return f"; could not check for Smart campaigns ({str(e)[:80]})"
    if not found:
        return ""
    return (f"\n    STILL PROMOTED: {'; '.join(found)}. The API can't remove it. "
            f"Stop it by hand: {SELLER_HUB_STOP}")


def promote_new_listing(listing_id: str, price, creds=None, *,
                        store: Optional[str] = None, api_send=None,
                        config: Optional[dict] = None, sku: str = "",
                        folder=None) -> str:
    """Add `listing_id` to the store's auto-promote campaign. Returns a
    one-line outcome for the caller to print. Never raises. `sku` and
    `folder` (the draft's item folder) find a consignment lot."""
    try:
        if api_send is None:
            from ebay_client import api_send
        store = store if store is not None else getattr(creds, "store", None)
        s = settings(store, config)
        if not s["enabled"] or not s.get("campaign"):
            return "auto-promote off for this store"
        why = consignment_excluded(s, sku=sku, listing_id=listing_id, folder=folder)
        if why:
            return f"not promoted: {why}" + (_still_promoted(listing_id, price, api_send, creds)
                                             if why.endswith(" excluded") else "")
        p = _price(price)
        if p is not None and p < s["min_price"]:
            return f"not promoted: ${p:.2f} is under the ${s['min_price']:.2f} floor"

        camp = api_send("GET", f"{API}/ad_campaign/get_campaign_by_name"
                               f"?campaign_name={quote(s['campaign'])}", creds=creds)
        cid = str(camp.get("campaignId") or "")
        if not cid:
            return f"not promoted: campaign '{s['campaign']}' not found"
        if camp.get("campaignStatus") != "RUNNING":
            return (f"not promoted: campaign '{s['campaign']}' is "
                    f"{camp.get('campaignStatus')}, not RUNNING")
        if (camp.get("fundingStrategy") or {}).get("fundingModel") != "COST_PER_SALE":
            return (f"not promoted: '{s['campaign']}' is not cost-per-sale; "
                    f"auto-promote only adds to cost-per-sale campaigns")

        r = api_send("POST", f"{API}/ad_campaign/{cid}/bulk_create_ads_by_listing_id",
                     {"requests": [{"listingId": str(listing_id)}]}, creds=creds) or {}
        resp = (r.get("responses") or [{}])[0]
        code = int(resp.get("statusCode") or 201)
        if code < 300:
            return f"promoted in '{s['campaign']}'"
        msg = ((resp.get("errors") or [{}])[0].get("message") or "").strip()
        if "already exists" in msg.lower():
            # A listing can sit in only one cost-per-sale campaign; this may be
            # that campaign or another one, and either way it is promoted.
            return "already has a cost-per-sale ad"
        return f"not promoted: eBay refused the ad ({code}) {msg[:120]}"
    except Exception as e:                                           # noqa: BLE001
        return f"not promoted: {str(e)[:160]}"
