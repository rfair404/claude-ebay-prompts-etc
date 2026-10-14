"""tests/test_live_marquee.py — lib/live_marquee.py: picking a show's items
from Browse API summaries, the start-time lines, and the rendered page.
Fixture summaries are shaped like item_summary/search results; nothing here
calls eBay or a browser."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import live_marquee as LM  # noqa: E402


def rec(item_id, created, price=40.0, leaf="Bracelets & Charms", title=None):
    return {"item_id": item_id, "title": title or f"Vintage Bracelet {item_id}",
            "created": created, "price": price, "leaf": leaf, "image": f"https://x/{item_id}/s-l225.jpg"}


def test_summary_record_reads_browse_fields():
    r = LM.summary_record({
        "legacyItemId": "206466525038", "title": "Fly Fishing Cufflinks",
        "itemCreationDate": "2026-08-05T14:00:00.000Z", "price": {"value": "37.00"},
        "categories": [{"categoryName": "Cufflinks"}, {"categoryName": "Jewelry & Watches"}],
        "image": {"imageUrl": "https://i.ebayimg.com/images/g/a/s-l225.jpg"}})
    assert r == {"item_id": "206466525038", "title": "Fly Fishing Cufflinks",
                 "created": "2026-08-05T14:00:00.000Z", "price": 37.0, "leaf": "Cufflinks",
                 "image": "https://i.ebayimg.com/images/g/a/s-l225.jpg"}


def test_summary_record_reads_get_item_category_path():
    r = LM.summary_record({"legacyItemId": "1", "categoryPath": "Collectibles|Religion|Medals",
                           "currentBidPrice": {"value": "5.00"}})
    assert r["leaf"] == "Medals" and r["price"] == 5.0


def test_select_is_oldest_first_and_limited():
    recs = [rec("c", "2026-09-01"), rec("a", "2026-05-23"), rec("b", "2026-07-16")]
    picked, _ = LM.select_items(recs, limit=2)
    assert [r["item_id"] for r in picked] == ["a", "b"]


def test_select_filters_and_counts_why():
    recs = [
        rec("parts", "2026-07-01", leaf="Other Watch Parts"),
        rec("xmas", "2026-07-02", title="Vintage Christmas Tree Brooch"),
        rec("cheap", "2026-07-03", price=19.99),
        rec("gold", "2026-07-04", price=499.0),
        rec("bad", "2026-07-05"),
        rec("ok", "2026-07-06"),
    ]
    picked, dropped = LM.select_items(recs, min_price=25, max_price=100, skip_seasonal=True,
                                      exclude=frozenset({"bad"}))
    assert [r["item_id"] for r in picked] == ["ok"]
    assert dropped == {"excluded": 1, "not jewelry": 1, "seasonal": 1,
                       "under min price": 1, "over max price": 1}


def test_include_skips_category_check_but_not_price():
    recs = [rec("medal", "2026-07-16", leaf="Other Watch Parts"),
            rec("pricey", "2026-07-17", leaf="Other Watch Parts", price=500)]
    picked, _ = LM.select_items(recs, max_price=100, include=frozenset({"medal", "pricey"}))
    assert [r["item_id"] for r in picked] == ["medal"]


def test_seasonal_kept_unless_asked():
    recs = [rec("xmas", "2026-07-02", title="Holly Leaves Brooch")]
    assert LM.select_items(recs)[0] and not LM.select_items(recs, skip_seasonal=True)[0]


def test_short_label_drops_lead_words_and_fits():
    assert LM.short_label("Vintage NOS Swank Cufflinks Gold Tone Oval Pair Box") == "Swank Cufflinks Gold Tone"
    assert len(LM.short_label("EXCELLENT " + "x" * 40)) <= 40


def test_when_lines_daylight_time_crosses_midnight_gmt():
    w = LM.when_lines(LM.parse_start("2026-10-14 20:00"))
    assert w["kicker"] == "WED OCT 14 · 8 PM ET"
    assert w["footer"] == "WEDNESDAY, OCT 14 · 8 PM ET / 5 PM PT"
    assert w["gmt"] == "Thu Oct 15 00:00 GMT"


def test_when_lines_standard_time_and_minutes():
    w = LM.when_lines(LM.parse_start("2026-11-04 18:30"))
    assert w["kicker"] == "WED NOV 4 · 6:30 PM ET"
    assert w["gmt"] == "Wed Nov 4 23:30 GMT"


def test_set_items_keeps_labels_and_heroes():
    spec = LM.default_spec("s")
    LM.set_items(spec, [rec("a", "1"), rec("b", "2")])
    spec["items"][1]["label"] = "Hand label"
    spec["heroes"] = ["b"]
    LM.set_items(spec, [rec("b", "2"), rec("c", "3")])
    assert spec["heroes"] == ["b", "c"]
    assert spec["items"][0]["label"] == "Hand label"


def test_check_spec_catches_layout_overflow():
    spec = LM.default_spec("s")
    LM.set_items(spec, [rec(str(i), str(i)) for i in range(LM.MAX_HEROES + LM.MAX_GRID + 1)])
    assert any("thumbnails" in p for p in LM.check_spec(spec))
    spec["heroes"].append("nope")
    assert any("not in items" in p for p in LM.check_spec(spec))


def test_render_places_heroes_grid_and_escapes():
    spec = LM.default_spec("s")
    spec["start"] = "2026-10-14T20:00:00-04:00"
    LM.set_items(spec, [rec(str(i), str(i)) for i in range(6)])
    spec["items"][5]["label"] = "<b>Tara & Co</b>"
    page = LM.render(spec, "img")
    assert page.count('class="ph"') == 6
    assert page.index("img/0.jpg") < page.index("ALSO ON THE TABLE") < page.index("img/5.jpg")
    assert "&lt;b&gt;Tara &amp; Co&lt;/b&gt;" in page
    assert "WED OCT 14 · 8 PM ET" in page and "FIRST SHOW!" in page


def test_render_without_start_or_ribbon():
    spec = LM.default_spec("s")
    spec["ribbon"] = None
    LM.set_items(spec, [rec("a", "1")])
    page = LM.render(spec)
    assert 'class="ribbon"' not in page and " ET" not in page
