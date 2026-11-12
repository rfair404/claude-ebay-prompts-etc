#!/usr/bin/env python3
"""Accepted-Best-Offer sold rows stay out of the price stats.

On eBay's sold results a "Best offer accepted" row shows the seller's ASK,
not what the item sold for. K01-C188 (Western Electric candlestick phone,
2026-11-11): item 128163328916 was logged at $100; its page showed the offer
accepted at $80. Counted as sales, those asks pushed the median and the
Push-high ceiling up.

These tests run the real path: extractor-shaped rows -> rows_to_comps ->
save_browse_run -> price_from_runs, and the text fallback parser.

Run:  pytest tests/test_price_stats_best_offer.py
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import comps_csv  # noqa: E402
import ebay_sold_browse as esb  # noqa: E402
import price_stats  # noqa: E402

QUERY = "western electric candlestick telephone"


def _row(item_id, price, ship, *, bo=False, title=None):
    """One row in the shape EXTRACTOR_JS returns."""
    return {
        "item_id": item_id, "url": f"https://www.ebay.com/itm/{item_id}",
        "title": title or f"Western Electric Brass Candlestick Telephone {item_id}",
        "sold_price": price, "sold_date": "Oct 30, 2026",
        "shipping_cost": ship, "shipping_type": None,
        "listing_type": "Best Offer" if bo else "Buy It Now",
        "bids_count": None, "bo_accepted": bo,
        "seller_username": "seller", "seller_feedback_score": 500,
        "seller_feedback_pct": 100.0,
    }


# Four true sales and the four C188 Best Offer asks, all priced above them.
TRUE_SALES = [_row("960114026141", 63.0, 7.0), _row("940041141835", 70.0, 10.0),
              _row("800760643343", 72.0, 8.0), _row("206537971849", 75.0, 0.0)]
BO_ASKS = [_row("128163328916", 100.0, 12.99, bo=True),
           _row("257335976915", 80.0, 28.50, bo=True),
           _row("117404743326", 89.0, 19.99, bo=True),
           _row("236867585416", 140.0, 11.88, bo=True)]


def _run(tmp_path, rows, sort):
    comps = esb.rows_to_comps(rows, QUERY)
    return esb.save_browse_run(comps, QUERY, sort, save_dir=tmp_path)


def _report(tmp_path):
    rows = TRUE_SALES + BO_ASKS
    return price_stats.price_from_runs(
        best_match_json=_run(tmp_path / "bm", rows, "best_match"),
        price_high_json=_run(tmp_path / "ph", rows, "price_high"),
        unit_type="single", condition=None, price_field="total",
    )


def test_bo_asks_left_out_of_distribution(tmp_path):
    r = _report(tmp_path)
    assert r["n_raw"] == 8
    assert r["n_kept"] == 4
    kept_urls = {c["url"] for c in r["kept_comps"]}
    for row in BO_ASKS:
        assert row["url"] not in kept_urls
    # Delivered prices of the true sales: 70, 80, 80, 75 -> median 77.5.
    assert r["distribution"]["median"] == 77.5
    assert r["distribution"]["max"] == 80.0


def test_bo_ask_never_sets_push_high(tmp_path):
    r = _report(tmp_path)
    assert r["ceiling_comp"]["url"] not in {row["url"] for row in BO_ASKS}
    assert r["tiers"]["push_high"]["price"] == 80


def test_bo_drops_are_named_in_the_filter_log(tmp_path):
    r = _report(tmp_path)
    removed = [x for lg in r["filter_log"] for x in lg["removed"]]
    bo = [x for x in removed if x["reason"] == price_stats.BO_ACCEPTED_REASON]
    assert len(bo) == 4
    assert "Best Offer accepted" in price_stats.render(r)


def test_text_fallback_flags_best_offer(tmp_path):
    page = "\n".join([
        "Sold Oct 30, 2026", "WE Brass Candlestick Rotary Phone - Please Read",
        "$100.00", "Best offer accepted", "+$12.99 delivery",
        "Sold Nov 1, 2026", "Western Electric Candlestick Telephone Brass",
        "$63.00", "Buy It Now", "+$6.89 delivery",
    ])
    comps = esb.parse_sold_page(page, query=QUERY)
    by_title = {c.title[:10]: c for c in comps}
    assert by_title["WE Brass C"].bo_accepted is True
    assert by_title["Western El"].bo_accepted is False


def test_comps_csv_marks_bo_rows(tmp_path):
    path = _run(tmp_path / "runs", [TRUE_SALES[0], BO_ASKS[0]], "best_match")
    comps_csv.from_apify_json(tmp_path, path, item="1")
    rows = list(csv.DictReader((tmp_path / "comps.csv").open(encoding="utf-8")))
    by_url = {r["url"]: r for r in rows}
    bo = by_url[BO_ASKS[0]["url"]]
    assert bo["listing_type"] == comps_csv.BO_ACCEPTED
    assert "ASK" in bo["note"]
    assert by_url[TRUE_SALES[0]["url"]]["listing_type"] == "Buy It Now"
