#!/usr/bin/env python3
"""sales_report — one command that syncs eBay's actuals and draws the dashboard.

Run it periodically. It does three things, in order:

  1. **Sync.** `lib/sync_actuals.py --apply` (orders -> sales_ledger.csv, SOLD
     stamps, ledger advanced) and `tools/ebay_sheet.py` (live listings ->
     inventory_sheet.csv), then pulls the Promoted Listings campaigns and every
     ad in them into `reports/ebay_ads.json`.
  2. **Read.** Everything below this line reads the LOCAL store, never the API,
     so `--no-sync` gives you the same dashboard offline and a failed network
     call cannot silently produce a zero.
  3. **Draw.** `reports/sales_dashboard.html`, in the house style.

    python tools/sales_report.py                 # sync, then draw
    python tools/sales_report.py --no-sync       # redraw from local data
    python tools/sales_report.py --days 365      # order window (default 365)

WHY PROMOTED LISTINGS GET THEIR OWN PANEL

`totalMarketplaceFee` is the FINAL VALUE FEE ONLY (#115). Promoted-listing fees
are billed separately and appear nowhere in the order payload — so there is no
ad fee in that number to separate out, and every "net" derived from it is
overstated by the whole ad bill. Measured on one month of live orders: splitting
clean single-line orders by `lineItems[].properties.soldViaAdCampaign` gives a
fee rate of 14.67% advertised vs 14.97% not — indistinguishable, where an
embedded 9-10% ad bid would show as a 9-10 point gap. Seller Hub's Listings
Sales Report reconciles its monthly final-value-fee total to this field exactly.

Promoted performance therefore has to come from the Marketing API side: which listings carry an ad, in which campaign, at what bid, and whether
that campaign is actually RUNNING. Joining the ad set to the sold set is what
turns that into "did promotion sell anything".

That join answers a narrower question than eBay's own ad report, and says so on
the page: an ad on the listing at report time is not proof the sale came through
the ad. eBay's attributed numbers live behind an async report task
(`/sell/marketing/v1/ad_report_task`, a POST) which this tool deliberately does
not create — it writes nothing to the account.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling tools

REPORTS = REPO / "reports"
ADS_JSON = REPORTS / "ebay_ads.json"
FINANCES_STATUS_JSON = REPORTS / "finances_sync_status.json"
OUT_HTML = REPORTS / "sales_dashboard.html"


# ---------------------------------------------------------------------------
# 1 · sync
# ---------------------------------------------------------------------------

def _run(label: str, args: list[str]) -> bool:
    print(f"  {label} …", flush=True)
    p = subprocess.run([sys.executable, *args], cwd=REPO,
                       capture_output=True, text=True)
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-4:]
        print(f"    FAILED ({p.returncode}): " + " / ".join(tail))
        return False
    return True


def pull_ads() -> dict:
    """Campaigns + every ad in them. Written to reports/ebay_ads.json.

    Ads are fetched per campaign and paged. A campaign whose channel is
    OFF_SITE answers HTTP 400 to the ad endpoint — those campaigns have no
    per-listing ads to enumerate — so the error is recorded on the campaign
    rather than aborting the pull.
    """
    from ebay_client import api_send

    camps = api_send("GET", "/sell/marketing/v1/ad_campaign?limit=100")
    out = {"pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "campaigns": [], "ads": []}
    for c in camps.get("campaigns", []):
        rec = {k: c.get(k) for k in (
            "campaignId", "campaignName", "campaignStatus", "campaignTargetingType",
            "channels", "startDate", "endDate")}
        rec["fundingModel"] = (c.get("fundingStrategy") or {}).get("fundingModel")
        rec["biddingStrategy"] = (c.get("fundingStrategy") or {}).get("biddingStrategy")
        budget = ((c.get("budget") or {}).get("daily") or {}).get("amount") or {}
        rec["dailyBudget"] = budget.get("value")
        rec["adError"] = None
        n = 0
        offset = 0
        while True:
            try:
                page = api_send("GET", f"/sell/marketing/v1/ad_campaign/"
                                       f"{c['campaignId']}/ad?limit=200&offset={offset}")
            except Exception as e:                                   # noqa: BLE001
                rec["adError"] = str(e)[:200]
                break
            ads = page.get("ads") or []
            for a in ads:
                out["ads"].append({
                    "campaignId": c["campaignId"],
                    "campaignName": c.get("campaignName"),
                    "campaignStatus": c.get("campaignStatus"),
                    "targeting": c.get("campaignTargetingType"),
                    "channels": c.get("channels"),
                    "adId": a.get("adId"),
                    "listingId": str(a.get("listingId") or ""),
                    "adStatus": a.get("adStatus"),
                    "bidPercentage": a.get("bidPercentage"),
                })
            n += len(ads)
            offset += len(ads)
            if len(ads) < 200 or offset >= int(page.get("total") or 0):
                break
        rec["adCount"] = n
        out["campaigns"].append(rec)
    REPORTS.mkdir(exist_ok=True)
    ADS_JSON.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def sync(days: int) -> None:
    print("SYNC")
    _run("orders -> sales_ledger.csv", ["lib/sync_actuals.py", "--days", str(days), "--apply"])
    _run("live listings -> inventory_sheet.csv",
         ["tools/ebay_sheet.py", "--csv", "inventory_sheet.csv",
          "--json", "inventory_sheet.json"])
    print("  promoted listings -> reports/ebay_ads.json …", flush=True)
    try:
        a = pull_ads()
        print(f"    {len(a['campaigns'])} campaign(s), {len(a['ads'])} ad(s)")
    except Exception as e:                                           # noqa: BLE001
        print(f"    FAILED: {str(e)[:200]}")


# ---------------------------------------------------------------------------
# 2 · read
# ---------------------------------------------------------------------------

def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _ledger_has_finance_columns(path: Path) -> bool:
    """Whether sales_ledger.csv's header carries the #119 ad_fee/actual_postage
    columns at all — a ledger with NO finances_sync_status.json yet is
    either "sync_actuals.py hasn't been re-run since #119" (columns absent)
    or "it has, just not applied this window" (columns present); those are
    different explanations for the reader (see gather()'s fin_qualifier)."""
    if not path.exists():
        return False
    with path.open(newline="", encoding="utf-8-sig") as fh:
        header = next(csv.reader(fh), [])
    return "ad_fee" in header and "actual_postage" in header


def _f(v, default=0.0) -> float:
    try:
        return float(str(v).replace("$", "").replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def _fopt(v) -> Optional[float]:
    """Like `_f`, but blank/missing stays None (#119) — `ad_fee`/
    `actual_postage` are legitimately UNKNOWN for an order the Finances API
    hasn't been read for yet, and 0.0 would falsely claim "no ad spend"."""
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _date(v: str):
    if not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    # The ledgers are not uniformly stamped — some rows carry an offset and some
    # do not, and mixing the two raises on any comparison. Everything eBay
    # writes is UTC, so an absent offset means UTC rather than local.
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def gather(days: int) -> dict:
    sales = _rows(REPO / "sales_ledger.csv")
    live = _rows(REPO / "inventory_sheet.csv")
    ledger = _rows(REPO / "listings_ledger.csv")
    ads_doc = json.loads(ADS_JSON.read_text(encoding="utf-8")) if ADS_JSON.exists() else \
        {"campaigns": [], "ads": [], "pulled_at": None}
    fin_status = json.loads(FINANCES_STATUS_JSON.read_text(encoding="utf-8")) \
        if FINANCES_STATUS_JSON.exists() else None

    # listing_id -> when we published it, for days-to-sale
    published = {r["listing_id"]: _date(r.get("published_at") or r.get("created_at") or "")
                 for r in ledger if r.get("listing_id")}
    # listing_id -> the ad on it (most recent campaign wins if several)
    ad_by_listing: dict[str, dict] = {}
    for a in ads_doc["ads"]:
        if a["listingId"]:
            ad_by_listing.setdefault(a["listingId"], a)

    out = {"ads": ads_doc, "ad_by_listing": ad_by_listing, "live": live}

    rows = []
    for r in sales:
        sold = _date(r.get("sold_at", ""))
        pub = published.get(r.get("listing_id", ""))
        rows.append({
            "sold_at": sold,
            "title": r.get("title", ""),
            "listing_id": r.get("listing_id", ""),
            "shoot": r.get("shoot_dir", ""),
            "gross": _f(r.get("gross")),
            "fee": _f(r.get("ebay_fee")),
            "net": _f(r.get("net_before_postage")),
            "ask": _f(r.get("listed_price")),
            "pct": _f(r.get("pct_of_ask")),
            "days": (sold - pub).days if (sold and pub and sold > pub) else None,
            "ad": ad_by_listing.get(r.get("listing_id", "")),
            # #119 (route B, sell.finances) — real ad fee + actual postage per
            # order, if lib/sync_actuals.py has read it; None (not 0.0) when
            # it hasn't, so a missing figure is never rendered as "$0 spent".
            "ad_fee": _fopt(r.get("ad_fee")),
            "actual_postage": _fopt(r.get("actual_postage")),
        })
    rows.sort(key=lambda r: r["sold_at"] or datetime.min.replace(tzinfo=timezone.utc),
              reverse=True)
    out["sales"] = rows

    # ---- #119: how much of the window has real ad-fee/postage coverage ----
    # A row needs BOTH figures known to drop the "before ads & postage"
    # qualifier for that row's own net; a headline coverage fraction below
    # decides whether the PAGE-WIDE qualifier can come off, per #119
    # acceptance ("comes off only when it stops being true").
    fin_known = [r for r in rows if r["ad_fee"] is not None and r["actual_postage"] is not None]
    out["fin_ad_fee_total"] = sum(r["ad_fee"] for r in fin_known) if fin_known else None
    out["fin_postage_total"] = sum(r["actual_postage"] for r in fin_known) if fin_known else None
    out["fin_covered_n"] = len(fin_known)
    out["fin_status"] = fin_status
    if not rows:
        out["fin_qualifier"] = ("no sold line items in this window", "")
    elif len(fin_known) == len(rows):
        out["fin_qualifier"] = (None, "")  # fully known — qualifier drops
    else:
        missing = len(rows) - len(fin_known)
        if fin_status and fin_status.get("reason"):
            why = str(fin_status["reason"])[:140]
        elif fin_status is None:
            if _ledger_has_finance_columns(REPO / "sales_ledger.csv"):
                why = "sync_actuals.py --apply has not read the Finances API yet"
            else:
                why = ("sales_ledger.csv predates #119 (no ad_fee/actual_postage "
                       "columns yet) — run sync_actuals.py --apply to add them")
        elif fin_status.get("ok"):
            # The sync succeeded (no reason to report) but coverage is still
            # partial — a genuinely different situation from "not re-consented
            # yet", which would be a false explanation here.
            why = ("the Finances API was read successfully, but some sold "
                   "line items have no matching transaction yet (still "
                   "settling on eBay's side, or genuinely none)")
        else:
            why = ("re-consent with the sell.finances scope has not happened yet "
                   "(#119) — see lib/ebay_client.py USER_SCOPES_SELL")
        out["fin_qualifier"] = (
            f"{missing} of {len(rows)} sold line item(s) have no ad-fee/postage "
            f"match yet — {why}", why)

    # headline
    out["gross"] = sum(r["gross"] for r in rows)
    out["fee"] = sum(r["fee"] for r in rows)
    out["net"] = sum(r["net"] for r in rows)
    out["count"] = len(rows)
    # #119: the real headline, once every sold row this window has a Finances
    # match for BOTH ad fee and actual postage — gross minus the final value
    # fee AND the promoted-listing bill AND what postage actually cost.
    # `None` (not the #115 partial number) whenever coverage is incomplete,
    # so the two never get silently conflated on the page.
    out["net_after_ads_postage"] = (
        out["net"] - out["fin_ad_fee_total"] - out["fin_postage_total"]
        if out["fin_covered_n"] == len(rows) and rows else None)
    out["fee_pct"] = (out["fee"] / out["gross"] * 100) if out["gross"] else 0
    withask = [r for r in rows if r["ask"] > 0 and r["pct"]]
    out["avg_pct"] = statistics.mean(r["pct"] for r in withask) if withask else 0
    out["below"] = sorted((r for r in withask if r["pct"] < 99),
                          key=lambda r: r["pct"])
    dd = [r["days"] for r in rows if r["days"] is not None]
    out["median_days"] = statistics.median(dd) if dd else None
    out["dated_sales"] = len(dd)
    out["tracked"] = sum(1 for r in rows if r["shoot"])
    out["untracked"] = out["count"] - out["tracked"]

    # by month
    months = defaultdict(lambda: {"n": 0, "gross": 0.0})
    for r in rows:
        if r["sold_at"]:
            k = r["sold_at"].strftime("%Y-%m")
            months[k]["n"] += 1
            months[k]["gross"] += r["gross"]
    out["months"] = sorted(months.items())[-12:]

    # by collection (top two path segments of the shoot dir)
    coll = defaultdict(lambda: {"n": 0, "gross": 0.0, "net": 0.0})
    for r in rows:
        parts = [p for p in r["shoot"].replace("\\", "/").split("/") if p and p != "inventory"]
        key = "/".join(parts[:2]) if len(parts) > 1 else (parts[0] if parts else "— untracked —")
        coll[key]["n"] += 1
        coll[key]["gross"] += r["gross"]
        coll[key]["net"] += r["net"]
    out["collections"] = sorted(coll.items(), key=lambda kv: -kv[1]["gross"])

    # promoted
    promoted = [r for r in rows if r["ad"]]
    plain = [r for r in rows if not r["ad"]]
    out["promoted_sales"] = promoted
    out["promo"] = {
        "n": len(promoted), "gross": sum(r["gross"] for r in promoted),
        "avg_pct": statistics.mean([r["pct"] for r in promoted if r["pct"]] or [0]),
        "plain_n": len(plain), "plain_gross": sum(r["gross"] for r in plain),
        "plain_avg_pct": statistics.mean([r["pct"] for r in plain if r["pct"]] or [0]),
    }
    # `live` is the whole sheet, sold and ended rows included, and a CHOICE
    # listing contributes one row per variation — so filter to live rows and
    # count DISTINCT listing ids, or the denominator overstates the store.
    live_ids = {r["listing_id"] for r in live
                if r.get("listing_id") and (r.get("live") or "").lower() == "yes"}
    running = {c["campaignId"] for c in ads_doc["campaigns"]
               if c.get("campaignStatus") == "RUNNING"}
    out["live_count"] = len(live_ids)
    out["live_with_ad"] = len(live_ids & set(ad_by_listing))
    # A cost-per-sale ad carries no `adStatus` at all — the field is CPC-only.
    # Requiring "ACTIVE" therefore reported 0 of 141 promoted on the very day a
    # rules-based CPS campaign filled itself with 130 ads. An ad in a RUNNING
    # campaign counts unless it says otherwise.
    out["live_with_running_ad"] = len(
        {lid for lid, a in ad_by_listing.items()
         if lid in live_ids and a["campaignId"] in running
         and a.get("adStatus") in (None, "", "ACTIVE")})
    out["running_campaigns"] = len(running)
    out["ad_status_mix"] = Counter(a.get("adStatus") or "?" for a in ads_doc["ads"])
    # ---- the store as it stands, not just what sold -------------------------
    # A CHOICE listing contributes one sheet row per variation, so walk to
    # distinct listing ids before summing asks or the shop looks twice its size.
    seen: set[str] = set()
    live_rows, ask_total = [], 0.0
    cat = Counter()
    for r in live:
        lid = r.get("listing_id") or ""
        if not lid or (r.get("live") or "").lower() != "yes" or lid in seen:
            continue
        seen.add(lid)
        live_rows.append(r)
        ask_total += _f(r.get("price"))
        cat[r.get("category_top") or "—"] += 1
    out["live_rows"] = live_rows
    out["live_ask_total"] = ask_total
    out["categories"] = cat.most_common(8)
    sold_n = out["count"]
    out["sell_through"] = sold_n / (sold_n + len(live_rows)) * 100 if (sold_n + len(live_rows)) else 0

    # ---- did the PRICE stage's justified band hold? -------------------------
    out["bands"] = band_stats()
    out["days"] = days
    return out


def band_stats() -> dict:
    """Ask/realised against the Conservative-Recommended-Push-high band.

    Delegated to price_vs_actual so there is ONE parser for price.txt. That file
    is prose, not a serialiser, and a loose regex already read a Burberry floor
    of $25 off the line "Conservative estimate: $25-$50 incremental value" when
    the real floor was $225 two lines below. Two copies of that rule would drift.
    """
    try:
        import price_vs_actual as pva
    except Exception:                                                # noqa: BLE001
        return {"n": 0, "rows": [], "breaches": [], "cohorts": []}

    rows = pva.gather()
    for r in rows:
        r["where"] = pva.classify(r)
    withceil = [r for r in rows if r["ask"] and r["ceiling"]]
    at_ceiling = [r for r in withceil if r["ask"] >= r["ceiling"] * 0.995]
    breaches = sorted((r for r in rows if r["where"] == "BELOW FLOOR"),
                      key=lambda r: (r["sold"] - r["floor"]))

    def med(vals):
        vals = sorted(v for v in vals if v)
        return vals[len(vals) // 2] if vals else None

    coh = defaultdict(lambda: {"n": 0, "sold": 0.0, "rec": 0.0})
    for r in rows:
        if not r["rec"]:
            continue
        key = r["shoot"].split("/")[0]
        coh[key]["n"] += 1
        coh[key]["sold"] += r["sold"]
        coh[key]["rec"] += r["rec"]
    cohorts = sorted(((k, v) for k, v in coh.items() if v["n"] >= 2),
                     key=lambda kv: kv[1]["sold"] / kv[1]["rec"])
    return {
        "n": len(rows),
        "rows": rows,
        "breaches": breaches,
        "gap": sum(r["floor"] - r["sold"] for r in breaches),
        "med_rec": med([r["sold"] / r["rec"] for r in rows if r["rec"]]),
        "med_ceil": med([r["sold"] / r["ceiling"] for r in rows if r["ceiling"]]),
        "at_ceiling": len(at_ceiling), "with_ceiling": len(withceil),
        "above_ceiling": sum(1 for r in rows if r["where"] == "at/above ceiling"),
        "cohorts": cohorts,
    }


# ---------------------------------------------------------------------------
# 3 · draw
# ---------------------------------------------------------------------------

STYLE = """
:root{
  --ground:#ECEDEF; --surface:#F8F8F9; --sunk:#E3E5E8;
  --ink:#17181C; --muted:#6A6E76; --rule:#D7D9DD;
  --accent:#6B5E8C; --ok:#3E7A5E; --warn:#A6702A;
  --shadow:0 1px 2px rgba(20,22,26,.07), 0 14px 34px -22px rgba(20,22,26,.30);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ground:#121316; --surface:#1A1C20; --sunk:#0D0E10;
    --ink:#E9E9EC; --muted:#979BA3; --rule:#2A2D33;
    --accent:#A99AC9; --ok:#63B189; --warn:#D3A05C;
    --shadow:0 1px 2px rgba(0,0,0,.5), 0 14px 34px -22px rgba(0,0,0,.8);
  }
}
:root[data-theme="dark"]{
  --ground:#121316; --surface:#1A1C20; --sunk:#0D0E10;
  --ink:#E9E9EC; --muted:#979BA3; --rule:#2A2D33;
  --accent:#A99AC9; --ok:#63B189; --warn:#D3A05C;
  --shadow:0 1px 2px rgba(0,0,0,.5), 0 14px 34px -22px rgba(0,0,0,.8);
}
*{box-sizing:border-box}
body{margin:0;padding:26px 20px 60px;background:var(--ground);color:var(--ink);
  font:15px/1.5 "IBM Plex Sans",ui-sans-serif,system-ui,sans-serif}
.wrap{max-width:1120px;margin:0 auto;display:flex;flex-direction:column;gap:18px}
.card{background:var(--surface);border:1px solid var(--rule);border-radius:4px;
  box-shadow:var(--shadow);overflow:hidden}
.hdr{padding:22px 24px 18px;border-bottom:1px solid var(--rule)}
.eyebrow{font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--muted);margin:0 0 7px}
h1{font:600 21px/1.25 "IBM Plex Sans",sans-serif;margin:0}
.ct{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:11.5px;
  color:var(--muted);margin-top:7px}
h2{font:600 11px/1 "IBM Plex Sans",sans-serif;letter-spacing:.13em;
  text-transform:uppercase;color:var(--muted);margin:0 0 14px}
.pad{padding:22px 24px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;
  background:var(--rule)}
.stat{background:var(--surface);padding:18px 20px}
.stat .amt{font:500 26px/1.1 "IBM Plex Mono",ui-monospace,monospace;
  font-variant-numeric:tabular-nums;color:var(--accent)}
.stat .lbl{font-size:11px;color:var(--muted);margin-top:6px;letter-spacing:.04em}
.stat .sub{font-size:11.5px;color:var(--muted);margin-top:3px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font:600 10.5px/1 "IBM Plex Sans",sans-serif;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);padding:0 10px 9px 0;
  border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:8px 10px 8px 0;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:0}
.num{font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums;
  text-align:right;white-space:nowrap}
.dim{color:var(--muted)}
.warn{color:var(--warn)}
.ok{color:var(--ok)}
a{color:var(--accent)}
.scroll{overflow-x:auto}
.bars{display:flex;align-items:flex-end;gap:10px;height:150px;padding-top:6px}
.bar{flex:1;display:flex;flex-direction:column;justify-content:flex-end;
  align-items:center;gap:6px;min-width:0}
.bar .fill{width:100%;background:var(--accent);border-radius:2px 2px 0 0;min-height:2px}
.bar .cap{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:10px;
  color:var(--muted);white-space:nowrap}
.bar .val{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:10.5px;
  color:var(--ink);font-variant-numeric:tabular-nums}
.note{font-size:12.5px;color:var(--muted);margin:14px 0 0;
  padding-top:12px;border-top:1px solid var(--rule)}
.pill{display:inline-block;font:600 10px/1 "IBM Plex Mono",monospace;
  letter-spacing:.08em;padding:4px 7px;border-radius:3px;border:1px solid var(--rule);
  color:var(--muted)}
.pill.on{color:var(--ok);border-color:var(--ok)}
.pill.off{color:var(--warn);border-color:var(--warn)}
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _money(v) -> str:
    return f"${v:,.2f}"


def _stat(amt: str, lbl: str, sub: str = "") -> str:
    return (f'<div class="stat"><div class="amt">{_e(amt)}</div>'
            f'<div class="lbl">{_e(lbl)}</div>'
            + (f'<div class="sub">{_e(sub)}</div>' if sub else "") + "</div>")


def _itm(lid: str, text: str) -> str:
    if not lid:
        return _e(text)
    return f'<a href="https://www.ebay.com/itm/{_e(lid)}">{_e(text)}</a>'


def draw(d: dict) -> str:
    P = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    pulled = d["ads"].get("pulled_at") or "never"

    # #119: the headline NET drops the "before ads & postage" qualifier only
    # once every sold row this window carries a real Finances-API match for
    # BOTH ad fee and postage — see gather()'s net_after_ads_postage.
    if d["net_after_ads_postage"] is not None:
        net_stat = _stat(_money(d["net_after_ads_postage"]), "net after ads & postage",
                         f'gross minus final value fee, ad fees, and actual postage '
                         f'({d["fin_covered_n"]}/{d["count"]} sold line items, #119)')
    else:
        qualifier, _why = d["fin_qualifier"]
        net_stat = _stat(_money(d["net"]), "net before ads & postage",
                         (f'gross minus final value fee only — {qualifier}' if qualifier
                          else 'gross minus final value fee only (#115)'))

    P.append(f'<div class="card"><div class="hdr">'
             f'<p class="eyebrow">ebaybiz · sales</p><h1>Sales &amp; promotion dashboard</h1>'
             f'<div class="ct">built {_e(now)} local · order window {d["days"]} days · '
             f'ads pulled {_e(pulled)}</div></div>'
             f'<div class="stats">'
             + _stat(_money(d["gross"]), "gross realised", f'{d["count"]} sold line items')
             + _stat(_money(d["fee"]), "eBay fees", f'{d["fee_pct"]:.1f}% of gross')
             + net_stat
             + _stat(f'{d["avg_pct"]:.0f}%', "average of ask",
                     f'{len(d["below"])} of {d["count"]} sold below ask')
             + _stat(f'{d["median_days"]:.0f}d' if d["median_days"] is not None else "—",
                     "median days to sell", f'{d["dated_sales"]} datable')
             + '</div></div>')

    # the shop as it stands right now
    cats = " · ".join(f"{k} {v}" for k, v in d["categories"])
    P.append('<div class="card"><div class="pad"><h2>The shop right now</h2>'
             '<div class="stats" style="margin:0 -24px 0;border-top:1px solid var(--rule)">'
             + _stat(str(len(d["live_rows"])), "live listings", "CHOICE groups counted once")
             + _stat(_money(d["live_ask_total"]), "unsold, at ask",
                     "inventory still on the shelf")
             + _stat(f'{d["sell_through"]:.0f}%', "sell-through",
                     f'{d["count"]} sold vs {len(d["live_rows"])} live')
             + _stat(str(d["tracked"]), "sales from the pipeline",
                     f'{d["untracked"]} listed outside it')
             + '</div>'
             f'<p class="note">Category mix: {_e(cats) or "—"}</p></div></div>')

    # months
    if d["months"]:
        top = max(m[1]["gross"] for m in d["months"]) or 1
        bars = "".join(
            f'<div class="bar"><div class="val">{m[1]["gross"]:,.0f}</div>'
            f'<div class="fill" style="height:{m[1]["gross"] / top * 100:.1f}%"></div>'
            f'<div class="cap">{_e(m[0][2:])}</div></div>'
            for m in d["months"])
        P.append(f'<div class="card"><div class="pad"><h2>Gross by month</h2>'
                 f'<div class="bars">{bars}</div></div></div>')

    # promoted listings
    pr = d["promo"]
    camp_rows = "".join(
        f'<tr><td>{_e(c["campaignName"])}<div class="dim" style="font-size:11.5px">'
        f'{_e(c["campaignId"])} · {_e(c.get("fundingModel") or "")}'
        + (f' · {_e(c.get("biddingStrategy"))}' if c.get("biddingStrategy") else "")
        + '</div></td>'
        f'<td><span class="pill {"on" if c["campaignStatus"] == "RUNNING" else "off"}">'
        f'{_e(c["campaignStatus"])}</span></td>'
        f'<td>{_e(c.get("campaignTargetingType") or "manual")}</td>'
        f'<td>{_e(", ".join(c.get("channels") or []))}</td>'
        f'<td class="num">{_e(c.get("dailyBudget") or "—")}</td>'
        f'<td class="num">{c["adCount"]}</td></tr>'
        for c in d["ads"]["campaigns"])
    mix = " · ".join(f"{k} {v}" for k, v in sorted(d["ad_status_mix"].items()))
    cover = (f'{d["live_with_running_ad"]} of {d["live_count"]}'
             if d["live_count"] else "—")
    # #119: real ad-fee spend, once known — attributed by order id, NOT by
    # joining to soldViaAdCampaign (a cost-per-click ad bills regardless of
    # which sale eBay ends up crediting it to).
    ad_fee_stat = (
        _stat(_money(d["fin_ad_fee_total"]), "actual ad-fee spend (#119)",
              f'{d["fin_covered_n"]}/{d["count"]} sold line items — Finances API, by order id')
        if d["fin_ad_fee_total"] is not None else "")
    if d["fin_ad_fee_total"] is not None:
        note_tail = (
            '<code>totalMarketplaceFee</code> is the final value fee <em>only</em> '
            '(#115); the actual ad-fee spend above comes from '
            '<code>/sell/finances/v1/transaction</code> instead (#119), attributed by '
            'order id — an order\'s ad fee is counted whether or not that sale is '
            'flagged <code>soldViaAdCampaign</code>, since a cost-per-click ad bills '
            'either way.')
    else:
        qualifier, _why = d["fin_qualifier"]
        note_tail = (
            '<code>totalMarketplaceFee</code> is the final value fee <em>only</em>: '
            'promoted-listing fees are billed separately and are absent from the order '
            'payload entirely, so the ad bill is missing from every fee and net figure '
            'on this page rather than blended into them (#115). A real per-order reader '
            'exists (#119, <code>/sell/finances/v1/transaction</code>) but has no data '
            'for this window yet'
            + (f' — {_e(qualifier)}' if qualifier else '') + '.')
    P.append(
        '<div class="card"><div class="pad"><h2>Promoted &amp; targeted listings</h2>'
        '<div class="stats" style="margin:0 -24px 18px;border-top:1px solid var(--rule);'
        'border-bottom:1px solid var(--rule)">'
        + _stat(str(d["running_campaigns"]), "running campaigns",
                f'{len(d["ads"]["campaigns"])} exist')
        + _stat(cover, "live listings actively promoted",
                f'{d["live_with_ad"]} carry an ad in any campaign')
        + _stat(str(pr["n"]), "sales on a promoted listing",
                _money(pr["gross"]) + " gross")
        + _stat(f'{pr["avg_pct"]:.0f}% / {pr["plain_avg_pct"]:.0f}%',
                "of ask — promoted / not",
                f'{pr["plain_n"]} unpromoted sales')
        + ad_fee_stat
        + '</div>'
        f'<div class="scroll"><table><tr><th>Campaign</th><th>Status</th>'
        f'<th>Targeting</th><th>Channel</th><th class="num">Daily</th>'
        f'<th class="num">Ads</th></tr>{camp_rows}</table></div>'
        f'<p class="note">Ad states across all campaigns: {_e(mix) or "—"}. '
        'An ad on the listing is not proof the sale came through it — eBay attributes '
        f'that only in its own ad report. {note_tail}</p></div></div>')

    # pricing band — how the PRICE stage's justified tiers held up
    b = d["bands"]
    if b["n"]:
        adher = (f'{b["at_ceiling"]}/{b["with_ceiling"]}')
        rows = "".join(
            f'<tr><td>{_itm(r["listing_id"], r["title"])}'
            f'<div class="dim" style="font-size:11.5px">{_e(r["shoot"])}</div></td>'
            f'<td class="num">{_money(r["floor"])}</td>'
            f'<td class="num">{_money(r["ask"] or 0)}</td>'
            f'<td class="num warn">{_money(r["sold"])}</td>'
            f'<td class="num warn">{r["sold"] / r["floor"] * 100:.0f}%</td></tr>'
            for r in b["breaches"][:12])
        coh = "".join(
            f'<tr><td>{_e(k)}</td><td class="num">{v["n"]}</td>'
            f'<td class="num {"warn" if v["sold"] / v["rec"] < 0.9 else "ok"}">'
            f'{v["sold"] / v["rec"] * 100:.0f}%</td></tr>'
            for k, v in b["cohorts"][:8])
        P.append(
            '<div class="card"><div class="pad"><h2>Priced band vs realised</h2>'
            '<div class="stats" style="margin:0 -24px 18px;border-top:1px solid var(--rule);'
            'border-bottom:1px solid var(--rule)">'
            + _stat(f'{b["med_rec"] * 100:.0f}%' if b["med_rec"] else "—",
                    "median of Recommended", f'across {b["n"]} priced sales')
            + _stat(f'{b["med_ceil"] * 100:.0f}%' if b["med_ceil"] else "—",
                    "median of Push-high", f'{b["above_ceiling"]} sold at/above it')
            + _stat(adher, "asked AT the ceiling", "push-high policy followed")
            + _stat(str(len(b["breaches"])), "sold below their own floor",
                    _money(b["gap"]) + " under the floors")
            + '</div>'
            '<div class="scroll"><table><tr><th>Floor breach</th><th class="num">Floor</th>'
            '<th class="num">Ask</th><th class="num">Sold</th>'
            f'<th class="num">% of floor</th></tr>{rows}</table></div>'
            + (f'<h2 style="margin:22px 0 12px">Cohorts, realised vs Recommended</h2>'
               f'<div class="scroll"><table><tr><th>Collection</th><th class="num">Sales</th>'
               f'<th class="num">Realised / Rec</th></tr>{coh}</table></div>' if coh else "")
            + '<p class="note">The band comes from each shoot\'s <code>price.txt</code> '
            '(Conservative / Recommended / Push-high). A sale under the floor is a Best '
            'Offer that was accepted below the auto-decline the price file specified — a '
            'settings problem, not a pricing one. A cohort well under 100% is the comp '
            'read needing a refit.</p></div></div>')

    # collections
    rows = "".join(
        f'<tr><td>{_e(k)}</td><td class="num">{v["n"]}</td>'
        f'<td class="num">{_money(v["gross"])}</td>'
        f'<td class="num">{_money(v["net"])}</td></tr>'
        for k, v in d["collections"][:16])
    P.append('<div class="card"><div class="pad"><h2>Realised by collection</h2>'
             '<div class="scroll"><table><tr><th>Collection</th><th class="num">Sold</th>'
             f'<th class="num">Gross</th><th class="num">Net</th></tr>{rows}</table></div>'
             f'<p class="note">{d["untracked"]} of {d["count"]} sales have no local '
             'shoot folder — listed outside the pipeline.</p></div></div>')

    # below ask
    rows = "".join(
        f'<tr><td>{_itm(r["listing_id"], r["title"][:64])}</td>'
        f'<td class="num">{_money(r["ask"])}</td>'
        f'<td class="num">{_money(r["gross"])}</td>'
        f'<td class="num warn">{r["pct"]:.0f}%</td>'
        f'<td class="num dim">{r["days"] if r["days"] is not None else "—"}</td></tr>'
        for r in d["below"][:15])
    P.append('<div class="card"><div class="pad"><h2>Furthest below ask</h2>'
             '<div class="scroll"><table><tr><th>Item</th><th class="num">Ask</th>'
             '<th class="num">Sold</th><th class="num">% of ask</th>'
             f'<th class="num">Days</th></tr>{rows}</table></div></div></div>')

    # recent
    rows = "".join(
        f'<tr><td class="dim num">{r["sold_at"].strftime("%m-%d") if r["sold_at"] else "—"}</td>'
        f'<td>{_itm(r["listing_id"], r["title"][:60])}'
        + ('<span class="pill on" style="margin-left:8px">AD</span>' if r["ad"] else "")
        + f'</td><td class="num">{_money(r["gross"])}</td>'
        f'<td class="num dim">{_money(r["fee"])}</td>'
        f'<td class="num">{_money(r["net"])}</td></tr>'
        for r in d["sales"][:25])
    P.append('<div class="card"><div class="pad"><h2>Most recent sales</h2>'
             '<div class="scroll"><table><tr><th>Date</th><th>Item</th>'
             '<th class="num">Gross</th><th class="num">Fee</th>'
             f'<th class="num">Net</th></tr>{rows}</table></div></div></div>')

    return ('<meta charset="utf-8">\n<title>Sales Dashboard</title>\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f'<style>{STYLE}</style>\n<div class="wrap">\n' + "\n".join(P) + "\n</div>\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=365, help="order window (default 365)")
    ap.add_argument("--no-sync", action="store_true", help="draw from local data only")
    ap.add_argument("--out", default=str(OUT_HTML))
    a = ap.parse_args()

    REPORTS.mkdir(exist_ok=True)
    if not a.no_sync:
        sync(a.days)
    d = gather(a.days)
    out = Path(a.out)
    out.write_text(draw(d), encoding="utf-8")

    if d["net_after_ads_postage"] is not None:
        print(f"\n{d['count']} sales · {_money(d['gross'])} gross · "
              f"{_money(d['net_after_ads_postage'])} net (after ads & postage, #119) · "
              f"{d['avg_pct']:.0f}% of ask")
    else:
        qualifier, _why = d["fin_qualifier"]
        print(f"\n{d['count']} sales · {_money(d['gross'])} gross · {_money(d['net'])} net "
              f"(before ads & postage{' — ' + qualifier if qualifier else ''}) · "
              f"{d['avg_pct']:.0f}% of ask")
    print(f"promoted: {d['running_campaigns']} running campaign(s), "
          f"{d['live_with_running_ad']}/{d['live_count']} live listings actively promoted")
    print(f"[OK] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
