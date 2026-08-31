#!/usr/bin/env python3
"""lib/list_edit.py / lib/list_edit_group.py — the actual eBay money path
(build offer, publish it, sync a multi-variation group), tested offline
(GH #30 "Additional test coverage (resilience)" slice).

Until now this ~2,200-line module (and its variation-listing companion) had
NO dedicated tests, despite being the one place that builds every payload
eBay ever receives and the one place `publishOffer` is called. Covered here:

  1. Offer-build golden tests (_build_offer / _build_inventory_item / the
     list_edit_group builders) — a fixed set of inputs must produce an exact,
     reviewed payload shape. A silent change here ships wrong.
  2. Publish-payload golden tests (publish_offer / publish_group) — what
     actually gets POSTed to make a listing go live.
  3. Multi-variation constraints:
       - Best Offer + variations: eBay rejects Best Offer on any SKU in an
         inventory item group (error 25737). _variation_offer already never
         emits bestOfferTerms (structural safety, tested below) but
         validate_group() did not flag a draft that sets best_offer.enabled
         anyway — that setting would be silently dropped rather than
         applied, which reads as a bug to whoever set it. Fixed here (see
         list_edit_group.validate_group) and tested.
       - The canonical-SKU rule (hash of title+folder, "stick once stamped")
         — including its known limitation for an in-place MPN/title rename,
         documented rather than silently changed (see the docstring on
         test_sku_is_sticky_once_stamped_even_if_title_changes below).
  4. Idempotency: record_draft (the --record path) and create_or_update_listing
     (the --sync path) each write ONE ledger row per SKU no matter how many
     times they run, and a re-sync updates the existing eBay offer rather
     than creating a second one. publish_offer does not re-call publishOffer
     on an offer that is already PUBLISHED.

All HTTP is faked by monkeypatching `list_edit.api_send` / `list_edit_group.api_send`
(the house pattern already used in tests/test_sellable_guard.py) — no network,
no credentials, no writes outside a tmp dir (EBAYBIZ_LISTINGS_LEDGER is
redirected per-test).

Explicitly OUT of scope for this file (separate GH #30 bullets, left for
later slices): the Apify Stage B guard, the ledger_reconcile/sync_actuals
interaction regression test, schema/contract tests for terse tool outputs,
and the config/policy-guard test for stale policy ids.

Run:  python tests/test_list_edit.py
  or: pytest tests/test_list_edit.py
"""
import contextlib
import csv
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import list_edit as L                                          # noqa: E402
import list_edit_group as G                                    # noqa: E402
from draft_io import Draft                                     # noqa: E402
from ebay_client import EbayAPIError, EbayAuthError            # noqa: E402


class _Creds:
    has_user = True


# ---------------------------------------------------------------------------
# Shared fakes / helpers
# ---------------------------------------------------------------------------

def _fake_api(responses):
    """Same pattern as tests/test_sellable_guard.py: a dict of {substring in
    path: response-or-Exception}, first match wins; every call is recorded so
    idempotency tests can assert exactly how many writes happened."""
    calls = []

    def send(method, path, body=None, creds=None, **kw):
        calls.append((method, path, body))
        for pat, resp in responses.items():
            if pat in path:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return {}
    return send, calls


@contextlib.contextmanager
def _ledger_at(tmp_path):
    """Redirect the listings ledger to a throwaway CSV for the test's
    duration — never touch the repo's real listings_ledger.csv."""
    path = tmp_path / "listings_ledger.csv"
    prev = os.environ.get("EBAYBIZ_LISTINGS_LEDGER")
    os.environ["EBAYBIZ_LISTINGS_LEDGER"] = str(path)
    try:
        yield path
    finally:
        if prev is None:
            os.environ.pop("EBAYBIZ_LISTINGS_LEDGER", None)
        else:
            os.environ["EBAYBIZ_LISTINGS_LEDGER"] = prev


@contextlib.contextmanager
def _patched(module, **attrs):
    """Monkeypatch module attributes for the duration of the with-block, then
    restore whatever was there before (missing-not-set restores by delattr)."""
    sentinel = object()
    saved = {k: getattr(module, k, sentinel) for k in attrs}
    for k, v in attrs.items():
        setattr(module, k, v)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is sentinel:
                delattr(module, k)
            else:
                setattr(module, k, v)


