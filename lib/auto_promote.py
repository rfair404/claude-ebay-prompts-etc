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

CONFIG (per store, #156)

    store:                       # the default store
      auto_promote:
        campaign: "Revenue Seasoned 15d"
        min_price: 25
        enabled: true

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

from typing import Optional
from urllib.parse import quote

DEFAULTS = {"enabled": True, "campaign": "Revenue Seasoned 15d", "min_price": 25.0}

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
    return out


def _price(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def promote_new_listing(listing_id: str, price, creds=None, *,
                        store: Optional[str] = None, api_send=None,
                        config: Optional[dict] = None) -> str:
    """Add `listing_id` to the store's auto-promote campaign. Returns a
    one-line outcome for the caller to print. Never raises."""
    try:
        if api_send is None:
            from ebay_client import api_send
        store = store if store is not None else getattr(creds, "store", None)
        s = settings(store, config)
        if not s["enabled"] or not s.get("campaign"):
            return "auto-promote off for this store"
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
