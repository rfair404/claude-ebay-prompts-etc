#!/usr/bin/env python3
"""lib/list_edit.py --convert-to-auction, and drafts whose photos are the
eBay-hosted URLs in meta.ebay_image_urls (tools/draft_rebuild.py drafts).

eBay is a small in-memory fake with offer state, so the tests check behaviour:
which calls go out, in what order, what the new offer says, what the draft
and ledger end up holding. No network, no credentials.
"""
import contextlib
import io
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import list_edit as L                                          # noqa: E402
from draft_io import parse_draft                               # noqa: E402
from ebay_client import EbayAPIError                           # noqa: E402

SKU = "6b10dfd6"
OLD_OFFER, OLD_LISTING = "269336432011", "206565326378"
LIVE_URLS = [
    "https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/kqwAAeSwdb9qrTs7/$_12.JPG?set_id=880000500F",
    "https://i.ebayimg.com/00/s/MTYwMFgxNjAw/z/B2QAAeSwYHRqrTs8/$_12.JPG?set_id=880000500F",
    "https://i.ebayimg.com/00/s/MTIwMFgxNjAw/z/fDQAAeSwjBpqrTs9/$_12.JPG?set_id=880000500F",
]
_POLICIES = {"fulfillment": "F-1", "payment": "P-1", "return": "R-1",
             "fulfillment_media": None, "fulfillment_local_pickup": None,
             "fulfillment_international": None, "payment_auction": "P-AUCTION"}


class _Creds:
    has_user = True
    store = "default"
    environment = "production"


class FakeEbay:
    """Offers by id; answers the Inventory API calls convert_to_auction makes."""

    def __init__(self, *, old_status="PUBLISHED", listing_status="ACTIVE", sold=0,
                 with_old=True, auction=None):
        self.calls = []
        self.offers = {}
        self.item = {"product": {"imageUrls": list(LIVE_URLS)},
                     "availability": {"shipToLocationAvailability": {"quantity": 1}}}
        if with_old:
            self.offers[OLD_OFFER] = {
                "offerId": OLD_OFFER, "sku": SKU, "format": "FIXED_PRICE",
                "status": old_status, "availableQuantity": 1,
                "pricingSummary": {"price": {"value": "195.00"}},
                "listing": {"listingId": OLD_LISTING, "listingStatus": listing_status,
                            "soldQuantity": sold}}
        if auction:
            self.offers[auction["offerId"]] = auction

    def writes(self):
        return [(m, p) for m, p, _b in self.calls if m != "GET"]

    def __call__(self, method, path, body=None, creds=None, **kw):
        self.calls.append((method, path, body))
        if method == "GET" and "/offer?sku=" in path:
            offers = [o for o in self.offers.values() if o["sku"] == SKU]
            if not offers:
                raise EbayAPIError(404, "no offers", None)
            return {"offers": offers}
        if path.endswith(f"/inventory_item/{SKU}"):
            if method == "PUT":
                self.item = body
            return self.item
        if method == "POST" and path.endswith("/withdraw"):
            o = self.offers[path.split("/")[-2]]
            o["status"] = "UNPUBLISHED"
            o["listing"]["listingStatus"] = "ENDED"
            return {}
        if method == "POST" and path.endswith("/publish"):
            o = self.offers[path.split("/")[-2]]
            o["status"] = "PUBLISHED"
            o["listing"] = {"listingId": "NEW-LISTING", "listingStatus": "ACTIVE"}
            return {"listingId": "NEW-LISTING"}
        if method == "POST" and path.endswith("/offer"):
            if body.get("sku") in {o["sku"] for o in self.offers.values()
                                   if o["format"] != body.get("format")}:
                raise EbayAPIError(400, "errorId 25758 format mismatch", None)
            self.offers["NEW-OFFER"] = {**body, "offerId": "NEW-OFFER", "status": "UNPUBLISHED"}
            return {"offerId": "NEW-OFFER"}
        if method == "DELETE" and "/offer/" in path:
            self.offers.pop(path.split("/")[-1])
            return {}
        if method == "PUT" and "/offer/" in path:
            oid = path.split("/")[-1]
            if self.offers[oid]["format"] != body.get("format"):
                raise EbayAPIError(400, "errorId 25758", None)
            self.offers[oid] = {**self.offers[oid], **body}
            return {}
        if method == "GET" and "/offer/" in path:
            return self.offers[path.split("/")[-1]]
        return {}


