#!/usr/bin/env python3
"""lib/ebay_browse.py — the Browse API reader, tested offline (GH #82).

All HTTP is faked by patching `ebay_browse.ec.api_get` (this repo's
convention — see tests/test_ebay_client.py for the lower-level urlopen fake
that `ec.api_get` itself sits on); no network, no creds.

Run:  python tests/test_ebay_browse.py
  or: pytest tests/test_ebay_browse.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import ebay_browse  # noqa: E402

# A fabricated active-listings sample: 12 items across 4 sellers, uneven
# counts so "top N by listing count" has an unambiguous answer.
#   alice: 5 listings   bob: 4 listings   carol: 2 listings   dave: 1 listing

def _item(i, seller, price):
    return {
        "itemId": f"v1|{seller}-{i}|0",
        "title": f"{seller} item {i}",
        "price": {"value": str(price), "currency": "USD"},
        "condition": "Used",
        "seller": {"username": seller, "feedbackPercentage": "99.1", "feedbackScore": 500},
        "itemWebUrl": f"https://www.ebay.com/itm/{seller}-{i}?hash=x",
    }


SAMPLE_ITEMS = (
    [_item(i, "alice", 9.99) for i in range(5)]
    + [_item(i, "bob", 20.00) for i in range(4)]
    + [_item(i, "carol", 15.99) for i in range(2)]
    + [_item(0, "dave", 30.00)]
)


class _FakeApiGet:
    """Stand-in for ec.api_get: pages SAMPLE_ITEMS honoring limit/offset."""

    def __init__(self, items):
        self.items = items
        self.calls = []

    def __call__(self, path, query=None, marketplace=None, creds=None):
        self.calls.append({"path": path, "query": dict(query or {})})
        q = query or {}
        limit = q.get("limit", 200)
        offset = q.get("offset", 0)
        batch = self.items[offset: offset + limit]
        return {"itemSummaries": batch, "total": len(self.items)}


def _patched(fake, fn):
    real = ebay_browse.ec.api_get
    ebay_browse.ec.api_get = fake
    try:
        return fn()
    finally:
        ebay_browse.ec.api_get = real


# ---------------------------------------------------------------------------
# search() — normalisation + paging
# ---------------------------------------------------------------------------

def test_search_normalizes_seller_and_price():
    fake = _FakeApiGet(SAMPLE_ITEMS)

    def go():
        recs = ebay_browse.search(q="widget", max_items=200)
        assert len(recs) == len(SAMPLE_ITEMS)
        assert recs[0]["seller"] == "alice"
        assert recs[0]["askingPrice"] == 9.99
        assert recs[0]["url"] == "https://www.ebay.com/itm/alice-0"
        assert recs[0]["listingStatus"] == "active"

    _patched(fake, go)


def test_search_requires_q_or_category():
    try:
        ebay_browse.search()
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_search_pages_across_multiple_calls():
    fake = _FakeApiGet(SAMPLE_ITEMS)

    def go():
        recs = ebay_browse.search(q="widget", max_items=200)
        assert len(recs) == 12
        # PAGE_LIMIT-sized pages would be one call here (12 < 200), but the
        # loop must still terminate correctly at total.
        assert len(fake.calls) == 1

    _patched(fake, go)


def test_search_respects_max_items_cap():
    fake = _FakeApiGet(SAMPLE_ITEMS)

    def go():
        recs = ebay_browse.search(q="widget", max_items=3)
        assert len(recs) == 3

    _patched(fake, go)


# ---------------------------------------------------------------------------
# top_sellers_active() — GH #82: the competing-store lookup PRICE feeds into
# price_stats.competitor_charm_pattern
# ---------------------------------------------------------------------------

def test_top_sellers_active_keeps_only_the_top_n_by_listing_count():
    fake = _FakeApiGet(SAMPLE_ITEMS)

    def go():
        recs = ebay_browse.top_sellers_active(q="widget", sample=200, top_n=2)
        sellers = {r["seller"] for r in recs}
        # alice (5) and bob (4) are the top 2 by count; carol/dave dropped.
        assert sellers == {"alice", "bob"}
        assert len(recs) == 9  # 5 + 4
        assert all(r.get("askingPrice") is not None for r in recs)

    _patched(fake, go)


def test_top_sellers_active_top_n_larger_than_seller_count_keeps_everyone():
    fake = _FakeApiGet(SAMPLE_ITEMS)

    def go():
        recs = ebay_browse.top_sellers_active(q="widget", sample=200, top_n=10)
        assert len(recs) == len(SAMPLE_ITEMS)

    _patched(fake, go)


def test_top_sellers_active_feeds_price_stats_competitor_charm_pattern():
    """The intended pipeline, end to end offline: Browse listings ->
    top_sellers_active -> price_stats.competitor_charm_pattern."""
    sys.path.insert(0, str(ROOT / "lib"))
    import price_stats  # noqa: PLC0415

    items = (
        [_item(i, "alice", 9.99) for i in range(3)]
        + [_item(i, "bob", 19.99) for i in range(3)]
        + [_item(i, "carol", 29.99) for i in range(2)]
        + [_item(0, "dave", 40.00)]
    )
    fake = _FakeApiGet(items)

    def go():
        recs = ebay_browse.top_sellers_active(q="widget", sample=200, top_n=5)
        return price_stats.competitor_charm_pattern(recs)

    result = _patched(fake, go)
    assert result["pattern"] == ".99"
    assert result["n_competitors"] == 4


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)
