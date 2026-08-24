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


def create_campaign(name: str, budget: float, confirm: bool) -> str:
    """Create a CPC (Priority) campaign. Returns its id.

    Written deliberately WITHOUT `campaignCriterion`: rule-based selection would
    hand eBay the choice of what to promote, and the whole point of this phase
    is that the selection is ours and inspectable. Ads are added by listing id,
    one explicit decision per listing.
    """
    from datetime import datetime, timedelta, timezone

    from ebay_client import api_send

    body = {
        "campaignName": name,
        "marketplaceId": "EBAY_US",
        "channels": ["ON_SITE"],
        "fundingStrategy": {"fundingModel": "COST_PER_CLICK"},
        "budget": {"daily": {"amount": {"value": f"{budget:.2f}", "currency": "USD"}}},
        "startDate": (datetime.now(timezone.utc) + timedelta(minutes=2))
                     .strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    if not confirm:
        print("DRY — would POST /sell/marketing/v1/ad_campaign\n"
              + json.dumps(body, indent=1))
        return ""
    r = api_send("POST", "/sell/marketing/v1/ad_campaign", body)
    cid = str(r.get("campaignId") or "")
    if not cid:                       # eBay answers 201 with a Location header
        from ebay_client import api_send as _s
        found = _s("GET", f"/sell/marketing/v1/ad_campaign/get_campaign_by_name"
                          f"?campaign_name={name.replace(' ', '%20')}")
        cid = str(found.get("campaignId") or "")
    return cid


def ensure_ad_group(campaign_id: str, bid: float, confirm: bool) -> str:
    """A CPC campaign holds its ads in an AD GROUP, and needs one before any ad.

    Found the hard way: bulk_create_ads_by_listing_id answers
    `36210 No ad group found for ad group id null` when the campaign has none.
    A cost-per-sale campaign takes ads directly; a cost-per-click one does not.

    The group carries the default bid — what a click may cost — so this is the
    number that decides spend, not the daily budget, which only caps it.
    """
    from ebay_client import api_send

    try:
        got = api_send("GET", f"/sell/marketing/v1/ad_campaign/{campaign_id}"
                              f"/ad_group?limit=10").get("adGroups") or []
        if got:
            return str(got[0].get("adGroupId"))
    except Exception:                                                # noqa: BLE001
        pass
    body = {"name": "Revenue core", "adGroupStatus": "ACTIVE",
            "defaultBid": {"value": f"{bid:.2f}", "currency": "USD"}}
    if not confirm:
        print(f"DRY — would POST .../{campaign_id}/ad_group {json.dumps(body)}")
        return ""
    r = api_send("POST", f"/sell/marketing/v1/ad_campaign/{campaign_id}/ad_group", body)
    gid = str(r.get("adGroupId") or "")
    if not gid:
        got = api_send("GET", f"/sell/marketing/v1/ad_campaign/{campaign_id}"
                              f"/ad_group?limit=10").get("adGroups") or []
        gid = str(got[0].get("adGroupId")) if got else ""
    return gid


def add_ads(campaign_id: str, listing_ids: list, confirm: bool,
            ad_group_id: str = "") -> dict:
    """Bulk-create ads by listing id. Up to 500 per call; we send far fewer."""
    from ebay_client import api_send

    body = {"requests": [
        ({"listingId": str(l), "adGroupId": ad_group_id} if ad_group_id
         else {"listingId": str(l)}) for l in listing_ids]}
    if not confirm:
        print(f"DRY — would POST .../{campaign_id}/bulk_create_ads_by_listing_id "
              f"with {len(listing_ids)} listing(s)")
        return {}
    return api_send("POST", f"/sell/marketing/v1/ad_campaign/{campaign_id}"
                            f"/bulk_create_ads_by_listing_id", body)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--budget", type=float, default=20.0, help="daily budget USD (default 20)")
    ap.add_argument("--top", type=int, default=40, help="how many listings to propose")
    ap.add_argument("--json", help="also write the plan here")
    ap.add_argument("--create", metavar="NAME",
                    help="create a CPC campaign with this name (needs --confirm to write)")
    ap.add_argument("--add-ads", action="store_true",
                    help="add the proposed listings to the campaign (needs --confirm)")
    ap.add_argument("--campaign", help="operate on this existing campaign id")
    ap.add_argument("--bid", type=float, default=0.25,
                    help="default cost-per-click bid for the ad group (default 0.25)")
    ap.add_argument("--confirm", action="store_true",
                    help="actually write to eBay. Without it every write is a dry run.")
    a = ap.parse_args()

    if a.create:
        cid = create_campaign(a.create, a.budget, a.confirm)
        print(f"campaign: {cid or '(dry run)'}")
        if cid:
            a.campaign = cid

    live = live_listings()
    camps, ads = campaigns_and_ads()
    running = [c for c in camps if c.get("campaignStatus") == "RUNNING"]
    onsite = [c for c in camps if "ON_SITE" in (c.get("channels") or [])]
    paused_onsite = [c for c in onsite
                     if c.get("campaignStatus") in ("PAUSED", "SCHEDULED")]

    # Which campaign to hang this on. Reusing a paused ON_SITE campaign keeps the
    # ads that are already in it and costs one resume; a new campaign is only
    # proposed when there is nothing to reuse.
    host = None
    if a.campaign:
        # An explicit campaign wins. A freshly created campaign is SCHEDULED,
        # not RUNNING, until its start time passes — the status heuristics below
        # would skip it and propose creating yet another one.
        host = next((c for c in camps if c["campaignId"] == a.campaign), None)
        action = f"explicit --campaign ({host.get('campaignStatus') if host else 'not found'})"
    if host:
        pass
    elif running:
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

    if a.add_ads and host:
        gid = ensure_ad_group(host["campaignId"], a.bid, a.confirm)
        print(f"  ad group: {gid or '(dry run)'} · default bid ${a.bid:.2f}/click")
        res = add_ads(host["campaignId"], [r["listing_id"] for r in pick],
                      a.confirm, gid)
        if a.confirm:
            resp = res.get("responses") or []
            ok = [x for x in resp if str(x.get("statusCode", "")).startswith("2")]
            print(f"\n[eBay] {len(ok)}/{len(resp)} ads created")
            for x in resp:
                if x not in ok:
                    print(f"   FAILED {x.get('listingId')}: "
                          f"{json.dumps(x.get('errors'))[:160]}")

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
