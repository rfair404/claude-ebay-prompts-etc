"""tests/test_live_lineup.py — tools/live_lineup.py's --start parsing, the
Best Offer fix and restore, and tools/live_prep.py's choice of items. eBay
calls are replaced with fakes; nothing here touches the network."""
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

live_lineup = pytest.importorskip(
    "tools.live_lineup", reason="live_lineup imports stores (config module)")
live_prep = pytest.importorskip("tools.live_prep")
import live_show as LS  # noqa: E402


def _offer(item_id, best_offer=True, status="ACTIVE", fmt="FIXED_PRICE",
           listing_id=None, reserve=None):
    o = {"offerId": f"off{item_id}", "sku": f"sku{item_id}", "format": fmt,
         "marketplaceId": "EBAY_US", "categoryId": "1",
         "pricingSummary": {"price": {"value": "10.0", "currency": "USD"}},
         "listingPolicies": {"fulfillmentPolicyId": "F1"},
         "listing": {"listingId": listing_id or item_id, "listingStatus": status}}
    if best_offer:
        o["listingPolicies"]["bestOfferTerms"] = {"bestOfferEnabled": True}
    if reserve:
        o["format"] = "AUCTION"
        o["pricingSummary"] = {"auctionStartPrice": {"value": "1.0", "currency": "USD"},
                               "auctionReservePrice": {"value": reserve, "currency": "USD"}}
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


def test_clear_best_offer_confirm_rereads_puts_and_keeps_old_terms(fake_ebay):
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
    assert lots[0]["best_offer_was"] == {"bestOfferEnabled": True}
    assert "best_offer_was" not in lots[1]


def test_restore_best_offer_only_on_unsold_buy_it_now(fake_ebay):
    offers, puts = fake_ebay
    lots = LS.build_lineup([(_offer(i), t, None) for i, t in
                            (("1", "unsold"), ("2", "sold"), ("3", "running"))])
    for l in lots:
        l["best_offer_was"] = {"bestOfferEnabled": True}
    offers["off1"] = _offer("1", best_offer=False)
    offers["off2"] = _offer("2", best_offer=False, status="ENDED")
    offers["off3"] = _offer("3", best_offer=False, listing_id="999")  # converted auction

    assert live_lineup.restore_best_offer("junk", lots, confirm=False) == 0
    assert puts == []

    assert live_lineup.restore_best_offer("junk", lots, confirm=True) == 1
    assert [p[1] for p in puts] == ["/sell/inventory/v1/offer/off1"]
    assert puts[0][2]["listingPolicies"]["bestOfferTerms"] == {"bestOfferEnabled": True}
    assert "best_offer_was" not in lots[0]          # restored
    assert "best_offer_was" not in lots[1]          # sold: nothing left to do
    assert lots[2]["best_offer_was"]                # still an auction: try later


def test_build_refuses_to_drop_a_lot_with_a_pending_restore(tmp_path, monkeypatch):
    monkeypatch.setattr(LS, "LIVE_DIR", tmp_path)
    lots = LS.build_lineup([(_offer("1", best_offer=False), "A", None)])
    lots[0]["best_offer_was"] = {"bestOfferEnabled": True}
    LS.save_lineup("s", "junk", lots)
    with pytest.raises(SystemExit, match="restore-best-offer"):
        live_lineup.build("junk", "s", [(_offer("2", best_offer=False), "B", None)], [])
    _, kept = live_lineup.build("junk", "s", [(_offer("1", best_offer=False), "A", None)], [])
    assert kept[0]["best_offer_was"] == {"bestOfferEnabled": True}


def test_prep_chooses_compliant_or_fixable_items_in_order():
    rec = lambda i: {"item_id": i, "title": f"t{i}"}            # noqa: E731
    lots = {l["item_id"]: l for l in LS.build_lineup([
        (_offer("1", best_offer=False), "", None),              # complies
        (_offer("2", reserve="50.0", best_offer=False), "", None),  # can't fix
        (_offer("3"), "", None),                                # Best Offer: fixable
        (_offer("5", best_offer=False), "", None)])}
    kept, skipped = live_prep.choose([rec("1"), rec("2"), rec("3"), rec("4"), rec("5")],
                                     lots, limit=2)
    assert [r["item_id"] for r in kept] == ["1", "3"]
    assert [(r["item_id"], "reserve" in why) for r, why in skipped] == [("2", True)]
    kept, skipped = live_prep.choose([rec("4"), rec("5")], lots, limit=5)
    assert [r["item_id"] for r in kept] == ["5"]
    assert "no active offer" in skipped[0][1]