def _ledger_rows(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


_BODY = ("A well-loved vintage widget from our collection, kept in a "
         "climate-controlled space and gently cared for over the years.")


def _write_single_draft(tmp: Path, *, sku="", offer_id="") -> Path:
    """A minimal, sync-ready single-item draft.md with one real (empty) photo
    file on disk, so validate_draft_for_sync passes."""
    shoot = tmp / "shoot"
    (shoot / "listing").mkdir(parents=True, exist_ok=True)
    (shoot / "listing" / "a.jpg").write_bytes(b"\xff\xd8\xff")  # jpeg magic, content irrelevant
    meta_sku_line = f'  ebay_inventory_sku: "{sku}"\n' if sku else ""
    meta_offer_line = f'  ebay_offer_id: "{offer_id}"\n' if offer_id else ""
    draft_path = shoot / "draft.md"
    draft_path.write_text(
        "---\n"
        'title: "Vintage Widget MPN-100"\n'
        'price: 24.99\n'
        "quantity: 1\n"
        'condition: "USED_GOOD"\n'
        'condition_description: "Light shelf wear."\n'
        "category_id: \"12345\"\n"
        "item_specifics:\n"
        '  type: "Widget"\n'
        "photos:\n"
        '  - "listing/a.jpg"\n'
        "shipping:\n"
        "  fulfillment_mode: SHIP\n"
        "meta:\n"
        + meta_sku_line
        + meta_offer_line +
        "---\n"
        + _BODY + "\n",
        encoding="utf-8",
    )
    return draft_path


def _write_group_draft(tmp: Path) -> Path:
    """A minimal, sync-ready multi-variation group draft with two variations,
    each with a real (empty) photo file on disk."""
    shoot = tmp / "group-shoot"
    (shoot / "listing").mkdir(parents=True, exist_ok=True)
    (shoot / "listing" / "v1.jpg").write_bytes(b"\xff\xd8\xff")
    (shoot / "listing" / "v2.jpg").write_bytes(b"\xff\xd8\xff")
    path = shoot / "draft_group.md"
    path.write_text(
        "---\n"
        "listing_kind: multi_variation\n"
        'title: "Vintage Widget Lot"\n'
        'category_id: "261680"\n'
        "varies_by: MPN\n"
        'condition: "USED_GOOD"\n'
        "variations:\n"
        '  - sku: "sku-mpn-100"\n'
        '    mpn: "MPN-100"\n'
        "    price: 19.99\n"
        "    quantity: 1\n"
        "    photos:\n"
        '      - "listing/v1.jpg"\n'
        '  - sku: "sku-mpn-200"\n'
        '    mpn: "MPN-200"\n'
        "    price: 29.99\n"
        "    quantity: 1\n"
        "    photos:\n"
        '      - "listing/v2.jpg"\n'
        "meta:\n"
        "---\n"
        + _BODY + "\n",
        encoding="utf-8",
    )
    return path


_POLICIES = {"fulfillment": "F-1", "payment": "P-1", "return": "R-1",
             "fulfillment_media": None, "fulfillment_local_pickup": None,
             "fulfillment_international": None, "payment_auction": None}


# ---------------------------------------------------------------------------
# 1) Offer-build golden tests
# ---------------------------------------------------------------------------

def test_build_offer_golden_fixed_price_with_best_offer():
    draft = Draft(path=Path("/fake/shoots/widget-100/draft.md"), frontmatter={
        "price": "199.00", "format": "fixed_price", "quantity": 2,
        "best_offer": {"enabled": True, "auto_decline_amount": "120.00",
                       "auto_accept_amount": "180.00"},
    }, body=_BODY)

    offer = L._build_offer(draft, sku="abc12300", category_id="12345",
                           location_key="LOC-1", policies=_POLICIES)

    assert offer == {
        "sku": "abc12300",
        "marketplaceId": "EBAY_US",
        "format": "FIXED_PRICE",
        "availableQuantity": 2,
        "categoryId": "12345",
        "listingDescription": L._body_to_html(_BODY),
        "merchantLocationKey": "LOC-1",
        "listingPolicies": {
            "fulfillmentPolicyId": "F-1",
            "paymentPolicyId": "P-1",
            "returnPolicyId": "R-1",
            "bestOfferTerms": {
                "bestOfferEnabled": True,
                "autoDeclinePrice": {"value": "120.00", "currency": "USD"},
                "autoAcceptPrice": {"value": "180.00", "currency": "USD"},
            },
        },
        "pricingSummary": {"price": {"value": "199.00", "currency": "USD"}},
    }


def test_build_offer_golden_no_best_offer_when_not_enabled():
    draft = Draft(path=Path("/fake/x/draft.md"),
                  frontmatter={"price": "10.00", "quantity": 1}, body=_BODY)
    offer = L._build_offer(draft, sku="s", category_id="1", location_key="L",
                           policies=_POLICIES)
    assert "bestOfferTerms" not in offer["listingPolicies"]


def test_build_offer_golden_auction_never_has_best_offer_or_quantity():
    draft = Draft(path=Path("/fake/x/draft.md"), frontmatter={
        "price": "5.00", "format": "AUCTION", "quantity": 3,
        "listing_duration": "days_10",
        "best_offer": {"enabled": True},   # AUCTION must ignore this entirely
    }, body=_BODY)
    pol = {**_POLICIES, "payment_auction": "PAY-AUCTION"}

    offer = L._build_offer(draft, sku="s", category_id="1", location_key="L",
                           policies=pol)

    assert offer["pricingSummary"] == {
        "auctionStartPrice": {"value": "5.00", "currency": "USD"}}
    assert offer["listingDuration"] == "DAYS_10"
    assert "availableQuantity" not in offer          # eBay rejects it for AUCTION
    assert "bestOfferTerms" not in offer["listingPolicies"]  # not permitted on auctions
    assert offer["listingPolicies"]["paymentPolicyId"] == "PAY-AUCTION"


def test_build_inventory_item_golden():
    draft = Draft(path=Path("/fake/x/draft.md"), frontmatter={
        "title": "Vintage Widget MPN-100",
        "condition": "USED_GOOD",
        "condition_description": "Light shelf wear.",
        "quantity": 2,
        "item_specifics": {"brand": "Acme", "type": "Widget", "upc": "012345678905",
                           "extra": {"MPN": "MPN-100"}},
        "shipping": {"weight": {"major_lb": 1, "minor_oz": 4},
                    "package_in": {"length": 10, "width": 6, "depth": 2}},
    }, body="# Heading\n\nSome **bold** body text.")

    item = L._build_inventory_item(draft, image_urls=["https://eps/a.jpg"])

    assert item == {
        "availability": {"shipToLocationAvailability": {"quantity": 2}},
        "condition": "USED_GOOD",
        "conditionDescription": "Light shelf wear.",
        "product": {
            "title": "Vintage Widget MPN-100",
            "description": "<h2>Heading</h2>\n<p>Some <strong>bold</strong> body text.</p>",
            "imageUrls": ["https://eps/a.jpg"],
            "aspects": {"Brand": ["Acme"], "Type": ["Widget"], "MPN": ["MPN-100"]},
            "upc": ["012345678905"],
        },
        "packageWeightAndSize": {
            "weight": {"value": 1.25, "unit": "POUND"},
            "dimensions": {"length": 10.0, "width": 6.0, "height": 2.0, "unit": "INCH"},
            "packageType": "PACKAGE_THICK_ENVELOPE",
        },
    }


def test_build_inventory_item_condition_description_dropped_for_new():
    draft = Draft(path=Path("/fake/x/draft.md"), frontmatter={
        "title": "New Widget", "condition": "NEW",
        "condition_description": "should be dropped for NEW", "quantity": 1,
    }, body=_BODY)
    item = L._build_inventory_item(draft, image_urls=[])
    assert "conditionDescription" not in item


# ---------------------------------------------------------------------------
# list_edit_group builders — offer-build golden tests, multi-variation shape
# ---------------------------------------------------------------------------

def test_group_sync_dry_run_golden_shape():
    """sync_group(dry_run=True) needs no creds/network at all — perfect for a
    golden test of the whole multi-variation build (items + group + offers)."""
    with tempfile.TemporaryDirectory() as td:
        path = _write_group_draft(Path(td))
        built = G.sync_group(path, creds=_Creds(), dry_run=True)

    assert built["variant_skus"] == ["sku-mpn-100", "sku-mpn-200"]
    assert built["group_key"] == path.parent.name

    item100 = built["items"][0]["sku-mpn-100"]
    assert item100["product"]["aspects"]["MPN"] == ["MPN-100"]
    assert item100["availability"]["shipToLocationAvailability"]["quantity"] == 1
    assert item100["product"]["imageUrls"] == ["EPS<v1.jpg>"]

    offer100 = built["offers"][0]["sku-mpn-100"]
    assert offer100["sku"] == "sku-mpn-100"
    assert offer100["pricingSummary"] == {"price": {"value": "19.99", "currency": "USD"}}
    assert offer100["categoryId"] == "261680"
    # eBay rejects Best Offer on any SKU in an inventory item group (25737) —
    # the variation offer builder must NEVER emit bestOfferTerms.
    assert "bestOfferTerms" not in offer100["listingPolicies"]

    group_body = built["group"][path.parent.name]
    assert group_body["variantSKUs"] == ["sku-mpn-100", "sku-mpn-200"]
    assert group_body["variesBy"] == {
        "aspectsImageVariesBy": ["MPN"],
        "specifications": [{"name": "MPN", "values": ["MPN-100", "MPN-200"]}],
    }


# ---------------------------------------------------------------------------
# 2) Publish-payload golden tests
# ---------------------------------------------------------------------------

def test_publish_offer_dry_run_makes_no_publish_call():
    send, calls = _fake_api({"/offer/OFFER-1": {
        "status": "UNPUBLISHED", "sku": "abc12300",
        "pricingSummary": {"price": {"value": "24.99"}},
    }})
    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td), sku="abc12300", offer_id="OFFER-1")
        with _patched(L, api_send=send), _ledger_at(Path(td)):
            res = L.publish_offer(draft_path, creds=_Creds(), confirm=False)

    assert res.dry_run is True
    assert res.price == "24.99"
    assert not [c for c in calls if c[0] == "POST"], "dry run must never publish"


