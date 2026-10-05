"""AD REPORT — the request shapes eBay only teaches by refusing, the two file
formats it hands back, and the strategy rules.

Every request-shape assertion is a refusal from the live account on
2026-10-05, named by eBay error id. No network: api_send and the download are
replaced with fakes wherever a pull is exercised.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "lib"))

import ad_report as A                                             # noqa: E402


# --- what an API-made report looks like (tab-separated, metric keys) --------
API_TSV = (
    "campaign_id\tcampaign_name\tad_group_id\tlisting_id\tlisting_title\tlisting_price\t"
    "cpc_impressions\tcpc_clicks\tcpc_attributed_sales\tcpc_sale_amount_listingsite_currency\t"
    "cpc_ad_fees_listingsite_currency\tchannels\n"
    "1\tFirehose\t9\t111\tBrass Lamp\tUSD 40.00\t1000\t30\t1\tUSD 40.00\tUSD 9.50\tON_SITE\n"
    "1\tFirehose\t9\t222\tTin Toy\tUSD 1,200.00\t500\t12\t0\tUSD 0.00\tUSD 6.25\tON_SITE\n"
)

# --- what a Seller Hub download looks like (CSV, disclaimer, human labels) --
HUB_CSV = (
    '﻿Some details are not available for inactive listings and campaigns.\n'
    '\n'
    'Start date,End date,Campaign name,Campaign ID,Item ID,Title,Price (Current or Last Price),'
    'Promoted Listings Impressions (via eBay Placements),Total Promoted Listings Clicks,'
    'Total Promoted Listings Sold quantity,Organic Sold Quantity,'
    'Promoted Listings Conversion rate (Promoted Listings Sold quantity/Promoted Listings Clicks),'
    'Total Promoted Listings Sales,Ad fees,Return on Ad spend (Sales/Ad fees),'
    'Ad fees (via eBay placements)\n'
    '"Jun 25, 2026","Sep 22, 2026",Revenue Auto,7,333,"Jar, Cobalt",$ 318.00,'
    '4000,20,1,2,5.00%,$ 318.00,$ 31.80,10.00,$ 31.80\n'
)


def test_parse_api_tsv_keys_and_money():
    rows = A.parse_report(API_TSV)
    assert len(rows) == 2
    r = rows[0]
    assert r["campaign_id"] == "1" and r["listing_id"] == "111" and r["title"] == "Brass Lamp"
    # cpc_attributed_sales is a COUNT; the sale amount is the dollars
    assert r["sold"] == 1 and r["sales"] == 40.0 and r["ad_fees"] == 9.5
    assert r["impressions"] == 1000 and r["clicks"] == 30
    assert rows[1]["price"] == 1200.0          # "USD 1,200.00"


def test_parse_seller_hub_csv_labels():
    rows = A.parse_report(HUB_CSV)
    assert len(rows) == 1
    r = rows[0]
    assert r["campaign_id"] == "7" and r["title"] == "Jar, Cobalt"
    # label "Sales" is DOLLARS here, unlike key `sales`
    assert r["sales"] == 318.0 and r["sold"] == 1
    # "Ad fees" once, never doubled by "Ad fees (via eBay placements)"
    assert r["ad_fees"] == 31.8
    assert r["organic_sold"] == 2 and r["impressions"] == 4000 and r["clicks"] == 20


def test_parse_no_header_is_empty():
    assert A.parse_report("nothing here\n") == []


def test_cps_body_uses_accepted_metric_keys():
    """35115: pls_ad_fees_listing_site_currency / organic_sales refused for
    CAMPAIGN_PERFORMANCE_REPORT, though Seller Hub's own tasks use them."""
    b = A.task_body("CPS", ["1"], "2026-09-04T16:00:00.000Z", "2026-10-05T16:00:00.000Z")
    assert b["fundingModels"] == ["COST_PER_SALE"]
    assert "ad_fees" in b["metricKeys"]
    assert not {"pls_ad_fees_listing_site_currency", "organic_sales"} & set(b["metricKeys"])
    assert "channels" not in b


def test_cpc_body_has_ad_group_dimension():
    """35119: minimum dimensionKeys for a CPC campaign report include ad_group_id."""
    b = A.task_body("CPC", ["1"], "a", "b")
    assert [d["dimensionKey"] for d in b["dimensions"]] == ["campaign_id", "ad_group_id",
                                                            "listing_id"]
    assert b["fundingModels"] == ["COST_PER_CLICK"]


