"""A sold item is never re-listed (user rule 2026-09-28).

sync_actuals stamps SOLD.md and advances the ledger; publish refuses either
marker; later ledger writes can't make a sold row look listable again."""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import list_edit  # noqa: E402
import sync_actuals  # noqa: E402


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "listings_ledger.csv"
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(p))
    return p


def _status(path, sku):
    with path.open(newline="", encoding="utf-8") as f:
        return next(r["status"] for r in csv.DictReader(f) if r["sku"] == sku)


def test_sold_stamp_blocks_publish(tmp_path, ledger):
    d = tmp_path / "item"; d.mkdir()
    (d / "SOLD.md").write_text("# SOLD — x\n\n- Sold: 2026-09-27  ·  order 21-1\n", encoding="utf-8")
    with pytest.raises(list_edit.AlreadySoldError, match="order 21-1"):
        list_edit._refuse_if_sold(d / "draft.md", "abc")


def test_sold_ledger_row_blocks_publish(tmp_path, ledger):
    d = tmp_path / "item"; d.mkdir()
    list_edit.upsert_listing("abc", "SOLD")
    with pytest.raises(list_edit.AlreadySoldError, match="SOLD"):
        list_edit._refuse_if_sold(d / "draft.md", "abc")


def test_unsold_draft_passes(tmp_path, ledger):
    d = tmp_path / "item"; d.mkdir()
    list_edit.upsert_listing("abc", "SYNCED")
    list_edit._refuse_if_sold(d / "draft.md", "abc")


@pytest.mark.parametrize("cur,new,want", [
    ("SOLD", "SYNCED", "SOLD"), ("SOLD", "PUBLISHED", "SOLD"),
    ("SHIPPED", "SOLD", "SHIPPED"), ("SOLD", "SHIPPED", "SHIPPED"),
    ("SOLD", "ENDED", "ENDED"), ("PUBLISHED", "SOLD", "SOLD"),
])
def test_sold_status_is_sticky(ledger, cur, new, want):
    list_edit.upsert_listing("s1", cur)
    list_edit.upsert_listing("s1", new)
    assert _status(ledger, "s1") == want


def test_title_matched_sale_marks_the_drafts_own_sku(ledger):
    rows = [{"sku": "", "listing_id": "206", "shoot_dir": "inventory/x/dog"}]
    drafts = [{"dir": "inventory/x/dog", "sku": "4f32d8dd"}]
    assert sync_actuals.mark_sold_in_ledger(rows, drafts) == 1
    assert _status(ledger, "4f32d8dd") == "SOLD"