def test_publish_offer_golden_publish_body_is_empty_and_updates_ledger():
    """The Sell API publishOffer call carries NO body — everything lives in
    the offerId already on eBay from --sync. That emptiness is itself the
    golden shape worth pinning: a future change that starts attaching fields
    here would be sending stale client-side data instead of trusting the
    already-synced offer."""
    send, calls = _fake_api({
        "/offer/OFFER-1/publish": {"listingId": "999888777"},
        "/offer/OFFER-1": {"status": "UNPUBLISHED", "sku": "abc12300",
                           "pricingSummary": {"price": {"value": "24.99"}}},
    })
    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td), sku="abc12300", offer_id="OFFER-1")
        with _patched(L, api_send=send), _ledger_at(Path(td)) as ledger:
            res = L.publish_offer(draft_path, creds=_Creds(), confirm=True)

            assert res.dry_run is False
            assert res.listing_id == "999888777"
            assert res.listing_url == "https://www.ebay.com/itm/999888777"
            publish_calls = [c for c in calls if c[1].endswith("/publish")]
            assert len(publish_calls) == 1
            assert publish_calls[0] == ("POST", "/sell/inventory/v1/offer/OFFER-1/publish", {})

            rows = _ledger_rows(ledger)
            assert len(rows) == 1
            assert rows[0]["sku"] == "abc12300"
            assert rows[0]["status"] == "PUBLISHED"
            assert rows[0]["listing_id"] == "999888777"


