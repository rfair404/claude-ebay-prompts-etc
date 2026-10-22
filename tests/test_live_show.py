"""tests/test_live_show.py — lib/live_show.py (eBay Live lineup + on-screen
state) and its /live/* routes in webapp/server.py. Fixture offers are shaped
like the Inventory API's GET /offer response; nothing here calls eBay."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import live_show as LS  # noqa: E402


def offer(item_id, fmt="FIXED_PRICE", status="ACTIVE", best_offer=False,
          reserve=None, price="10.0", sku=None, bin_price=None, qty=None):
    # Auctions carry the start bid in auctionStartPrice; `price` on an
    # auction offer is its Buy It Now price (bin_price).
    ps = ({"auctionStartPrice": {"value": price, "currency": "USD"}}
          if fmt == "AUCTION" else {"price": {"value": price, "currency": "USD"}})
    if bin_price:
        ps["price"] = {"value": bin_price, "currency": "USD"}
    o = {"sku": sku or f"sku{item_id}", "format": fmt, "categoryId": "1",
         "offerId": f"off{item_id}", "pricingSummary": ps,
         "listingPolicies": {},
         "listing": {"listingId": item_id, "listingStatus": status}}
    if qty is not None:
        o["availableQuantity"] = qty
    if best_offer:
        o["listingPolicies"]["bestOfferTerms"] = {"bestOfferEnabled": True}
    if reserve:
        o["pricingSummary"]["auctionReservePrice"] = {"value": reserve, "currency": "USD"}
    return o


def test_clean_active_listing_has_no_flags():
    assert LS.live_flags(offer("1")) == []
    assert LS.live_flags(offer("2", fmt="AUCTION")) == []


@pytest.mark.parametrize("kw,needle", [
    ({"best_offer": True}, "Best Offer"),
    ({"reserve": "50.0"}, "reserve"),
    ({"status": "ENDED"}, "not an active listing"),
    ({"status": None}, "not an active listing"),
    ({"fmt": "AUCTION", "bin_price": "40.0"}, "Buy It Now price on an auction"),
])
def test_documented_rules_flag(kw, needle):
    flags = LS.live_flags(offer("1", **kw))
    assert len(flags) == 1 and needle in flags[0]


def test_notes_flag_what_change_to_auction_cannot_take():
    assert LS.live_notes(offer("1", qty=1)) == []
    assert "quantity 3" in LS.live_notes(offer("1", qty=3))[0]
    assert LS.live_notes(offer("1", fmt="AUCTION")) == []
    # two offers on one Item ID = a variation listing
    lots = LS.build_lineup([(offer("7", sku="a"), "V", None),
                            (offer("7", sku="b"), "V", None),
                            (offer("8"), "Single", None)])
    assert [l["item_id"] for l in lots] == ["7", "8"]
    assert "2 SKUs" in lots[0]["notes"][0] and lots[1]["notes"] == []
    assert lots[0]["flags"] == []   # a note never keeps a lot off the show


def test_best_offer_only_and_the_update_body():
    lots = LS.build_lineup([(offer("1", best_offer=True), "A", None),
                            (offer("2", best_offer=True, status="ENDED"), "B", None),
                            (offer("3"), "C", None)])
    assert [l["item_id"] for l in LS.best_offer_only(lots)] == ["1"]
    cur = offer("1", best_offer=True)
    cur["listingPolicies"]["fulfillmentPolicyId"] = "F1"
    cur["listingDescription"] = "desc"
    keys = ("listingPolicies", "pricingSummary", "format", "sku",
            "listingDescription", "categoryId")
    body = LS.offer_with_best_offer(cur, keys, None)
    assert body["listingPolicies"] == {"fulfillmentPolicyId": "F1"}
    assert body["listingDescription"] == "desc" and body["format"] == "FIXED_PRICE"
    assert "listing" not in body and "offerId" not in body
    assert cur["listingPolicies"]["bestOfferTerms"]   # input left untouched
    back = LS.offer_with_best_offer(body, keys, {"bestOfferEnabled": True})
    assert back["listingPolicies"] == {"fulfillmentPolicyId": "F1",
                                       "bestOfferTerms": {"bestOfferEnabled": True}}


def test_fixable_is_eligible_or_best_offer_only():
    assert LS.fixable(LS.lot_from_offer(offer("1")))
    assert LS.fixable(LS.lot_from_offer(offer("1", best_offer=True)))
    assert not LS.fixable(LS.lot_from_offer(offer("1", fmt="AUCTION", reserve="9.0")))
    assert not LS.fixable(LS.lot_from_offer(offer("1", best_offer=True, status="ENDED")))


def test_carry_restores_keeps_the_record_and_names_orphans():
    old = LS.build_lineup([(offer("1"), "A", None), (offer("2"), "B", None),
                           (offer("3"), "C", None)])
    old[0]["best_offer_was"] = {"bestOfferEnabled": True}
    old[1]["best_offer_was"] = {"bestOfferEnabled": True, "autoAcceptPrice": {}}
    new = LS.build_lineup([(offer("1"), "A", None), (offer("3"), "C", None)])
    orphans = LS.carry_restores(old, new)
    assert new[0]["best_offer_was"] == {"bestOfferEnabled": True}
    assert "best_offer_was" not in new[1]
    assert [l["item_id"] for l in orphans] == ["2"]


def test_scheduled_start_is_saved_in_utc(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    start = datetime(2026, 10, 20, 20, 0, tzinfo=ZoneInfo("America/New_York"))
    LS.save_lineup("s", "junk", [], base=tmp_path, scheduled_start=start)
    lu = LS.load_lineup("s", base=tmp_path)
    assert lu["scheduled_start"] == "2026-10-21T00:00:00Z"
    s, closes = LS.shopping_window(lu)
    assert (closes - s).total_seconds() == 8 * 3600


def _order(item_id, created, sku="x"):
    return {"orderId": f"o{item_id}", "creationDate": created,
            "lineItems": [{"legacyItemId": item_id, "sku": sku}]}


def test_open_windows_and_which_orders_they_hold(tmp_path):
    from datetime import datetime, timezone
    start = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)
    LS.save_lineup("show", "junk", LS.build_lineup([(offer("1"), "A", None)]),
                   base=tmp_path, scheduled_start=start)
    LS.save_lineup("other", "default", [], base=tmp_path, scheduled_start=start)
    LS.save_lineup("nostart", "junk", [], base=tmp_path)

    during = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)
    wins = LS.open_windows("junk", now=during, base=tmp_path)
    assert [w["show"] for w in wins] == ["show"]
    after = datetime(2026, 10, 21, 8, 0, tzinfo=timezone.utc)
    assert LS.open_windows("junk", now=after, base=tmp_path) == []

    held = lambda o: LS.holding_window(o, wins)            # noqa: E731
    assert held(_order("1", "2026-10-20T22:30:00.000Z"))   # lineup lot, just pre-show
    assert held(_order("1", "2026-10-20T22:00:00.000Z"))   # exactly PRE_SHOW_MARGIN before
    assert held(_order("99", "2026-10-21T01:00:00.000Z"))  # new id (converted to auction)
    assert held(_order("1", "2026-10-21T00:00:00Z", sku="sku1"))
    assert not held(_order("99", "2026-10-20T12:00:00.000Z"))  # unrelated, pre-show
    assert not held(_order("99", "2026-10-20T23:00:00.000Z"))  # unrelated, in the margin
    assert not held(_order("1", "2026-10-20T21:59:00.000Z"))   # lineup lot, sold earlier
    assert not held(_order("1", "2026-10-20T12:00:00.000Z"))   # ordinary store sale
    assert not held(_order("1", "2026-10-21T09:00:00.000Z"))   # after the window
    assert not held({"orderId": "x", "lineItems": []})         # no creationDate


def test_a_lineup_saved_after_a_sale_does_not_reach_back_for_it(tmp_path):
    """The case from 2026-10-19: an order placed and printed at 1 PM, a
    lineup with an 8 PM start saved at 2 PM, a poll at 3 PM."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    LS.save_lineup("tonight", "junk", LS.build_lineup([(offer("1"), "A", None)]),
                   base=tmp_path, scheduled_start=datetime(2026, 10, 19, 20, 0, tzinfo=et))
    wins = LS.open_windows("junk", now=datetime(2026, 10, 19, 15, 0, tzinfo=et),
                           base=tmp_path)
    assert [w["show"] for w in wins] == ["tonight"]
    assert LS.holding_window(_order("1", "2026-10-19T16:55:00.000Z"), wins) is None


