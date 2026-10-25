#!/usr/bin/env python3
"""tools/draft_rebuild.py and `list_edit --update --dry-run`.

The rebuild exists because the live listings lost their drafts in the Linux
migration, and every field-scoped edit reads from a draft. Two properties
matter more than the rest:

  1. A rebuilt draft pushed straight back is a no-op. Aspects, the
     description and Best Offer must come back out of `_build_aspects` /
     `_body_to_html` / `_best_offer_terms` the way eBay holds them, or the
     first `--fields aspects` push silently rewrites the listing.
  2. A rebuilt draft has no local photos, and `--fields photos` with an
     empty list would PUT `imageUrls: []` onto a live listing.

Run:  python tests/test_draft_rebuild.py
  or: pytest tests/test_draft_rebuild.py
"""
import csv
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "tools"))

import list_edit as L                                        # noqa: E402
import draft_rebuild as R                                    # noqa: E402
from draft_io import parse_draft                             # noqa: E402


class _Creds:
    has_user = True
    store = "default"
    environment = "sandbox"


def _not_held(listing_id, creds=None):
    """Stand-in for the Trading GetItem hold check: the listing is not on hold."""
    return None


BODY_MD = """Vintage 1847 Rogers Bros Reflection pierced serving spoon, new old stock.

It still has the factory sleeve, and the **pierced bowl** catches the light.

## What's Included

- One pierced serving spoon
- Original sleeve, sealed, with the 1847 Rogers label and a long line that
wraps onto a second line

## Condition

Unused. See [my other listings](https://www.ebay.com/str/popsgames).
"""

LIVE_HTML = L._body_to_html(BODY_MD)

ITEM = {
    "sku": "5a568d66",
    "availability": {"shipToLocationAvailability": {"quantity": 1}},
    "condition": "NEW_OTHER",
    "conditionDescription": "Sealed in original sleeve.",
    "packageWeightAndSize": {"weight": {"value": 0.5, "unit": "POUND"},
                             "dimensions": {"length": 12, "width": 4, "height": 2, "unit": "INCH"},
                             "packageType": "PACKAGE_THICK_ENVELOPE"},
    "product": {
        "title": "1847 Rogers Bros Reflection Silverplate Pierced Serving Spoon NOS Sealed",
        "description": LIVE_HTML,
        "imageUrls": ["https://i.ebayimg.com/a.jpg", "https://i.ebayimg.com/b.jpg"],
        "aspects": {"Brand": ["1847 Rogers Bros."], "Pattern": ["Reflection"],
                    "Type": ["Serving Spoon"], "Material": ["Silverplate", "Brass"],
                    "Composition": ["Silverplate"]},
    },
}
OFFER = {
    "offerId": "111", "sku": "5a568d66", "status": "PUBLISHED", "format": "FIXED_PRICE",
    "categoryId": "177010", "availableQuantity": 1,
    "listingDescription": LIVE_HTML,
    "pricingSummary": {"price": {"value": "17.99", "currency": "USD"}},
    "listingPolicies": {"fulfillmentPolicyId": "F1",
                        "bestOfferTerms": {"bestOfferEnabled": True,
                                           "autoDeclinePrice": {"value": "12.00", "currency": "USD"}}},
    "listing": {"listingId": "206454286267", "listingStatus": "ACTIVE"},
}


def _patch(obj, **attrs):
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    return lambda: [setattr(obj, k, v) for k, v in old.items()]


def _fake_api(responses):
    calls = []

    def send(method, path, body=None, creds=None, **kw):
        calls.append((method, path, body))
        for pat, resp in responses.items():
            if pat in path:
                return resp
        return {}
    return send, calls


def _rebuilt(tmp: Path) -> Path:
    undo = _patch(L, _ebay_extra=lambda f, store=None: {"fulfillment_policy_id": "F1"}.get(f))
    try:
        fm, body, warns = R.build_draft(ITEM, OFFER, "5a568d66", "default")
    finally:
        undo()
    d = tmp / "5a568d66"
    d.mkdir(parents=True)
    (d / "draft.md").write_text(R.render_draft(fm, body), encoding="utf-8")
    return d / "draft.md"


# ---------------------------------------------------------------------------
# Rebuild: eBay -> draft -> eBay is a no-op
# ---------------------------------------------------------------------------

def test_description_html_round_trips():
    md = R.html_to_md(LIVE_HTML)
    assert R.description_roundtrips(LIVE_HTML, md), md


