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
    assert sr.parse_context("") == {"kind": None, "spend": None, "spend_unit": "lot",
                                     "acquired": None}


def test_prose_only_file_does_not_recover_spend_by_pattern_matching():
    # The whole point of #56: "Spend $575" as ENGLISH must not become the
    # spend field. Only an explicit `spend:` key line counts.
    text = ("An estate sale in Social Circle Georgia. Loaded truck twice. "
            "Spend $575 all told, worth every penny.")
    got = sr.parse_context(text)
    assert got == {"kind": None, "spend": None, "spend_unit": "lot", "acquired": None}


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
    assert got == {"kind": "event", "spend": 575.0, "spend_unit": "lot", "acquired": "2026-07"}


def test_dollar_sign_and_commas_in_spend_are_tolerated():
    assert sr.parse_context("spend: $1,250.50\n")["spend"] == 1250.50


def test_unrecognised_kind_value_is_treated_as_unset():
    assert sr.parse_context("kind: garage-sale\n")["kind"] is None


# --------------------------------------------------------------------------
# spend_unit — the #118 schema gap: lot (default) vs item vs pair
# --------------------------------------------------------------------------

def test_spend_unit_defaults_to_lot_when_absent():
    # Backward compatible with every real file and every fixture written
    # before #118 — a bare `spend:` means "the whole lot", same as always.
    assert sr.parse_context("spend: 575\n")["spend_unit"] == "lot"


def test_spend_unit_item_is_read_explicitly():
    got = sr.parse_context("spend: 8\nspend_unit: item\n")
    assert got["spend"] == 8.0
    assert got["spend_unit"] == "item"


def test_spend_unit_pair_is_read_explicitly():
    assert sr.parse_context("spend_unit: pair\n")["spend_unit"] == "pair"


def test_unrecognised_spend_unit_value_falls_back_to_lot():
    assert sr.parse_context("spend_unit: box\n")["spend_unit"] == "lot"


def test_load_context_missing_file_is_all_none(tmp_path):
    assert sr.load_context(tmp_path) == {"kind": None, "spend": None, "spend_unit": "lot",
                                          "acquired": None}


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
# resolve_cost_basis — the #118 spend_unit division: lot / item / pair
# --------------------------------------------------------------------------

def test_resolve_cost_basis_lot_passes_spend_through_unchanged():
    # Old single-scalar behaviour, unit_count is irrelevant for a lot.
    assert sr.resolve_cost_basis(575.0, "lot", unit_count=0) == 575.0
    assert sr.resolve_cost_basis(575.0, None, unit_count=7) == 575.0


def test_resolve_cost_basis_item_multiplies_by_unit_count():
    assert sr.resolve_cost_basis(8.0, "item", unit_count=12) == pytest.approx(96.0)


def test_resolve_cost_basis_pair_multiplies_by_unit_count():
    assert sr.resolve_cost_basis(15.0, "pair", unit_count=4) == pytest.approx(60.0)


def test_resolve_cost_basis_item_with_zero_units_is_zero_not_none():
    # No items counted yet is zero dollars of resolved basis so far, not an
    # unknown one — a real, if provisional, number, distinct from "no spend:
    # key recorded at all" (which stays None).
    assert sr.resolve_cost_basis(8.0, "item", unit_count=0) == 0.0


def test_resolve_cost_basis_missing_spend_is_none_regardless_of_unit():
    assert sr.resolve_cost_basis(None, "item", unit_count=12) is None
    assert sr.resolve_cost_basis(None, "lot", unit_count=0) is None


def test_resolve_cost_basis_never_goes_negative_on_a_bad_unit_count():
    assert sr.resolve_cost_basis(8.0, "item", unit_count=-3) == 0.0


# --------------------------------------------------------------------------
# missing_spend_with_sales — the #118 validation check
# --------------------------------------------------------------------------

def test_missing_spend_with_sales_flags_a_sold_bucket_with_no_cost_basis():
    rows = [{"key": "FREE", "sold_n": 2, "cost_known": False}]
    assert sr.missing_spend_with_sales(rows) == ["FREE"]


def test_missing_spend_with_sales_does_not_flag_one_that_has_it():
    rows = [{"key": "ESTATES/SCJ", "sold_n": 1, "cost_known": True}]
    assert sr.missing_spend_with_sales(rows) == []


def test_missing_spend_with_sales_ignores_a_bucket_with_no_sales_yet():
    # No sales = nothing actionable yet, regardless of cost_known.
    rows = [{"key": "ESTATES/NEW", "sold_n": 0, "cost_known": False}]
    assert sr.missing_spend_with_sales(rows) == []


def test_missing_spend_with_sales_is_not_limited_to_event_kind():
    # Unlike is_basis_gap(), this check does not care about `kind` — a
    # channel bucket that has already sold something still needs a
    # recorded spend: to keep COST/PROFIT honest, even with no ROI shown.
    rows = [{"key": "THRIFT", "sold_n": 3, "cost_known": False, "kind": "channel"}]
    assert sr.missing_spend_with_sales(rows) == ["THRIFT"]


def test_missing_spend_with_sales_sorts_and_names_every_flagged_bucket():
    rows = [
        {"key": "ZZZ", "sold_n": 1, "cost_known": False},
        {"key": "AAA", "sold_n": 1, "cost_known": False},
        {"key": "MMM", "sold_n": 1, "cost_known": True},
    ]
    assert sr.missing_spend_with_sales(rows) == ["AAA", "ZZZ"]


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