def test_open_windows_for_every_store_and_cross_store_overlaps(tmp_path):
    from datetime import datetime, timezone
    start = datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)
    LS.save_lineup("a", "junk", [], base=tmp_path, scheduled_start=start)
    LS.save_lineup("b", "default", [], base=tmp_path,
                   scheduled_start=datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc))
    LS.save_lineup("c", "default", [], base=tmp_path,
                   scheduled_start=datetime(2026, 10, 22, 0, 0, tzinfo=timezone.utc))
    LS.save_lineup("d", "junk", [], base=tmp_path,
                   scheduled_start=datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc))
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    wins = LS.open_windows(None, now=now, base=tmp_path)
    assert sorted((w["show"], w["store"]) for w in wins) == [
        ("a", "junk"), ("b", "default"), ("c", "default"), ("d", "junk")]
    pairs = LS.cross_store_overlaps(wins)
    # a/d share a store (not warned); c starts after both junk windows close
    assert sorted(tuple(sorted((x["show"], y["show"]))) for x, y in pairs) == [
        ("a", "b"), ("b", "d")]
    assert "show 'a' on store 'junk' (Tue Oct 20 08:00 PM ET - " in LS.describe_window(wins[0])


def test_set_scheduled_start_clears_only_the_start(tmp_path):
    from datetime import datetime, timezone
    lots = LS.build_lineup([(offer("1"), "A", None), (offer("2", best_offer=True), "B", None)])
    d = LS.save_lineup("s", "junk", lots, keep_flagged=True, base=tmp_path,
                       scheduled_start=datetime(2026, 10, 21, tzinfo=timezone.utc))
    before = LS.load_lineup("s", base=tmp_path)
    LS.set_scheduled_start("s", None, base=tmp_path)
    after = LS.load_lineup("s", base=tmp_path)
    assert after["scheduled_start"] is None
    assert {k: v for k, v in after.items() if k != "scheduled_start"} == \
        {k: v for k, v in before.items() if k != "scheduled_start"}
    assert (d / "item_ids.txt").read_text() == "1\n2\n"     # untouched
    assert LS.open_windows("junk", base=tmp_path) == []
    with pytest.raises(LookupError):
        LS.set_scheduled_start("nope", None, base=tmp_path)


