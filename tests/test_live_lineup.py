"""tests/test_live_lineup.py — tools/live_lineup.py's --start parsing and
--clear-best-offer. eBay calls are replaced with fakes; nothing here touches
the network."""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

live_lineup = pytest.importorskip(
    "tools.live_lineup", reason="live_lineup imports stores (config module)")
import live_show as LS  # noqa: E402


def _offer(item_id, best_offer=True, status="ACTIVE"):
    o = {"offerId": f"off{item_id}", "sku": f"sku{item_id}", "format": "FIXED_PRICE",
         "marketplaceId": "EBAY_US", "categoryId": "1",
         "pricingSummary": {"price": {"value": "10.0", "currency": "USD"}},
         "listingPolicies": {"fulfillmentPolicyId": "F1"},
         "listing": {"listingId": item_id, "listingStatus": status}}
    if best_offer:
        o["listingPolicies"]["bestOfferTerms"] = {"bestOfferEnabled": True}
    return o


def test_parse_start_is_eastern_unless_offset_given():
    assert live_lineup.parse_start("2026-10-20 20:00").utcoffset().total_seconds() == -4 * 3600
    assert live_lineup.parse_start("2026-10-21T00:00:00Z").utcoffset().total_seconds() == 0


@pytest.fixture
def fake_ebay(monkeypatch):
    """Fake ebay_client: GET returns the offer as stored, PUT records it."""
    offers = {}
    puts = []

    def get_offer(oid, creds=None):
        return offers[oid]

    def api_send(method, path, body=None, creds=None, **_kw):
        puts.append((method, path, body))
        return {}

    fake = types.SimpleNamespace(
        load_credentials=lambda store=None: object(), get_offer=get_offer,
        api_send=api_send, EbayAPIError=RuntimeError)
    monkeypatch.setitem(sys.modules, "ebay_client", fake)
    return offers, puts


def test_clear_best_offer_dry_run_changes_nothing(fake_ebay):
    offers, puts = fake_ebay
    lots = LS.build_lineup([(_offer("1"), "A", None)])
    assert live_lineup.clear_best_offer("junk", lots, confirm=False) == 0
    assert puts == [] and lots[0]["flags"] == [LS.BEST_OFFER_FLAG]


def test_clear_best_offer_confirm_rereads_and_puts(fake_ebay):
    offers, puts = fake_ebay
    a, b = _offer("1"), _offer("2")
    lots = LS.build_lineup([(a, "A", None), (b, "B", None)])
    offers["off1"] = a
    offers["off2"] = _offer("2", best_offer=False)   # already turned off on eBay
    assert live_lineup.clear_best_offer("junk", lots, confirm=True) == 1
    assert len(puts) == 1
    method, path, body = puts[0]
    assert (method, path) == ("PUT", "/sell/inventory/v1/offer/off1")
    assert "bestOfferTerms" not in body["listingPolicies"]
    assert body["listingPolicies"]["fulfillmentPolicyId"] == "F1"
    assert "listing" not in body and "offerId" not in body
    assert [l["flags"] for l in lots] == [[], []]
