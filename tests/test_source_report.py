#!/usr/bin/env python3
"""Regression tests for lib/source_report.py — bucket ROI reporting (#56).

Covers the acceptance criteria that are unit-testable without a live account:
bucket resolution by context.txt ownership (not path depth), the context.txt
parser tolerating all three shapes seen on disk (empty / prose-only / keyed +
prose), ROI suppression, the missing-basis-vs-channel-kind distinction, and
end-to-end gather() wiring for backup-dir exclusion + unattributed sales.

Run:  pytest tests/test_source_report.py
"""
import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import source_report as sr  # noqa: E402


# --------------------------------------------------------------------------
# bucket_for — nearest ancestor holding a context.txt, not path depth
# --------------------------------------------------------------------------

def test_bucket_is_the_context_txt_owner_not_the_top_two_segments(tmp_path):
    # ESTATES/SCJ owns context.txt directly -> it IS the bucket.
    scj = tmp_path / "ESTATES" / "SCJ"
    (scj / "item-1").mkdir(parents=True)
    (scj / "context.txt").write_text("kind: event\nspend: 575\n")
    assert sr.bucket_for(scj / "item-1", root=tmp_path) == scj
    assert sr.bucket_for(scj, root=tmp_path) == scj


def test_a_sub_lot_two_levels_deep_still_resolves_to_the_owning_bucket(tmp_path):
    # FREE/more-mags-444 is a sub-lot INSIDE the FREE channel bucket, not a
    # peer of it — this is exactly the grain the old "top two segments" read
    # got wrong after the ESTATES re-org.
    free = tmp_path / "FREE"
    lot = free / "more-mags-444" / "nested"
    lot.mkdir(parents=True)
    (free / "context.txt").write_text("kind: channel\n")
    assert sr.bucket_for(lot, root=tmp_path) == free


def test_a_reorg_that_moves_folders_does_not_change_the_bucket_answer(tmp_path):
    # Moving SCJ under a different parent doesn't change what "the bucket" is
    # — it's still "the nearest ancestor with a context.txt", wherever that
    # ancestor now lives.
    old = tmp_path / "ESTATES" / "SCJ" / "item-1"
    old.mkdir(parents=True)
    (tmp_path / "ESTATES" / "SCJ" / "context.txt").write_text("kind: event\n")
    new = tmp_path / "ARCHIVE" / "2026" / "SCJ" / "item-1"
    new.mkdir(parents=True)
    (tmp_path / "ARCHIVE" / "2026" / "SCJ" / "context.txt").write_text("kind: event\n")
    assert sr.bucket_for(old, root=tmp_path).name == "SCJ"
    assert sr.bucket_for(new, root=tmp_path).name == "SCJ"
    assert sr.bucket_for(old, root=tmp_path) != sr.bucket_for(new, root=tmp_path)


def test_no_context_txt_anywhere_up_to_root_is_unresolved(tmp_path):
    orphan = tmp_path / "MYSTERY" / "item-1"
    orphan.mkdir(parents=True)
    assert sr.bucket_for(orphan, root=tmp_path) is None


def test_walk_is_bounded_to_root_and_does_not_escape_it(tmp_path):
    # A context.txt sitting ABOVE root must never be picked up — otherwise an
    # unrelated file elsewhere on disk could silently become "the bucket".
    (tmp_path / "context.txt").write_text("kind: event\n")
    inv = tmp_path / "inventory"
    orphan = inv / "MYSTERY" / "item-1"
    orphan.mkdir(parents=True)
    assert sr.bucket_for(orphan, root=inv) is None


def test_bucket_label_is_relative_to_root(tmp_path):
    scj = tmp_path / "ESTATES" / "SCJ"
    scj.mkdir(parents=True)
    assert sr.bucket_label(scj, root=tmp_path) == "ESTATES/SCJ"


# --------------------------------------------------------------------------
# context.txt parsing — empty / prose-only / keyed-plus-prose
# --------------------------------------------------------------------------

def test_empty_file_parses_to_all_none():
    assert sr.parse_context("") == {"kind": None, "spend": None, "acquired": None}


