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
KEYED = {"campaignId": "K9", "campaignName": "All Items $25-5000 CPC keyed",
         "campaignStatus": "RUNNING", "campaignTargetingType": "SMART",
         "fundingStrategy": {"fundingModel": "COST_PER_CLICK"}}
NO_CPC = "; not in 'All Items $25-5000 CPC keyed': campaign not found"


def _fake(campaign=CAMP, add_response=None, raise_on=None, cpc=None):
    """`cpc` is what eBay returns for the keyed CPC campaign's name; None
    means it doesn't exist yet (404)."""
    calls = []

    def send(method, path, body=None, **kw):
        calls.append((method, path, body))
        if raise_on and raise_on in path:
            raise RuntimeError("HTTP 500")
        if "get_campaign_by_name" in path and "CPC%20keyed" in path:
            if cpc is None:
                raise RuntimeError("HTTP 404")
            return cpc
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
    assert msg == "promoted in 'Revenue Seasoned 15d'" + NO_CPC
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
    assert msg == "promoted in 'Other'" + NO_CPC
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
                                  config={}) == "already has a cost-per-sale ad" + NO_CPC


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
    assert msg == "promoted in 'Revenue Seasoned 15d'" + NO_CPC
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


# --- the key-based Smart CPC campaign (replaces the rule-based one) -------

def test_new_listing_also_goes_into_the_keyed_cpc_campaign():
    send, calls = _fake(cpc=KEYED)
    msg = AP.promote_new_listing("111", "49.00", store="default", api_send=send, config={})
    assert msg == ("promoted in 'Revenue Seasoned 15d'; "
                   "promoted in 'All Items $25-5000 CPC keyed'")
    assert [c[1] for c in _adds(calls)] == [
        "/sell/marketing/v1/ad_campaign/C1/bulk_create_ads_by_listing_id",
        "/sell/marketing/v1/ad_campaign/K9/bulk_create_ads_by_listing_id"]
    assert _adds(calls)[1][2] == {"requests": [{"listingId": "111"}]}   # no ad group


def test_k01_goes_into_neither_campaign(k01_lot):
    """2026-10-25, K01-C055 (206653565514) and 14 more K01 listings sat in the
    rule-based CPC campaign. The keyed campaign only gets what publish adds."""
    send, calls = _fake(cpc=KEYED)
    msg = AP.promote_new_listing("999", "49.00", store="default", api_send=send,
                                 config={}, sku=K01_SKU)
    assert msg.startswith("not promoted: consignment K01 excluded")
    assert not _adds(calls)


def test_keyed_cpc_keeps_its_price_band():
    send, calls = _fake(cpc=KEYED)
    msg = AP.promote_new_listing("111", "5995.00", store="default", api_send=send,
                                 config={})
    assert msg.endswith("not in 'All Items $25-5000 CPC keyed': "
                        "$5995.00 is over its $5000.00 ceiling")
    assert len(_adds(calls)) == 1                      # the cost-per-sale ad only
    send, calls = _fake(cpc=KEYED)
    AP.promote_new_listing("111", "19.99", store="default", api_send=send, config={})
    assert not calls                                   # under the shared floor


def test_keyed_cpc_refuses_a_rule_based_or_manual_campaign():
    rules = {**KEYED, "campaignCriterion": {"autoSelectFutureInventory": True}}
    manual = {**KEYED, "campaignTargetingType": "MANUAL"}
    for camp in (rules, manual):
        send, calls = _fake(cpc=camp)
        msg = AP.promote_new_listing("111", "49", store="default", api_send=send,
                                     config={})
        assert msg.endswith("not a key-based Smart cost-per-click campaign")
        assert len(_adds(calls)) == 1


def test_keyed_cpc_failure_never_hides_the_cost_per_sale_result():
    send, calls = _fake(cpc={**KEYED, "campaignStatus": "SCHEDULED"})
    msg = AP.promote_new_listing("111", "49", store="default", api_send=send, config={})
    assert msg == ("promoted in 'Revenue Seasoned 15d'; not in 'All Items $25-5000 "
                   "CPC keyed': it is SCHEDULED, not RUNNING")


def test_keyed_cpc_is_off_when_config_clears_it_and_never_inherited_by_a_named_store(
        monkeypatch):
    send, calls = _fake(cpc=KEYED)
    cfg = {"store": {"auto_promote": {"cpc_campaign": ""}}}
    assert AP.promote_new_listing("111", "49", store="default", api_send=send,
                                  config=cfg) == "promoted in 'Revenue Seasoned 15d'"
    import stores
    monkeypatch.setattr(stores, "resolve_store_name", lambda s=None: s or "default")
    junk = {"storefronts": {"junk": {"auto_promote": {"campaign": "Junk CPS"}}}}
    assert AP.settings("junk", junk)["cpc_campaign"] == ""


def test_copy_ads_moves_active_ads_minus_k01_and_reports_failures(k01_lot, tmp_path,
                                                                     monkeypatch, capsys):
    """tools/promote.py --copy-ads-from OLD --end OLD: the rule-based campaign's
    ACTIVE ads go into the keyed one by listing id with no ad group, K01 is
    dropped, and a refused ad makes it report failure (main then keeps OLD)."""
    import config
    import ebay_client
    sys.path.insert(0, str(REPO / "tools"))
    import promote
    monkeypatch.setattr(config, "load_config", lambda: {})
    ledger = tmp_path / "listings_ledger.csv"
    ledger.write_text("sku,status,listing_id\n", encoding="utf-8")
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(ledger))
    ads = [{"listingId": "111", "adStatus": "ACTIVE"},
           {"listingId": K01_LID, "adStatus": "ACTIVE"},
           {"listingId": "222", "adStatus": "ARCHIVED"}]
    sent = []

    def send(m, p, b=None, **k):
        sent.append((m, p, b))
        if p.endswith("/ad?limit=500&offset=0"):
            return {"ads": ads, "total": 3}
        if p.endswith("bulk_create_ads_by_listing_id"):
            return {"responses": [{"listingId": r["listingId"], "statusCode": 201}
                                  for r in b["requests"]]}
        return {}
    monkeypatch.setattr(ebay_client, "api_send", send)
    assert promote.copy_ads("OLD", "NEW", True) is True
    adds = [x for x in sent if x[1].endswith("bulk_create_ads_by_listing_id")]
    assert adds == [("POST", "/sell/marketing/v1/ad_campaign/NEW/bulk_create_ads_by_listing_id",
                     {"requests": [{"listingId": "111"}]})]
    assert "consignment K01 excluded" in capsys.readouterr().out

    def refuse(m, p, b=None, **k):
        r = send(m, p, b)
        if p.endswith("bulk_create_ads_by_listing_id"):
            return {"responses": [{"listingId": "111", "statusCode": 400}]}
        return r
    monkeypatch.setattr(ebay_client, "api_send", refuse)
    assert promote.copy_ads("OLD", "NEW", True) is False