def test_foreign_html_is_flagged_not_trusted():
    src = '<div style="color:red"><table><tr><td>Hand-made</td></tr></table></div>'
    md = R.html_to_md(src)
    assert "Hand-made" in md
    assert not R.description_roundtrips(src, md)


def test_rebuilt_draft_pushes_back_unchanged():
    with tempfile.TemporaryDirectory() as td:
        d = parse_draft(_rebuilt(Path(td)))
    assert L._build_aspects(d) == ITEM["product"]["aspects"]
    assert L._body_to_html(d.body) == LIVE_HTML
    assert d.get("price") == "17.99" and d.get("condition") == "NEW_OTHER"
    assert d.get("category_id") == "177010"
    terms = L._best_offer_terms(d, "17.99")
    assert terms and terms["autoDeclinePrice"]["value"] == "12.00", terms
    assert d.get("shipping.weight.minor_oz") == 8
    assert d.get("shipping.package_in.depth") == 2.0
    assert d.get("meta.ebay_listing_id") == "206454286267"
    assert d.get("meta.description_roundtrip") is True
    assert d.get("photos") == []
    assert d.get("meta.ebay_image_urls") == ITEM["product"]["imageUrls"]
    assert L._sku_for(d) == "5a568d66"
    assert d.get("_field_constraints"), "the validator reads this block"


def test_unknown_fulfillment_policy_is_flagged():
    undo = _patch(L, _ebay_extra=lambda f, store=None: {"fulfillment_policy_id": "OTHER"}.get(f))
    try:
        _fm, _b, warns = R.build_draft(ITEM, OFFER, "5a568d66", "default")
    finally:
        undo()
    assert any("fulfillment policy F1" in w for w in warns), warns


def test_non_canonical_sku_is_refused():
    status, msg = R.rebuild("ebaybiz-sand-dollars", _Creds(), "default",
                            ledger=[], existing={})
    assert status == "error" and "canonical" in msg


def test_existing_real_draft_is_never_overwritten():
    status, msg = R.rebuild("5a568d66", _Creds(), "default", ledger=[], force=True,
                            existing={"5a568d66": ROOT / "inventory" / "shoot" / "draft.md"})
    assert status == "skipped", (status, msg)


# ---------------------------------------------------------------------------
# --update --dry-run, and the empty-photos guard
# ---------------------------------------------------------------------------

def _live_api():
    import copy
    return _fake_api({"/offer?sku=": {"offers": [OFFER]},
                      "/inventory_item/": copy.deepcopy(ITEM),
                      "/offer/111": copy.deepcopy(OFFER)})


def test_dry_run_writes_nothing_and_reports_the_diff():
    send, calls = _live_api()
    undo = _patch(L, get_listing_hold=_not_held, api_send=send, get_allowed_condition_ids=lambda *a, **k: (set(), None))
    try:
        with tempfile.TemporaryDirectory() as td:
            path = _rebuilt(Path(td))
            text = path.read_text(encoding="utf-8")
            text = text.replace('pattern: "Reflection"', 'pattern: "Reflection Pierced"')
            text = text.replace('price: "17.99"', 'price: "15.99"')
            path.write_text(text, encoding="utf-8")
            plan: list = []
            changed = L.update_listing_fields(path, ["aspects", "price"], creds=_Creds(),
                                              dry_run=True, plan=plan)
            assert "last_synced: null" in path.read_text(encoding="utf-8"), \
                "a dry run must not stamp last_synced"
    finally:
        undo()
    assert not [c for c in calls if c[0] != "GET"], f"dry run wrote: {calls}"
    assert set(changed) == {"aspects", "price"}
    diffs = {w["path"]: L.payload_diff(w["before"], w["after"]) for w in plan}
    item_diff = diffs["/sell/inventory/v1/inventory_item/5a568d66"]
    assert item_diff == ["product.aspects.Pattern: ['Reflection'] -> ['Reflection Pierced']"], item_diff
    offer_diff = diffs["/sell/inventory/v1/offer/111"]
    assert any("pricingSummary.price.value: '17.99' -> '15.99'" in x for x in offer_diff), offer_diff


def test_unchanged_rebuilt_draft_diffs_clean():
    send, _ = _live_api()
    undo = _patch(L, get_listing_hold=_not_held, api_send=send, get_allowed_condition_ids=lambda *a, **k: (set(), None))
    try:
        with tempfile.TemporaryDirectory() as td:
            plan: list = []
            L.update_listing_fields(_rebuilt(Path(td)),
                                    ["aspects", "title", "description", "condition", "price",
                                     "bestoffer"], creds=_Creds(), dry_run=True, plan=plan)
    finally:
        undo()
    for w in plan:
        assert L.payload_diff(w["before"], w["after"]) == [], (w["path"], L.payload_diff(w["before"], w["after"]))