def test_offsite_body_is_cpc_keys_on_off_site_channel():
    """35115: oa_* refused for CAMPAIGN_PERFORMANCE_REPORT. 35128: ad_group_id is
    not valid for OFF_SITE. Without channels the report comes back empty."""
    b = A.task_body("OFFSITE", ["1"], "a", "b")
    assert b["channels"] == ["OFF_SITE"]
    assert all(k.startswith("cpc_") for k in b["metricKeys"])
    assert "ad_group_id" not in [d["dimensionKey"] for d in b["dimensions"]]


def test_iso_is_whole_seconds():
    dt = datetime(2026, 10, 5, 16, 0, 10, 987654, tzinfo=timezone.utc)
    assert A._iso(dt) == "2026-10-05T16:00:10.000Z"


def test_campaign_group():
    assert A.campaign_group({"fundingStrategy": {"fundingModel": "COST_PER_SALE"}}) == "CPS"
    assert A.campaign_group({"fundingStrategy": {"fundingModel": "COST_PER_CLICK"},
                             "channels": ["OFF_SITE"]}) == "OFFSITE"
    assert A.campaign_group({"fundingStrategy": {"fundingModel": "COST_PER_CLICK"},
                             "channels": ["ON_SITE"]}) == "CPC"


def test_find_task_ignores_rewritten_metric_keys():
    """eBay stores an offsite task under oa_* keys though cpc_* were sent."""
    body = A.task_body("OFFSITE", ["5", "4"], "2026-09-04T16:00:10.000Z", "x")
    tasks = {"total": 2, "reportTasks": [
        {"reportTaskId": "other", "dateFrom": "2026-09-04T16:00:09.000Z",
         "campaignIds": ["4", "5"], "metricKeys": ["oa_clicks"]},
        {"reportTaskId": "mine", "dateFrom": "2026-09-04T16:00:10.000Z",
         "campaignIds": ["4", "5"], "metricKeys": ["oa_clicks"],
         "reportTaskCreationDate": "2026-10-05T16:05:00.000Z"},
    ]}
    found = A._find_task(lambda *a, **k: tasks, None, body)
    assert found["reportTaskId"] == "mine"


def test_run_task_deletes_only_after_success(monkeypatch):
    import ebay_client
    calls, statuses = [], iter(["PENDING", "SUCCESS"])

    def fake(method, path, body=None, **kw):
        calls.append((method, path))
        if method == "GET":
            return {"total": 1, "reportTasks": [{
                "reportTaskId": "T1", "reportId": "R1", "reportTaskStatus": next(statuses),
                "dateFrom": "2026-09-04T16:00:00.000Z", "campaignIds": ["1"]}]}
        return {}

    monkeypatch.setattr(ebay_client, "api_send", fake)
    monkeypatch.setattr(A, "_download", lambda creds, rid: API_TSV)
    rows = A.run_task("CPC", ["1"], "2026-09-04T16:00:00.000Z", "x", None,
                      sleep=lambda s: None)
    assert [r["group"] for r in rows] == ["CPC", "CPC"]
    methods = [m for m, _ in calls]
    assert methods[0] == "POST" and methods[-1] == "DELETE"
    assert methods.count("DELETE") == 1          # never while PENDING (409)


# --- strategy --------------------------------------------------------------