def test_publish_offer_is_idempotent_second_call_does_not_republish():
    """Calling publish twice on an offer that is already PUBLISHED must not
    fire a second publishOffer call, and must not duplicate the ledger row."""
    state = {"status": "UNPUBLISHED"}

    def send(method, path, body=None, creds=None, **kw):
        if path.endswith("/publish"):
            state["status"] = "PUBLISHED"
            return {"listingId": "999888777"}
        if "/offer/OFFER-1" in path:
            resp = {"status": state["status"], "sku": "abc12300",
                    "pricingSummary": {"price": {"value": "24.99"}}}
            if state["status"] == "PUBLISHED":
                resp["listing"] = {"listingId": "999888777"}
            return resp
        return {}

    calls = []
    def counting_send(*a, **kw):
        r = send(*a, **kw)
        calls.append((a[0], a[1]))
        return r

    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td), sku="abc12300", offer_id="OFFER-1")
        with _patched(L, api_send=counting_send), _ledger_at(Path(td)) as ledger:
            first = L.publish_offer(draft_path, creds=_Creds(), confirm=True)
            second = L.publish_offer(draft_path, creds=_Creds(), confirm=True)

            assert first.listing_id == second.listing_id == "999888777"
            publish_calls = [c for c in calls if c[1].endswith("/publish")]
            assert len(publish_calls) == 1, (
                "a second publish_offer() on an already-PUBLISHED offer must "
                f"not call publishOffer again: {calls}")

            rows = _ledger_rows(ledger)
            assert len(rows) == 1, "idempotent publish must not duplicate the ledger row"
            assert rows[0]["status"] == "PUBLISHED"


