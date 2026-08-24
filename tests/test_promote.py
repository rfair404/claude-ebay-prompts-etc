"""PROMOTE — the payload rules eBay only teaches you by refusing.

Every assertion here corresponds to a real error from a live account, named in
the test. They are cheap to hold and expensive to rediscover: each one cost a
round trip against production, and two of them cost a campaign rebuild.

No network. `create_campaign(confirm=False)` and friends print the body and
return, so the shaping is testable without touching the account.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "lib"))

import promote                                                   # noqa: E402


def _body(capsys, **kw) -> dict:
    """The JSON create_campaign would POST, captured from its dry run."""
    promote.create_campaign(kw.pop("name", "T"), kw.pop("budget", 20.0),
                            False, **kw)
    out = capsys.readouterr().out
    return json.loads(out[out.index("{"):])


def test_cpc_when_no_rule_asked_for(capsys):
    b = _body(capsys)
    assert b["fundingStrategy"]["fundingModel"] == "COST_PER_CLICK"
    assert b["budget"]["daily"]["amount"]["value"] == "20.00"
    assert "campaignCriterion" not in b


def test_a_rule_forces_cost_per_sale(capsys):
    """36151 'campaignCriterion' is not supported for CPC funding model.

    Asking for auto-add IS asking for CPS. The two are not independent choices,
    so the model must follow the rule rather than be set beside it.
    """
    b = _body(capsys, min_price=25.0, ad_rate=10.0)
    assert b["fundingStrategy"]["fundingModel"] == "COST_PER_SALE"
    assert b["fundingStrategy"]["bidPercentage"] == "10.0"
    # CPS takes no daily budget at all -- sending one is a contradiction.
    assert "budget" not in b


def test_criterion_carries_category_scope(capsys):
    """35039 categoryScope is required for criterion based campaigns.

    Required even though this rule names no category, only a price floor.
    """
    b = _body(capsys, min_price=25.0)
    crit = b["campaignCriterion"]
    assert crit["autoSelectFutureInventory"] is True
    assert crit["criterionType"] == "INVENTORY_PARTITION"
    rule = crit["selectionRules"][0]
    assert rule["categoryScope"] in ("MARKETPLACE", "STORE")
    assert rule["minPrice"] == {"value": "25.00", "currency": "USD"}


def test_ads_carry_the_ad_group(capsys):
    """36210 No ad group found for ad group id null.

    A CPC campaign holds its ads in an ad group; without the id every ad in the
    bulk call is rejected.
    """
    promote.add_ads("C1", ["1", "2"], False, "G9")
    assert "2 listing" in capsys.readouterr().out

    sent = {}

    def fake(method, path, body=None):
        sent.update(method=method, path=path, body=body)
        return {"responses": []}

    import ebay_client                                            # noqa: E402
    real, ebay_client.api_send = ebay_client.api_send, fake
    try:
        promote.add_ads("C1", ["1", "2"], True, "G9")
    finally:
        ebay_client.api_send = real
    assert sent["path"].endswith("/bulk_create_ads_by_listing_id")
    assert all(r["adGroupId"] == "G9" for r in sent["body"]["requests"])


def test_nothing_is_sent_without_confirm(capsys):
    """The default is propose-only. A dry run must not construct a client."""
    import ebay_client                                            # noqa: E402

    def explode(*a, **k):
        raise AssertionError("a dry run must not call the API")

    real, ebay_client.api_send = ebay_client.api_send, explode
    try:
        promote.create_campaign("T", 20.0, False, 25.0)
        promote.add_ads("C1", ["1"], False, "G9")
        promote.set_bidding("C1", "DYNAMIC", False)
        promote.delete_campaign("C1", False)
    finally:
        ebay_client.api_send = real


@pytest.mark.parametrize("val,want", [("$1,234.50", 1234.5), ("", 0.0), (None, 0.0)])
def test_price_parsing(val, want):
    assert promote._f(val) == want