def test_empty_photos_never_reach_a_live_listing():
    for dry in (True, False):
        send, calls = _live_api()
        undo = _patch(L, get_listing_hold=_not_held, api_send=send)
        try:
            with tempfile.TemporaryDirectory() as td:
                try:
                    L.update_listing_fields(_rebuilt(Path(td)), ["photos"], creds=_Creds(),
                                            dry_run=dry)
                    raise AssertionError("an empty photos list must be refused")
                except ValueError as e:
                    assert "no local photos" in str(e)
        finally:
            undo()
        assert not [c for c in calls if c[0] == "PUT"], calls


def test_diff_ignores_unpadded_prices():
    """eBay echoes 14.0 for a floor we sent as 14.00 — not a change."""
    before = {"listingPolicies": {"bestOfferTerms": {"autoDeclinePrice": {"value": "14.0"}}}}
    after = {"listingPolicies": {"bestOfferTerms": {"autoDeclinePrice": {"value": "14.00"}}}}
    assert L.payload_diff(before, after) == []
    after["listingPolicies"]["bestOfferTerms"]["autoDeclinePrice"]["value"] = "13.00"
    assert len(L.payload_diff(before, after)) == 1


def test_multi_value_aspects_survive():
    class D:
        frontmatter = {"item_specifics": {"material": ["Silverplate", "Brass"],
                                          "extra": {"Features": ["Boxed", " "], "Era": "1960s"}}}
    assert L._build_aspects(D()) == {"Material": ["Silverplate", "Brass"],
                                     "Features": ["Boxed"], "Era": ["1960s"]}


# ---------------------------------------------------------------------------
# A rebuilt draft, moved into a lot folder, reads back through every reader.
# safe_dump single-quoted price / listing id / all-digit SKU, and every regex
# reader below expects double quotes: sync_actuals' listing-id match,
# live_audit's drift check and report.collect's ledger merge all missed
# rebuilt drafts without a word.
# ---------------------------------------------------------------------------

import live_audit as LA                                      # noqa: E402
import report as RP                                          # noqa: E402
import stores                                                # noqa: E402
import sync_actuals as SA                                    # noqa: E402

LOT_SKU, LOT_PRICE, LOT_LID = "56606948", "77.00", "206455986454"
LOT_TITLES = [
    pytest.param("Kutani Hand Painted Porcelain Teapot Red Gold", id="plain"),
    pytest.param("Pop's Kutani Teapot 'Thousand Faces' Red", id="single-quote"),
    pytest.param("Kutani Teapot: Thousand Faces, Red: Gold", id="colon-space"),
]
INCH_TITLE = 'Rhinestone Christmas Tree Brooch 2" Gold Tone'   # 8 of the 105 today


def _isolate(root, monkeypatch):
    """Every reader's inventory/, repo root and ledger path under `root`."""
    monkeypatch.setattr(stores, "REPO", root)
    monkeypatch.setattr(stores, "load_config", lambda: {})
    for var in ("EBAYBIZ_STORE", "EBAYBIZ_LISTINGS_LEDGER", "EBAYBIZ_LISTINGS_LOG"):
        monkeypatch.delenv(var, raising=False)
    for mod in (SA, LA, RP):
        monkeypatch.setattr(mod, "REPO", root)
        monkeypatch.setattr(mod, "INVENTORY", root / "inventory")
    monkeypatch.setattr(R, "REPO", root)


def _lot_draft(root, monkeypatch, title) -> Path:
    """build_draft + render_draft for a live listing, written where the
    rebuilt drafts were moved to: a lot leaf, not inventory/_rebuilt/."""
    import copy
    item, offer = copy.deepcopy(ITEM), copy.deepcopy(OFFER)
    item["sku"] = offer["sku"] = LOT_SKU
    item["product"]["title"] = title
    offer["pricingSummary"]["price"]["value"] = LOT_PRICE
    offer["listing"]["listingId"] = LOT_LID
    monkeypatch.setattr(L, "_ebay_extra",
                        lambda f, store=None: {"fulfillment_policy_id": "F1"}.get(f))
    fm, body, _w = R.build_draft(item, offer, LOT_SKU, "default")
    path = root / "inventory" / "ESTATES" / "X" / "leaf" / "draft.md"
    path.parent.mkdir(parents=True)
    path.write_text(R.render_draft(fm, body), encoding="utf-8")
    return path


