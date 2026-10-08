"""AUTO-PROMOTE — a listing goes into the cost-per-sale campaign at publish.

No network: every call goes to a fake api_send, and config is passed in.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

import auto_promote as AP                                         # noqa: E402

CAMP = {"campaignId": "C1", "campaignName": "Revenue Seasoned 15d",
        "campaignStatus": "RUNNING", "fundingStrategy": {"fundingModel": "COST_PER_SALE"}}


def _fake(campaign=CAMP, add_response=None, raise_on=None):
    calls = []

    def send(method, path, body=None, **kw):
        calls.append((method, path, body))
        if raise_on and raise_on in path:
            raise RuntimeError("HTTP 500")
        if "get_campaign_by_name" in path:
            return campaign
        if path.endswith("bulk_create_ads_by_listing_id"):
            return add_response or {"responses": [{"listingId": body["requests"][0]["listingId"],
                                                   "statusCode": 201}]}
        return {}
    return send, calls


def _adds(calls):
    return [c for c in calls if c[1].endswith("bulk_create_ads_by_listing_id")]


def test_default_store_promotes_with_no_config():
    send, calls = _fake()
    msg = AP.promote_new_listing("111", "49.00", store="default", api_send=send, config={})
    assert msg == "promoted in 'Revenue Seasoned 15d'"
    assert _adds(calls)[0][1] == "/sell/marketing/v1/ad_campaign/C1/bulk_create_ads_by_listing_id"
    assert _adds(calls)[0][2] == {"requests": [{"listingId": "111"}]}


def test_below_floor_is_not_promoted_and_calls_nothing():
    send, calls = _fake()
    msg = AP.promote_new_listing("111", "19.99", store="default", api_send=send, config={})
    assert "under the $25.00 floor" in msg and not calls


def test_config_overrides_campaign_and_floor():
    cfg = {"store": {"auto_promote": {"campaign": "Other", "min_price": 10}}}
    send, calls = _fake()
    msg = AP.promote_new_listing("111", "12.00", store="default", api_send=send, config=cfg)
    assert msg == "promoted in 'Other'"
    assert "campaign_name=Other" in calls[0][1]


def test_disabled_calls_nothing():
    cfg = {"store": {"auto_promote": {"enabled": False}}}
    send, calls = _fake()
    assert AP.promote_new_listing("111", "99", store="default", api_send=send,
                                  config=cfg) == "auto-promote off for this store"
    assert not calls


def test_named_store_is_off_without_its_own_block(monkeypatch):
    """Campaigns are per eBay account: the default store's campaign name must
    never be used on another account."""
    import stores
    monkeypatch.setattr(stores, "resolve_store_name", lambda s=None: s or "default")
    cfg = {"store": {"auto_promote": {"campaign": "Revenue Seasoned 15d"}},
           "storefronts": {"junk": {}}}
    send, calls = _fake()
    assert AP.promote_new_listing("111", "99", store="junk", api_send=send,
                                  config=cfg) == "auto-promote off for this store"
    cfg["storefronts"]["junk"]["auto_promote"] = {"campaign": "Junk CPS", "min_price": 5}
    assert AP.promote_new_listing("111", "6", store="junk", api_send=send,
                                  config=cfg) == "promoted in 'Junk CPS'"


def test_campaign_not_running_is_reported_not_raised():
    send, calls = _fake(campaign={**CAMP, "campaignStatus": "PAUSED"})
    msg = AP.promote_new_listing("111", "99", store="default", api_send=send, config={})
    assert "PAUSED" in msg and not _adds(calls)


def test_never_adds_to_a_cost_per_click_campaign():
    cpc = {**CAMP, "fundingStrategy": {"fundingModel": "COST_PER_CLICK"}}
    send, calls = _fake(campaign=cpc)
    msg = AP.promote_new_listing("111", "99", store="default", api_send=send, config={})
    assert "not cost-per-sale" in msg and not _adds(calls)


def test_missing_campaign():
    send, _ = _fake(campaign={})
    assert "not found" in AP.promote_new_listing("111", "99", store="default",
                                                 api_send=send, config={})


def test_already_in_a_cps_campaign_is_fine():
    send, _ = _fake(add_response={"responses": [{"statusCode": 409, "errors": [
        {"message": "An ad for listing Id 111 already exists"}]}]})
    assert AP.promote_new_listing("111", "99", store="default", api_send=send,
                                  config={}) == "already has a cost-per-sale ad"


def test_api_error_never_raises():
    send, _ = _fake(raise_on="bulk_create")
    msg = AP.promote_new_listing("111", "99", store="default", api_send=send, config={})
    assert msg.startswith("not promoted:")
