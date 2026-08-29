#!/usr/bin/env python3
"""Pick list for orders that still have to be packed (GH #32 — pack & ship).

Reads eBay's Fulfillment API and prints what a person carries to the shelves:
what to pull, where it lives locally, where it is going, and by when. Three
things live here:

  --poll             check for orders awaiting shipment; render each NEW one to
                      pick_lists/ (idempotent — an order already rendered is
                      skipped on the next poll; see --reprint to force one).
  (no flags)          the original one-shot report to the terminal (+ --out).
  --record-tracking   after a human has a tracking number some other way
                      (Seller Hub, a label already bought), write it back to
                      eBay via POST .../shipping_fulfillment and advance the
                      local listings ledger to SHIPPED. DRY RUN unless
                      --confirm is also given.

By default the queue read (both --poll and the plain report) lists only orders
awaiting shipment — the filter eBay honours is a single `orderfulfillmentstatus`
with both open states in one brace group; asking for one state at a time is an
HTTP 400. When nothing is waiting there is nothing to pick, so --latest N
renders the most recent already-shipped orders instead, which is the only way
to see the format when the queue is empty.

Buyer names and street addresses are in this output. It prints to the terminal
and, with --out / --poll, to a local file (pick_lists/, gitignored); it is
never written anywhere that leaves the machine — not a commit, not an
artifact, not a shared log.

Buying a shipping label is explicitly OUT of scope here — see GH #32: eBay's
Logistics API (shipping_quote / shipment) returns an empty-bodied 404 for this
app, meaning the route isn't served at all pending eBay enabling it for the
developer account. Even once/if it is, no polling loop may ever purchase a
label unattended — only --record-tracking exists, and only for a tracking
number a human already obtained.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from comps_csv import _now as _now_iso                              # noqa: E402
from ebay_client import api_send, EbayAPIError                      # noqa: E402
from sync_actuals import fetch_orders, load_listings_ledger, match_sale, scan_drafts  # noqa: E402

OPEN_FILTER = "orderfulfillmentstatus:%7BNOT_STARTED%7CIN_PROGRESS%7D"

# Idempotent-print ledger + rendered-sheet output. Both are local-only:
# STATE_FILE matches the repo's existing `/.*.json` gitignore rule; OUT_DIR
# has its own rule (buyer PII, never committed — see .gitignore).
STATE_FILE = ROOT / ".pick_list_state.json"
OUT_DIR = ROOT / "pick_lists"


def _money(m: dict | None) -> str:
    return f"${float((m or {}).get('value', 0)):,.2f}"


def fetch_open() -> list[dict]:
    orders, offset = [], 0
    while True:
        d = api_send("GET", f"/sell/fulfillment/v1/order?limit=50&offset={offset}"
                            f"&filter={OPEN_FILTER}", creds=None, marketplace=None)
        batch = d.get("orders") or []
        orders.extend(batch)
        offset += 50
        if offset >= (d.get("total") or 0) or not batch:
            return orders


def ship_to(o: dict) -> dict:
    for f in o.get("fulfillmentStartInstructions") or []:
        step = f.get("shippingStep") or {}
        if step.get("shipTo"):
            return {**step["shipTo"], "carrier": step.get("shippingCarrierCode", ""),
                    "service": step.get("shippingServiceCode", ""),
                    "by": (f.get("maxEstimatedDeliveryDate") or "")[:10]}
    return {}


def render(o: dict, drafts: list[dict], ledger: list[dict]) -> str:
    to = ship_to(o)
    addr = to.get("contactAddress") or {}
    items = o.get("lineItems") or []
    ship_by = min((li.get("lineItemFulfillmentInstructions", {}).get("shipByDate") or "zz"
                   for li in items), default="")[:10]

    L = []
    L.append("=" * 66)
    L.append(f"PICK  order {o.get('orderId','')}   sales record #{o.get('salesRecordReference','')}"
             f"   {o.get('creationDate','')[:10]}")
    L.append(f"      status {o.get('orderFulfillmentStatus','')} / {o.get('orderPaymentStatus','')}"
             + (f"   SHIP BY {ship_by}" if ship_by and ship_by != "zz" else ""))
    L.append("-" * 66)
    for n, li in enumerate(items, 1):
        row = {"sku": li.get("sku") or "", "listing_id": li.get("legacyItemId", ""),
               "title": li.get("title", "")}
        folder, ask, how = match_sale(row, drafts, ledger)
        L.append(f"  [{n}] x{li.get('quantity',1)}  {li.get('title','')}")
        L.append(f"       item {li.get('legacyItemId','')}"
                 + (f"   sku {row['sku']}" if row["sku"] else "   sku —")
                 + f"   {_money(li.get('lineItemCost'))}")
        L.append(f"       FROM  {folder or '(no local folder — listed by hand)'}"
                 + (f"   [{how}]" if folder else ""))
    L.append("-" * 66)
    L.append(f"  SHIP TO   {to.get('fullName','')}")
    for line in (addr.get("addressLine1"), addr.get("addressLine2")):
        if line:
            L.append(f"            {line}")
    L.append(f"            {addr.get('city','')}, {addr.get('stateOrProvince','')} "
             f"{addr.get('postalCode','')} {addr.get('countryCode','')}")
    L.append(f"  VIA       {to.get('carrier','')} {to.get('service','')}"
             f"   buyer paid {_money((o.get('pricingSummary') or {}).get('deliveryCost'))} shipping")
    L.append(f"  ORDER     {_money((o.get('pricingSummary') or {}).get('total'))} total"
             f"   ·   you keep {_money((o.get('paymentSummary') or {}).get('totalDueSeller'))}")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# idempotent-print state (GH #32 — "an order already printed doesn't print
# again on the next poll")
# --------------------------------------------------------------------------- #
def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"printed": {}, "shipped": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"printed": {}, "shipped": {}}
    if not isinstance(data, dict):
        return {"printed": {}, "shipped": {}}
    data.setdefault("printed", {})
    data.setdefault("shipped", {})
    return data


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=1), encoding="utf-8")


def _safe_filename(order_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", order_id) or "order"


def _send_to_printer(path: Path) -> bool:
    """Best-effort send to the OS default printer. Never raises — a failed
    print still leaves the file on disk for the human to print by hand.

    Returns True only when the OS *confirmed* the print command succeeded —
    on POSIX that's `lp` exiting 0. On Windows there is no such confirmation:
    os.startfile("print") hands the job to the shell asynchronously and
    returns immediately whether or not a default printer exists or the
    spooler accepts it, so this always returns False there (the job was
    fired, not confirmed) rather than claiming a success it can't verify.
    Unverified on real hardware either way (this was built/tested headless);
    the rendered file in pick_lists/ is always the fallback regardless of
    what this returns.
    """
    import platform
    import subprocess

    try:
        if platform.system() == "Windows":
            import os
            os.startfile(str(path), "print")  # type: ignore[attr-defined]
            print(f"  ~ sent {path.name} to the default printer (Windows does not "
                  f"confirm the job reached it — file is in {path.parent.name}/ either way)")
            return False
        subprocess.run(["lp", str(path)], check=True, capture_output=True, timeout=15)
        return True
    except Exception as e:                                          # noqa: BLE001
        print(f"  ! could not print {path.name} automatically ({e}); "
              f"file is ready to print by hand")
        return False


def poll_and_print(*, out_dir: Path = OUT_DIR, state: dict | None = None,
                   do_print: bool = False, reprint: str | None = None,
                   fetch=fetch_open) -> tuple[list[str], list[str], dict, list[str]]:
    """Fetch orders awaiting shipment; render each NEW one to `out_dir`.

    Idempotent: an orderId already recorded in `state["printed"]` is skipped
    unless it is `reprint`. Returns (new_order_ids, skipped_order_ids, state,
    unconfirmed_print_ids) so a caller (tests, or --poll) can inspect what
    happened without re-parsing stdout. `state` may be passed in (tests); when
    None it is loaded from disk and NOT saved here — the caller decides when
    to persist.

    `unconfirmed_print_ids` holds orders where `do_print` was requested but
    `_send_to_printer` could not confirm the job reached a printer (on
    Windows this is *every* order, since os.startfile fires the job async and
    never reports back) — the pick list still rendered to out_dir either way.
    """
    orders = fetch()
    drafts, ledger = scan_drafts(), load_listings_ledger()
    st = state if state is not None else _load_state()
    out_dir.mkdir(parents=True, exist_ok=True)

    new_ids: list[str] = []
    skipped_ids: list[str] = []
    unconfirmed_print_ids: list[str] = []
    for o in orders:
        oid = o.get("orderId", "")
        if not oid:
            continue
        if oid in st["printed"] and oid != reprint:
            skipped_ids.append(oid)
            continue
        text = render(o, drafts, ledger)
        path = out_dir / f"pick_{_safe_filename(oid)}.txt"
        path.write_text(text + "\n", encoding="utf-8")
        try:
            recorded_path = str(path.relative_to(ROOT))
        except ValueError:
            recorded_path = str(path)  # out_dir given outside ROOT (e.g. tests)
        st["printed"][oid] = {"printed_at": _now_iso(),
                              "file": recorded_path.replace("\\", "/")}
        new_ids.append(oid)
        if do_print:
            # _send_to_printer already catches everything it knows about and
            # is contracted to never raise — this guard is for the case that
            # contract is ever violated (e.g. by a test double, or a future
            # bug): a poll loop must never die over a printer, the file in
            # out_dir is always the fallback.
            try:
                confirmed = _send_to_printer(path)
            except Exception as e:                                  # noqa: BLE001
                print(f"  ! printing {path.name} raised unexpectedly ({e}); "
                      f"file is still ready to print by hand")
                confirmed = False
            if not confirmed:
                unconfirmed_print_ids.append(oid)
    return new_ids, skipped_ids, st, unconfirmed_print_ids


# --------------------------------------------------------------------------- #
# record tracking — POST .../shipping_fulfillment + advance the local ledger
# (GH #32 step 4; step 3 "buy a label" is deliberately NOT implemented here —
# see the module docstring and NotImplementedError below)
# --------------------------------------------------------------------------- #
def fetch_order(order_id: str) -> dict | None:
    """One order by id, or None on a 404 (unknown / mistyped id)."""
    try:
        return api_send("GET", f"/sell/fulfillment/v1/order/{order_id}",
                        creds=None, marketplace=None)
    except EbayAPIError as e:
        if e.status == 404:
            return None
        raise


def build_shipping_fulfillment_body(order: dict, carrier: str, tracking_number: str,
                                    line_item_ids: list[str] | None = None) -> dict:
    """The POST body for /sell/fulfillment/v1/order/{orderId}/shipping_fulfillment.

    Defaults to every line item on the order (a single tracking number covers
    the whole box — multi-line orders ship together); pass `line_item_ids` to
    cover only some of them (a split shipment)."""
    items = order.get("lineItems") or []
    if line_item_ids:
        wanted = set(line_item_ids)
        items = [li for li in items if li.get("lineItemId") in wanted]
    return {
        "lineItems": [{"lineItemId": li["lineItemId"], "quantity": li.get("quantity", 1)}
                      for li in items if li.get("lineItemId")],
        "shippedDate": _now_iso().replace("Z", ".000Z"),
        "shippingCarrierCode": carrier,
        "trackingNumber": tracking_number,
    }


def record_tracking(order_id: str, carrier: str, tracking_number: str,
                    line_item_ids: list[str] | None = None, order: dict | None = None) -> dict:
    """POST the tracking number to eBay for one order. Real write — no dry-run
    gate here; the CLI (--record-tracking / --confirm) is what gates it.

    Pass `order` when the caller already fetched it (e.g. cmd_record_tracking
    fetches once to build the dry-run preview, then reuses that same order
    here on --confirm instead of fetching it a second time)."""
    if order is None:
        order = fetch_order(order_id)
        if order is None:
            raise ValueError(f"no such order: {order_id}")
    body = build_shipping_fulfillment_body(order, carrier, tracking_number, line_item_ids)
    if not body["lineItems"]:
        raise ValueError(f"order {order_id} has no matching line items to mark shipped")
    resp = api_send("POST", f"/sell/fulfillment/v1/order/{order_id}/shipping_fulfillment",
                    body=body, creds=None, marketplace=None)
    return {"order": order, "response": resp}


def advance_ledger_for_order(order: dict) -> int:
    """Advance every SKU on this order to SHIPPED in listings_ledger.csv.

    Follows the pattern lib/sync_actuals.mark_sold_in_ledger uses for the
    SOLD transition: one upsert_listing() call per line item, keyed by SKU.
    Best-effort by construction (upsert_listing never raises); a line item
    with no local SKU (listed by hand, outside the pipeline) is skipped —
    there is no local ledger row for it to advance.
    """
    from list_edit import upsert_listing
    n = 0
    for li in order.get("lineItems") or []:
        sku = li.get("sku") or ""
        if not sku:
            continue
        listing_id = li.get("legacyItemId", "")
        upsert_listing(sku, "SHIPPED", listing_id=listing_id,
                       url=f"https://www.ebay.com/itm/{listing_id}" if listing_id else "")
        n += 1
    return n


def buy_shipping_label(*_args, **_kwargs):
    """NOT IMPLEMENTED — deliberately.

    This is the hook for eBay's Logistics API (shipping_quote -> shipment ->
    download_label). As of the 2026-08-26 measurement in GH #32 it returns an
    empty-bodied 404 for this app: the route is limited-release and has to be
    enabled by eBay for the developer account, not just scoped in the token.

    Even once/if it is enabled: NO polling loop may ever purchase a label
    unattended (buying postage spends real money). If this is ever wired up,
    it must quote, print/show the quote, and purchase ONLY on an explicit
    per-label human confirmation passed in by the caller — never inferred,
    never defaulted to yes.
    """
    raise NotImplementedError(
        "label purchase is out of scope (GH #32) — eBay's Logistics API is not "
        "served to this app yet, and even once it is, no automated path may "
        "spend money without a per-label human confirmation. Use "
        "--record-tracking once a label/tracking number exists (bought "
        "through Seller Hub or elsewhere).")


def cmd_poll(args) -> int:
    """--poll: terse verdict to stdout; the PII detail goes to pick_lists/ only
    (house style — see tools/live_audit.py and friends: one-line verdict,
    detail in a file, never dumped to the terminal wholesale)."""
    new_ids, skipped_ids, state, unconfirmed = poll_and_print(
        do_print=args.do_print, reprint=args.reprint)
    _save_state(state)
    if not new_ids and not skipped_ids:
        print("[OK] nothing awaiting shipment")
        return 0
    rel = OUT_DIR.relative_to(ROOT)
    print(f"[OK] {len(new_ids)} new pick list(s) written to {rel}/"
          + (f"  ({', '.join(new_ids)})" if new_ids else "")
          + (f"; {len(skipped_ids)} already printed (skipped)" if skipped_ids else ""))
    if unconfirmed:
        print(f"  ~ {len(unconfirmed)} sent to the printer but NOT confirmed printed "
              f"({', '.join(unconfirmed)}) — check the printer, the file in {rel}/ is the fallback")
    return 0


def cmd_record_tracking(args) -> int:
    order_id = args.record_tracking
    if not args.carrier or not args.tracking_number:
        print("[X] --record-tracking needs both --carrier and --tracking-number")
        return 2

    # Dry-run needs the order + body to report line-item count without writing
    # anything, so it fetches/builds once here; the real write below goes
    # through record_tracking() (same fetch+build+POST it would do internally)
    # rather than re-fetching, to keep the single POST call in one place.
    order = fetch_order(order_id)
    if order is None:
        print(f"[X] no such order: {order_id}")
        return 1
    body = build_shipping_fulfillment_body(order, args.carrier, args.tracking_number)
    if not body["lineItems"]:
        print(f"[X] order {order_id} has no line items to mark shipped")
        return 1

    if not args.confirm:
        print(f"[DRY RUN] would POST shipping_fulfillment for order {order_id}: "
              f"{args.carrier} {args.tracking_number}, {len(body['lineItems'])} line item(s)")
        print("  re-run with --confirm to write it to eBay and advance the local ledger")
        return 0

    try:
        result = record_tracking(order_id, args.carrier, args.tracking_number, order=order)
    except (ValueError, EbayAPIError) as e:
        print(f"[X] could not record tracking for order {order_id}: {e}")
        return 1
    resp = result["response"]
    advanced = advance_ledger_for_order(result["order"])

    state = _load_state()
    state["shipped"][order_id] = {"recorded_at": _now_iso(), "carrier": args.carrier}
    _save_state(state)

    fid = resp.get("fulfillmentId") if isinstance(resp, dict) else None
    print(f"[OK] recorded tracking for order {order_id} ({args.carrier} {args.tracking_number})"
          + (f"  fulfillmentId={fid}" if fid else "")
          + f" — advanced {advanced} SKU(s) to SHIPPED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--poll", action="store_true",
                    help="poll for orders awaiting shipment; render NEW ones to pick_lists/ "
                         "(idempotent — an order already rendered is skipped)")
    ap.add_argument("--print", dest="do_print", action="store_true",
                    help="with --poll: also best-effort send each new pick list to the "
                         "default printer (falls back to the file on any failure)")
    ap.add_argument("--reprint", metavar="ORDER_ID",
                    help="with --poll: force this one order to render again even though "
                         "it was already printed")
    ap.add_argument("--record-tracking", metavar="ORDER_ID",
                    help="write a tracking number back to eBay for one order "
                         "(POST shipping_fulfillment) and advance its SKUs to SHIPPED in "
                         "the local ledger. Needs --carrier and --tracking-number. "
                         "DRY RUN unless --confirm is also given.")
    ap.add_argument("--carrier", metavar="CODE",
                    help="carrier code for --record-tracking, e.g. USPS, UPS, FEDEX "
                         "(eBay's ShippingCarrierCodeType)")
    ap.add_argument("--tracking-number", metavar="NUM", help="tracking number for --record-tracking")
    ap.add_argument("--confirm", action="store_true",
                    help="required with --record-tracking to actually write to eBay and "
                         "advance the ledger (otherwise dry run)")
    ap.add_argument("--latest", type=int, metavar="N",
                    help="ignore the queue; render the N most recent orders (format check)")
    ap.add_argument("--days", type=int, default=30, help="window for --latest (default 30)")
    ap.add_argument("--order-id", help="render one specific order")
    ap.add_argument("--out", metavar="FILE", help="also write to a local file")
    args = ap.parse_args()

    if args.record_tracking and args.poll:
        ap.error("--poll and --record-tracking are separate modes; run them separately "
                 "(--record-tracking would otherwise run and --poll would be silently skipped)")

    if args.record_tracking:
        return cmd_record_tracking(args)

    if args.poll:
        return cmd_poll(args)

    if args.latest or args.order_id:
        orders = sorted(fetch_orders(args.days, verbose=False),
                        key=lambda o: o.get("creationDate", ""), reverse=True)
        if args.order_id:
            orders = [o for o in orders if args.order_id in
                      (o.get("orderId", ""), o.get("legacyOrderId", ""))]
            if not orders:
                print(f"no order {args.order_id} in the last {args.days} days")
                return 1
        else:
            orders = orders[:args.latest]
        header = f"(not the queue — {len(orders)} most recent order(s), already shipped)"
    else:
        orders = fetch_open()
        header = f"AWAITING SHIPMENT — {len(orders)} order(s)"
        if not orders:
            print("Nothing to pick — no orders awaiting shipment.\n"
                  "Use --latest 1 to see the format against a recent order.")
            return 0

    drafts, ledger = scan_drafts(), load_listings_ledger()
    out = "\n".join([header] + [render(o, drafts, ledger) for o in orders] + ["=" * 66])
    print(out)
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
        print(f"\n[OK] wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
