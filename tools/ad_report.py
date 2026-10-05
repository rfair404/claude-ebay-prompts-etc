#!/usr/bin/env python3
"""ad_report — eBay's own attributed Promoted Listings numbers, plus what to do
about them.

    python tools/ad_report.py                  # pull the last 30 days, then advise
    python tools/ad_report.py --days 90        # a longer window
    python tools/ad_report.py --no-sync        # advise from the last pull, offline
    python tools/ad_report.py --store junk     # a named store (#156)

WHY THIS EXISTS

`tools/sales_report.py` joins the ad SET to the sold SET: which listings carry
an ad, and which of those sold. That cannot say what an ad cost or earned. The
numbers that can (impressions, clicks, attributed sales and the fee for each
campaign and listing) exist only behind the Marketing API's async report task,
which is what Seller Hub's Advertising dashboard draws from. Measured
2026-10-05 against that dashboard: $495.79 of ad fees in 31 days, $373.70 of
them on two cost-per-click campaigns that returned $284.47 in sales. The
cost-per-sale campaigns cost $122 and sold about $1,250. That gap is what this
tool makes visible every time it runs, not only when someone opens Seller Hub.

WHAT IT WRITES TO THE ACCOUNT, AND WHAT IT DOESN'T

A pull creates up to three report TASKS (POST /ad_report_task), downloads each
finished report, then deletes the task it made. A report task is a request for
a file. It changes no campaign, ad, bid or budget and costs nothing. Every
recommendation printed here is advice: changing a campaign stays with
`tools/promote.py` and its `--confirm`, or with a person in Seller Hub.

THE RULES EBAY ONLY STATES IN ONE GUIDE (pl-reports.html)

Every rule below was learned from a refusal on the live account (2026-10-05).
GET /ad_report_metadata answers 403 for this app, so the refusals were the
only way to find out.

* One funding model per report. Cost-per-sale ("general") and cost-per-click
  ("priority") metrics have different keys (`sales` vs `cpc_attributed_sales`)
  and a request that mixes them fails. So: three groups, three tasks.
* Cost-per-sale: `pls_ad_fees_listing_site_currency` and `organic_sales`,
  which Seller Hub's own tasks use, are refused for CAMPAIGN_PERFORMANCE_REPORT
  (35115). The plain `ad_fees` key is accepted.
* On-site cost-per-click requires `ad_group_id` as a dimension (35119).
* Offsite is cost-per-click with `channels: ["OFF_SITE"]`. The `oa_*` keys
  are refused (35115), and `ad_group_id` is refused for OFF_SITE (35128).
  Ask with the `cpc_*` keys instead. eBay then stores the task under the
  `oa_*` keys. Without `channels` an offsite campaign returns an empty report.
* `dateTo` cannot be in the future. eBay keeps `dateFrom` to the SECOND, not
  the millisecond.
* The create call answers 202 with the task's URI in the Location header and
  no body. `api_send` returns bodies only, so the task is found again by
  listing tasks and matching `dateFrom` plus campaign IDs. Each group's
  `dateFrom` is one second apart so two groups never match each other. Metric
  keys cannot be matched, because eBay rewrites them (above).
* A task can only be deleted once it has finished. Deleting a PENDING one is
  a 409.
* An API-made report is real TSV with the metric keys as column names and
  money as "USD 28.00". A report Seller Hub makes is CSV with a disclaimer
  line and human labels ("Ad fees  "). `_canonical()` reads both, so a
  Seller Hub download can be parsed by the same code.

BILLED vs REPORTED

The report's fees are eBay's attribution model. The Finances API's
NON_SALE_CHARGE transactions are the bill. Both are read, and a gap between
them is reported rather than reconciled away. eBay reconciles report metrics
for 72 hours, and a cost-per-click charge can bill for a click whose sale is
never attributed. `lib/ebay_finances.py` holds the rules for reading that feed.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))

import stores                                                    # noqa: E402

API = "/sell/marketing/v1"

# --- what each group asks eBay for ------------------------------------------

DIMS_CAMPAIGN_LISTING = [
    {"dimensionKey": "campaign_id", "annotationKeys": ["campaign_name"]},
    {"dimensionKey": "listing_id", "annotationKeys": ["listing_title", "listing_price"]},
]

CPS_METRICS = ["impressions", "clicks", "sales", "sale_amount", "ad_fees"]

# From pl-reports.html, "Required fields for all priority strategy campaign
# reports". `ad_group_id` is a required dimension for priority reports.
CPC_METRICS = ["cpc_impressions", "cpc_clicks", "cpc_attributed_sales",
               "cpc_sale_amount_listingsite_currency",
               "cpc_ad_fees_listingsite_currency"]

GROUPS = {
    "CPS": {"metrics": CPS_METRICS, "dims": DIMS_CAMPAIGN_LISTING,
            "funding": ["COST_PER_SALE"]},
    "CPC": {"metrics": CPC_METRICS,
            "dims": DIMS_CAMPAIGN_LISTING[:1]
                    + [{"dimensionKey": "ad_group_id"}]
                    + DIMS_CAMPAIGN_LISTING[1:],
            "funding": ["COST_PER_CLICK"]},
    "OFFSITE": {"metrics": CPC_METRICS, "dims": DIMS_CAMPAIGN_LISTING,
                "funding": ["COST_PER_CLICK"], "channels": ["OFF_SITE"]},
}

POLL_SECONDS = 3
POLL_TIMEOUT = 180

# --- strategy thresholds ----------------------------------------------------

MIN_SPEND = 10.0          # below this a campaign's ROAS is noise, not a verdict
LISTING_BURN = 5.0        # per-click fees on one listing with no sale
AD_SHARE_WARN = 0.15      # ad fees as a share of ALL gross sales in the window
NOT_SERVING_DAYS = 3      # a running campaign this old with 0 impressions
RECONCILE_TOL = 5.0       # $ gap between billed and reported before it's named
CPC_VS_CPS_FACTOR = 1.5   # CPC cost-of-sale this many times CPS's is "pricier"


# ---------------------------------------------------------------------------
# 1 · pull
# ---------------------------------------------------------------------------

def _iso(dt: datetime) -> str:
    """eBay's yyyy-MM-ddThh:mm:ss.sssZ, at whole seconds (all eBay keeps)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def campaign_group(c: dict) -> str:
    """CPS / CPC / OFFSITE — which report a campaign's numbers live in."""
    model = (c.get("fundingStrategy") or {}).get("fundingModel")
    if model == "COST_PER_SALE":
        return "CPS"
    if "OFF_SITE" in (c.get("channels") or []):
        return "OFFSITE"
    return "CPC"