def _data(**over):
    camps = [
        {"campaignId": "1", "campaignName": "Firehose", "status": "PAUSED", "group": "CPC",
         "startDate": "2026-08-24T00:00:00Z", "endDate": None, "dailyBudget": "20.0"},
        {"campaignId": "2", "campaignName": "General", "status": "RUNNING", "group": "CPS",
         "startDate": "2026-08-24T00:00:00Z", "endDate": None, "dailyBudget": None},
        {"campaignId": "3", "campaignName": "Idle CPC", "status": "RUNNING", "group": "CPC",
         "startDate": "2026-09-23T00:00:00Z", "endDate": None, "dailyBudget": "10.0"},
        {"campaignId": "4", "campaignName": "Live CPC", "status": "RUNNING", "group": "CPC",
         "startDate": "2026-09-01T00:00:00Z", "endDate": None, "dailyBudget": "10.0"},
    ]
    row = lambda cid, g, lid, fees, sales, sold, impr=100: {  # noqa: E731
        "group": g, "campaign_id": cid, "campaign_name": "", "listing_id": lid,
        "title": f"item {lid}", "price": 0, "impressions": impr, "clicks": 5,
        "sold": sold, "sales": sales, "ad_fees": fees, "organic_sold": 0}
    d = {"days": 30, "date_from": "2026-09-05T00:00:00.000Z",
         "date_to": "2026-10-05T00:00:00.000Z", "pulled_at": "now",
         "campaigns": camps, "errors": {}, "billed": None,
         "rows": [row("1", "CPC", "a", 260.0, 250.0, 6),
                  row("1", "CPC", "b", 40.0, 0.0, 0),
                  row("2", "CPS", "c", 120.0, 1200.0, 20),
                  row("4", "CPC", "d", 8.0, 0.0, 0),
                  row("4", "CPC", "e", 12.0, 90.0, 1)]}
    d.update(over)
    return d


def _by(findings, campaign):
    return [f for f in findings if f["campaign"] == campaign]


def test_losing_paused_campaign_says_leave_it_off():
    f = _by(A.advise(_data()), "Firehose")
    assert f[0]["level"] == "ACT" and "leave it off" in f[0]["text"]


def test_losing_running_campaign_says_pause():
    d = _data()
    d["campaigns"][0]["status"] = "RUNNING"
    f = _by(A.advise(d), "Firehose")
    assert f[0]["level"] == "ACT" and "Pause or end it" in f[0]["text"]


def test_good_cps_is_keep():
    f = _by(A.advise(_data()), "General")
    assert f[0]["level"] == "KEEP"


def test_ended_good_cps_is_info_not_keep():
    d = _data()
    d["campaigns"][1]["status"] = "ENDED"
    assert _by(A.advise(d), "General")[0]["level"] == "INFO"


def test_running_with_no_impressions_is_watch():
    f = _by(A.advise(_data()), "Idle CPC")
    assert f and f[0]["level"] == "WATCH" and "0 impressions" in f[0]["text"]


def test_listing_burn_counts_only_running_campaigns():
    """Listing b ($40, no sale) is in a PAUSED campaign: it spends nothing now.
    Listing d ($8, no sale) is in a running one and is named."""
    f = _by(A.advise(_data()), "per-click listings")
    assert len(f) == 1
    assert " d " in f[0]["text"] and " b " not in f[0]["text"]


def test_cpc_pricier_than_cps_is_watch():
    """Live CPC: $20 fees for $90 sales = 22% cost of sale vs CPS 10%."""
    f = _by(A.advise(_data()), "Live CPC")
    assert f and f[0]["level"] == "WATCH" and "cost-per-sale" in f[0]["text"]


def test_ad_share_of_gross():
    f = _by(A.advise(_data(), gross_sales=1000.0), "all campaigns")
    assert f[0]["level"] == "WATCH"           # $440 of $1,000
    f = _by(A.advise(_data(), gross_sales=100000.0), "all campaigns")
    assert f[0]["level"] == "INFO"


def test_billed_gap_named_only_beyond_tolerance():
    near = _data(billed={"total": 442.0, "by_fee_type": {}})
    assert not _by(A.advise(near), "billing")
    far = _data(billed={"total": 500.0, "by_fee_type": {"AD_FEE": 500.0}})
    assert _by(A.advise(far), "billing")[0]["level"] == "INFO"


def test_failed_group_is_not_judged_as_zero():
    """A group whose report failed must not read as 'running, 0 impressions'."""
    d = _data(errors={"CPC": "HTTP 400"})
    d["rows"] = [r for r in d["rows"] if r["group"] != "CPC"]
    f = A.advise(d)
    assert not _by(f, "Idle CPC")
    assert _by(f, "CPC")[0]["text"].startswith("Not reported")


def test_findings_sorted_most_urgent_first():
    levels = [f["level"] for f in A.advise(_data(), gross_sales=1000.0)]
    order = {"ACT": 0, "WATCH": 1, "KEEP": 2, "INFO": 3}
    assert levels == sorted(levels, key=order.get)