def _read_back(root):
    """What each reader makes of the one draft on disk, as
    {reader: (sku, listing_id, price, title)}."""
    with (root / "listings_ledger.csv").open("w", newline="", encoding="utf-8") as f:
        # sku + listing id only, so collect()'s merged price/title are disk's
        w = csv.DictWriter(f, fieldnames=["sku", "status", "title", "price", "listing_id"])
        w.writeheader()
        w.writerow({"sku": LOT_SKU, "status": "PUBLISHED", "listing_id": LOT_LID})
    sa, = SA.scan_drafts("default", ledger=[])
    la, = LA.scan_drafts("default", ledger=[])
    merged = RP.collect("default")
    assert len(merged) == 1, f"collect() did not merge the draft into its ledger row: {merged}"
    rp, = merged
    assert rp["src"] == "both", rp
    pick = lambda d: (d["sku"], d["listing_id"], d["price"], d["title"])  # noqa: E731
    return {"sync_actuals": pick(sa), "live_audit": pick(la), "report": pick(rp)}


@pytest.mark.parametrize("title", LOT_TITLES + [pytest.param(INCH_TITLE, id="double-quote")])
def test_rebuilt_lot_draft_ids_and_price_read_back_everywhere(tmp_path, monkeypatch, title):
    _isolate(tmp_path, monkeypatch)
    path = _lot_draft(tmp_path, monkeypatch, title)
    got = _read_back(tmp_path)
    for reader, (sku, lid, price, _t) in got.items():
        assert (sku, lid, price) == (LOT_SKU, LOT_LID, LOT_PRICE), (reader, got[reader])
    assert R._existing_drafts() == {LOT_SKU: path}
    d = parse_draft(path)
    assert (d.get("meta.ebay_inventory_sku"), d.get("meta.ebay_listing_id"),
            d.get("price"), d.get("title")) == (LOT_SKU, LOT_LID, LOT_PRICE, title)


@pytest.mark.parametrize("title", LOT_TITLES)
def test_rebuilt_lot_draft_title_reads_back_everywhere(tmp_path, monkeypatch, title):
    _isolate(tmp_path, monkeypatch)
    _lot_draft(tmp_path, monkeypatch, title)
    got = _read_back(tmp_path)
    assert {reader: t for reader, (*_, t) in got.items()} == \
        {reader: title for reader in got}


def test_rebuilt_inch_mark_title_reads_back_in_report(tmp_path, monkeypatch):
    # A `"` title is written single-quoted (as live_audit._yaml_str does).
    # report.collect reads it back (#270); sync_actuals and live_audit don't yet.
    _isolate(tmp_path, monkeypatch)
    _lot_draft(tmp_path, monkeypatch, INCH_TITLE)
    assert _read_back(tmp_path)["report"][3] == INCH_TITLE


def test_live_audit_apply_row_really_changes_a_rebuilt_drafts_price(tmp_path, monkeypatch):
    # The live price moved in Seller Hub. On a single-quoted price the audit
    # saw no local price, and apply_row's `^price:\s*".*"$` rewrite matched
    # nothing: it backed the draft up, said "price -> 69.00", and changed nothing.
    _isolate(tmp_path, monkeypatch)
    path = _lot_draft(tmp_path, monkeypatch, "Kutani Hand Painted Porcelain Teapot Red Gold")
    offer = {"sku": LOT_SKU, "listing_id": LOT_LID, "status": "PUBLISHED",
             "listing_status": "ACTIVE", "price": "69.00",
             "title": "Kutani Hand Painted Porcelain Teapot Red Gold"}
    row, = LA.reconcile([offer], {}, LA.scan_drafts("default", ledger=[]), [])
    assert row["local_price"] == LOT_PRICE
    assert "local price $77.00 != live $69.00" in row["issues"], row["issues"]

    assert LA.apply_row(row, verbose=False) == ["price -> 69.00"]

    d = parse_draft(path)
    assert d.get("price") == "69.00"
    assert "live-audit" in d.get("meta.notes") and "price -> 69.00" in d.get("meta.notes")
    assert d.get("meta.ebay_listing_id") == LOT_LID     # nothing else moved
    assert len(list((path.parent / ".history").glob("draft-*.md"))) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
