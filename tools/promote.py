#!/usr/bin/env python3
"""promote — plan a Promoted Listings campaign. Proposes; never writes.

Reads three things and joins them:

  * the account's campaigns and their ads      (Marketing API)
  * every live listing with its ask            (inventory_sheet.csv)
  * eBay's own suggested items + estimated     (Marketing API, per campaign)
    search impressions

and proposes: which campaign to use, what daily budget, and exactly which
listings to add — ranked for the goal you picked.

GOAL: MAXIMISE REVENUE

Rank by ask value, gated on demand. An expensive listing nobody searches for
earns nothing from promotion, so a listing must appear in eBay's suggested set
(which is where the impression estimate comes from) to be eligible at all.
Score is `ask x estimated impressions`, which is the crude expected-value of
putting the listing in front of people, and it is deliberately crude: the honest
version needs eBay's attributed ad report, which needs a POST this tool does not
make.

Items already carrying an ACTIVE ad in a RUNNING campaign are excluded — they
are already promoted. Items in a PAUSED campaign are shown, because resuming
that campaign is usually cheaper than building a new one.

IT WRITES NOTHING. Not to eBay, not to the ledgers. It prints the plan and the
exact API calls that would enact it, and stops. Creating a campaign, adding an
ad, or setting a bid are all writes against a live account and stay your
keystroke — the same shape as the PREP gate and list_edit's --confirm.

    python tools/promote.py                      # the plan
    python tools/promote.py --budget 20 --top 40
    python tools/promote.py --json reports/promote_plan.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))

REPORTS = REPO / "reports"


def _f(v, d=0.0):
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return d


def live_listings() -> dict:
    """listing_id -> {title, ask, category} for everything currently live.

    Distinct listing ids: a CHOICE listing is one ad, not one per variation.
    """
    out = {}
    p = REPO / "inventory_sheet.csv"
    if not p.exists():
        raise SystemExit("inventory_sheet.csv missing — run tools/sales_report.py first")
    with p.open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            lid = r.get("listing_id") or ""
            if not lid or (r.get("live") or "").lower() != "yes" or lid in out:
                continue
            out[lid] = {"title": r.get("title", ""), "ask": _f(r.get("price")),
                        "category": r.get("category_top", ""),
                        "sku": r.get("sku", "")}
    return out


def campaigns_and_ads() -> tuple[list, dict]:
    from ebay_client import api_send

    camps = api_send("GET", "/sell/marketing/v1/ad_campaign?limit=100").get("campaigns", [])
    ads: dict[str, dict] = {}
    for c in camps:
        offset = 0
        while True:
            try:
                page = api_send("GET", f"/sell/marketing/v1/ad_campaign/{c['campaignId']}"
                                       f"/ad?limit=200&offset={offset}")
            except Exception:                                        # noqa: BLE001
                break                # OFF_SITE campaigns have no per-listing ads
            got = page.get("ads") or []
            for a in got:
                lid = str(a.get("listingId") or "")
                if lid:
                    ads.setdefault(lid, {"campaign": c, "ad": a})
            offset += len(got)
            if len(got) < 200 or offset >= int(page.get("total") or 0):
                break
    return camps, ads


def suggestions(campaign_id: str) -> dict:
    """listing_id -> estimated search impressions, from eBay's own suggestion set."""
    from ebay_client import api_send

    out, offset = {}, 0
    while True:
        page = api_send("GET", f"/sell/marketing/v1/ad_campaign/{campaign_id}"
                               f"/suggest_items?limit=200&offset={offset}")
        items = page.get("suggestedItems") or []
        for it in items:
            est = 0
            for b in it.get("bases") or []:
                if b.get("metric") == "SEARCH_IMPRESSIONS":
                    est = max(est, int(b.get("estimatedValue") or 0))
            out[str(it.get("listingId"))] = est
        offset += len(items)
        if len(items) < 200 or offset >= int(page.get("total") or 0):
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--budget", type=float, default=20.0, help="daily budget USD (default 20)")
    ap.add_argument("--top", type=int, default=40, help="how many listings to propose")
    ap.add_argument("--json", help="also write the plan here")
    a = ap.parse_args()

    live = live_listings()
    camps, ads = campaigns_and_ads()
    running = [c for c in camps if c.get("campaignStatus") == "RUNNING"]
    onsite = [c for c in camps if "ON_SITE" in (c.get("channels") or [])]
    paused_onsite = [c for c in onsite if c.get("campaignStatus") == "PAUSED"]

    # Which campaign to hang this on. Reusing a paused ON_SITE campaign keeps the
    # ads that are already in it and costs one resume; a new campaign is only
    # proposed when there is nothing to reuse.
    host = None
    if running:
        host = running[0]
        action = "already running — add items to it"
    elif paused_onsite:
        host = max(paused_onsite, key=lambda c: len(
            [1 for v in ads.values() if v["campaign"]["campaignId"] == c["campaignId"]]))
        action = "PAUSED — resume it, then add items"
    else:
        action = "no reusable ON_SITE campaign — one must be created"

    promoted_now = {lid for lid, v in ads.items()
                    if v["campaign"].get("campaignStatus") == "RUNNING"
                    and v["ad"].get("adStatus") == "ACTIVE"}

    sug = suggestions(host["campaignId"]) if host else {}
    rows = []
    for lid, rec in live.items():
        if lid in promoted_now:
            continue
        est = sug.get(lid)
        if not est:
            continue                      # no demand signal -> not eligible
        rows.append({**rec, "listing_id": lid, "impressions": est,
                     "score": rec["ask"] * est,
                     "in_campaign": ads.get(lid, {}).get("campaign", {}).get("campaignName")})
    rows.sort(key=lambda r: -r["score"])
    pick = rows[:a.top]

    print("PROMOTE — plan (goal: maximise revenue)\n")
    print(f"  live listings            {len(live)}")
    print(f"  already actively promoted{len(promoted_now):>5}")
    print(f"  eBay suggests            {len(sug)}")
    print(f"  eligible (live+suggested){len(rows):>5}")
    if host:
        print(f"\n  host campaign: {host['campaignName']}")
        print(f"                 {host['campaignId']} · {host.get('campaignStatus')} · "
              f"{host.get('campaignTargetingType') or 'manual'} · "
              f"{(host.get('fundingStrategy') or {}).get('fundingModel')}")
    print(f"  status:        {action}")
    print(f"  daily budget:  ${a.budget:,.2f}  (~${a.budget * 30:,.0f}/month ceiling)\n")

    ask_total = sum(r["ask"] for r in pick)
    print(f"{'ask':>7} {'impr':>7} {'score':>9}  {'already in':<22} item")
    print("-" * 104)
    for r in pick:
        print(f"{r['ask']:>7,.0f} {r['impressions']:>7,} {r['score']:>9,.0f}  "
              f"{(r['in_campaign'] or '')[:20]:<22} {r['title'][:44]}")
    print("-" * 104)
    print(f"{len(pick)} listings · ${ask_total:,.2f} of ask value put in front of buyers")
    print(f"at ${a.budget:.0f}/day the campaign spends at most "
          f"${a.budget * 30:,.0f}/month, or {a.budget * 30 / ask_total * 100:.1f}% "
          f"of that ask value per month." if ask_total else "")

    print("\nTO ENACT — these are WRITES; this tool does not make them:")
    if host and host.get("campaignStatus") == "PAUSED":
        print(f"  POST /sell/marketing/v1/ad_campaign/{host['campaignId']}/resume")
    if host:
        print(f"  POST /sell/marketing/v1/ad_campaign/{host['campaignId']}/bulk_create_ads_by_listing_id")
        print(f"       body: {{\"requests\": [{{\"listingId\": \"...\"}} x {len(pick)}]}}")
        print(f"  PUT  /sell/marketing/v1/ad_campaign/{host['campaignId']}/update_campaign_budget")
        print(f"       body: {{\"daily\": {{\"amount\": {{\"value\": \"{a.budget}\", \"currency\": \"USD\"}}}}}}")
    else:
        print("  POST /sell/marketing/v1/ad_campaign   (create one first)")

    if a.json:
        REPORTS.mkdir(exist_ok=True)
        Path(a.json).write_text(json.dumps(
            {"goal": "maximise revenue", "budget_daily": a.budget,
             "host_campaign": host, "action": action, "items": pick}, indent=1),
            encoding="utf-8")
        print(f"\n[OK] wrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