def test_publish_group_dry_run_golden_body():
    with tempfile.TemporaryDirectory() as td:
        path = _write_group_draft(Path(td))
        res = G.publish_group(path, creds=_Creds(), confirm=False)

    assert res == {
        "dry_run": True,
        "group_key": path.parent.name,
        "would_call": "POST /sell/inventory/v1/offer/publish_by_inventory_item_group",
        "body": {"inventoryItemGroupKey": path.parent.name, "marketplaceId": "EBAY_US"},
    }


def test_publish_group_golden_confirmed_body_and_response():
    send, calls = _fake_api({
        "/offer/publish_by_inventory_item_group": {"listingId": "555444333"},
    })
    with tempfile.TemporaryDirectory() as td:
        path = _write_group_draft(Path(td))
        with _patched(G, api_send=send):
            res = G.publish_group(path, creds=_Creds(), confirm=True)

    assert res["published"] is True
    assert res["listing_id"] == "555444333"
    publishes = [c for c in calls if "publish_by_inventory_item_group" in c[1]]
    assert len(publishes) == 1
    method, url, body = publishes[0]
    assert method == "POST"
    assert body == {"inventoryItemGroupKey": path.parent.name, "marketplaceId": "EBAY_US"}


# ---------------------------------------------------------------------------
# 3) Multi-variation constraints
# ---------------------------------------------------------------------------

def test_variation_offer_never_emits_best_offer_even_if_draft_enables_it():
    """Structural safety net: even before validate_group() rejects it, the
    variation offer builder itself is incapable of putting bestOfferTerms on
    a grouped SKU (eBay error 25737 if it ever did)."""
    draft = Draft(path=Path("/fake/g/draft_group.md"), frontmatter={
        "title": "Lot", "best_offer": {"enabled": True, "auto_decline_amount": "5.00"},
    }, body=_BODY)
    var = {"sku": "sku-1", "mpn": "MPN-1", "price": "9.99", "quantity": 1}
    offer = G._variation_offer(draft, var, category_id="1", location_key="L",
                               policies=_POLICIES)
    assert "bestOfferTerms" not in offer["listingPolicies"]
    assert offer["listingPolicies"] == {
        "fulfillmentPolicyId": "F-1", "paymentPolicyId": "P-1", "returnPolicyId": "R-1"}