def overlaps(c: dict, start: datetime, end: datetime) -> bool:
    """Did the campaign exist at any point in [start, end]?"""
    s, e = _parse_iso(c.get("startDate")), _parse_iso(c.get("endDate"))
    return (s is None or s <= end) and (e is None or e >= start)


def task_body(group: str, campaign_ids: list[str], date_from: str,
              date_to: str, marketplace: str = "EBAY_US") -> dict:
    g = GROUPS[group]
    body = {
        "reportType": "CAMPAIGN_PERFORMANCE_REPORT",
        "reportFormat": "TSV_GZIP",
        "marketplaceId": marketplace,
        "dateFrom": date_from,
        "dateTo": date_to,
        "fundingModels": g["funding"],
        "campaignIds": campaign_ids,
        "dimensions": g["dims"],
        "metricKeys": g["metrics"],
    }
    if g.get("channels"):
        body["channels"] = g["channels"]
    return body


def _find_task(api_send, creds, body: dict) -> Optional[dict]:
    """The task this run just created: same dateFrom (to the second) and
    campaign IDs. Not metric keys: eBay rewrites those (offsite cpc_* -> oa_*)."""
    want = (body["dateFrom"][:19], sorted(body["campaignIds"]))
    offset, best = 0, None
    while True:
        page = api_send("GET", f"{API}/ad_report_task?limit=200&offset={offset}",
                        creds=creds, marketplace=None)
        tasks = page.get("reportTasks") or []
        for t in tasks:
            if ((t.get("dateFrom") or "")[:19], sorted(t.get("campaignIds") or [])) == want:
                if best is None or (t.get("reportTaskCreationDate") or "") > \
                        (best.get("reportTaskCreationDate") or ""):
                    best = t
        offset += len(tasks)
        if not tasks or offset >= int(page.get("total") or 0):
            return best


