"""PROMOTE_SEASONED — the 15-day wait before a listing is promoted. No network."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "lib"))

import promote_seasoned as ps                                     # noqa: E402

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


def _l(created, price=50.0):
    return {"created": created, "price": price, "title": "t"}


def test_age_boundary_is_inclusive_at_15_days():
    got = ps.split_by_age({"old": _l("2026-09-15T12:00:00.000Z"),
                           "young": _l("2026-09-15T12:00:01.000Z")}, NOW)
    assert got == (["old"], ["young"])


def test_price_floor_drops_cheap_listings_entirely():
    seasoned, young = ps.split_by_age({"cheap": _l("2026-01-01T00:00:00.000Z", 24.99),
                                       "ok": _l("2026-01-01T00:00:00.000Z", 25.0)}, NOW)
    assert seasoned == ["ok"] and young == []


def test_missing_creation_date_is_never_promoted():
    assert ps.split_by_age({"x": _l(None)}, NOW) == ([], ["x"])


def test_campaign_has_no_auto_add_rule():
    """The whole point: a campaignCriterion would add day-0 listings by itself."""
    b = ps.campaign_body(start=NOW)
    assert "campaignCriterion" not in b
    fs = b["fundingStrategy"]
    assert fs["fundingModel"] == "COST_PER_SALE"
    assert fs["dynamicAdRatePreferences"][0]["adRateCapPercent"] == "10.0"
    assert "budget" not in b


def test_to_add_skips_listings_already_in_campaign():
    assert ps.to_add(["1", "2", "3"], {"2"}) == ["1", "3"]