def test_build_lineup_numbers_in_order_and_dedupes():
    lots = LS.build_lineup([(offer("9"), "A", None), (offer("3"), "B", "x/y"),
                            (offer("9"), "A again", None)])
    assert [(l["lot"], l["item_id"], l["title"]) for l in lots] == [
        (1, "9", "A"), (2, "3", "B")]
    assert lots[1]["shoot"] == "x/y"
    assert lots[0]["url"] == "https://www.ebay.com/itm/9"
    assert lots[0]["price"] == "10.00"


def test_merge_appends_without_moving_existing_lots():
    a = LS.build_lineup([(offer("1"), "A", None), (offer("2"), "B", None)])
    b = LS.build_lineup([(offer("2"), "B", None), (offer("3"), "C", None)])
    m = LS.merge_lineup(a, b)
    assert [(l["lot"], l["item_id"]) for l in m] == [(1, "1"), (2, "2"), (3, "3")]


def test_item_ids_file_leaves_out_flagged_unless_asked(tmp_path):
    lots = LS.build_lineup([(offer("1"), "A", None),
                            (offer("2", best_offer=True), "B", None)])
    d = LS.save_lineup("show1", "junk", lots, base=tmp_path)
    assert (d / "item_ids.txt").read_text() == "1\n"
    LS.save_lineup("show1", "junk", lots, keep_flagged=True, base=tmp_path)
    assert (d / "item_ids.txt").read_text() == "1\n2\n"
    assert LS.load_lineup("show1", base=tmp_path)["store"] == "junk"


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", "a\\b", ".hidden", "x" * 65])
def test_show_names_cannot_escape(bad):
    with pytest.raises(ValueError):
        LS.validate_show_name(bad)