def _write_draft(tmp: Path, *, fmt="AUCTION", duration="DAYS_10", price="149.00",
                 listing_id=OLD_LISTING, offer_id=OLD_OFFER, urls=LIVE_URLS,
                 extra_meta="") -> Path:
    shoot = tmp / "rebuilt-6b10dfd6"
    shoot.mkdir(parents=True, exist_ok=True)
    url_lines = "".join(f'  - "{u}"\n' for u in urls)
    path = shoot / "draft.md"
    path.write_text(
        "---\n"
        'title: "1970 Masters Tournament Series Patron Badge Augusta National"\n'
        f'format: "{fmt}"\n'
        f'listing_duration: "{duration}"\n'
        f'price: "{price}"\n'
        "quantity: 1\n"
        'condition: "USED_EXCELLENT"\n'
        'category_id: "428"\n'
        "item_specifics:\n"
        '  type: "Series Admission Badge"\n'
        "best_offer:\n"
        "  enabled: false\n"
        "shipping:\n"
        "  fulfillment_mode: SHIP\n"
        "photos: []\n"
        "meta:\n"
        f'  ebay_inventory_sku: "{SKU}"\n'
        f'  ebay_offer_id: "{offer_id}"\n'
        f'  ebay_listing_id: "{listing_id}"\n'
        + extra_meta +
        "  ebay_image_urls:\n" + url_lines +
        "---\n"
        "An original 1970 Masters series badge, embossed plastic with its pin.\n",
        encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Patch everything around api_send: config policies, category lookups,
    consignment ledgers, auto-promote; ledger goes to a tmp CSV."""
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(tmp_path / "ledger.csv"))
    monkeypatch.setattr(L, "_resolve_policies_and_location",
                        lambda creds: (dict(_POLICIES), "LOC-1"))
    monkeypatch.setattr(L, "get_allowed_condition_ids", lambda *a, **kw: (set(), False))
    monkeypatch.setattr(L, "_conditionless_category_check", lambda *a, **kw: None)
    monkeypatch.setattr(L, "_resolve_shipping_policy",
                        lambda draft, policies, creds, strict=False: (policies["fulfillment"], []))
    monkeypatch.setattr(L, "_consignment_of", lambda sku, lid, folder: None)

    def no_upload(*a, **kw):
        raise AssertionError("eBay-hosted photos must not be re-uploaded")
    monkeypatch.setattr(L, "upload_site_hosted_picture", no_upload)
    import auto_promote
    monkeypatch.setattr(auto_promote, "promote_new_listing",
                        lambda *a, **kw: "added to campaign (fake)")
    return tmp_path


def _go(monkeypatch, fake, draft, **kw):
    monkeypatch.setattr(L, "api_send", fake)
    return L.convert_to_auction(draft, creds=_Creds(), **kw)


# --- dry run -----------------------------------------------------------------

def test_dry_run_sends_only_gets_and_writes_nothing(env, monkeypatch):
    draft = _write_draft(env)
    before = draft.read_bytes()
    fake = FakeEbay()
    res = _go(monkeypatch, fake, draft)
    assert res.dry_run
    assert fake.writes() == []
    assert draft.read_bytes() == before
    assert not (env / "ledger.csv").exists()
    assert [s.split()[0] for s in res.steps] == ["end", "delete", "create", "publish"]
    assert any("3 eBay-hosted photo(s) kept" in n for n in res.notes)


# --- the full conversion -----------------------------------------------------

def test_confirm_ends_deletes_creates_auction_publishes_and_verifies(env, monkeypatch):
    draft = _write_draft(env)
    fake = FakeEbay()
    res = _go(monkeypatch, fake, draft, confirm=True)

    w = fake.writes()
    order = [(m, p.rsplit("/", 2)[-2] + "/" + p.rsplit("/", 1)[-1]) for m, p in w]
    assert order == [
        ("POST", f"{OLD_OFFER}/withdraw"),
        ("DELETE", f"offer/{OLD_OFFER}"),
        ("PUT", f"inventory_item/{SKU}"),
        ("POST", "v1/offer"),
        ("POST", "NEW-OFFER/publish"),
    ]
    body = next(b for m, p, b in fake.calls if m == "POST" and p.endswith("/offer"))
    assert body["format"] == "AUCTION"
    assert body["pricingSummary"] == {"auctionStartPrice": {"value": "149.00", "currency": "USD"}}
    assert body["listingDuration"] == "DAYS_10"
    assert "bestOfferTerms" not in body["listingPolicies"]
    assert body["listingPolicies"]["paymentPolicyId"] == "P-AUCTION"
    # photos: the live item's own URLs, same order, hero first
    assert fake.item["product"]["imageUrls"] == LIVE_URLS

    assert res.verify_issues == []
    assert res.listing_id == "NEW-LISTING"
    d = parse_draft(draft)
    assert d.get("meta.ebay_offer_id") == "NEW-OFFER"
    assert d.get("meta.ebay_listing_id") == "NEW-LISTING"
    assert d.get("meta.converted_from_listing_id") == OLD_LISTING
    ledger = (env / "ledger.csv").read_text(encoding="utf-8")
    assert "NEW-LISTING" in ledger and "PUBLISHED" in ledger


def test_read_back_mismatch_is_reported(env, monkeypatch):
    draft = _write_draft(env)
    fake = FakeEbay()
    real = fake.__call__

    def lying(method, path, body=None, creds=None, **kw):
        out = real(method, path, body, creds, **kw)
        if method == "GET" and path.endswith("/offer/NEW-OFFER"):
            out = {**out, "listingDuration": "DAYS_7"}
        return out
    monkeypatch.setattr(L, "api_send", lying)
    res = L.convert_to_auction(draft, creds=_Creds(), confirm=True)
    assert any("listingDuration" in v for v in res.verify_issues)


# --- resume after a partial run ---------------------------------------------

def test_rerun_after_delete_only_creates_and_publishes(env, monkeypatch):
    draft = _write_draft(env, listing_id="", offer_id="",
                         extra_meta=f'  converted_from_listing_id: "{OLD_LISTING}"\n')
    fake = FakeEbay(with_old=False)
    res = _go(monkeypatch, fake, draft, confirm=True)
    kinds = [(m, p.rsplit("/", 1)[-1]) for m, p in fake.writes()]
    assert ("POST", "withdraw") not in kinds and not any(m == "DELETE" for m, _ in kinds)
    assert ("POST", "publish") in kinds
    assert res.verify_issues == []


def test_rerun_after_withdraw_deletes_without_withdrawing_again(env, monkeypatch):
    draft = _write_draft(env, listing_id="",
                         extra_meta=f'  converted_from_listing_id: "{OLD_LISTING}"\n')
    fake = FakeEbay(old_status="UNPUBLISHED", listing_status="ENDED")
    res = _go(monkeypatch, fake, draft, confirm=True)
    paths = [p for _m, p in fake.writes()]
    assert not any(p.endswith("/withdraw") for p in paths)
    assert any(m == "DELETE" for m, _p in fake.writes())
    assert res.verify_issues == []


def test_already_converted_is_verified_not_touched(env, monkeypatch):
    auction = {"offerId": "NEW-OFFER", "sku": SKU, "format": "AUCTION", "status": "PUBLISHED",
               "listingDuration": "DAYS_10",
               "pricingSummary": {"auctionStartPrice": {"value": "149.00"}},
               "listing": {"listingId": "NEW-LISTING", "listingStatus": "ACTIVE"}}
    draft = _write_draft(env, listing_id="NEW-LISTING", offer_id="NEW-OFFER")
    fake = FakeEbay(with_old=False, auction=auction)
    res = _go(monkeypatch, fake, draft, confirm=True)
    assert fake.writes() == []
    assert res.verify_issues == []


# --- refusals: nothing is written ---------------------------------------------

@pytest.mark.parametrize("kw, fake_kw, msg", [
    (dict(fmt="FIXED_PRICE"), {}, "format"),
    (dict(duration="DAYS_30"), {}, "listing_duration"),
    ({}, dict(sold=1), "sold"),
    ({}, dict(listing_status="OUT_OF_STOCK"), "OUT_OF_STOCK"),
    (dict(listing_id="999"), {}, "repair-meta"),
    ({}, dict(old_status="UNPUBLISHED"), "nothing live"),
    ({}, dict(with_old=False), "nothing live"),
])
def test_refusals_write_nothing(env, monkeypatch, kw, fake_kw, msg):
    draft = _write_draft(env, **kw)
    before = draft.read_bytes()
    fake = FakeEbay(**fake_kw)
    with pytest.raises(ValueError, match=msg):
        _go(monkeypatch, fake, draft, confirm=True)
    assert fake.writes() == []
    assert draft.read_bytes() == before


def test_consignment_refused_without_flag_and_allowed_with_it(env, monkeypatch):
    monkeypatch.setattr(L, "_consignment_of", lambda sku, lid, folder: "consignment K01 (CONSIGN-K01)")
    draft = _write_draft(env)
    fake = FakeEbay()
    with pytest.raises(ValueError, match="allow-consignment"):
        _go(monkeypatch, fake, draft, confirm=True)
    assert fake.writes() == []
    res = _go(monkeypatch, FakeEbay(), draft, allow_consignment=True)
    assert res.dry_run and any("K01" in n for n in res.notes)


def test_consignment_check_fails_closed_without_ledgers(tmp_path, monkeypatch):
    import auto_promote
    consign = auto_promote._consign()
    monkeypatch.setattr(consign, "INVENTORY", tmp_path / "missing")
    assert "can't rule it out" in L._consignment_of(SKU, OLD_LISTING, tmp_path)


def test_missing_no_immediate_pay_policy_refuses_before_ending(env, monkeypatch):
    monkeypatch.setattr(L, "_resolve_policies_and_location",
                        lambda creds: ({**_POLICIES, "payment_auction": None}, "LOC-1"))
    draft = _write_draft(env)
    fake = FakeEbay()
    with pytest.raises(ValueError, match="25003"):
        _go(monkeypatch, fake, draft, confirm=True)
    assert fake.writes() == []


# --- CLI -----------------------------------------------------------------------

def test_cli_dry_run_prints_the_plan_and_sends_nothing(env, monkeypatch):
    draft = _write_draft(env)
    fake = FakeEbay()
    monkeypatch.setattr(L, "api_send", fake)
    monkeypatch.setattr(L, "load_credentials", lambda store=None: _Creds())
    monkeypatch.setattr(sys, "argv", ["list_edit.py", "--convert-to-auction", str(draft)])
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        L._cli()
    assert "[DRY RUN]" in out.getvalue() and "--confirm" in out.getvalue()
    assert fake.writes() == []


# --- Blocker A: eBay-hosted photos on the --list/--sync path -----------------

def test_validate_accepts_ebay_hosted_photos_without_local_files(env):
    assert L.validate_draft_for_sync(_write_draft(env)) == []


def test_validate_refuses_photos_not_on_ebay(env):
    draft = _write_draft(env, urls=["https://example.com/a.jpg"])
    assert any("not eBay-hosted" in i for i in L.validate_draft_for_sync(draft))


def test_validate_still_refuses_a_draft_with_no_photos_at_all(env):
    draft = _write_draft(env, urls=[])
    draft.write_text(draft.read_text().replace("  ebay_image_urls:\n", ""))
    assert "photos: none listed" in L.validate_draft_for_sync(draft)


def test_sync_reuses_hosted_urls_in_order_without_upload(env, monkeypatch):
    """The --list/--sync path for a rebuilt FIXED_PRICE draft: the inventory
    item gets meta.ebay_image_urls as they are, hero first, no EPS upload."""
    draft = _write_draft(env, fmt="FIXED_PRICE")
    fake = FakeEbay()
    monkeypatch.setattr(L, "api_send", fake)
    res = L.create_or_update_listing(draft, creds=_Creds())
    assert res.photo_eps_urls == LIVE_URLS
    assert fake.item["product"]["imageUrls"] == LIVE_URLS
