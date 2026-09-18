#!/usr/bin/env python3
"""Regression tests for the Pacific reporting-timezone conversion (#122).

Three shapes, matching the issue's "Tests worth having":

  * a fixture order timestamped inside the 00:00-07:00 UTC window lands in
    the PREVIOUS Pacific day/month via to_report_date()/to_report_month();
  * fixtures on each side of a real US DST transition (spring-forward and
    fall-back), confirming ZoneInfo picks PST/PDT correctly rather than a
    fixed offset silently misbucketing the changeover days;
  * a reconciliation test: an API-shaped fixture (raw UTC `creationDate`
    values) and a Seller-Hub-shaped fixture (eBay's own, already-Pacific
    month total) agree on order count once boundary-converted -- where a
    naive UTC truncation would disagree, reproducing the exact discrepancy
    #122 reports against eBay's own downloads.

Run:  pytest tests/test_report_timezone.py
"""
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import report as R  # noqa: E402


# --------------------------------------------------------------------------
# the core bug: 00:00-07:00 UTC falls in the PREVIOUS Pacific day
# --------------------------------------------------------------------------

def test_early_utc_order_lands_in_the_previous_pacific_day():
    # 2026-08-01T05:00:00Z is 2026-07-31T22:00:00 Pacific (PDT, UTC-7) -- an
    # evening sale that the naive `creationDate[:10]` truncation this PR
    # replaces would have counted on August 1st instead of July 31st.
    assert R.to_report_date("2026-08-01T05:00:00.000Z") == date(2026, 7, 31)


def test_early_utc_order_lands_in_the_previous_pacific_month():
    # Same order: July under Pacific bucketing, August under naive UTC
    # truncation -- the exact discrepancy #122 reports against eBay's own
    # Seller Hub month totals.
    assert R.to_report_month("2026-08-01T05:00:00.000Z") == "2026-07"
    naive_utc_truncation_month = "2026-08-01T05:00:00.000Z"[:7]
    assert naive_utc_truncation_month == "2026-08"


def test_utc_order_at_or_after_the_pacific_offset_stays_same_day():
    # 08:00 UTC is past the PDT (UTC-7) rollover -> 01:00 Pacific, same
    # calendar day as the UTC date -- confirms the window boundary itself,
    # not just "always shift back a day".
    assert R.to_report_date("2026-08-01T08:00:00.000Z") == date(2026, 8, 1)


def test_empty_and_unparsable_input_returns_none():
    assert R.to_report_date("") is None
    assert R.to_report_date("not-a-date") is None
    assert R.to_report_month("") is None
    assert R.to_report_month("also not a date") is None


# --------------------------------------------------------------------------
# DST transitions -- must use the IANA zone, not a fixed offset
# --------------------------------------------------------------------------

def test_spring_forward_2026_uses_pst_before_and_pdt_after():
    # US DST starts 2026-03-08 at 02:00 local (clocks jump to 03:00), which
    # is 10:00 UTC. Just before the rollover: PST (UTC-8). Just after: PDT
    # (UTC-7). A fixed offset gets one side of this pair wrong.
    before = "2026-03-08T09:59:00.000Z"   # 01:59 PST
    after = "2026-03-08T10:01:00.000Z"    # 03:01 PDT
    dt_before = R._parse_utc(before).astimezone(R.REPORTING_TZ)
    dt_after = R._parse_utc(after).astimezone(R.REPORTING_TZ)
    assert dt_before.utcoffset().total_seconds() / 3600 == -8
    assert dt_after.utcoffset().total_seconds() / 3600 == -7
    # Both still land on the same Pacific calendar day (the rollover itself
    # doesn't cross midnight this time) -- the offset is what must differ.
    assert R.to_report_date(before) == R.to_report_date(after) == date(2026, 3, 8)


def test_fall_back_2026_uses_pdt_before_and_pst_after():
    # US DST ends 2026-11-01 at 02:00 local (PDT), clocks fall back to
    # 01:00 (PST) -- the 09:00 UTC rollover.
    before = "2026-11-01T08:59:00.000Z"   # 01:59 PDT
    after = "2026-11-01T09:01:00.000Z"    # 01:01 PST (after falling back)
    dt_before = R._parse_utc(before).astimezone(R.REPORTING_TZ)
    dt_after = R._parse_utc(after).astimezone(R.REPORTING_TZ)
    assert dt_before.utcoffset().total_seconds() / 3600 == -7
    assert dt_after.utcoffset().total_seconds() / 3600 == -8


def test_a_january_and_a_july_timestamp_pick_different_fixed_offsets():
    # A hard-coded "PST" (UTC-8) would be right in January and wrong in
    # July; ZoneInfo picks the correct one for each without being told
    # which season it is.
    winter = R._parse_utc("2026-01-15T20:00:00.000Z").astimezone(R.REPORTING_TZ)
    summer = R._parse_utc("2026-07-15T20:00:00.000Z").astimezone(R.REPORTING_TZ)
    assert winter.utcoffset().total_seconds() / 3600 == -8
    assert summer.utcoffset().total_seconds() / 3600 == -7


# --------------------------------------------------------------------------
# reconciliation: API-derived Pacific month counts agree with Seller Hub's
# own month counts, where naive UTC truncation would not (#122's core bug)
# --------------------------------------------------------------------------

def test_api_and_seller_hub_order_counts_agree_once_pacific_bucketed():
    # "API" fixture: raw creationDate timestamps as the Fulfillment API
    # actually returns them (UTC, trailing `Z`). Two of these are the exact
    # #122 case -- created just after 00:00 UTC on the 1st, which is still
    # the evening of the last day of the PREVIOUS month in Pacific.
    api_orders_creation_dates = [
        "2026-07-05T18:00:00.000Z",   # 11:00 Pacific -- unambiguous July
        "2026-07-15T23:00:00.000Z",   # 16:00 Pacific -- unambiguous July
        "2026-07-31T23:30:00.000Z",   # 16:30 Pacific -- unambiguous July
        "2026-08-01T02:00:00.000Z",   # 19:00 Pacific, July 31 -- #122 case
        "2026-08-01T06:00:00.000Z",   # 23:00 Pacific, July 31 -- #122 case
    ]

    # "Seller Hub" fixture: eBay's own month total, already in its native
    # (Pacific) zone -- what the seller invoice / Listings Sales Report
    # would actually show for July.
    seller_hub_july_order_count = 5

    naive_utc_july_count = sum(
        1 for c in api_orders_creation_dates if c[:7] == "2026-07")
    pacific_july_count = sum(
        1 for c in api_orders_creation_dates if R.to_report_month(c) == "2026-07")

    assert naive_utc_july_count == 3, (
        "sanity check: the naive UTC-truncation bug this fixture is built "
        "to demonstrate must actually reproduce here")
    assert naive_utc_july_count != seller_hub_july_order_count, (
        "a naive UTC-truncated count must NOT agree with eBay's own "
        "Seller-Hub count -- reproducing the exact discrepancy #122 reports")
    assert pacific_july_count == seller_hub_july_order_count, (
        "the Pacific-bucketed order count must agree with eBay's own "
        "Seller-Hub-shaped month total")