def _download(creds, report_id: str) -> str:
    """GET /ad_report/{id}: gzip bytes, not JSON, so not via api_send."""
    from ebay_client import _base_for, get_user_access_token

    path = f"{API}/ad_report/{report_id}"
    req = urllib.request.Request(_base_for(creds.env, path) + path, headers={
        "Authorization": f"Bearer {get_user_access_token(creds)}"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
            break
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8-sig", errors="replace")


def run_task(group: str, campaign_ids: list[str], date_from: str, date_to: str,
             creds, *, sleep=time.sleep) -> list[dict]:
    """Create one report task, wait for it, download, parse, delete the task."""
    from ebay_client import api_send

    body = task_body(group, campaign_ids, date_from, date_to)
    api_send("POST", f"{API}/ad_report_task", body, creds=creds)
    task, waited = None, 0
    while waited <= POLL_TIMEOUT:
        task = _find_task(api_send, creds, body)
        status = (task or {}).get("reportTaskStatus")
        if status == "SUCCESS":
            break
        if status == "FAILED":
            raise RuntimeError(f"{group} report task {task.get('reportTaskId')} FAILED")
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
    else:
        raise RuntimeError(f"{group} report not ready after {POLL_TIMEOUT}s "
                           f"(task {(task or {}).get('reportTaskId')})")
    try:
        text = _download(creds, task["reportId"])
    finally:
        try:
            api_send("DELETE", f"{API}/ad_report_task/{task['reportTaskId']}",
                     creds=creds, marketplace=None)
        except Exception:                                        # noqa: BLE001
            pass   # an orphaned task expires on its own; never fail a pull over it
    return [dict(r, group=group) for r in parse_report(text)]


def billed_ad_fees(days: int, store: Optional[str]) -> dict:
    """What the Finances API says ads actually cost: {total, by_fee_type}.

    eBay classifies by `ebay_finances._is_ad_fee`, the same test sync_actuals
    uses. A CREDIT (an ad fee handed back, e.g. on a cancelled order) counts
    against the total rather than adding to it."""
    import ebay_finances

    by_type: dict[str, float] = defaultdict(float)
    for t in ebay_finances.fetch_transactions(days, verbose=False, store=store):
        if t.get("transactionType") != ebay_finances.FEE_TRANSACTION_TYPE:
            continue
        if not ebay_finances._is_ad_fee(t):
            continue
        amt = float(ebay_finances._dec(t.get("amount")))
        if t.get("bookingEntry") == "CREDIT":
            amt = -abs(amt)
        else:
            amt = abs(amt)
        by_type[(t.get("feeType") or "(unlabeled)").strip()] += amt
    return {"total": round(sum(by_type.values()), 2),
            "by_fee_type": {k: round(v, 2) for k, v in sorted(by_type.items())}}


def pull(days: int, store: Optional[str] = None, *, now: Optional[datetime] = None) -> dict:
    """Pull every group's report for the last `days`, plus the billed figure,
    into `store`'s reports/ad_report*.json. A failing group is recorded in
    `errors` and the others still land."""
    from ebay_client import api_send, load_credentials

    creds = load_credentials(store)
    end = (now or datetime.now(timezone.utc)) - timedelta(minutes=5)
    start = end - timedelta(days=days)
    date_from, date_to = _iso(start), _iso(end)

    camps = api_send("GET", f"{API}/ad_campaign?limit=100", creds=creds).get("campaigns") or []
    meta = []
    ids: dict[str, list[str]] = defaultdict(list)
    for c in camps:
        g = campaign_group(c)
        budget = ((c.get("budget") or {}).get("daily") or {}).get("amount") or {}
        meta.append({"campaignId": c.get("campaignId"), "campaignName": c.get("campaignName"),
                     "status": c.get("campaignStatus"), "group": g,
                     "startDate": c.get("startDate"), "endDate": c.get("endDate"),
                     "dailyBudget": budget.get("value")})
        if overlaps(c, start, end):
            ids[g].append(str(c["campaignId"]))

    out = {"pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "store": stores.resolve_store_name(store), "days": days,
           "date_from": date_from, "date_to": date_to,
           "campaigns": meta, "rows": [], "errors": {}, "billed": None}
    for i, group in enumerate(GROUPS):
        if not ids.get(group):
            continue
        print(f"  {group}: {len(ids[group])} campaign(s) …", flush=True)
        # one second apart per group, so _find_task can never confuse them
        group_from = _iso(start - timedelta(seconds=i))
        try:
            out["rows"] += run_task(group, ids[group], group_from, date_to, creds)
        except Exception as e:                                   # noqa: BLE001
            out["errors"][group] = str(e)[:300]
            print(f"    FAILED: {str(e)[:200]}")
    try:
        out["billed"] = billed_ad_fees(days, store)
    except Exception as e:                                       # noqa: BLE001
        out["errors"]["billed"] = str(e)[:300]

    path = stores.paths(store).ad_report_json
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# 2 · parse — eBay's human-labelled CSV -> one row shape
# ---------------------------------------------------------------------------

def _num(v) -> float:
    """'$ 1,294.37', 'USD 28.00', '12.5%' -> float; anything else -> 0."""
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(v or ""))
    return float(m.group().replace(",", "")) if m else 0.0


# Metric keys as column names (API-made reports). Every funding model's key
# for the same quantity lands on the same field.
KEY_FIELDS = {
    "campaign_id": "campaign_id", "campaign_name": "campaign_name",
    "listing_id": "listing_id", "listing_title": "title", "listing_price": "price",
    "impressions": "impressions", "cpc_impressions": "impressions",
    "oa_impressions": "impressions",
    "clicks": "clicks", "cpc_clicks": "clicks", "oa_clicks": "clicks",
    "sales": "sold", "cpc_attributed_sales": "sold", "oa_attributed_sales": "sold",
    "sale_amount": "sales", "cpc_sale_amount_listingsite_currency": "sales",
    "oa_sale_amount_listingsite_currency": "sales",
    "ad_fees": "ad_fees", "cpc_ad_fees_listingsite_currency": "ad_fees",
    "oa_ad_fees_listingsite_currency": "ad_fees",
}


def _canonical(header: str, keyed: bool = False) -> Optional[str]:
    """Map one of eBay's column labels onto a row field, or None to drop it.

    Order matters: the specific tests (organic, per-placement splits, rates)
    run before the generic ones so "Promoted Listings Conversion rate" never
    reads as sales and "Ad fees (via eBay placements)" never doubles "Ad fees".

    `keyed` says the header row is metric keys (an API-made report). The two
    formats disagree on one word: key `sales` is a COUNT, label "Sales" is
    DOLLARS, so the key table is only consulted for a keyed header.
    """
    h = " ".join(header.lower().split())
    if not h:
        return None
    if keyed:
        return KEY_FIELDS.get(h)
    if h == "campaign id":
        return "campaign_id"
    if h == "campaign name":
        return "campaign_name"
    if h in ("item id", "listing id"):
        return "listing_id"
    if h in ("title", "listing title"):
        return "title"
    if h.startswith("price"):
        return "price"
    if "organic" in h:
        return "organic_sold" if ("sold" in h or "sales" in h) and "amount" not in h \
            and "click" not in h and "impression" not in h else None
    # The CPS download's ONLY impressions column is "...(via eBay Placements)",
    # so impressions are let through before the per-placement filter below.
    if "impressions" in h and "external" not in h and "share" not in h:
        return "impressions"
    if "(via" in h or any(w in h for w in ("rate", "ctr", "return", "average", "avg",
                                           "cost per", "contribution", "roas", "acos")):
        return None
    if "ad fees" in h:
        return "ad_fees"
    if "impressions" in h:
        return "impressions"
    if "clicks" in h:
        return "clicks"
    if "sold" in h or h in ("attributed sales", "sales (quantity)"):
        return "sold"
    if "sales" in h or "sale amount" in h:
        return "sales"
    return None


def parse_report(text: str) -> list[dict]:
    """Rows of a downloaded report, as {campaign_id, campaign_name, listing_id,
    title, price, impressions, clicks, sold, sales, ad_fees, organic_sold}."""
    lines = text.splitlines()
    try:
        i = next(n for n, l in enumerate(lines)
                 if "campaign id" in l.lower() or "campaign_id" in l.lower())
    except StopIteration:
        return []
    body = "\n".join(lines[i:])
    delim = "\t" if "\t" in lines[i] else ","
    reader = csv.reader(io.StringIO(body), delimiter=delim)
    header = next(reader)
    keyed = "campaign_id" in [h.strip() for h in header]
    fields = [_canonical(h, keyed) for h in header]
    rows = []
    for rec in reader:
        if not any(x.strip() for x in rec):
            continue
        r = {"campaign_id": "", "campaign_name": "", "listing_id": "", "title": "",
             "price": 0.0, "impressions": 0.0, "clicks": 0.0, "sold": 0.0,
             "sales": 0.0, "ad_fees": 0.0, "organic_sold": 0.0}
        seen = set()
        for f, v in zip(fields, rec):
            if f is None or f in seen:       # first column of a kind wins
                continue
            seen.add(f)
            r[f] = v.strip() if f in ("campaign_id", "campaign_name",
                                      "listing_id", "title") else _num(v)
        rows.append(r)
    return rows


# ---------------------------------------------------------------------------
# 3 · advise
# ---------------------------------------------------------------------------

def _roas(sales: float, fees: float) -> Optional[float]:
    return round(sales / fees, 2) if fees else None


def campaign_totals(data: dict) -> list[dict]:
    """One line per campaign: metadata plus the summed report rows."""
    sums: dict[str, dict] = defaultdict(lambda: defaultdict(float))
    for r in data.get("rows") or []:
        s = sums[str(r["campaign_id"])]
        for k in ("impressions", "clicks", "sold", "sales", "ad_fees"):
            s[k] += float(r.get(k) or 0)
        s["listings"] += 1
    out = []
    for c in data.get("campaigns") or []:
        s = sums.get(str(c["campaignId"]), {})
        t = {**c, **{k: round(float(s.get(k, 0)), 2) for k in
                     ("impressions", "clicks", "sold", "sales", "ad_fees", "listings")}}
        t["roas"] = _roas(t["sales"], t["ad_fees"])
        t["reported"] = c["group"] not in (data.get("errors") or {})
        out.append(t)
    return out


def _gross_sales(store: Optional[str], since: datetime) -> Optional[float]:
    p = stores.paths(store).sales_ledger
    if not p.exists():
        return None
    total = 0.0
    with p.open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            d = (r.get("sold_at") or "")[:10]
            if d and d >= since.date().isoformat():
                total += _num(r.get("gross"))
    return round(total, 2)


def advise(data: dict, gross_sales: Optional[float] = None,
           now: Optional[datetime] = None) -> list[dict]:
    """Strategy findings, most urgent first: {level, campaign, text}.

    level: ACT (money is going out for nothing), WATCH (worth a look), KEEP
    (working; leave it alone), INFO (context for the numbers above)."""
    now = now or datetime.now(timezone.utc)
    end = _parse_iso(data.get("date_to")) or now
    tots = campaign_totals(data)
    F: list[dict] = []

    def add(level, campaign, text):
        F.append({"level": level, "campaign": campaign, "text": text})

    cps = [t for t in tots if t["group"] == "CPS" and t["ad_fees"]]
    cps_fees = sum(t["ad_fees"] for t in cps)
    cps_sales = sum(t["sales"] for t in cps)
    cps_cost = cps_fees / cps_sales if cps_sales else None   # fees per $ sold

    for t in tots:
        name, fees, sales = t["campaignName"], t["ad_fees"], t["sales"]
        live = t["status"] in ("RUNNING", "SCHEDULED")
        if not t["reported"]:
            continue
        # A: costs more than it sells
        if fees >= MIN_SPEND and sales < fees:
            what = ("Pause or end it." if live else
                    f"It is {t['status'].lower()}; leave it off.")
            add("ACT", name, f"${fees:.2f} in ad fees returned ${sales:.2f} in sales "
                             f"(ROAS {t['roas'] or 0:.2f}). {what}")
            continue
        # B: per-click cost of a sale well above the cost-per-sale benchmark
        if (t["group"] != "CPS" and fees >= MIN_SPEND and sales and cps_cost
                and fees / sales > cps_cost * CPC_VS_CPS_FACTOR):
            add("WATCH", name, f"Costs {fees / sales:.0%} of each sale; the cost-per-sale "
                               f"campaigns cost {cps_cost:.0%}. Those listings would cost "
                               f"less in a cost-per-sale campaign.")
        # C: running but not serving
        started = _parse_iso(t.get("startDate"))
        if (live and not t["impressions"] and started
                and (end - started).days >= NOT_SERVING_DAYS):
            # costs nothing, so not ACT, but it is a strategy that isn't running
            add("WATCH", name,
                f"Running since {started.date()} with 0 impressions in the window. "
                f"eBay is not showing these ads; check the campaign in Seller Hub "
                f"(listings, keywords or bids).")
        # D: working
        if t["group"] == "CPS" and fees and t["roas"] and t["roas"] >= 5:
            if live:
                add("KEEP", name, f"${sales:.2f} in sales for ${fees:.2f} in fees "
                                  f"(ROAS {t['roas']:.1f}).")
            else:
                add("INFO", name, f"Worked while it ran: ${sales:.2f} in sales for "
                                  f"${fees:.2f} in fees (ROAS {t['roas']:.1f}).")

    # E: per-click listings that only cost money, in campaigns still running.
    # A paused campaign's listings spend nothing now; A above already says
    # whether that campaign should stay off.
    live_ids = {str(t["campaignId"]) for t in tots
                if t["status"] in ("RUNNING", "SCHEDULED")}
    burn = defaultdict(lambda: [0.0, 0.0, "", ""])
    for r in data.get("rows") or []:
        if r.get("group") == "CPS" or str(r.get("campaign_id")) not in live_ids:
            continue
        b = burn[r["listing_id"]]
        b[0] += float(r.get("ad_fees") or 0)
        b[1] += float(r.get("sold") or 0)
        b[2] = r.get("title") or b[2]
        b[3] = r.get("campaign_name") or b[3]
    losers = sorted(((lid, *v) for lid, v in burn.items()
                     if v[0] >= LISTING_BURN and not v[1]), key=lambda x: -x[1])
    if losers:
        spend = sum(x[1] for x in losers)
        top = "; ".join(f"{lid} {title[:40]} ${fees:.2f}"
                        for lid, fees, _, title, _ in losers[:5])
        add("ACT", "per-click listings",
            f"{len(losers)} listing(s) cost ${spend:.2f} in clicks with no sale. "
            f"Remove them from per-click campaigns. Top: {top}")

    # F: ad fees against the whole business
    total_fees = sum(t["ad_fees"] for t in tots)
    if gross_sales:
        share = total_fees / gross_sales
        add("WATCH" if share > AD_SHARE_WARN else "INFO", "all campaigns",
            f"Ad fees ${total_fees:.2f} = {share:.0%} of ${gross_sales:.2f} gross sales "
            f"in the window" + (f" (over the {AD_SHARE_WARN:.0%} line)."
                                if share > AD_SHARE_WARN else "."))

    # G: billed vs reported
    billed = (data.get("billed") or {}).get("total")
    if billed is not None and abs(billed - total_fees) > RECONCILE_TOL:
        add("INFO", "billing",
            f"Finances API billed ${billed:.2f} in ad fees; the reports attribute "
            f"${total_fees:.2f}. eBay reconciles report metrics for 72 hours, and "
            f"by type the bill was {data['billed']['by_fee_type']}.")

    for g, err in (data.get("errors") or {}).items():
        add("INFO", g, f"Not reported: {err}")

    order = {"ACT": 0, "WATCH": 1, "KEEP": 2, "INFO": 3}
    return sorted(F, key=lambda f: order[f["level"]])


# ---------------------------------------------------------------------------
# 4 · print
# ---------------------------------------------------------------------------

def render(data: dict, findings: list[dict]) -> str:
    L = [f"AD REPORT — {data['date_from'][:10]} → {data['date_to'][:10]} "
         f"({data['days']} days, pulled {data['pulled_at']})", ""]
    L.append(f"  {'campaign':34} {'type':7} {'status':8} {'impr':>8} {'clicks':>6} "
             f"{'sold':>4} {'sales':>9} {'fees':>8} {'ROAS':>5}")
    for t in sorted(campaign_totals(data), key=lambda t: -t["ad_fees"]):
        if not (t["impressions"] or t["ad_fees"] or t["status"] == "RUNNING"):
            continue
        L.append(f"  {(t['campaignName'] or '')[:34]:34} {t['group']:7} "
                 f"{(t['status'] or '')[:8]:8} {t['impressions']:>8.0f} {t['clicks']:>6.0f} "
                 f"{t['sold']:>4.0f} {t['sales']:>9.2f} {t['ad_fees']:>8.2f} "
                 + (f"{t['roas']:>5.1f}" if t["roas"] is not None else f"{'—':>5}"))
    L += ["", "WHAT TO DO"]
    for f in findings:
        L.append(f"  [{f['level']:5}] {f['campaign']}: {f['text']}")
    if not findings:
        L.append("  nothing stands out.")
    return "\n".join(L)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30, help="report window (default 30)")
    ap.add_argument("--no-sync", action="store_true",
                    help="advise from the last pull; touch no API")
    ap.add_argument("--json", action="store_true", help="print findings as JSON")
    stores.add_store_args(ap)
    a = ap.parse_args(argv)
    store = stores.resolve_store_name(a.store)
    path = stores.paths(store).ad_report_json

    if a.no_sync:
        if not path.exists():
            print(f"[ERR] no {path.name} yet; run without --no-sync first")
            return 1
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        print("PULL ad reports" + ("" if stores.is_default(store)
                                   else f" {stores.store_label(store)}"))
        data = pull(a.days, store)

    since = _parse_iso(data["date_from"]) or datetime.now(timezone.utc)
    findings = advise(data, _gross_sales(store, since))
    if a.json:
        print(json.dumps(findings, indent=1))
    else:
        print(render(data, findings))
        print(f"\n[OK] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
