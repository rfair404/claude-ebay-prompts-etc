"""ebaybiz — activity + performance reporting.

Two questions, one module:

  * **Activity** — "what did I actually do?" over a window: what got listed,
    what is sitting in draft, what the asks add up to.
  * **Performance** — "what did it actually MAKE?": real sold prices, eBay's
    real fee bite, how far under ask Best Offer pulls us, how fast things sell,
    and which categories are systematically mispriced.

Performance reads `sales_ledger.csv`, written by `lib/sync_actuals.py` from the
Sell > Fulfillment API. That file — not the listings ledger — is the only place
that knows what an item FINALLY sold for: the listings ledger records the ASK,
and a $325 ask that cleared at $99 on an accepted Best Offer looks identical to
one that cleared at $325 if you only read the ask. Run `sync_actuals --apply`
before a performance report if the numbers look stale.

## Why this doesn't just read the ledger

`listings_ledger.csv` is incomplete by construction:

  * **CHOICE / multi-variation listings never reach it.** `list_edit_group.py`
    publishes from `draft_group.md` and does not call `upsert_listing()`, so
    four live group listings (26 variations) were invisible to the ledger as of
    2026-08-15.
  * **Rows go stale.** `--update` doesn't touch the ledger, and a sale never
    writes back at all, so `status` drifts from reality.

So this reads BOTH the ledger and every `draft.md` / `draft_group.md` on disk,
merges them on `ebay_listing_id`, and reports which source each row came from.
Disk wins on conflict — the draft is what the publisher actually wrote.

Times are stored UTC (`...Z`) and displayed in LOCAL time, because "listed
today" means the user's today, not UTC's.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "listings_ledger.csv"
INVENTORY = REPO / "inventory"


def _parse_utc(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    s = raw.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _local(dt: Optional[datetime]) -> Optional[datetime]:
    return dt.astimezone() if dt else None


def _yaml_scalar(text: str, key: str) -> str:
    """Pull one top-level-ish scalar out of a draft's frontmatter.

    Deliberately regex, not a YAML parser: drafts carry a `_field_constraints`
    block with `{...}` flow mappings and free-text notes that trip strict
    loaders, and we only ever need a handful of flat keys.
    """
    m = re.search(rf'^\s*{re.escape(key)}:\s*"?([^"\n]*)"?\s*$', text, re.M)
    if not m:
        return ""
    val = m.group(1).strip()
    return "" if val in ("null", "~") else val


def _scan_drafts() -> list[dict]:
    rows = []
    for path in sorted(INVENTORY.rglob("draft*.md")):
        if path.name not in ("draft.md", "draft_group.md"):
            continue
        try:
            t = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        listing_id = _yaml_scalar(t, "ebay_listing_id")
        rows.append({
            "sku": _yaml_scalar(t, "ebay_inventory_sku"),
            "title": _yaml_scalar(t, "title"),
            "price": _yaml_scalar(t, "price"),
            "listing_id": listing_id,
            "offer_id": _yaml_scalar(t, "ebay_offer_id"),
            "published_at": _yaml_scalar(t, "published_at"),
            "drafted_at": _yaml_scalar(t, "drafted_at"),
            "synced_at": _yaml_scalar(t, "last_synced"),
            "path": path.relative_to(REPO).as_posix(),
            "group": path.name == "draft_group.md",
            "src": "disk",
        })
    return rows


def _read_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    with LEDGER.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            out.append({
                "sku": (r.get("sku") or "").strip(),
                "title": (r.get("title") or "").strip(),
                "price": (r.get("price") or "").strip(),
                "listing_id": (r.get("listing_id") or "").strip(),
                "offer_id": (r.get("offer_id") or "").strip(),
                "published_at": (r.get("published_at") or "").strip(),
                "drafted_at": (r.get("drafted_at") or "").strip(),
                "synced_at": (r.get("synced_at") or "").strip(),
                "status": (r.get("status") or "").strip(),
                "path": "", "group": False, "src": "ledger",
            })
    return out


def collect() -> list[dict]:
    """Merge ledger + disk. Disk wins; key on listing_id, else sku, else path."""
    merged: dict[str, dict] = {}
    for row in _read_ledger() + _scan_drafts():          # disk second => wins
        key = row["listing_id"] or row["sku"] or row["path"]
        if not key:
            continue
        if key in merged:
            prev = merged[key]
            row = {**prev, **{k: v for k, v in row.items() if v not in ("", None)}}
            row["src"] = "both" if prev["src"] != row["src"] else prev["src"]
        merged[key] = row
    return list(merged.values())


def _money(raw: str) -> float:
    try:
        return float(str(raw).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def listed_between(rows: list[dict], start: date, end: date) -> list[dict]:
    out = []
    for r in rows:
        loc = _local(_parse_utc(r.get("published_at", "")))
        if loc and start <= loc.date() <= end:
            r = dict(r)
            r["_when"] = loc
            out.append(r)
    return sorted(out, key=lambda r: r["_when"])


def report_listed(rows: list[dict], start: date, end: date, *, verbose=False) -> str:
    hits = listed_between(rows, start, end)
    label = ("today" if start == end == date.today()
             else f"{start.isoformat()}" if start == end
             else f"{start.isoformat()} .. {end.isoformat()}")
    if not hits:
        return f"No items listed {label}."

    total = sum(_money(r["price"]) for r in hits)
    lines = [f"LISTED {label} — {len(hits)} item(s), ${total:,.2f} total ask",
             ""]
    w = max(len(r["title"][:52]) for r in hits)
    for r in hits:
        flag = " [GROUP]" if r.get("group") else ""
        ledger = "" if r["src"] in ("ledger", "both") else "  !not-in-ledger"
        lines.append(f"  {r['_when']:%H:%M}  ${_money(r['price']):>8,.2f}  "
                     f"{r['title'][:52]:<{w}}{flag}{ledger}")
        if verbose:
            url = (f"https://www.ebay.com/itm/{r['listing_id']}"
                   if r["listing_id"] else "(no listing id)")
            lines.append(f"          {url}   {r['path'] or r['sku']}")

    # group by shoot so a batch reads as a batch
    buckets: dict[str, list[dict]] = {}
    for r in hits:
        p = r.get("path") or ""
        shoot = "/".join(p.split("/")[:2]) if p.startswith("inventory/") else "(ledger only)"
        buckets.setdefault(shoot, []).append(r)
    if len(buckets) > 1:
        lines += ["", "  By shoot:"]
        for shoot, rs in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
            sub = sum(_money(r["price"]) for r in rs)
            lines.append(f"    {len(rs):>3}  ${sub:>9,.2f}  {shoot}")

    missing = [r for r in hits if r["src"] == "disk"]
    if missing:
        lines += ["", f"  NOTE: {len(missing)} listing(s) are on disk but NOT in "
                      f"listings_ledger.csv (group listings don't write to it)."]
    return "\n".join(lines)


def report_pipeline(rows: list[dict]) -> str:
    """What's waiting: drafted but never listed, and synced but never published."""
    drafted, synced = [], []
    for r in rows:
        if r.get("published_at"):
            continue
        (synced if r.get("offer_id") or r.get("synced_at") else drafted).append(r)
    lines = [f"PIPELINE — {len(synced)} synced-not-published, {len(drafted)} drafted-only", ""]
    if synced:
        lines.append("  SYNCED (an eBay draft exists — one step from live):")
        for r in sorted(synced, key=lambda r: -_money(r["price"]))[:15]:
            lines.append(f"    ${_money(r['price']):>8,.2f}  {r['title'][:56]:<56}  {r['path'] or r['sku']}")
    if drafted:
        lines += ["", "  DRAFTED (local only), top 15 by ask:"]
        for r in sorted(drafted, key=lambda r: -_money(r["price"]))[:15]:
            lines.append(f"    ${_money(r['price']):>8,.2f}  {r['title'][:56]:<56}  {r['path'] or r['sku']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# PERFORMANCE — what it actually made (reads sales_ledger.csv)
# --------------------------------------------------------------------------- #
SALES = REPO / "sales_ledger.csv"

# Bands are for reading the market, not for accounting: eBay's fee is
# effectively regressive (a flat per-order component weighs far more on a $15
# sale than a $500 one) and Best Offer behaves differently at each tier, so a
# single blended number hides both effects.
_BANDS = [(0, 25), (25, 50), (50, 100), (100, 250), (250, 10 ** 6)]

# Keyword -> category. Deliberately crude and ordered: the first hit wins, so
# put the specific ahead of the general. Titles are what buyers search, so they
# classify better here than our folder names do.
_CATEGORIES = [
    ("gold/jewelry", ("14k", "10k", "12k gold", "18k", "gold filled", "sterling",
                      "necklace", "bracelet", "earrings", " ring", "locket",
                      "brooch", "cufflink", "pendant", "tie bar")),
    ("catalogs", ("catalog", "catalogue", "handbook", "lookbook", "preview")),
    ("magazines", ("magazine", "issue", "esquire", "gq ", "vanity fair")),
    ("books", ("szekely", "book", "gospel", "hardcover", "paperback", "dulcimer")),
    ("silverplate", ("silverplate", "rogers", "wm rogers")),
    ("marbles", ("marble",)),
    ("electronics", ("vacuum tube", "projector lamp", "radio shack")),
    ("glass/ceramic", ("fenton", "glass", "bisque", "stoneware", "jug", "vase",
                       "porcelain", "hurricane")),
    ("clothing/textile", ("scarf", "jacket", "shirt", "sandal", "blanket",
                          "shoes", "quilt")),
]


def categorize(title: str) -> str:
    t = (title or "").lower()
    for name, kws in _CATEGORIES:
        if any(k in t for k in kws):
            return name
    return "other"


def load_sales(days: Optional[int] = None) -> list[dict]:
    if not SALES.exists():
        return []
    cutoff = (date.today() - timedelta(days=days)) if days else None
    out = []
    with SALES.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                sold = date.fromisoformat(r.get("sold_at", ""))
            except ValueError:
                continue
            if cutoff and sold < cutoff:
                continue
            r["_sold"] = sold
            r["_cat"] = categorize(r.get("title", ""))
            out.append(r)
    return out


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _pct_of_ask(r: dict) -> Optional[float]:
    ask, got = _money(r.get("listed_price")), _money(r.get("item_price"))
    return (got / ask * 100) if ask > 0 and got > 0 else None


def report_performance(sales: list[dict], rows: list[dict], *, label: str = "") -> str:
    if not sales:
        return ("No sales data. Run:  python lib/sync_actuals.py --apply\n"
                "(writes sales_ledger.csv from the Fulfillment API)")

    L = "\n".join
    gross = sum(_money(r["gross"]) for r in sales)
    fees = sum(_money(r["ebay_fee"]) for r in sales)
    net = sum(_money(r["net_before_postage"]) for r in sales)
    out = [f"PERFORMANCE — {len(sales)} sold line item(s){label}", "",
           f"  gross ${gross:,.2f}   eBay fees ${fees:,.2f} ({fees/gross*100:.1f}%)   "
           f"net before postage ${net:,.2f}", ""]

    # --- fee reality ---------------------------------------------------------
    out.append("  FEE RATE (what eBay actually took, by sale size)")
    for lo, hi in _BANDS:
        rs = [_money(r["ebay_fee"]) / _money(r["gross"]) * 100
              for r in sales if lo <= _money(r["gross"]) < hi and _money(r["gross"]) > 0]
        if rs:
            cap = "+" if hi > 10 ** 5 else f"-{hi}"
            out.append(f"    ${lo:>4}{cap:<6} n={len(rs):>3}   median {_median(rs):5.2f}%")
    out.append("")

    # --- ask vs actual -------------------------------------------------------
    pcts = [(p, r) for r in sales if (p := _pct_of_ask(r)) is not None]
    if pcts:
        full = [p for p, _ in pcts if p >= 99.5]
        low = [p for p, _ in pcts if p < 99.5]
        out += [f"  ASK vs ACTUAL  (n={len(pcts)} with a known ask)",
                f"    median sale = {_median([p for p, _ in pcts]):.0f}% of ask   ·   "
                f"{len(full)} at full ask, {len(low)} below "
                f"(those cleared at a median {_median(low):.0f}%)" if low else
                f"    every matched sale cleared at full ask"]
        for lo, hi in _BANDS:
            sub = [(p, r) for p, r in pcts if lo <= _money(r["listed_price"]) < hi]
            if len(sub) >= 3:
                atask = sum(1 for p, _ in sub if p >= 99.5)
                cap = "+" if hi > 10 ** 5 else f"-{hi}"
                out.append(f"    ask ${lo:>4}{cap:<6} n={len(sub):>3}   "
                           f"median {_median([p for p, _ in sub]):3.0f}% of ask   "
                           f"{atask}/{len(sub)} at full ask")
        out.append("")

    # --- speed ---------------------------------------------------------------
    byid = {r.get("sku"): r for r in rows if r.get("sku")}
    aged = []
    for r in sales:
        lr = byid.get(r.get("sku"))
        pub = _parse_utc((lr or {}).get("published_at", ""))
        if pub:
            d = (r["_sold"] - pub.date()).days
            if d >= 0:
                aged.append((d, r))
    if aged:
        ds = [d for d, _ in aged]
        fast = [(d, r) for d, r in aged if d <= 2]
        out += [f"  SPEED  (n={len(ds)} with a publish date)",
                f"    median {_median([float(x) for x in ds]):.0f} days to sell"]
        if fast:
            fullfast = sum(1 for _, r in fast if (_pct_of_ask(r) or 0) >= 99.5)
            out.append(f"    ⚡ {len(fast)} sold within 48h ({len(fast)/len(ds)*100:.0f}%), "
                       f"{fullfast} of them at FULL ask — the classic under-priced signal:")
            for d, r in sorted(fast, key=lambda x: -_money(x[1]["item_price"]))[:8]:
                p = _pct_of_ask(r)
                out.append(f"      {d}d  ${_money(r['item_price']):>8,.2f}  "
                           f"{(f'{p:3.0f}% of ask' if p else '  ask unknown')}  "
                           f"{r['title'][:46]}")
        out.append("")

    # --- categories ----------------------------------------------------------
    buckets: dict[str, list[dict]] = {}
    for r in sales:
        buckets.setdefault(r["_cat"], []).append(r)
    out += ["  BY CATEGORY", f"    {'category':<18}{'n':>4}{'gross':>11}"
            f"{'median sale':>13}{'% of ask':>10}{'≤48h':>7}"]
    for k, rs in sorted(buckets.items(), key=lambda kv: -sum(_money(r["gross"]) for r in kv[1])):
        ps = [p for r in rs if (p := _pct_of_ask(r)) is not None]
        quick = sum(1 for d, r in aged if r["_cat"] == k and d <= 2)
        out.append(f"    {k:<18}{len(rs):>4}"
                   f"{sum(_money(r['gross']) for r in rs):>11,.2f}"
                   f"{_median([_money(r['item_price']) for r in rs]):>13,.2f}"
                   f"{(f'{_median(ps):.0f}%' if ps else '-'):>10}{quick:>7}")
    out.append("")

    # --- coverage ------------------------------------------------------------
    un = [r for r in sales if r.get("matched_by") == "unmatched"]
    if un:
        out += [f"  ⚠ COVERAGE — {len(un)}/{len(sales)} sales "
                f"(${sum(_money(r['gross']) for r in un):,.2f}, "
                f"{sum(_money(r['gross']) for r in un)/gross*100:.0f}% of revenue) "
                f"have NO local record.",
                "    Listed outside the pipeline, so they carry no comp research, no "
                "ask-vs-actual, and no reusable draft.", ""]

    # --- dial-in flags (mechanical, not editorial) ---------------------------
    flags = []
    blended = fees / gross * 100 if gross else 0
    if blended > 14:
        flags.append(f"Fee assumption: PRICE uses 13% + $0.40; reality is "
                     f"{blended:.1f}% blended. Net-to-us lines are optimistic.")
    for k, rs in buckets.items():
        ps = [p for r in rs if (p := _pct_of_ask(r)) is not None]
        if len(ps) >= 4 and _median(ps) >= 99:
            quick = sum(1 for d, r in aged if r["_cat"] == k and d <= 2)
            flags.append(f"{k}: median {_median(ps):.0f}% of ask over {len(ps)} sales"
                         f"{f', {quick} inside 48h' if quick else ''} — nothing is "
                         f"negotiating us down. Test a higher ask.")
        elif len(ps) >= 3 and _median(ps) <= 75:
            flags.append(f"{k}: clears at a median {_median(ps):.0f}% of ask over "
                         f"{len(ps)} sales — asks are set above what this category pays.")
    for lo, hi in _BANDS:
        sub = [(p, r) for p, r in pcts if lo <= _money(r["listed_price"]) < hi]
        if len(sub) >= 5 and not any(p >= 99.5 for p, _ in sub):
            cap = "+" if hi > 10 ** 5 else f"-{hi}"
            flags.append(f"${lo}{cap} band: 0 of {len(sub)} sold at full ask "
                         f"(median {_median([p for p, _ in sub]):.0f}%) — buyers always "
                         f"negotiate here; set auto-decline against that, not the ask.")
    if flags:
        out.append("  DIAL-IN FLAGS")
        out += [f"    • {f}" for f in flags]
    return L(out)


def _cli() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="ebaybiz activity + performance report.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--today", action="store_true", help="Items listed today (default).")
    g.add_argument("--yesterday", action="store_true")
    g.add_argument("--days", type=int, metavar="N", help="Last N days, inclusive.")
    g.add_argument("--since", metavar="YYYY-MM-DD")
    g.add_argument("--on", metavar="YYYY-MM-DD", help="One specific day.")
    ap.add_argument("--pipeline", action="store_true",
                    help="Also show what's drafted/synced but not yet live.")
    ap.add_argument("--performance", "--sales", dest="performance", action="store_true",
                    help="What it actually MADE: real prices, fees, ask-vs-actual, "
                         "speed, category patterns, dial-in flags.")
    ap.add_argument("--category", metavar="NAME",
                    help="Restrict --performance to one category (see BY CATEGORY).")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="Show listing URLs and shoot paths.")
    args = ap.parse_args()

    today = date.today()
    if args.yesterday:
        start = end = today - timedelta(days=1)
    elif args.days:
        start, end = today - timedelta(days=args.days - 1), today
    elif args.since:
        start, end = date.fromisoformat(args.since), today
    elif args.on:
        start = end = date.fromisoformat(args.on)
    else:
        start = end = today

    rows = collect()

    if args.performance:
        # A performance window is "sales in the last N days", not "listed on
        # date X" — an item listed in June and sold in August belongs in
        # August's numbers.
        days = args.days if args.days else None
        sales = load_sales(days)
        if args.category:
            sales = [s for s in sales if s["_cat"] == args.category]
        label = (f" · last {days} days" if days else " · all time") + \
                (f" · {args.category}" if args.category else "")
        print(report_performance(sales, rows, label=label))
        return

    print(report_listed(rows, start, end, verbose=args.verbose))
    if args.pipeline:
        print()
        print(report_pipeline(rows))


if __name__ == "__main__":
    _cli()