def test_prose_only_file_does_not_recover_spend_by_pattern_matching():
    # The whole point of #56: "Spend $575" as ENGLISH must not become the
    # spend field. Only an explicit `spend:` key line counts.
    text = ("An estate sale in Social Circle Georgia. Loaded truck twice. "
            "Spend $575 all told, worth every penny.")
    got = sr.parse_context(text)
    assert got == {"kind": None, "spend": None, "acquired": None}


def test_keyed_lines_plus_prose_parses_the_keys_and_keeps_the_prose_intact():
    text = (
        "kind: event\n"
        "spend: 575\n"
        "acquired: 2026-07\n"
        "\n"
        "An estate sale in Social Circle Georgia. Loaded truck twice. "
        "Spend $575 all told, worth every penny.\n"
    )
    got = sr.parse_context(text)
    assert got == {"kind": "event", "spend": 575.0, "acquired": "2026-07"}


def test_dollar_sign_and_commas_in_spend_are_tolerated():
    assert sr.parse_context("spend: $1,250.50\n")["spend"] == 1250.50


def test_unrecognised_kind_value_is_treated_as_unset():
    assert sr.parse_context("kind: garage-sale\n")["kind"] is None


def test_load_context_missing_file_is_all_none(tmp_path):
    assert sr.load_context(tmp_path) == {"kind": None, "spend": None, "acquired": None}


def test_load_context_reads_the_real_file(tmp_path):
    (tmp_path / "context.txt").write_text("kind: channel\n")
    assert sr.load_context(tmp_path)["kind"] == "channel"


# --------------------------------------------------------------------------
# ROI suppression + missing-basis-vs-channel-kind distinction
# --------------------------------------------------------------------------

def test_roi_computed_for_an_event_bucket_with_recorded_spend():
    assert sr.roi_for(1169.0, 575.0, "event") == pytest.approx(1169 / 575)


def test_roi_suppressed_not_zero_for_channel_kind_even_with_a_number_present():
    # A channel bucket with SOME dollar figure recorded still gets no ROI —
    # the axis is meaningless for an ongoing habit, not merely "zero".
    assert sr.roi_for(910.0, 100.0, "channel") is None


def test_roi_suppressed_not_infinite_when_basis_is_absent():
    assert sr.roi_for(910.0, None, "event") is None


def test_roi_suppressed_when_spend_is_zero_rather_than_a_real_basis():
    assert sr.roi_for(910.0, 0.0, "event") is None


def test_roi_suppressed_when_kind_is_unspecified():
    assert sr.roi_for(500.0, 200.0, None) is None


def test_missing_spend_on_an_event_bucket_is_flagged_a_gap():
    assert sr.is_basis_gap("event", None) is True


def test_missing_spend_on_a_channel_bucket_is_not_a_gap():
    assert sr.is_basis_gap("channel", None) is False


def test_missing_spend_with_unspecified_kind_is_not_asserted_a_gap():
    # We don't know the intended kind, so this is not confidently flagged as
    # a data gap — only an explicit `kind: event` triggers the flag.
    assert sr.is_basis_gap(None, None) is False


def test_recorded_spend_on_an_event_bucket_is_not_a_gap():
    assert sr.is_basis_gap("event", 575.0) is False


def test_sell_through_percentage():
    assert sr.sell_through(sold_n=3, live_n=1) == 75.0
    assert sr.sell_through(sold_n=0, live_n=0) is None


# --------------------------------------------------------------------------
# backup-dir exclusion
# --------------------------------------------------------------------------

def test_backup_dirs_are_recognised_by_name_or_dot_prefix():
    assert sr._is_backup_path("inventory/FREE/_prepped/item-1")
    assert sr._is_backup_path("inventory/ESTATES/SCJ/.prior-run-bak/item-1")
    assert sr._is_backup_path("inventory/ESTATES/SCJ/.orig/foo.jpg")
    assert not sr._is_backup_path("inventory/ESTATES/SCJ/item-1")


# --------------------------------------------------------------------------
# gather() — end-to-end wiring over a small fixture tree
# --------------------------------------------------------------------------

_SALES_FIELDS = [
    "order_id", "sold_at", "listing_id", "sku", "title", "quantity", "sold_format",
    "item_price", "buyer_shipping", "refunded", "gross", "ebay_fee",
    "net_before_postage", "listed_price", "pct_of_ask", "shoot_dir", "matched_by",
]