def test_gather_needs_spend_flags_the_channel_bucket_that_sold_with_no_basis(fixture_repo):
    # FREE (channel, no spend:) has a real sale in the fixture — the #118
    # validation check must name it, unlike the strict event-only `gaps` list.
    d = sr.gather()
    assert d["needs_spend"] == ["FREE"]
    assert "ESTATES/SCJ" not in d["needs_spend"]         # has a recorded spend


def test_render_table_surfaces_one_needs_spend_summary_line(fixture_repo):
    out = sr.render_table(sr.gather())
    assert "ebz context" in out
    assert "FREE" in out


def test_draw_html_surfaces_the_needs_spend_note(fixture_repo):
    html_out = sr.draw(sr.gather())
    assert "ebz context" in html_out
    assert "FREE" in html_out


# --------------------------------------------------------------------------
# nested buckets, end to end through gather() — not just bucket_for()
# --------------------------------------------------------------------------
#
# The issue's own author made exactly this mistake against live data: an
# `inventory/`-walker bounded by a shallow max-depth silently rolls a
# sub-lot's sales up into its PARENT bucket instead of the sub-lot's own
# context.txt. bucket_for() is unit-tested against this directly
# (test_a_sub_lot_two_levels_deep_still_resolves_to_the_owning_bucket), but
# that only proves the resolver is correct in isolation — this fixture runs
# a sale under a sub-lot THAT HAS ITS OWN context.txt through the full
# gather() pipeline (sales_ledger.csv -> bucket_for() -> per-bucket rows) to
# confirm the sub-lot's sale rolls up to the sub-lot, not the parent,
# end-to-end.

@pytest.fixture
def nested_fixture_repo(tmp_path, monkeypatch):
    inv = tmp_path / "inventory"
    # Parent: FREE, a channel bucket with its own sale.
    (inv / "FREE" / "misc-item").mkdir(parents=True)
    (inv / "FREE" / "context.txt").write_text("kind: channel\n")
    # Sub-lot: FREE/more-mags-444, TWO levels under FREE, with its OWN
    # context.txt — a real acquisition nested inside an ongoing channel.
    (inv / "FREE" / "more-mags-444" / "item-1").mkdir(parents=True)
    (inv / "FREE" / "more-mags-444" / "context.txt").write_text(
        "kind: event\nspend: 40\n")

    sales = tmp_path / "sales_ledger.csv"
    rows = [
        _sale("10", "inventory/FREE/misc-item", gross=12, fee=2, net=10),
        _sale("11", "inventory/FREE/more-mags-444/item-1", gross=30, fee=4, net=26),
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


def test_gather_rolls_a_nested_sub_lot_sale_up_to_the_sub_lot_not_the_parent(nested_fixture_repo):
    d = sr.gather()
    by_key = {b["key"]: b for b in d["buckets"]}
    assert set(by_key) == {"FREE", "FREE/more-mags-444"}

    # The parent's own sale stays on the parent, untouched by the sub-lot.
    assert by_key["FREE"]["sold_n"] == 1
    assert by_key["FREE"]["net"] == pytest.approx(10.0)

    # The sub-lot's sale rolls up to ITSELF, not FREE — the exact mistake
    # a shallow-max-depth context.txt walk would make against live data.
    assert by_key["FREE/more-mags-444"]["sold_n"] == 1
    assert by_key["FREE/more-mags-444"]["net"] == pytest.approx(26.0)
    assert by_key["FREE/more-mags-444"]["kind"] == "event"
    assert by_key["FREE/more-mags-444"]["cost_basis"] == pytest.approx(40.0)
    assert by_key["FREE/more-mags-444"]["roi"] == pytest.approx(26 / 40)

    # Neither bucket's sale total leaks into the other's.
    assert by_key["FREE"]["net"] != by_key["FREE/more-mags-444"]["net"]


# --------------------------------------------------------------------------
# spend_unit end-to-end through gather() — item/pair rate x unit_count
# --------------------------------------------------------------------------

@pytest.fixture
def spend_unit_fixture_repo(tmp_path, monkeypatch):
    inv = tmp_path / "inventory"
    (inv / "THRIFT" / "item-1").mkdir(parents=True)
    (inv / "THRIFT" / "item-2").mkdir(parents=True)
    # $5/item, resolved against however many listings THRIFT has (sold+live+pending).
    (inv / "THRIFT" / "context.txt").write_text("kind: event\nspend: 5\nspend_unit: item\n")

    sales = tmp_path / "sales_ledger.csv"
    rows = [
        _sale("20", "inventory/THRIFT/item-1", gross=20, fee=3, net=17),
        _sale("21", "inventory/THRIFT/item-2", gross=18, fee=2, net=16),
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


def test_gather_resolves_a_per_item_spend_against_the_buckets_own_unit_count(spend_unit_fixture_repo):
    d = sr.gather()
    b = next(x for x in d["buckets"] if x["key"] == "THRIFT")
    # 2 sold listings, no live/pending -> unit_count 2 -> $5 x 2 = $10 basis.
    assert b["unit_count"] == 2
    assert b["cost_basis"] == pytest.approx(10.0)
    assert b["cost_known"] is True
    assert b["gap"] is False
    assert b["roi"] == pytest.approx((17 + 16) / 10)