def test_validate_group_rejects_best_offer_with_variations():
    """Real correctness gap found + fixed in this PR: validate_group() did not
    flag best_offer.enabled on a group draft. It has no effect (see the test
    above) but was silently dropped rather than applied — someone who set it
    expecting Best Offer to go live would never find out it didn't. Now it's
    a blocking validation issue instead."""
    with tempfile.TemporaryDirectory() as td:
        path = _write_group_draft(Path(td))
        text = path.read_text(encoding="utf-8")
        text = text.replace("varies_by: MPN\n",
                            "varies_by: MPN\nbest_offer:\n  enabled: true\n")
        path.write_text(text, encoding="utf-8")

        issues = G.validate_group(path)

    assert any("best offer" in i.lower() and "variation" in i.lower() for i in issues), issues


def test_validate_group_passes_without_best_offer():
    with tempfile.TemporaryDirectory() as td:
        path = _write_group_draft(Path(td))
        issues = G.validate_group(path)
    assert issues == []


def test_sku_is_sticky_once_stamped_even_if_title_changes():
    """The canonical SKU is a hash of (title, folder) computed ONCE and then
    stamped into meta.ebay_inventory_sku; _sku_for() prefers that stamped
    value over recomputing so a later cosmetic title fix does not spin up a
    duplicate listing (see the docstrings on _canonical_sku / _sku_for).

    This is deliberate for typo fixes, but it also means an MPN embedded in
    the title (as in this fixture's "Vintage Widget MPN-100") can be renamed
    to a DIFFERENT MPN after the SKU is stamped, and _sku_for() will still
    return the OLD sku — i.e. list_edit.py has no MPN-specific identity
    check, and an in-place MPN rename mutates the existing listing rather
    than minting a new SKU. Flagged as a pre-existing gap in the PR
    description rather than fixed here: teaching list_edit.py to recognize
    "the MPN changed -> this is a different item -> orphan the old SKU" is a
    real behavior change to the sync/ledger identity contract (with live-
    listing implications), and belongs in its own reviewed slice, not folded
    into a test-coverage PR. The multi-variation path is unaffected: there,
    SKUs are explicit per-variation values the human assigns in the draft,
    never derived from a hash, so an MPN rename only mutates a SKU in place
    if the human also keeps the same `sku:` value for the new MPN.
    """
    draft_v1 = Draft(path=Path("/fake/shoots/widget-100/draft.md"),
                     frontmatter={"title": "Vintage Widget MPN-100"}, body=_BODY)
    sku_before_stamping = L._sku_for(draft_v1)

    # Same folder, sku now stamped, title/MPN renamed post-sync.
    draft_renamed = Draft(
        path=Path("/fake/shoots/widget-100/draft.md"),
        frontmatter={"title": "Vintage Widget MPN-200",
                    "meta": {"ebay_inventory_sku": sku_before_stamping}},
        body=_BODY)

    assert L._sku_for(draft_renamed) == sku_before_stamping


def test_canonical_sku_differs_for_different_title_same_folder():
    """Before a SKU is stamped, the hash DOES key off the title — two distinct
    items that happen to share a folder name (shouldn't normally happen, but
    the hash is what actually protects against it) get distinct SKUs."""
    folder = Path("/fake/shoots/some-folder/draft.md")
    d1 = Draft(path=folder, frontmatter={"title": "Widget MPN-100"}, body=_BODY)
    d2 = Draft(path=folder, frontmatter={"title": "Widget MPN-200"}, body=_BODY)
    assert L._canonical_sku(d1) != L._canonical_sku(d2)


def test_canonical_sku_is_deterministic():
    folder = Path("/fake/shoots/some-folder/draft.md")
    d1 = Draft(path=folder, frontmatter={"title": "Widget MPN-100"}, body=_BODY)
    d2 = Draft(path=folder, frontmatter={"title": "Widget MPN-100"}, body=_BODY)
    assert L._canonical_sku(d1) == L._canonical_sku(d2)
    assert L._CANONICAL_SKU_RE.match(L._canonical_sku(d1))


# ---------------------------------------------------------------------------
# 4) Idempotency
# ---------------------------------------------------------------------------