def _sale(order_id, shoot_dir, *, gross, fee, net):
    return {
        "order_id": order_id, "sold_at": "2026-07-01", "listing_id": f"L{order_id}",
        "sku": f"s{order_id}", "title": f"t{order_id}", "quantity": "1",
        "sold_format": "FIXED_PRICE", "item_price": str(gross), "buyer_shipping": "0",
        "refunded": "0", "gross": str(gross), "ebay_fee": str(fee),
        "net_before_postage": str(net), "listed_price": str(gross), "pct_of_ask": "100",
        "shoot_dir": shoot_dir, "matched_by": "listing_id" if shoot_dir else "unmatched",
    }


@pytest.fixture
def fixture_repo(tmp_path, monkeypatch):
    """A tiny inventory tree + sales_ledger.csv, wired into source_report's
    (and its lib/report.py dependency's) module-level path constants."""
    inv = tmp_path / "inventory"
    (inv / "ESTATES" / "SCJ" / "item-1").mkdir(parents=True)
    (inv / "ESTATES" / "SCJ" / "context.txt").write_text("kind: event\nspend: 575\n")
    (inv / "FREE" / "more-mags-444").mkdir(parents=True)
    (inv / "FREE" / "context.txt").write_text("kind: channel\n")
    (inv / "FREE" / "_prepped" / "backup-item").mkdir(parents=True)

    sales = tmp_path / "sales_ledger.csv"
    rows = [
        _sale("1", "inventory/ESTATES/SCJ/item-1", gross=100, fee=13, net=87),
        _sale("2", "inventory/FREE/more-mags-444", gross=20, fee=3, net=17),
        _sale("3", "", gross=15, fee=2, net=13),                      # unattributed
        _sale("4", "inventory/FREE/_prepped/backup-item", gross=9, fee=1, net=8),
    ]
    with sales.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_SALES_FIELDS)
        w.writeheader()
        w.writerows(rows)

    monkeypatch.setattr(sr, "REPO", tmp_path)
    monkeypatch.setattr(sr, "INVENTORY", inv)
    monkeypatch.setattr(sr, "SALES_LEDGER", sales)
    monkeypatch.setattr(sr._report, "REPO", tmp_path)
    monkeypatch.setattr(sr._report, "INVENTORY", inv)
    monkeypatch.setattr(sr._report, "LEDGER", tmp_path / "listings_ledger.csv")
    return tmp_path


def test_gather_buckets_sales_by_context_txt_ownership(fixture_repo):
    d = sr.gather()
    by_key = {b["key"]: b for b in d["buckets"]}
    assert by_key["ESTATES/SCJ"]["sold_n"] == 1
    assert by_key["ESTATES/SCJ"]["net"] == pytest.approx(87.0)
    assert by_key["FREE"]["sold_n"] == 1
    assert by_key["FREE"]["net"] == pytest.approx(17.0)


def test_gather_excludes_backup_dirs_from_counts(fixture_repo):
    d = sr.gather()
    by_key = {b["key"]: b for b in d["buckets"]}
    # Only the real FREE sale counts; the _prepped/ backup sale must not
    # inflate FREE's sold count or gross.
    assert by_key["FREE"]["sold_n"] == 1
    assert by_key["FREE"]["gross"] == pytest.approx(20.0)


def test_gather_reports_unattributed_sales_as_their_own_line(fixture_repo):
    d = sr.gather()
    assert d["unattributed"]["sold_n"] == 1
    assert d["unattributed"]["net"] == pytest.approx(13.0)


def test_gather_flags_event_bucket_gap_and_computes_roi_for_the_known_one(fixture_repo):
    d = sr.gather()
    by_key = {b["key"]: b for b in d["buckets"]}
    assert by_key["ESTATES/SCJ"]["gap"] is False
    assert by_key["ESTATES/SCJ"]["roi"] == pytest.approx(87 / 575)
    assert by_key["FREE"]["gap"] is False          # channel, missing spend is correct
    assert by_key["FREE"]["roi"] is None


def test_gather_total_excludes_missing_basis_buckets_from_cost_not_zeros_it(fixture_repo):
    d = sr.gather()
    # Only ESTATES/SCJ has a recorded spend; FREE (channel, no spend) must not
    # contribute 0 to the cost total or otherwise be counted as pure profit.
    assert d["total"]["cost"] == pytest.approx(575.0)
    assert d["total"]["cost_bucket_n"] == 1


