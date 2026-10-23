#!/usr/bin/env python3
"""lib/report.py per store (#156): one store's ledger, one store's sales, and
only the drafts that belong to it — the same `draft_belongs_to_store()` rule
lib/sync_actuals.py matches orders with.

Run:  pytest tests/test_report_stores.py
"""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import report as R  # noqa: E402
import stores  # noqa: E402

THREE_STORES = {"ebay": {"stores": {"junk": {}, "outlet": {}}}}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(stores, "REPO", tmp_path)
    monkeypatch.setattr(stores, "load_config", lambda: THREE_STORES)
    for var in ("EBAYBIZ_STORE", "EBAYBIZ_LISTINGS_LEDGER", "EBAYBIZ_LISTINGS_LOG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(R, "REPO", tmp_path)
    monkeypatch.setattr(R, "INVENTORY", tmp_path / "inventory")
    for rel, sku, store in (("a", "aaaa1111", None), ("b", "bbbb2222", "junk"),
                            ("c", "cccc3333", None)):
        d = tmp_path / "inventory" / rel
        d.mkdir(parents=True)
        fm = [f'title: "item {rel}"', 'price: "10.00"', f'ebay_inventory_sku: "{sku}"',
              'published_at: "2026-08-01T18:00:00Z"']
        if store:
            fm.append(f'store: "{store}"')
        (d / "draft.md").write_text("---\n" + "\n".join(fm) + "\n---\nbody\n", encoding="utf-8")
    _ledger(tmp_path / "listings_ledger.csv", ["aaaa1111"])
    _ledger(tmp_path / "listings_ledger-junk.csv", ["bbbb2222", "cccc3333"])   # c relisted
    return tmp_path


def _ledger(path, skus):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["sku", "status", "title", "price"])
        w.writeheader()
        w.writerows({"sku": s, "status": "PUBLISHED", "title": s, "price": "10"} for s in skus)


def _sales(path, n):
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["order_id", "sold_at", "title", "gross"])
        w.writeheader()
        w.writerows({"order_id": str(i), "sold_at": "2026-08-02", "title": "x", "gross": "1"}
                    for i in range(n))


def _skus(rows):
    return sorted(r["sku"] for r in rows)


def test_default_store_paths_are_the_historic_names(repo):
    assert R._ledger_path("default") == repo / "listings_ledger.csv"
    assert R.sales_path("default") == repo / "sales_ledger.csv"


def test_collect_is_one_stores_ledger_and_drafts(repo):
    assert _skus(R.collect("default")) == ["aaaa1111", "cccc3333"]
    assert _skus(R.collect("junk")) == ["bbbb2222", "cccc3333"]
    assert R.collect("outlet") == []


def test_load_sales_reads_only_that_stores_ledger_three_stores(repo):
    _sales(repo / "sales_ledger.csv", 1)
    _sales(repo / "sales_ledger-junk.csv", 2)
    _sales(repo / "sales_ledger-outlet.csv", 3)
    assert [len(R.load_sales(store=s)) for s in ("default", "junk", "outlet")] == [1, 2, 3]


def test_a_patched_legacy_constant_only_redirects_the_default_store(repo, monkeypatch):
    # tests/test_dashboard.py patches report.LEDGER; that must never leak
    # into a named store's lookup.
    other = repo / "elsewhere.csv"
    monkeypatch.setattr(R, "LEDGER", other)
    assert R._ledger_path("default") == other
    assert R._ledger_path("junk") == repo / "listings_ledger-junk.csv"


def test_draft_store_field_reads_frontmatter_only():
    assert R.draft_store_field('---\nstore: "junk"\n---\n') == "junk"
    assert R.draft_store_field('---\nstore: ""\n---\n') == "default"
    assert R.draft_store_field('---\ntitle: "x"\n---\nstore: junk\n') == "default"
    # an invalid name matches no real store rather than defaulting
    assert R.draft_store_field('---\nstore: "no good"\n---\n') == "no good"


# The shape of a real published draft (inventory/ES/MON1020-M, 2026-10-23):
# the ids live under `meta:`, `meta.notes` has its own `title:`/`price:` lines,
# and `_field_constraints` repeats both keys. Only the top-level ones count.
LIVE_DRAFT = """---
template_version: v1
store: ""
meta:
  drafted_at: "2026-10-23T00:53:32Z"
  ebay_inventory_sku: "dddd4444"
  ebay_listing_id: "880000000184"
  notes: |
    title: INVESTIGATE claim 1, 80/80; no lead word earned
    price: 127.00 = Push-high (house rule: list at Push-high, ease down)
  published_at: "2026-10-23T01:04:57Z"
title: {title}
price: "127.00"
_field_constraints:
  title:                                    {{ required: true, max_len: 80 }}
  price:                                    {{ required: true, max_len: 13, numeric: true }}
---
body
"""


@pytest.mark.parametrize("title_yaml, title", [
    ('"Pair of Wm Rogers Sterling Candlesticks"', "Pair of Wm Rogers Sterling Candlesticks"),
    ("'Rhinestone Brooch 2\" Gold Tone'", 'Rhinestone Brooch 2" Gold Tone'),   # #270
])
def test_live_draft_ask_is_the_top_level_price_not_a_notes_line(repo, title_yaml, title):
    d = repo / "inventory" / "d"
    d.mkdir()
    (d / "draft.md").write_text(LIVE_DRAFT.format(title=title_yaml), encoding="utf-8")
    with (repo / "listings_ledger.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["sku", "listing_id", "status", "title", "price"])
        w.writeheader()
        w.writerow({"sku": "dddd4444", "listing_id": "880000000184", "status": "PUBLISHED",
                    "title": "ledger title", "price": "99.00"})

    row, = [r for r in R.collect("default") if r["sku"] == "dddd4444"]
    assert (row["price"], row["title"], row["listing_id"], row["published_at"], row["src"]) == \
        ("127.00", title, "880000000184", "2026-10-23T01:04:57Z", "both")


def test_a_draft_that_is_not_yaml_warns_and_leaves_the_ledger_values(repo, capsys):
    (repo / "inventory" / "a" / "draft.md").write_text(
        '---\ntitle: "item a\nprice: [\n---\nbody\n', encoding="utf-8")
    row, = [r for r in R.collect("default") if r["sku"] == "aaaa1111"]
    assert (row["price"], row["src"]) == ("10", "ledger")
    assert "frontmatter is not valid YAML" in capsys.readouterr().err
