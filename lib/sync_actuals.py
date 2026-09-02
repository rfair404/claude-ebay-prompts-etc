#!/usr/bin/env python3
"""sync_actuals — reconcile local inventory against what eBay ACTUALLY did.

The listings ledger records what we *intended* (drafted, synced, published at
our ask). It does not know what an item finally SOLD for, and it is blind to
anything listed outside the Inventory API. This routine closes both gaps.

Three sources, in order of authority:

  1. **Orders (Sell > Fulfillment API)** — the truth about money. Every sale on
     the account, including listings created by hand on eBay.com, with the
     ACTUAL price paid (a $140 ask that cleared at $115 on an accepted Best
     Offer shows as 115), buyer-paid shipping, and eBay's real marketplace fee.
  2. **Offers (Sell > Inventory API)** — current status of everything WE created
     through the pipeline. Incomplete by construction: it cannot see a listing
     made in the eBay web UI.
  3. **The seller's public store page** (optional, `--store-json`) — the only
     view that covers hand-made ACTIVE listings. Produced in the logged-in
     browser; see `--print-js`.

Outputs
  sales_ledger.csv        one row per sold line item — the actuals record
  listings_ledger.csv     status advanced to SOLD (list price left intact)
  <shoot-dir>/SOLD.md     per-item stamp: what it listed at vs what it made
  stdout                  reconciliation report + ask-vs-actual analysis

Nothing is written without --apply.

CLI
    python lib/sync_actuals.py                     # report only, last 90 days
    python lib/sync_actuals.py --days 730          # wider window
    python lib/sync_actuals.py --apply             # write the records
    python lib/sync_actuals.py --print-js          # store-page extractor
    python lib/sync_actuals.py --store-json s.json # + active-listing audit
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ebay_client import api_send, EbayAPIError, EbayAuthError  # noqa: E402
import ebay_finances  # noqa: E402  /sell/finances/v1/transaction reader (#119)

REPO = Path(__file__).resolve().parent.parent
SALES_LEDGER = REPO / "sales_ledger.csv"
LISTINGS_LEDGER = REPO / "listings_ledger.csv"
INVENTORY = REPO / "inventory"
REPORTS = REPO / "reports"
FINANCES_STATUS_JSON = REPORTS / "finances_sync_status.json"

SALES_FIELDS = [
    "order_id", "sold_at", "listing_id", "sku", "title", "quantity",
    "sold_format", "item_price", "buyer_shipping", "refunded", "gross", "ebay_fee",
    "net_before_postage", "listed_price", "pct_of_ask", "shoot_dir", "matched_by",
    # #119 (route B, sell.finances): real ad fee + actual postage, per order.
    # Blank — never 0.00 — whenever the Finances API hasn't been read for this
    # order (scope not yet re-consented, the call failed, or this order simply
    # had no such transaction): 0.00 would claim "no ad spend, no postage",
    # which is a different, false, statement from "we don't know yet".
    "ad_fee", "actual_postage",
]
_MONEY_FIELDS = ("item_price", "buyer_shipping", "refunded", "gross",
                 "ebay_fee", "net_before_postage")
# Merged in write_sales_ledger like _MONEY_FIELDS, but kept separate: unlike
# every field above, these two are legitimately ABSENT (None) rather than
# always present at 0.00 — see the SALES_FIELDS comment.
_OPTIONAL_MONEY_FIELDS = ("ad_fee", "actual_postage")

STORE_JS = r"""
/* Paste into claude-in-chrome javascript_tool on the seller's store page.
   Run it twice — once on each URL below — then save each result to a file and
   pass them with --store-json.
     ACTIVE https://www.ebay.com/sch/i.html?_ssn=<SELLER>&_ipg=240&_sop=10
     SOLD   https://www.ebay.com/sch/i.html?_ssn=<SELLER>&LH_Sold=1&LH_Complete=1&_ipg=240&_sop=13
*/
(()=>{const T=n=>(n?.textContent||'').replace(/\s+/g,' ').trim();
const rows=[];
for(const c of document.querySelectorAll('li.s-card')){
  const a=c.querySelector('a[href*="/itm/"]');
  const m=a&&a.getAttribute('href').match(/\/itm\/(\d{9,})/); if(!m) continue;
  const t=T(c.querySelector('.s-card__title')); if(!t) continue;
  rows.push({item_id:m[1],title:t,price:T(c.querySelector('.s-card__price'))});}
return JSON.stringify({n:rows.length,rows});})()
"""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _dec(obj, *path) -> Decimal:
    """Dig a {'value': '12.34'} money node out of a nested dict; 0 if absent."""
    cur = obj
    for p in path:
        if not isinstance(cur, dict):
            return Decimal(0)
        cur = cur.get(p)
    if isinstance(cur, dict):
        cur = cur.get("value")
    try:
        return Decimal(str(cur))
    except (InvalidOperation, TypeError):
        return Decimal(0)


def _money(d: Decimal) -> str:
    return f"{d:.2f}"


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split())


def _one_line(s: str, limit: int) -> str:
    """Collapse embedded newlines/tabs (EbayAuthError and some API error
    messages carry them) so a stored "one-line reason" — read straight into
    CLI output and downstream qualifiers — actually is one line."""
    return " ".join(str(s).split())[:limit]


def allocate_order_totals(order_lines: dict[str, list[dict]], totals_by_order: dict,
                          field: str, *, absence_is_zero: bool = False) -> None:
    """Split each order-level total in `totals_by_order` (eBay-signed — a
    charge is negative) across that order's line-item rows in `order_lines`,
    writing a positive-magnitude Decimal share onto `field` on each row (or
    None/0 when the order's total isn't known — see `absence_is_zero`).

    `ebay_finances.attribute_fees_by_order` / `attribute_postage_by_order`
    return ONE figure per ORDER (one ad-fee bill, one shipping label — never
    itemized per line), but `flatten_orders()` produces one row per LINE
    ITEM. Writing the same order total onto every one of a multi-line
    order's rows would double- (or N-) count it the moment a caller sums
    `field` across rows — the #119 Copilot double-counting finding. Each
    line instead gets the same share of the total as its share of the
    order's (item_price + buyer_shipping) basis — the same basis
    `flatten_orders()` already uses to split the eBay fee — with the
    rounding remainder folded onto the last line so the per-line shares sum
    back to the order total exactly (Decimal division can drift for amounts
    that don't split evenly).

    When every line in an order has a $0 basis (a free bundle item /
    giveaway, so item_price + buyer_shipping is 0 for all of them), there is
    no meaningful share to compute — split the total evenly across those
    lines instead of dumping it all onto one arbitrary line.

    `absence_is_zero`: when True, an order with NO key in `totals_by_order`
    is written as a real, known Decimal("0.00") rather than left None
    (unknown). Pass True ONLY when the caller has independently confirmed
    the underlying Finances-API read succeeded this run (`fin_status["ok"]`)
    — `ebay_finances.attribute_fees_by_order()` only has a dict key for
    orders with at least one AD-classified fee transaction, so with
    absence_is_zero=False an order with zero ad spend would stay "unknown"
    forever and coverage could never reach 100% for a window containing any
    unpromoted sale. `actual_postage` must always pass False (the default)
    here: a missing SHIPPING_LABEL transaction more plausibly means the
    label hasn't posted yet than that postage was free, so "absence means
    unknown" stays the right read for postage even after a successful sync.
    """
    for order_id, lines in order_lines.items():
        total = totals_by_order.get(order_id)
        if total is None:
            known_zero = Decimal("0.00") if absence_is_zero else None
            for ln in lines:
                ln[field] = known_zero
            continue
        # positive magnitudes, matching ebay_fee/item_price's convention —
        # see ebay_finances' module docstring "Sign convention".
        total = abs(total)
        basis = [ln["item_price"] + ln["buyer_shipping"] for ln in lines]
        basis_sum = sum(basis, Decimal(0))
        if basis_sum > 0:
            shares = [(total * b / basis_sum).quantize(Decimal("0.01")) for b in basis]
        else:
            even = (total / len(lines)).quantize(Decimal("0.01"))
            shares = [even] * len(lines)
        shares[-1] += total - sum(shares, Decimal(0))
        for ln, s in zip(lines, shares):
            ln[field] = s


# --------------------------------------------------------------------------- #
# source 1 — orders (the actuals)
# --------------------------------------------------------------------------- #
def _fetch_orders_window(days: int, verbose: bool) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    orders: list[dict] = []
    offset, limit = 0, 200
    while True:
        path = (f"/sell/fulfillment/v1/order?limit={limit}&offset={offset}"
                f"&filter=creationdate:%5B{since}..%5D")
        data = api_send("GET", path, creds=None, marketplace=None)
        batch = data.get("orders") or []
        orders.extend(batch)
        total = data.get("total") or 0
        if verbose:
            print(f"  orders {min(offset + len(batch), total)}/{total}", end="\r")
        offset += limit
        if offset >= total or not batch:
            break
    return orders


def fetch_orders(days: int, verbose: bool = True) -> list[dict]:
    """Every order in the window, paged.

    eBay refuses a creationdate range that reaches too far back (HTTP 400 at
    ~2 years, and the exact cutoff moves). Rather than dying on a number the
    caller can't know, step the window down until the API accepts it and say
    which window actually ran — a routine that runs monthly must not break
    because someone passed --days 730.

    It must NEVER return an empty list because every attempt failed: an empty
    result is written straight into sales_ledger.csv and reads as "nothing
    sold". Exhausting the candidates raises."""
    windows = [d for d in (days, 540, 365, 180, 90) if d <= days] or [days]
    last_err: Optional[Exception] = None
    for attempt in windows:
        try:
            orders = _fetch_orders_window(attempt, verbose)
        except Exception as e:                                  # noqa: BLE001
            if "400" not in str(e):
                raise
            last_err = e
            if verbose:
                print(f"  eBay refused a {attempt}-day window; narrowing…" + " " * 20)
            continue
        if verbose:
            note = "" if attempt == days else f"  (requested {days}; eBay capped it)"
            print(f"  orders: {len(orders)} in the last {attempt} days{note}" + " " * 12)
        return orders
    raise RuntimeError(
        f"eBay rejected every order window tried ({', '.join(str(w) for w in windows)} "
        f"days). Refusing to report zero sales from a failed fetch. "
        f"Last error: {last_err}") from last_err


def flatten_orders(orders: list[dict]) -> tuple[list[dict], dict]:
    """One row per line item, plus a dict of what was excluded and why.

    Two things a naive read of this API gets wrong, both of which inflate
    revenue:

    * **Cancelled is not the only way a sale unwinds.** An order can sit at
      cancelState NONE_REQUESTED and still have been refunded in full —
      cancellation and returns are separate flows. Measured on this account:
      one $80 sale, fully refunded, totalDueSeller -$0.40, counted as revenue
      until this check existed. The test for "unwound" is
      `paymentSummary.totalDueSeller`, not the size of the refund: eBay refunded
      $68.47 of that $80 (86%, under any sane ratio threshold) while the seller
      was left owing $0.40. totalDueSeller is what we actually keep, and it is
      exactly `item - fee` on every clean order on this account.
    * **Shipping must not be added twice.** `lineItems[].total` is the line
      total; whether it already contains delivery is not something we should
      have to guess, so gross is built from the two unambiguous fields
      (`lineItemCost` + `deliveryCost.shippingCost`) instead. Where the two
      disagree — tax or a promotion moved `total` away from `lineItemCost` —
      that is surfaced rather than silently absorbed.
    """
    rows: list[dict] = []
    excluded = {"cancelled": 0, "refunded": 0, "partial_refund": 0, "total_mismatch": 0,
                # order ids behind the two drop-from-revenue counts above, so a
                # caller (the #119 fee/postage merge) can check whether a
                # dropped order still cost real money — see
                # ebay_finances.unwound_order_losses. Lists, not sets: order
                # ids are already unique per order dict here.
                "cancelled_order_ids": [], "refunded_order_ids": []}
    for o in orders:
        if (o.get("cancelStatus") or {}).get("cancelState") == "CANCELED":
            excluded["cancelled"] += 1
            excluded["cancelled_order_ids"].append(o.get("orderId", ""))
            continue

        items = o.get("lineItems") or []
        fee_total = _dec(o, "totalMarketplaceFee")
        # Fee is allocated by each line's share of the order, on the same basis
        # the rows are built from, so the parts sum back to the whole.
        line_values = [_dec(li, "lineItemCost") + _dec(li, "deliveryCost", "shippingCost")
                       for li in items]
        basis = sum(line_values, Decimal(0)) or Decimal(1)

        pay = o.get("paymentSummary") or {}
        refunds = pay.get("refunds") or []
        refunded = sum((_dec(r, "amount") for r in refunds), Decimal(0))
        has_due = "totalDueSeller" in pay
        due = _dec(pay, "totalDueSeller")
        if refunds and (due <= 0 if has_due else refunded >= basis * Decimal("0.95")):
            excluded["refunded"] += 1          # unwound sale — not revenue
            excluded["refunded_order_ids"].append(o.get("orderId", ""))
            continue

        for li, gross in zip(items, line_values):
            item_price = _dec(li, "lineItemCost")
            ship = _dec(li, "deliveryCost", "shippingCost")
            if _dec(li, "total") not in (Decimal(0), item_price):
                excluded["total_mismatch"] += 1
            share = gross / basis
            fee = (fee_total * share).quantize(Decimal("0.01"))
            # A partial refund is real money returned: take it off this line in
            # proportion to the line's share of the order.
            refund_share = (refunded * share).quantize(Decimal("0.01"))
            if refund_share > 0:
                excluded["partial_refund"] += 1
            # totalDueSeller is eBay's own "what the seller keeps" and already
            # nets out refunds and fee credits, so prefer it over our arithmetic.
            net = ((due * share).quantize(Decimal("0.01")) if has_due
                   else gross - refund_share - fee)
            rows.append({
                "order_id": o.get("orderId", ""),
                "sold_at": (o.get("creationDate") or "")[:10],
                "listing_id": li.get("legacyItemId", ""),
                "sku": li.get("sku") or "",
                "title": li.get("title", ""),
                "quantity": li.get("quantity", 1),
                "sold_format": li.get("soldFormat", ""),
                "item_price": item_price,
                "buyer_shipping": ship,
                "refunded": refund_share,
                "gross": gross - refund_share,
                "ebay_fee": fee,
                "net_before_postage": net,
            })
    return rows, excluded


# --------------------------------------------------------------------------- #
# local state — ledger + draft folders
# --------------------------------------------------------------------------- #
def load_listings_ledger() -> list[dict]:
    if not LISTINGS_LEDGER.exists():
        return []
    with LISTINGS_LEDGER.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def scan_drafts() -> list[dict]:
    """Every local draft: its folder, title, ask, SKU and listing id."""
    out = []
    for dr in sorted(INVENTORY.rglob("draft.md")):
        try:
            t = dr.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        def grab(pat):
            m = re.search(pat, t, re.M)
            return m.group(1) if m else ""
        out.append({
            "dir": str(dr.parent.relative_to(REPO)).replace("\\", "/"),
            "title": grab(r'^title:\s*"(.*)"'),
            "price": grab(r'^price:\s*"(.*)"'),
            "sku": grab(r'ebay_inventory_sku:\s*"?([0-9a-zA-Z\-]{6,})"?'),
            "listing_id": grab(r'ebay_listing_id:\s*"?(\d+)"?'),
        })
    return out


def match_sale(row: dict, drafts: list[dict], ledger: list[dict]) -> tuple[str, str, str]:
    """(shoot_dir, listed_price, matched_by) for a sold line item.

    SKU and listing id are exact; the title fallback exists because items listed
    by hand on eBay never carried our SKU.

    The title fallback REFUSES an ambiguous match. Several items on this account
    are listed twice under byte-identical titles (two Anson tie bars, two copies
    of Brother Tree), so "best match" would hand both sales to the same folder
    and the second would silently overwrite the first's record. When the top two
    candidates are effectively tied, no folder is claimed and the row is marked
    for a human."""
    if row["sku"]:
        for d in drafts:
            if d["sku"] and d["sku"] == row["sku"]:
                return d["dir"], d["price"], "sku"
    if row["listing_id"]:
        for d in drafts:
            if d["listing_id"] and d["listing_id"] == row["listing_id"]:
                return d["dir"], d["price"], "listing_id"
    scored = sorted(
        ((difflib.SequenceMatcher(None, _norm(row["title"]), _norm(d["title"])).ratio(), d)
         for d in drafts), key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] >= 0.75:
        score, best = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if score - runner_up < 0.02:
            return "", best["price"], f"ambiguous~{score:.2f}"
        return best["dir"], best["price"], f"title~{score:.2f}"
    # no folder — fall back to the ledger for the ask
    for lr in ledger:
        if lr.get("sku") and lr["sku"] == row["sku"]:
            return "", lr.get("price", ""), "ledger-sku"
        if lr.get("listing_id") and lr["listing_id"] == row["listing_id"]:
            return "", lr.get("price", ""), "ledger-listing"
    return "", "", "unmatched"


# --------------------------------------------------------------------------- #
# writers
# --------------------------------------------------------------------------- #
def _sale_key(row: dict) -> tuple:
    """(order_id, sku or listing_id) — stable across re-fetches of the same
    order, and distinct per line item within a multi-SKU order."""
    return (row.get("order_id", ""), row.get("sku") or row.get("listing_id", ""))


def _load_sales_ledger() -> dict:
    """sale_key -> row, for whatever sales_ledger.csv already holds."""
    if not SALES_LEDGER.exists():
        return {}
    with SALES_LEDGER.open(encoding="utf-8-sig", newline="") as f:
        return {_sale_key(r): r for r in csv.DictReader(f)}


def write_sales_ledger(rows: list[dict]) -> None:
    """Merge `rows` into sales_ledger.csv — never a wholesale overwrite.

    `rows` only covers the current `--days` window; a plain rewrite would
    drop every sale outside it. Existing rows survive untouched unless a
    fetched row shares their (order_id, sku) key, in which case the fresh
    fetch wins (it may carry a refund the earlier write didn't know about)."""
    merged = _load_sales_ledger()
    for r in rows:
        out = dict(r)
        for k in _MONEY_FIELDS:
            out[k] = _money(r[k])
        key = _sale_key(out)
        existing = merged.get(key)
        for k in _OPTIONAL_MONEY_FIELDS:
            # None (not yet read from the Finances API this run) stays
            # whatever the ledger already had, not "" — a --skip-finances
            # run or a degraded Finances call would otherwise silently
            # erase a real figure a PRIOR run already recorded. Only an
            # actual fetched value (never blank, per the SALES_FIELDS
            # comment on why 0.00 would be a false claim) overwrites it.
            v = r.get(k)
            out[k] = _money(v) if v is not None else (existing or {}).get(k, "")
        merged[key] = out
    with SALES_LEDGER.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SALES_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(merged.values(), key=lambda x: x["sold_at"], reverse=True):
            w.writerow(r)


def stamp_folder(row: dict) -> bool:
    """Per-item outcome stamp, so a folder tells its own story without the CSV.

    Never overwrites a stamp belonging to a DIFFERENT order: one folder holding
    two sales means the match was wrong (or the item genuinely sold twice), and
    silently replacing the first record would hide that. Returns False when it
    declines, so the caller can report it."""
    d = REPO / row["shoot_dir"]
    if not d.is_dir():
        return False
    stamp = d / "SOLD.md"
    if stamp.exists():
        prior = stamp.read_text(encoding="utf-8", errors="ignore")
        if row["order_id"] and row["order_id"] not in prior:
            print(f"  ! {row['shoot_dir']} already records a different order "
                  f"— leaving it alone (this sale: {row['order_id']})")
            return False
    ask = row.get("listed_price") or ""
    pct = row.get("pct_of_ask") or ""
    stamp.write_text(
        f"# SOLD — {row['title']}\n\n"
        f"- Sold: {row['sold_at']}  ·  order {row['order_id']}\n"
        f"- Listing: https://www.ebay.com/itm/{row['listing_id']}\n"
        f"- Asked: ${ask}   →   **Actually sold for: ${_money(row['item_price'])}**"
        f"{f'  ({pct} of ask)' if pct else ''}\n"
        f"- Buyer shipping: ${_money(row['buyer_shipping'])}  ·  "
        f"gross ${_money(row['gross'])}\n"
        f"- eBay fee: ${_money(row['ebay_fee'])}  →  "
        f"**net before our postage: ${_money(row['net_before_postage'])}**\n"
        f"- Format: {row['sold_format']}  ·  qty {row['quantity']}  ·  "
        f"matched by {row['matched_by']}\n"
        + (f"- ⚠ Partially refunded: ${_money(row['refunded'])} returned to the buyer; "
           f"gross above is net of it.\n" if row.get("refunded") else "")
        + "\nPostage we paid is not in the eBay API — subtract it for true net.\n",
        encoding="utf-8")
    return True


def sync_finances(days: int, verbose: bool = True) -> tuple[dict, dict, dict]:
    """Fetch + parse the Finances API window and attribute it by order id.

    Returns (ad_fee_by_order, postage_by_order, status). `status` is a small
    PII-free dict — {"ok": bool, "reason": str|None, "other_fee_labels": {...}}
    — written to `reports/finances_sync_status.json` by the caller so a
    reader with no direct API access (the sales dashboard) can say WHY the
    "before ads & postage" qualifier is still up, per one line, rather than
    silently keeping a caveat with no explanation (#119 acceptance).

    Never raises: sell.finances failing (401/403 before re-consent, a
    network error, an unexpected response shape) degrades to empty maps and
    a `status["reason"]` — exactly like `tools/sales_report.py.sync()`
    already does for the ads pull, so one degraded source doesn't crash the
    whole `--apply`.
    """
    if verbose:
        print(f"Fetching ad-fee + postage transactions (last {days} days, "
              f"sell.finances)…")
    try:
        txns = ebay_finances.fetch_transactions(days, verbose=verbose)
    except (EbayAuthError, EbayAPIError) as e:
        reason = _one_line(e, 300)
        if verbose:
            print(f"  unavailable: {reason[:200]}" + " " * 12)
        return {}, {}, {"ok": False, "reason": reason, "other_fee_labels": {}}
    except Exception as e:                                       # noqa: BLE001
        reason = _one_line(e, 300)
        if verbose:
            print(f"  unavailable: {reason[:200]}" + " " * 12)
        return {}, {}, {"ok": False, "reason": reason, "other_fee_labels": {}}

    parsed = ebay_finances.parse_transactions(txns)
    ad_fee_by_order = ebay_finances.attribute_fees_by_order(parsed["fees"])
    postage_by_order = ebay_finances.attribute_postage_by_order(parsed["postage"])
    status = {"ok": True, "reason": None,
             "other_fee_labels": dict(parsed["other_fee_labels"])}
    return ad_fee_by_order, postage_by_order, status


def write_finances_status(status: dict, *, days: int, orders_total: int,
                          orders_covered: int) -> None:
    """Persist `sync_finances`'s outcome so a reader with no API access
    (the dashboard-building code, tests, `--no-sync` runs) can render the
    "before ads & postage" qualifier's one-line reason without re-deriving
    it. PII-free by construction — `status` already is (see `sync_finances`),
    and the fields added here are counts and a window size only."""
    from datetime import datetime, timezone
    REPORTS.mkdir(exist_ok=True)
    doc = {
        **status,
        "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "days": days,
        "orders_total": orders_total,
        "orders_covered": orders_covered,
    }
    FINANCES_STATUS_JSON.write_text(json.dumps(doc, indent=1), encoding="utf-8")


def mark_sold_in_ledger(rows: list[dict]) -> int:
    """Advance the listings ledger to SOLD. The `price` column is deliberately
    left alone: it is the ASK, and sales_ledger.csv holds the actual."""
    from list_edit import upsert_listing
    n = 0
    for r in rows:
        if r["sku"]:
            upsert_listing(r["sku"], "SOLD", listing_id=r["listing_id"],
                           url=f"https://www.ebay.com/itm/{r['listing_id']}")
            n += 1
    return n


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def report(rows: list[dict], drafts: list[dict], ledger: list[dict],
           store: Optional[dict], excluded: Optional[dict] = None,
           unwound_losses: Optional[list[dict]] = None) -> None:
    gross = sum((r["gross"] for r in rows), Decimal(0))
    fees = sum((r["ebay_fee"] for r in rows), Decimal(0))
    net = sum((r["net_before_postage"] for r in rows), Decimal(0))
    # ad_fee/actual_postage are per-row SHARES of an order-level total (see
    # allocate_order_totals()), so coverage is counted by unique order id, not by
    # row — a 3-line order with a known ad fee is 1 covered order, not 3.
    order_ids = {r["order_id"] for r in rows}
    ad_fee_orders = {r["order_id"] for r in rows if r.get("ad_fee") is not None}
    postage_orders = {r["order_id"] for r in rows if r.get("actual_postage") is not None}
    ad_total = sum((r["ad_fee"] for r in rows if r.get("ad_fee") is not None), Decimal(0))
    post_total = sum((r["actual_postage"] for r in rows if r.get("actual_postage") is not None),
                     Decimal(0))

    print(f"\n=== ACTUALS — {len(rows)} sold line item(s)")
    print(f"  gross ${_money(gross)}  ·  eBay fees ${_money(fees)} "
          f"({(fees / gross * 100) if gross else 0:.1f}%)  ·  "
          f"net before postage ${_money(net)}")
    if ad_fee_orders or postage_orders:
        print(f"  ad fees (#119) ${_money(ad_total)} on {len(ad_fee_orders)} of "
              f"{len(order_ids)} order(s)  ·  actual postage ${_money(post_total)} on "
              f"{len(postage_orders)} of {len(order_ids)} order(s) — the rest have no "
              f"Finances-API match yet")
    else:
        print("  ad fees / actual postage (#119): none read this run — see "
              f"{FINANCES_STATUS_JSON.relative_to(REPO)} for why")

    if unwound_losses:
        lost = sum((u["loss"] for u in unwound_losses), Decimal(0))
        print(f"\n=== ⚠ UNWOUND ORDERS WITH A SUNK AD FEE / POSTAGE COST — "
              f"{len(unwound_losses)} order(s), ${_money(lost)} lost")
        print("  (fully refunded — $0 revenue above — but a label was bought and/or "
              "an ad fee only partly credited; not dropped)")
        for u in sorted(unwound_losses, key=lambda x: -x["loss"])[:15]:
            print(f"    order {u['order_id']}  ad fee ${_money(u['ad_fee'])}  "
                  f"postage ${_money(u['actual_postage'])}  loss ${_money(u['loss'])}")

    if excluded and any(excluded.values()):
        print(f"  excluded: {excluded['cancelled']} cancelled, "
              f"{excluded['refunded']} fully refunded"
              + (f", {excluded['partial_refund']} partially refunded (kept, net of refund)"
                 if excluded.get("partial_refund") else "")
              + (f", {excluded['total_mismatch']} line(s) where total != lineItemCost "
                 f"(tax/promo — check)" if excluded.get("total_mismatch") else ""))

    ambiguous = [r for r in rows if r["matched_by"].startswith("ambiguous")]
    if ambiguous:
        print(f"\n=== ⚠ AMBIGUOUS TITLE MATCH — {len(ambiguous)} sale(s) matched two "
              f"folders equally well; no folder claimed:")
        for r in ambiguous:
            print(f"  {r['sold_at']}  ${_money(r['item_price']):>7}  {r['title'][:56]}")

    discounted = [r for r in rows if r.get("pct_num") and r["pct_num"] < 99]
    if discounted:
        avg = sum(r["pct_num"] for r in discounted) / len(discounted)
        print(f"\n=== ASK vs ACTUAL — {len(discounted)} of {len(rows)} sold BELOW ask "
              f"(avg {avg:.0f}% of ask)")
        for r in sorted(discounted, key=lambda x: x["pct_num"])[:15]:
            print(f"  {r['pct_num']:3.0f}%  ask ${r['listed_price']:>7} -> "
                  f"${_money(r['item_price']):>7}  {r['title'][:52]}")

    unmatched = [r for r in rows if r["matched_by"] == "unmatched"]
    if unmatched:
        print(f"\n=== SOLD BUT UNTRACKED LOCALLY — {len(unmatched)} "
              f"(listed outside the pipeline; no folder, no ledger row)")
        for r in sorted(unmatched, key=lambda x: x["sold_at"], reverse=True)[:15]:
            print(f"  {r['sold_at']}  ${_money(r['item_price']):>7}  {r['title'][:56]}")

    # local drafts that are actually already sold -> would duplicate if published
    sold_dirs = {r["shoot_dir"] for r in rows if r["shoot_dir"]}
    stale = [d for d in drafts if d["dir"] in sold_dirs and not d["listing_id"]]
    if stale:
        print(f"\n=== ⚠ DRAFTS THAT ARE ALREADY SOLD — {len(stale)} "
              f"(publishing these would DUPLICATE a sold item)")
        for d in stale:
            print(f"  {d['dir']}  ask ${d['price']}")

    if store:
        active = store.get("active", [])
        sold_ids = {r["listing_id"] for r in rows}
        led_ids = {lr.get("listing_id") for lr in ledger if lr.get("listing_id")}
        untracked = [a for a in active if a["item_id"] not in led_ids
                     and a["item_id"] not in sold_ids]
        print(f"\n=== STORE CROSS-CHECK — {len(active)} active on the store page")
        print(f"  {len(untracked)} active listing(s) NOT in the local ledger "
              f"(created outside the pipeline):")
        for a in untracked[:20]:
            print(f"    {a.get('price',''):>9}  {a['title'][:58]}  /itm/{a['item_id']}")
        # duplicate titles among active listings
        seen, dupes = {}, []
        for a in active:
            k = _norm(a["title"])[:60]
            if k in seen and seen[k] != a["item_id"]:
                dupes.append((seen[k], a["item_id"], a["title"]))
            seen.setdefault(k, a["item_id"])
        if dupes:
            print(f"\n  ⚠ {len(dupes)} possible DUPLICATE active listing(s):")
            for x, y, t in dupes[:10]:
                print(f"    {t[:54]}  /itm/{x}  vs  /itm/{y}")


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sync local inventory to eBay's actual results (what it really sold for).")
    ap.add_argument("--days", type=int, default=90,
                    help="order window in days (default 90; eBay serves ~2 years)")
    ap.add_argument("--apply", action="store_true",
                    help="write sales_ledger.csv, stamp SOLD.md, advance the ledger")
    ap.add_argument("--store-json", nargs="+", metavar="FILE",
                    help="browser dumps of the store's active/sold pages (see --print-js)")
    ap.add_argument("--print-js", action="store_true",
                    help="print the store-page extractor and exit")
    ap.add_argument("--skip-finances", action="store_true",
                    help="don't attempt the sell.finances ad-fee/postage read "
                         "(#119) — useful before the account owner has "
                         "re-consented, to avoid a call that's known to fail")
    args = ap.parse_args()

    if args.print_js:
        print(STORE_JS)
        return 0

    print(f"Fetching orders (last {args.days} days)…")
    rows, excluded = flatten_orders(fetch_orders(args.days))
    drafts = scan_drafts()
    ledger = load_listings_ledger()

    for r in rows:
        d, ask, how = match_sale(r, drafts, ledger)
        r["shoot_dir"], r["listed_price"], r["matched_by"] = d, ask, how
        try:
            pct = float(r["item_price"]) / float(ask) * 100 if ask else None
        except (ValueError, ZeroDivisionError):
            pct = None
        r["pct_num"] = pct
        r["pct_of_ask"] = f"{pct:.0f}%" if pct else ""

    # #119 (route B): real ad fee + actual postage, per order — degrades to
    # "unavailable" rather than failing the whole sync (see sync_finances).
    if args.skip_finances:
        ad_fee_by_order, postage_by_order = {}, {}
        fin_status = {"ok": False, "reason": "--skip-finances", "other_fee_labels": {}}
    else:
        ad_fee_by_order, postage_by_order, fin_status = sync_finances(args.days)

    # Both totals are ORDER-level (one ad fee, one shipping label, per order —
    # not per line item), but flatten_orders() produces one row per line item.
    # allocate_order_totals() splits each order's total across its own rows
    # (see its docstring) so summing the field over rows never double-counts.
    order_lines: dict[str, list[dict]] = {}
    for r in rows:
        order_lines.setdefault(r["order_id"], []).append(r)

    # ad_fee: once this run's Finances read succeeded (fin_status["ok"]), an
    # order with no AD-classified transaction is a real, known $0.00 — not
    # "unknown forever" — so the "before ads & postage" qualifier can
    # actually drop for a window that includes an unpromoted sale (#119
    # Copilot finding: "known zero" ad-fee handling). postage always stays
    # "absence means unknown", never zero — see allocate_order_totals'
    # docstring for why the two fields are asymmetric here.
    allocate_order_totals(order_lines, ad_fee_by_order, "ad_fee",
                          absence_is_zero=bool(fin_status.get("ok")))
    allocate_order_totals(order_lines, postage_by_order, "actual_postage")

    unwound_losses = ebay_finances.unwound_order_losses(
        excluded["cancelled_order_ids"] + excluded["refunded_order_ids"],
        ad_fee_by_order, postage_by_order)
    # Coverage is per ORDER (matching orders_total below), and an order only
    # counts as covered once BOTH fields are known for it — a "some rows have
    # ad_fee but not postage" order is partial coverage, not full.
    covered = sum(1 for lines in order_lines.values()
                  if lines[0]["ad_fee"] is not None
                  and lines[0]["actual_postage"] is not None)
    if args.apply:
        write_finances_status(fin_status, days=args.days, orders_total=len(order_lines),
                              orders_covered=covered)

    store = None
    if args.store_json:
        active, sold = [], []
        for p in args.store_json:
            blob = json.loads(Path(p).read_text(encoding="utf-8"))
            rs = blob.get("rows", blob if isinstance(blob, list) else [])
            (sold if "sold" in Path(p).name.lower() else active).extend(rs)
        store = {"active": active, "sold": sold}

    report(rows, drafts, ledger, store, excluded, unwound_losses)

    if not args.apply:
        print("\n[DRY RUN] Nothing written. Re-run with --apply to record:")
        print(f"  • {SALES_LEDGER.name} — {len(rows)} actuals row(s)")
        print(f"  • SOLD.md in {len({r['shoot_dir'] for r in rows if r['shoot_dir']})} folder(s)")
        print("  • listings_ledger.csv — advance matched SKUs to SOLD")
        return 0

    write_sales_ledger(rows)
    stamped = declined = 0
    for r in rows:
        if r["shoot_dir"]:
            if stamp_folder(r):
                stamped += 1
            else:
                declined += 1
    marked = mark_sold_in_ledger(rows)
    print(f"\n[OK] wrote {SALES_LEDGER}  ({len(rows)} rows)")
    print(f"[OK] stamped SOLD.md in {stamped} folder(s)"
          + (f"; {declined} left alone (already record a different order)" if declined else ""))
    print(f"[OK] advanced {marked} ledger row(s) to SOLD (ask price left intact)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
