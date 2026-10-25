"""Every way list_edit ends a listing records it the same way (_record_end).

end_listing, withdraw_offer_by_id, delete_offer_by_id and delete_item_by_sku
each write the listings-ledger row and clear the matched draft's
ebay_listing_id (and ebay_offer_id when the offer is gone). A draft that only
shares the SKU is a re-shoot or a copy and stays byte-identical. eBay is
stubbed; the ledger and drafts are temp files.
"""
import csv
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_list_edit import L, _Creds  # noqa: E402  (also puts lib/ on sys.path)
import sync_actuals  # noqa: E402
from draft_io import parse_draft  # noqa: E402

SKU, OFFER, LISTING = "abcd1234", "OFFER-7", "110000000007"
OFFER_JSON = {"offerId": OFFER, "sku": SKU, "status": "PUBLISHED",
              "listing": {"listingId": LISTING}}


def _draft(root: Path, rel: str, listing_id: str) -> Path:
    d = root / "inventory" / rel
    d.mkdir(parents=True)
    p = d / "draft.md"
    p.write_text(
        "---\n"
        'title: "Brass Bell"\n'
        'price: "20.00"\n'
        "meta:\n"
        f'  ebay_inventory_sku: "{SKU}"\n'
        f'  ebay_offer_id: "{OFFER}"\n'
        f'  ebay_listing_id: "{listing_id}"\n'
        "---\n\nbody\n", encoding="utf-8")
    return p


@pytest.fixture
def shop(tmp_path, monkeypatch):
    ledger = tmp_path / "listings_ledger.csv"
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(ledger))
    monkeypatch.setattr(sync_actuals, "REPO", tmp_path)
    monkeypatch.setattr(sync_actuals, "INVENTORY", tmp_path / "inventory")
    with ledger.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=L._LEDGER_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerow({"sku": SKU, "status": "PUBLISHED", "title": "Brass Bell",
                    "offer_id": OFFER, "published_at": "2026-10-01T00:00:00Z"})

    calls = []
    monkeypatch.setattr(L, "api_send", lambda method, path, body=None, creds=None, **kw:
                        calls.append((method, path)) or dict(OFFER_JSON))
    monkeypatch.setattr(L, "get_offer", lambda oid, creds=None: dict(OFFER_JSON))
    monkeypatch.setattr(L, "get_offers_for_sku", lambda sku, creds=None: [dict(OFFER_JSON)])
    for name in ("withdraw_offer", "delete_offer", "delete_inventory_item"):
        monkeypatch.setattr(L, name, lambda target, creds=None, _n=name: calls.append(_n))

    matched = _draft(tmp_path, "shoot/bell", LISTING)
    sku_only = _draft(tmp_path, "shoot/bell-reshoot", "")
    return ledger, matched, sku_only, calls


ENDS = [
    ("end_listing", lambda m: L.end_listing(m, creds=_Creds(), confirm=True), "ENDED"),
    ("withdraw", lambda m: L.withdraw_offer_by_id(OFFER, creds=_Creds(), confirm=True),
     "ENDED"),
    ("delete-offer", lambda m: L.delete_offer_by_id(OFFER, creds=_Creds(), confirm=True),
     "DELETED"),
    ("delete-item", lambda m: L.delete_item_by_sku(SKU, creds=_Creds(), confirm=True),
     "DELETED"),
]


@pytest.mark.parametrize("name,end,status", ENDS, ids=[e[0] for e in ENDS])
def test_every_end_path_records_the_ledger_and_the_matched_draft(shop, name, end, status):
    ledger, matched, sku_only, calls = shop
    before = sku_only.read_bytes()

    end(matched)

    assert calls, "the stubbed eBay call was never made"
    with ledger.open(newline="", encoding="utf-8") as f:
        row = next(r for r in csv.DictReader(f) if r["sku"] == SKU)
    assert row["status"] == status
    assert row["listing_id"] == LISTING and row["title"] == "Brass Bell"

    d = parse_draft(matched)
    assert d.get("meta.ebay_listing_id") in ("", None)
    assert d.get("meta.ended_at")
    if status == "DELETED":
        assert d.get("meta.ebay_offer_id") in ("", None)
    else:
        assert d.get("meta.ebay_offer_id") == OFFER

    assert sku_only.read_bytes() == before
