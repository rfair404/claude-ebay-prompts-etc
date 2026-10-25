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


# --- consignment exclusion (Russell, 2026-10-25: K01 is never promoted) ------

import csv                                                        # noqa: E402

import pytest                                                     # noqa: E402

K01_SKU = "decd0001"
K01_LID = "880000000001"


@pytest.fixture
def k01_lot(tmp_path, monkeypatch):
    """inventory/CONSIGN-20261022 for client K01: one row with a SKU and
    listing id, and its item folder kept elsewhere (as some K01 rows are)."""
    import consign
    inv = tmp_path / "inventory"
    lot = inv / "CONSIGN-20261022"
    (lot / "bin-01").mkdir(parents=True)
    (lot / "context.txt").write_text("client: K01\n", encoding="utf-8")
    with (lot / consign.LEDGER_NAME).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=consign.COLUMNS)
        w.writeheader()
        w.writerow({**{c: "" for c in consign.COLUMNS}, "item_id": "K01-C001",
                    "client_code": "K01", "sku": K01_SKU, "listing_id": K01_LID,
                    "inventory_folder": "inventory/ES/k01-vase", "state": "LISTED"})
    monkeypatch.setattr(consign, "INVENTORY", inv)
    return inv


def test_k01_sku_is_never_promoted_and_no_ad_is_sent(k01_lot):
    send, calls = _fake()
    msg = AP.promote_new_listing("999", "49.00", store="default", api_send=send,
                                 config={}, sku=K01_SKU)
    assert msg == "not promoted: consignment K01 excluded"
    assert not _adds(calls)
    assert all(m == "GET" for m, _, _ in calls)


def test_non_consignment_sku_is_still_promoted(k01_lot):
    send, calls = _fake()
    msg = AP.promote_new_listing("111", "49.00", store="default", api_send=send,
                                 config={}, sku="abc12300",
                                 folder=k01_lot / "BIN-7" / "teapot")
    assert msg == "promoted in 'Revenue Seasoned 15d'"
    assert _adds(calls)[0][2] == {"requests": [{"listingId": "111"}]}


def test_fresh_k01_draft_is_caught_by_its_folder_before_sync_copies_the_sku(k01_lot):
    """At publish the client ledger may not have the SKU yet; the draft's
    folder inside the K01 intake (or a row's inventory_folder) still finds it."""
    for folder in (k01_lot / "CONSIGN-20261022" / "bin-01", k01_lot / "ES" / "k01-vase"):
        send, calls = _fake()
        msg = AP.promote_new_listing("999", "49.00", store="default", api_send=send,
                                     config={}, sku="ffff0000", folder=folder)
        assert msg == "not promoted: consignment K01 excluded" and not _adds(calls)


def test_exclusion_is_config_driven(k01_lot):
    by_folder = {"store": {"auto_promote": {"exclude_consignment": ["CONSIGN-20261022"]}}}
    send, calls = _fake()
    assert AP.promote_new_listing("999", "49", store="default", api_send=send,
                                  config=by_folder, sku=K01_SKU).startswith(
        "not promoted: consignment K01 excluded")
    none = {"store": {"auto_promote": {"exclude_consignment": []}}}
    send, calls = _fake()
    assert AP.promote_new_listing("999", "49", store="default", api_send=send,
                                  config=none, sku=K01_SKU).startswith("promoted")


def test_fails_closed_when_the_consignment_ledgers_are_missing(tmp_path, monkeypatch):
    import consign
    monkeypatch.setattr(consign, "INVENTORY", tmp_path / "no-inventory")
    send, calls = _fake()
    msg = AP.promote_new_listing("111", "49", store="default", api_send=send,
                                 config={}, sku="abc12300")
    assert msg.startswith("not promoted: consignment ledgers not found") and not calls


def test_k01_listing_in_a_smart_campaign_gets_the_seller_hub_step(k01_lot):
    """The API can't remove an ad from a Smart campaign, so the outcome names
    the campaign and the manual step, and changes nothing on eBay."""
    smart = {"campaignId": "167549767014", "campaignName": "All Items $25-5000 CPC",
             "campaignStatus": "RUNNING", "campaignTargetingType": "SMART",
             "fundingStrategy": {"fundingModel": "COST_PER_CLICK"},
             "campaignCriterion": {"autoSelectFutureInventory": True, "selectionRules": [
                 {"minPrice": {"value": "25.0"}, "maxPrice": {"value": "5000.0"}}]}}
    calls = []

    def send(method, path, body=None, **kw):
        calls.append((method, path, body))
        return {"campaigns": [smart, CAMP]} if "campaign_status=RUNNING" in path else {}

    msg = AP.promote_new_listing("999", "49.00", store="default", api_send=send,
                                 config={}, sku=K01_SKU)
    assert msg.startswith("not promoted: consignment K01 excluded")
    assert "STILL PROMOTED: 'All Items $25-5000 CPC' auto-selects" in msg
    assert AP.SELLER_HUB_STOP in msg and "Revenue Seasoned" not in msg
    assert all(m == "GET" for m, _, _ in calls)

    assert "STILL PROMOTED" not in AP.promote_new_listing(
        "999", "19.00", store="default", api_send=send, config={}, sku=K01_SKU)


def test_bulk_add_drops_k01_listings(k01_lot, tmp_path, monkeypatch, capsys):
    """tools/promote.add_ads (promote.py and promote_seasoned.py) never sends a
    K01 listing, matched by its listing id or through the listings ledger SKU."""
    import config
    import ebay_client
    sys.path.insert(0, str(REPO / "tools"))
    import promote
    monkeypatch.setattr(config, "load_config", lambda: {})
    ledger = tmp_path / "listings_ledger.csv"
    ledger.write_text(f"sku,status,listing_id\n{K01_SKU},PUBLISHED,{K01_LID}\n"
                      f"{K01_SKU},PUBLISHED,880000000002\n", encoding="utf-8")
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(ledger))
    sent = []
    monkeypatch.setattr(ebay_client, "api_send",
                        lambda m, p, b=None, **k: sent.append(b) or {"responses": []})
    promote.add_ads("C1", [K01_LID, "880000000002", "111"], True)
    assert sent == [{"requests": [{"listingId": "111"}]}]
    assert "skip 880000000002: not promoted: consignment K01 excluded" in capsys.readouterr().out