def test_render_table_states_the_postage_boundary(fixture_repo):
    out = sr.render_table(sr.gather())
    assert "postage" in out.lower()


def test_render_table_never_shows_missing_basis_as_a_profit_number(fixture_repo):
    d = sr.gather()
    out = sr.render_table(d)
    # FREE has no recorded spend and must render as a dash, not as its net
    # figure standing in for profit.
    by_key = {b["key"]: b for b in d["buckets"]}
    assert by_key["FREE"]["profit"] is None
    assert "basis not recorded" in out or "—" in out


# --------------------------------------------------------------------------
# #119 (route B, sell.finances) — the "before postage AND before
# advertising" qualifier comes off only when real ad-fee/postage data is
# actually available, and says why in one line when it isn't.
# --------------------------------------------------------------------------
_SALES_FIELDS_119 = _SALES_FIELDS + ["ad_fee", "actual_postage"]


def _write_sales(tmp_path, rows):
    sales = tmp_path / "sales_ledger.csv"
    with sales.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_SALES_FIELDS_119)
        w.writeheader()
        w.writerows(rows)
    return sales


def test_gather_reports_no_finances_coverage_when_columns_are_blank(fixture_repo):
    # fixture_repo's rows predate #119 — no ad_fee/actual_postage columns at
    # all, the exact shape sales_ledger.csv had before this PR.
    d = sr.gather()
    assert d["fin_covered_n"] == 0
    assert d["fin_ad_fee_total"] is None
    note = sr._fin_note(d)
    assert "before postage AND before advertising" in note


def test_gather_reports_zero_coverage_with_columns_present_says_so(fixture_repo, monkeypatch):
    # Post-#119 ledger shape (columns present) but nothing matched yet this
    # run — must NOT claim the column is absent (that's the pre-#119 case
    # covered by test_gather_reports_no_finances_coverage_when_columns_are_blank).
    rows = [
        {**_sale("1", "inventory/ESTATES/SCJ/item-1", gross=100, fee=13, net=87),
         "ad_fee": "", "actual_postage": ""},
    ]
    sales = _write_sales(fixture_repo, rows)
    monkeypatch.setattr(sr, "SALES_LEDGER", sales)

    d = sr.gather()
    assert d["fin_covered_n"] == 0
    assert d["fin_columns_present"] is True
    note = sr._fin_note(d)
    assert "carries no actual-postage column" not in note
    assert "finances_sync_status.json" in note


def test_gather_reports_full_finances_coverage(fixture_repo, monkeypatch):
    rows = [
        {**_sale("1", "inventory/ESTATES/SCJ/item-1", gross=100, fee=13, net=87),
         "ad_fee": "4.00", "actual_postage": "6.50"},
        {**_sale("2", "inventory/FREE/more-mags-444", gross=20, fee=3, net=17),
         "ad_fee": "0.00", "actual_postage": "3.00"},
    ]
    sales = _write_sales(fixture_repo, rows)
    monkeypatch.setattr(sr, "SALES_LEDGER", sales)

    d = sr.gather()
    assert d["fin_covered_n"] == 2 == d["fin_total_n"]
    assert d["fin_ad_fee_total"] == pytest.approx(4.00)
    assert d["fin_postage_total"] == pytest.approx(9.50)
    note = sr._fin_note(d)
    assert "before postage AND before advertising" not in note
    assert "#119" in note


def test_gather_reports_partial_finances_coverage_and_says_why(fixture_repo, monkeypatch):
    rows = [
        {**_sale("1", "inventory/ESTATES/SCJ/item-1", gross=100, fee=13, net=87),
         "ad_fee": "4.00", "actual_postage": "6.50"},
        {**_sale("2", "inventory/FREE/more-mags-444", gross=20, fee=3, net=17),
         "ad_fee": "", "actual_postage": ""},   # not read yet
    ]
    sales = _write_sales(fixture_repo, rows)
    monkeypatch.setattr(sr, "SALES_LEDGER", sales)

    d = sr.gather()
    assert d["fin_covered_n"] == 1
    assert d["fin_total_n"] == 2
    note = sr._fin_note(d)
    assert "1 of 2" in note
    assert "before postage AND before advertising" in note