def test_record_draft_twice_is_idempotent_single_ledger_row():
    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td))
        with _ledger_at(Path(td)) as ledger:
            sku1, _ = L.record_draft(draft_path)
            sku2, _ = L.record_draft(draft_path)

            assert sku1 == sku2
            rows = _ledger_rows(ledger)
            assert len(rows) == 1, f"expected exactly one ledger row, got {rows}"
            assert rows[0]["sku"] == sku1
            assert rows[0]["status"] == "DRAFTED"


def _patch_sync_collaborators():
    """Bypass everything create_or_update_listing() needs besides api_send
    and the ledger: config-derived policies, category metadata lookups, EPS
    photo upload and the PREP gate. None of that is this test's target —
    the target is the offer-build + SKU-lookup + ledger idempotency."""
    return _patched(
        L,
        _resolve_policies_and_location=lambda creds: (dict(_POLICIES), "LOC-1"),
        get_allowed_condition_ids=lambda *a, **kw: (set(), False),
        _resolve_shipping_policy=lambda draft, policies, creds, strict=False: (policies["fulfillment"], []),
        _assert_photos_cleared=lambda paths: None,
        upload_site_hosted_picture=lambda data, picture_name=None, creds=None: f"https://eps/{picture_name}.jpg",
    )


def test_create_or_update_listing_idempotent_second_call_updates_not_creates():
    responses = {
        "/offer?sku=": EbayAPIError(404, "no offer yet"),
    }
    calls = []

    def send(method, path, body=None, creds=None, **kw):
        calls.append((method, path, body))
        for pat, resp in responses.items():
            if pat in path:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        if method == "POST" and path.endswith("/offer"):
            return {"offerId": "OFFER-9"}
        return {}

    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td))
        with _patch_sync_collaborators(), _patched(L, api_send=send), _ledger_at(Path(td)) as ledger:
            first = L.create_or_update_listing(draft_path, creds=_Creds())
            assert first.operation == "created"
            sku = first.inventory_sku

            # From here on, the SKU has a real offer on "eBay" — flip the
            # lookup so the second call finds it and updates in place.
            responses["/offer?sku="] = {"offers": [{"offerId": "OFFER-9"}]}
            second = L.create_or_update_listing(draft_path, creds=_Creds())
            assert second.operation == "updated"
            assert second.offer_id == "OFFER-9"

            rows = _ledger_rows(ledger)
            assert len(rows) == 1, f"a re-sync of the same SKU must not add a ledger row: {rows}"
            assert rows[0]["sku"] == sku
            assert rows[0]["offer_id"] == "OFFER-9"

    creates = [c for c in calls if c[0] == "POST" and c[1].endswith("/offer")]
    updates = [c for c in calls if c[0] == "PUT" and "/offer/OFFER-9" in c[1]]
    assert len(creates) == 1, "exactly one createOffer across both syncs"
    assert len(updates) == 1, "the second sync must PUT (update) the existing offer"


# ---------------------------------------------------------------------------
# build_review_card() — estate context wiring (GH #46)
# ---------------------------------------------------------------------------

def test_build_review_card_shows_estate_context_and_flags_a_blocked_claim():
    """`_BODY` (the shared draft fixture) already says "climate-controlled
    space" — pairing it with an estate context.txt that records unclimatized
    storage should make the card show the block AND flag that the draft
    itself asserts the forbidden claim (defense-in-depth past DRAFT)."""
    import dir_context as DC
    DC.INVENTORY.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="dctx_", dir=str(DC.INVENTORY)))
    try:
        (tmp / "context.txt").write_text(
            "source: Frankie, estate, Greensboro NC\n"
            "storage: attic, unclimatized\n", encoding="utf-8")
        draft_path = _write_single_draft(tmp)
        with _patched(L, preflight_listing=lambda *a, **kw: ["category: 12345"],
                     resolve_draft_state=lambda *a, **kw:
                         {"stale": False, "offer_id": "", "meta_offer_id": ""}), \
             _ledger_at(tmp):
            card, _path = L.build_review_card(draft_path, creds=_Creds())

        assert "Context (estate background):" in card
        assert "climate-controlled" in card          # in MUST NOT CLAIM
        assert "MUST NOT CLAIM" in card
        assert any("draft asserts a claim the estate forbids" in ln
                  for ln in card.splitlines()), "the flag list must catch the leaked claim"
        assert "Frankie" not in card, "source: must never reach the card (PII guardrail)"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_review_card_context_section_is_quiet_with_no_context_txt():
    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td))
        with _patched(L, preflight_listing=lambda *a, **kw: ["category: 12345"],
                     resolve_draft_state=lambda *a, **kw:
                         {"stale": False, "offer_id": "", "meta_offer_id": ""}), \
             _ledger_at(Path(td)):
            card, _path = L.build_review_card(draft_path, creds=_Creds())

    assert "Context (estate background):" in card
    assert "no context.txt in chain" in card
    assert "estate forbids" not in card


