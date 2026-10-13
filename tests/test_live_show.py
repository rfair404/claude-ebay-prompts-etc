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
          reserve=None, price="10.0", sku=None):
    o = {"sku": sku or f"sku{item_id}", "format": fmt, "categoryId": "1",
         "pricingSummary": {"price": {"value": price, "currency": "USD"}},
         "listingPolicies": {},
         "listing": {"listingId": item_id, "listingStatus": status}}
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
])
def test_documented_rules_flag(kw, needle):
    flags = LS.live_flags(offer("1", **kw))
    assert len(flags) == 1 and needle in flags[0]


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


def test_unknown_or_bad_show_is_404(client):
    assert client.get("/live/nope").status_code == 404
    assert client.get("/live/..%2Fx").status_code == 404
    assert client.get("/live/nope/state").status_code == 404
