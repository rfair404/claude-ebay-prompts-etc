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


def test_start_and_no_start_are_mutually_exclusive_and_a_mode_is_required():
    with pytest.raises(SystemExit) as e:
        live_lineup.main(["--store", "junk", "--show", "s", "--all",
                          "--start", "2026-10-19 20:00", "--no-start"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        live_lineup.main(["--store", "junk", "--show", "s"])
    assert e.value.code == 2


def test_no_start_alone_clears_the_saved_start_and_nothing_else(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(LS, "LIVE_DIR", tmp_path)
    monkeypatch.setattr(live_lineup, "fetch_entries",
                        lambda *a, **k: pytest.fail("--no-start alone must not read eBay"))
    lots = LS.build_lineup([(_offer("1", best_offer=False), "A", None)])
    LS.save_lineup("s", "junk", lots, scheduled_start=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert live_lineup.main(["--store", "junk", "--show", "s", "--no-start"]) == 0
    lu = LS.load_lineup("s")
    assert lu["scheduled_start"] is None and lu["lots"] == lots
    # wrong store: refused, start untouched elsewhere
    with pytest.raises(SystemExit, match="no lineup"):
        live_lineup.main(["--store", "default", "--show", "s", "--no-start"])


def test_a_rebuild_keeps_the_start_unless_no_start(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(LS, "LIVE_DIR", tmp_path)
    entries = [(_offer("1", best_offer=False), "A", None)]
    live_lineup.build("junk", "s", entries, [],
                      start=datetime(2026, 10, 20, tzinfo=timezone.utc))
    live_lineup.build("junk", "s", entries, [])
    assert LS.load_lineup("s")["scheduled_start"] == "2026-10-20T00:00:00Z"
    monkeypatch.setattr(live_lineup, "fetch_entries", lambda store, ids: (entries, []))
    assert live_lineup.main(["--store", "junk", "--show", "s", "--items", "1",
                             "--no-start"]) == 0
    assert LS.load_lineup("s")["scheduled_start"] is None


def test_saving_a_start_that_overlaps_another_stores_show_warns(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setattr(LS, "LIVE_DIR", tmp_path)
    soon = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=1)
    LS.save_lineup("main-show", "default", [], scheduled_start=soon + timedelta(hours=3))
    LS.save_lineup("later", "default", [], scheduled_start=soon + timedelta(hours=9))
    LS.save_lineup("same-store", "junk", [], scheduled_start=soon)
    entries = [(_offer("1", best_offer=False), "A", None)]
    live_lineup.build("junk", "s", entries, [], start=soon)
    out = capsys.readouterr().out
    assert "[!] overlaps eBay Live show 'main-show' on store 'default'" in out
    assert "'later'" not in out and "'same-store'" not in out
    live_lineup.build("junk", "s", entries, [], start=soon + timedelta(days=2))
    assert "[!] overlaps" not in capsys.readouterr().out