def test_state_roundtrip_and_unknown_lot(tmp_path):
    LS.save_lineup("s", "junk", LS.build_lineup([(offer("1"), "A", None)]), base=tmp_path)
    assert LS.current("s", tmp_path) == {"show": "s", "lot": None, "note": ""}
    LS.save_state("s", 1, "Starting at $5", tmp_path)
    cur = LS.current("s", tmp_path)
    assert cur["lot"]["item_id"] == "1" and cur["note"] == "Starting at $5"
    with pytest.raises(ValueError):
        LS.save_state("s", 2, "", tmp_path)
    with pytest.raises(LookupError):
        LS.save_state("nope", 1, "", tmp_path)


# --- routes ---------------------------------------------------------------

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import webapp.server as server  # noqa: E402


@pytest.fixture
def client(tmp_path):
    LS.save_lineup("demo", "junk", LS.build_lineup([
        (offer("111"), "Brass <b>lamp</b>", None),
        (offer("222", best_offer=True), "Quilt", None)]), base=tmp_path)
    server.set_live_base(tmp_path)
    with TestClient(server.app) as c:
        yield c
    server.set_live_base(None)


def test_host_page_lists_lots_escaped_and_flagged(client):
    r = client.get("/live/demo")
    assert r.status_code == 200
    assert "Brass &lt;b&gt;lamp&lt;/b&gt;" in r.text
    assert "Best Offer is on" in r.text


def test_overlay_and_state_flow(client):
    assert client.get("/live/demo/overlay").status_code == 200
    assert client.get("/live/demo/state").json()["lot"] is None
    r = client.post("/live/demo/state", json={"lot": 1, "note": "Bid now"})
    assert r.status_code == 200 and r.json()["lot"]["item_id"] == "111"
    assert client.get("/live/demo/state").json()["note"] == "Bid now"
    assert client.post("/live/demo/state", json={"lot": 9}).status_code == 400
    assert client.post("/live/demo/state", json={"lot": None}).json()["lot"] is None


def test_lineup_json_for_the_pager(client):
    r = client.get("/live/demo/lineup")
    assert r.status_code == 200 and [l["lot"] for l in r.json()["lots"]] == [1, 2]
    # what the pager's lineup panel reads (lotMeta / renderLot)
    first, second = r.json()["lots"]
    assert (first["title"], first["item_id"], first["format"], first["price"]) == (
        "Brass <b>lamp</b>", "111", "FIXED_PRICE", "10.00")
    assert first["auction_start_price"] is None and first["flags"] == []
    assert second["flags"] == [LS.BEST_OFFER_FLAG]
    assert client.get("/live/nope/lineup").status_code == 404


def test_unknown_or_bad_show_is_404(client):
    assert client.get("/live/nope").status_code == 404
    assert client.get("/live/..%2Fx").status_code == 404
    assert client.get("/live/nope/state").status_code == 404


def test_state_post_works_when_server_runs_via_runpy(tmp_path):
    """`ebz serve` runs webapp.server through runpy under a name that isn't in
    sys.modules; the request models must still resolve, or POST /state 500s."""
    import runpy
    ns = runpy.run_module("webapp.server", run_name="ebz_serve_runpy_test")
    assert ns["LiveStateRequest"].__pydantic_complete__
    assert ns["EnqueueRequest"].__pydantic_complete__
    LS.save_lineup("demo", "junk", LS.build_lineup([(offer("111"), "Lamp", None)]),
                   base=tmp_path)
    ns["set_live_base"](tmp_path)
    with TestClient(ns["app"]) as c:
        r = c.post("/live/demo/state", json={"lot": 1, "note": "hi"})
    assert r.status_code == 200, r.text
