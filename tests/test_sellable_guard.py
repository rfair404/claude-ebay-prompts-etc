#!/usr/bin/env python3
"""The sold-item guard on `--update`.

Written after a live incident: a field-scoped photo update on a SKU that had
already sold came back as a NEW listing. Two things made that possible, and both
are covered here.

  1. The only "is it still on sale" check was the local ledger, which cannot
     answer it — an accepted Best Offer never writes back, which is why
     sync_actuals exists at all. A sold item can read as PUBLISHED locally for
     hours.
  2. `update_listing_fields` round-tripped `availability` on EVERY update: it
     GET the inventory item, copied the quantity, and PUT it back. On a sold-out
     SKU that hands eBay a positive quantity, and eBay relists.

Run:  python tests/test_sellable_guard.py
  or: pytest tests/test_sellable_guard.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import list_edit as L                                        # noqa: E402


class _Creds:
    has_user = True


def _fake_api(responses):
    """Stub api_send; record every write so a test can assert none happened."""
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


# ---------------------------------------------------------------------------
# offer_sellable_state — reading eBay's answer, not ours
# ---------------------------------------------------------------------------

def test_published_with_stock_is_sellable():
    send, _ = _fake_api({"/offer?sku=": {"offers": [
        {"offerId": "1", "status": "PUBLISHED", "availableQuantity": 1,
         "listing": {"listingId": "206", "listingStatus": "ACTIVE"}}]}})
    L.api_send = send
    st = L.offer_sellable_state("abc", _Creds())
    assert st["sellable"] and st["listing_id"] == "206"


def test_sold_out_quantity_zero_is_not_sellable():
    """The shape a single-quantity item takes the moment it sells."""
    send, _ = _fake_api({"/offer?sku=": {"offers": [
        {"offerId": "1", "status": "PUBLISHED", "availableQuantity": 0,
         "listing": {"listingId": "206", "listingStatus": "ENDED"}}]}})
    L.api_send = send
    st = L.offer_sellable_state("abc", _Creds())
    assert not st["sellable"]
    assert "ENDED" in st["reason"] or "sold out" in st["reason"]


def test_unpublished_offer_is_not_sellable():
    send, _ = _fake_api({"/offer?sku=": {"offers": [
        {"offerId": "1", "status": "UNPUBLISHED", "availableQuantity": 1}]}})
    L.api_send = send
    assert not L.offer_sellable_state("abc", _Creds())["sellable"]


def test_missing_offer_is_not_sellable():
    send, _ = _fake_api({"/offer?sku=": L.EbayAPIError(404, "gone")})
    L.api_send = send
    st = L.offer_sellable_state("abc", _Creds())
    assert not st["sellable"] and st["status"] == "NO_OFFER"


# ---------------------------------------------------------------------------
# update_listing_fields — refuses, and does not restock
# ---------------------------------------------------------------------------

def _draft(tmp: Path, sku="abc"):
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "listing").mkdir(exist_ok=True)
    (tmp / "draft.md").write_text(
        "---\nmeta:\n  ebay_inventory_sku: \"%s\"\n---\n"
        "photos:\n  - \"listing/a.jpg\"\n\nbody\n" % sku, encoding="utf-8")
    return tmp / "draft.md"


def test_update_refuses_a_sold_sku_and_writes_nothing():
    import tempfile
    send, calls = _fake_api({"/offer?sku=": {"offers": [
        {"offerId": "1", "status": "PUBLISHED", "availableQuantity": 0,
         "listing": {"listingId": "206", "listingStatus": "ENDED"}}]}})
    L.api_send = send
    with tempfile.TemporaryDirectory() as td:
        d = _draft(Path(td) / "s")
        try:
            L.update_listing_fields(d, ["photos"], creds=_Creds())
            raise AssertionError("a sold SKU must not be updated")
        except L.ListingNotSellable as e:
            assert "not on sale" in str(e)
    assert not [c for c in calls if c[0] in ("PUT", "POST")], \
        f"a refused update must not write: {calls}"


def test_update_does_not_round_trip_availability():
    """The restock that relists. Only a quantity update may touch it."""
    import tempfile
    inv = {"availability": {"shipToLocationAvailability": {"quantity": 1}},
           "condition": "USED_EXCELLENT",
           "product": {"title": "t", "imageUrls": ["old.jpg"]}}
    send, calls = _fake_api({
        "/offer?sku=": {"offers": [{"offerId": "1", "status": "PUBLISHED",
                                    "availableQuantity": 1,
                                    "listing": {"listingId": "206",
                                                "listingStatus": "ACTIVE"}}]},
        "/inventory_item/": inv,
    })
    L.api_send = send
    L.upload_photos_to_eps = lambda paths, creds=None: ["https://eps/new.jpg"]

    with tempfile.TemporaryDirectory() as td:
        d = _draft(Path(td) / "s")
        L.update_listing_fields(d, ["photos"], creds=_Creds())

    puts = [c for c in calls if c[0] == "PUT" and "/inventory_item/" in c[1]]
    assert len(puts) == 1, puts
    body = puts[0][2]
    assert "availability" not in body, \
        "photo-only update must not hand eBay a quantity — that is what relists a sold item"
    assert body["product"]["imageUrls"] == ["https://eps/new.jpg"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    bad = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:                              # noqa: BLE001
            bad += 1
            print(f"FAIL  {fn.__name__}: {e}")
    print(f"{len(fns) - bad}/{len(fns)} passed")
    sys.exit(1 if bad else 0)