# ---------------------------------------------------------------------------
# build_review_card() — ALL CLEAR banner (GH #100)
# ---------------------------------------------------------------------------

def test_build_review_card_shows_all_clear_when_every_section_is_clean():
    """GH #100: a fully clean item (no flags, PREP approved with a matching
    photo hash, no intl blockers, no stale meta) gets one banner line right
    under the header, so a clean approval is a fast read instead of a scan
    of every section to confirm there was nothing to see. This only changes
    what the card SHOWS — the explicit-approval command below is unchanged,
    and nothing here approves or publishes anything."""
    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td))
        shoot = draft_path.parent
        photo = shoot / "listing" / "a.jpg"
        digest = hashlib.sha256(photo.read_bytes()).hexdigest()
        prep_dir = shoot / ".prep"
        prep_dir.mkdir(parents=True, exist_ok=True)
        (prep_dir / "prep.json").write_text(json.dumps({
            "approved": True,
            "chosen_preset": "crisp",
            "photos": {"0": {"output": "listing/a.jpg", "out_sha256": digest}},
        }), encoding="utf-8")
        with _patched(L, preflight_listing=lambda *a, **kw: ["category: 12345"],
                     resolve_draft_state=lambda *a, **kw:
                         {"stale": False, "offer_id": "", "meta_offer_id": ""}), \
             _ledger_at(Path(td)):
            card, _path = L.build_review_card(draft_path, creds=_Creds())

    lines = card.splitlines()
    assert "ALL CLEAR" in lines[1], "banner must be the line right after the header"
    assert "→ Approve publishes this LIVE" in card, (
        "the banner must not replace or skip the explicit-approval instruction")


def test_build_review_card_withholds_all_clear_when_prep_is_unapproved():
    """The banner must never claim clean when PREP itself has no manifest at
    all for these photos -- claiming clarity nothing has actually verified
    defeats the point of a faster-but-still-honest signal."""
    with tempfile.TemporaryDirectory() as td:
        draft_path = _write_single_draft(Path(td))
        with _patched(L, preflight_listing=lambda *a, **kw: ["category: 12345"],
                     resolve_draft_state=lambda *a, **kw:
                         {"stale": False, "offer_id": "", "meta_offer_id": ""}), \
             _ledger_at(Path(td)):
            card, _path = L.build_review_card(draft_path, creds=_Creds())

    assert "ALL CLEAR" not in card, "no PREP manifest at all must not be reported clean"


def test_build_review_card_withholds_all_clear_when_something_is_flagged():
    """A flagged draft (estate context block, GH #46's fixture) must never
    show ALL CLEAR even if PREP/photos/intl are otherwise fine -- the banner
    is an AND of every section, not just the ones that happen to be checked
    first."""
    import dir_context as DC
    DC.INVENTORY.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="dctx_", dir=str(DC.INVENTORY)))
    try:
        (tmp / "context.txt").write_text(
            "source: Frankie, estate, Greensboro NC\n"
            "storage: attic, unclimatized\n", encoding="utf-8")
        draft_path = _write_single_draft(tmp)
        with _patched(L, preflight_listing=lambda *a, **kw: ["category: 12345"],
                     resolve_draft_state=lambda *a, **kw:
                         {"stale": False, "offer_id": "", "meta_offer_id": ""}), \
             _ledger_at(tmp):
            card, _path = L.build_review_card(draft_path, creds=_Creds())
        assert "ALL CLEAR" not in card
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
