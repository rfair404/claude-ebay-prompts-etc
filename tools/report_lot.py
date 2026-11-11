#!/usr/bin/env python3
"""report_lot — the full account of one lot: every item it brought in, where
each one stands, and a money summary.

    python tools/report_lot.py ES/<LOT>                # estate buy
    python tools/report_lot.py CONSIGN-YYYYMMDD        # consignment intake
    python tools/report_lot.py ES/<LOT> --print        # also print on CannonBW
    python tools/report_lot.py --all                   # every lot on one page

--all writes only reports/lots/lots-<date>.html: every lot's account (the
consignments, then ES/* and ESTATES/*) with a picker at the top to switch
between them. The link opens on the lot picked last (or the one in #hash).

Writes reports/lots/<lot>-<date>.txt (plain text), .csv (one row per item),
.html (web page, with photo thumbnails) and .pdf (landscape, for paper) and writes the .txt to
stdout. The PDF is Chromium's print of the .html; if it fails the rest still
land and the run says `FAILED pdf`. Paper only with --print, which is only for
when Russell asks for it. Reads local ledgers only, never eBay, so
run the finance sync and `consign sync --apply` first for fresh sale figures.

WHAT A LOT IS

A lot is a folder with a context.txt (a bucket, see lib/source_report.py).
Its members are:

  * the lot folder and every bucket nested under it, except a nested bucket
    whose context.txt says `client:` (a consignment) or `part_of:` another lot;
  * any bucket anywhere in inventory/ whose context.txt says
    `part_of: <lot>`, for items from the same buy filed elsewhere.

Paid is the sum of the members' `spend:`. A member's `note:` line is printed
beside it on the summary, for things such as a mixed-source lot.

A lot whose context.txt says `client:` is a consignment. Its items and money
come from its client_ledger.csv and the summary is tools/consign.py's
per-client account, so the two can never disagree.

ESTATE ITEM STATUS

An item is the shallowest folder holding a draft or photos. Its listings come
from both stores' ledgers merged with the drafts on disk (lib/report.collect).
Status, best first: SOLD (a sales-ledger row), LIVE (published and not ended),
ENDED, QUEUED (an offer on eBay, not published), DRAFT, PHOTOS ONLY, NOT SHOT
(an empty folder: the item is filed, typically with its cost share, but has
no photos yet). An unsold, not-live item whose draft.md says
`meta.channel: mall` is AVAILABLE LOCALLY (in the booth). The ledger lists
them in the overview's order: sold, live, local, queued/draft, photos only,
not shot, ended. Folders under `_trash/` (or any `_`/`.` folder) are never
members.
Net kept per sale = net_before_postage - ad_fee - actual_postage, counted only
once both actuals are in (the accountant's 2026-10-22 ruling); until then the
sale shows as "actuals pending".
"""
from __future__ import annotations

import argparse
import base64
import csv
import functools
import html
import io
import re
import os
import tempfile
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "tools"))
import report as _report                                        # noqa: E402
import source_report as SR                                      # noqa: E402
import stores                                                   # noqa: E402

INVENTORY = REPO / "inventory"
OUT_DIR = REPO / "reports" / "lots"
PRINTER = "CannonBW"
PDF_TIMEOUT = 60
THUMB_PX = 240  # stored at 3x the 80px it shows at, so it stays sharp on phones
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".nef", ".orf", ".heic"}
_LINE_RE = re.compile(r"^\s*(client|part_of|note)\s*:\s*(.*?)\s*$", re.I)
STATUS_ORDER = ["SOLD", "LIVE", "AVAILABLE LOCALLY", "QUEUED", "DRAFT", "PHOTOS ONLY",
                "NOT SHOT", "ENDED"]


# --------------------------------------------------------------------------- #
# lot membership
# --------------------------------------------------------------------------- #

def lot_dir(name: str) -> Path:
    p = Path(name)
    if not p.is_absolute():
        p = REPO / p if p.parts and p.parts[0] == "inventory" else INVENTORY / p
    return p.resolve()


def label(d: Path) -> str:
    return SR.bucket_label(d, INVENTORY)


def extra_keys(d: Path) -> dict:
    """`client:`, `part_of:` and `note:` from a context.txt (first wins)."""
    out: dict = {}
    f = d / "context.txt"
    if not f.is_file():
        return out
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE_RE.match(line)
        if m and m.group(2) and m.group(1).lower() not in out:
            out[m.group(1).lower()] = m.group(2).strip().strip("/")
    return out


def _skip(p: Path) -> bool:
    return p.name.startswith((".", "_")) or SR._is_backup_path(p.as_posix())


def members(lot: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """(member buckets, nested buckets left out and why)."""
    me = label(lot)
    keep, left_out = [lot], []
    for ctx in sorted(lot.rglob("context.txt")):
        d = ctx.parent
        if d == lot or any(_skip(Path(part)) for part in d.relative_to(lot).parts):
            continue
        k = extra_keys(d)
        if k.get("client"):
            left_out.append((d, f"consignment, client {k['client']}"))
        elif k.get("part_of") and k["part_of"] != me:
            left_out.append((d, f"part of {k['part_of']}"))
        else:
            keep.append(d)
    for ctx in sorted(INVENTORY.rglob("context.txt")):
        d = ctx.parent.resolve()
        if d in keep or lot in d.parents or SR._is_backup_path(d.as_posix()):
            continue
        # _trash/, _returned/ and hidden folders are not inventory
        inv = INVENTORY.resolve()          # a worktree's inventory/ is a link
        if inv in d.parents and any(_skip(Path(p)) for p in d.relative_to(inv).parts):
            continue
        if extra_keys(d).get("part_of") == me:
            keep.append(d)
    return keep, left_out


def item_dirs(bucket: Path, stop: set[Path]) -> list[Path]:
    """Shallowest folders under `bucket` holding a draft or photos, plus empty
    leaf folders (items filed but not shot). Doesn't descend into another
    member or left-out bucket (`stop`)."""
    def has_item(d: Path) -> bool:
        try:
            return any(f.is_file() and (f.name in ("draft.md", "draft_group.md")
                                         or f.suffix.lower() in PHOTO_EXT)
                       for f in d.iterdir())
        except OSError:
            return False

    if has_item(bucket):
        return [bucket]
    subs = [p for p in sorted(bucket.iterdir()) if p.is_dir() and not _skip(p)]
    if not subs:
        return [bucket]                     # an empty folder is an item not shot yet
    out = []
    for d in subs:
        if d not in stop:
            out.extend(item_dirs(d, stop))
    return out


# --------------------------------------------------------------------------- #
# estate account
# --------------------------------------------------------------------------- #

def _f(v) -> Optional[float]:
    return SR._to_float(v)


def _rank(r: dict) -> tuple:
    status = (r.get("status") or "").upper()
    if r.get("published_at") and status not in ("ENDED", "DELETED"):
        lvl = 4
    elif r.get("published_at"):
        lvl = 3
    elif r.get("offer_id") or r.get("synced_at"):
        lvl = 2
    else:
        lvl = 1
    return (lvl, r.get("published_at") or r.get("synced_at") or r.get("drafted_at") or "")


@functools.cache  # --all asks once per lot; the ledgers don't change mid-run
def _all_listings() -> list[dict]:
    rows = []
    for name in stores.configured_stores():
        for r in _report.collect(name):
            rows.append(dict(r, _store=name))
    return rows


@functools.cache
def _all_sales() -> list[dict]:
    rows = []
    for name in stores.configured_stores():
        for r in SR._sales_rows(name):
            rows.append(dict(r, _store=name))
    return rows


def _mall(d: Path) -> bool:
    """The item's draft says meta.channel: mall (sells in the booth, not on eBay)."""
    from draft_io import DraftParseError, draft_channel, parse_draft
    f = d / "draft.md"
    try:
        return f.is_file() and draft_channel(parse_draft(f).frontmatter) == "mall"
    except (OSError, ValueError, DraftParseError):
        return False


def _title_from(d: Path) -> str:
    return re.sub(r"^\d+[-_]", "", d.name).replace("-", " ").replace("_", " ")


def estate_items(lot: Path, keep: list[Path], left_out: list[tuple[Path, str]]) -> list[dict]:
    stop = set(keep) | {d for d, _ in left_out}
    items: list[tuple[Path, Path]] = []                   # (item dir, member)
    for m in keep:
        for d in item_dirs(m, stop - {m}):
            items.append((d.resolve(), m))
    listings, sales = _all_listings(), _all_sales()

    def owner(rel: str) -> Optional[Path]:
        if not rel:
            return None
        p = (REPO / SR._norm(rel)).resolve()
        best = None
        for d, _ in items:
            if (p == d or d in p.parents) and (best is None or len(d.parts) > len(best.parts)):
                best = d
        return best

    by_item: dict[Path, dict[str, dict]] = {d: {} for d, _ in items}
    for r in listings:
        d = owner(SR._norm(r.get("path") or ""))
        if d is None:
            continue
        key = r.get("sku") or r.get("listing_id") or r.get("path")
        prev = by_item[d].get(key)
        if prev is None or _rank(r) > _rank(prev):
            by_item[d][key] = r
    sku_to_item = {k: d for d, rows in by_item.items() for k in rows}
    lid_to_item = {r.get("listing_id"): d for d, rows in by_item.items()
                   for r in rows.values() if r.get("listing_id")}

    sold: dict[Path, list[dict]] = {}
    for s in sales:
        d = (owner(s.get("shoot_dir") or "") or sku_to_item.get(s.get("sku") or "")
             or lid_to_item.get(s.get("listing_id") or ""))
        if d is not None:
            sold.setdefault(d, []).append(s)

    out = []
    for d, m in items:
        rows = sorted(by_item[d].values(), key=_rank, reverse=True)
        best = rows[0] if rows else {}
        sales_here = sold.get(d, [])
        if sales_here:
            status = "SOLD"
        elif not best:
            photos = any(f.suffix.lower() in PHOTO_EXT for f in d.iterdir())
            status = "PHOTOS ONLY" if photos else "NOT SHOT"
        else:
            lvl = _rank(best)[0]
            status = {4: "LIVE", 3: "ENDED", 2: "QUEUED", 1: "DRAFT"}[lvl]
        if status not in ("SOLD", "LIVE") and _mall(d):
            status = "AVAILABLE LOCALLY"
        gross = sum(_f(s.get("gross")) or 0.0 for s in sales_here)
        nbp = sum(_f(s.get("net_before_postage")) or 0.0 for s in sales_here)
        actuals = all((s.get("ad_fee") or "").strip() and (s.get("actual_postage") or "").strip()
                      for s in sales_here)
        kept = (nbp - sum(_f(s.get("ad_fee")) or 0.0 for s in sales_here)
                - sum(_f(s.get("actual_postage")) or 0.0 for s in sales_here)) if sales_here else None
        if not best and sales_here:      # sold, but no listing row survived: use the sale's ids
            best = {k: sales_here[0].get(k) or "" for k in ("title", "sku", "listing_id", "_store")}
        title = best.get("title") or _title_from(d)
        out.append({
            "item": _short(title, 60),
            "title": title,
            "folder": d.relative_to(INVENTORY).as_posix() if INVENTORY in d.parents else str(d),
            "member": label(m),
            "status": status,
            "store": {"default": "main"}.get(best.get("_store") or "", best.get("_store") or ""),
            "sku": best.get("sku") or "",
            "listing_id": best.get("listing_id") or "",
            "ask": _f(best.get("price")),
            "sold_at": ", ".join((s.get("sold_at") or "")[:10] for s in sales_here),
            "gross": gross if sales_here else None,
            "net_before_postage": nbp if sales_here else None,
            "net_kept": kept if sales_here and actuals else None,
            "actuals_pending": bool(sales_here) and not actuals,
            "listings": len(rows),
        })
    out.sort(key=lambda r: (STATUS_ORDER.index(r["status"]), -(r["ask"] or 0)))
    return out


def _m(v) -> str:
    return "-" if v is None else f"{v:,.2f}"


def estate_text(lot: Path, keep: list[Path], left_out: list[tuple[Path, str]],
                items: list[dict], today: date) -> str:
    """Laid out like the consignment account: overview and money tables,
    then the ledger, then the member folders (the long list) last."""
    ctx = SR.load_context(lot)
    spends = [(m, SR.load_context(m)["spend"]) for m in keep]
    paid = sum(s for _, s in spends if s is not None)
    L = [f"LOT ACCOUNT - {label(lot)}   estate   (as of {today.isoformat()})",
         f"Acquired: {ctx['acquired'] or 'not recorded'}   Paid: ${paid:,.2f}"]

    def of(*status):
        return [i for i in items if i["status"] in status]

    def ask(group):
        return _m(sum(i["ask"] or 0 for i in group)) if any(i["ask"] for i in group) else "-"
    sold = of("SOLD")
    gross = sum(i["gross"] or 0 for i in sold)
    kept = sum(i["net_kept"] for i in sold if i["net_kept"] is not None)
    pend = [i for i in sold if i["actuals_pending"]]
    groups = [("Sold", sold, "-", _m(gross) if sold else "-", _m(kept) if sold else "-"),
              ("Live on eBay", of("LIVE"), ask(of("LIVE")), "-", "-"),
              ("Available locally", of("AVAILABLE LOCALLY"), ask(of("AVAILABLE LOCALLY")), "-", "-"),
              ("Queued/draft", of("QUEUED", "DRAFT"), ask(of("QUEUED", "DRAFT")), "-", "-"),
              ("Photos only", of("PHOTOS ONLY"), "-", "-", "-"),
              ("Not shot", of("NOT SHOT"), "-", "-", "-"),
              ("Ended", of("ENDED"), "-", "-", "-")]
    hdr = f"{'GROUP':<22}{'ITEMS':>6}{'LIST PRICE':>12}{'GROSS':>10}{'NET KEPT':>10}"
    L += ["", "OVERVIEW", hdr, "-" * len(hdr)]
    for name, g, a, gr, k in groups:
        if g:
            L.append(f"{name:<22}{len(g):>6}{a:>12}{gr:>10}{k:>10}")

    hdr = f"{'MONEY':<26}{'AMOUNT':>10}  NOTE"
    L += ["", "LOT MONEY", hdr, "-" * len(hdr)]
    lines = [("Paid", paid, f"{len(keep)} member folders"),
             ("Net kept (actuals in)", kept, f"{len(sold) - len(pend)} sales"),
             ("Sales with actuals pending", sum(i["net_before_postage"] or 0 for i in pend),
              f"{len(pend)} sales, net before postage; ad fee or postage not in yet"),
             ("Recovered vs paid", kept - paid, "paid back" if kept >= paid else "")]
    for name, amt, note in lines:
        L.append(f"{name:<26}{_m(amt):>10}  {note or '-'}")

    L += ["", "INVENTORY LEDGER"]
    hdr = (f"{'#':<6}{'ITEM':<54}{'STATUS':<19}{'STORE':<7}{'ASK':>10}{'SOLD':>10}"
           f"{'NET KEPT':>10}  {'SKU':<10}{'LISTING':<14}")
    L += [hdr, "-" * len(hdr)]
    for n, i in enumerate(items, 1):
        kept_s = "pending" if i["actuals_pending"] else _m(i["net_kept"])
        L.append(f"{n:<6}{_short(i['title'], 52):<54}{i['status']:<19}{i['store']:<7}{_m(i['ask']):>10}"
                 f"{_m(i['gross']):>10}{kept_s:>10}  {i['sku']:<10}{i['listing_id']:<14}".rstrip())
    L += ["", "Note: QUEUED = offer on eBay, not published. AVAILABLE LOCALLY = draft set to sell in the mall booth.",
          "Net kept counts a sale once its ad fee and postage are in."]

    L += ["", "MEMBERS (folders counted in this lot)"]
    for m, sp in spends:
        note = extra_keys(m).get("note")
        L.append(f"  {label(m):<44} spend {'not set' if sp is None else f'${sp:,.2f}'}"
                 + (f"   note: {note}" if note else ""))
    for d, why in left_out:
        L.append(f"  {label(d):<44} LEFT OUT: {why}")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# consignment account
# --------------------------------------------------------------------------- #

def _held(r: dict) -> bool:
    """HOLD state, or [HOLD] written in the row's why or state_note."""
    return r.get("state") == "HOLD" or any("[HOLD]" in (r.get(k) or "") for k in ("why", "state_note"))


def _short(desc: str, n: int) -> str:
    """A client-facing item name: the first clause, without parenthetical
    notes or any clause naming Russell, cut at a word near n characters."""
    d = _report.ascii_text((desc or "").split(";")[0].replace("\n", " "))
    d = re.sub(r"\s*\([^)]*\)", "", d)
    d = ", ".join(c for c in d.split(", ") if "Russell" not in c)
    d = re.sub(r"\s+", " ", d).strip(" ,")
    if len(d) <= n:
        return d
    cut = d[:n - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "~"


def _overview(C, f: dict, rows: list[dict]) -> list[str]:
    """The summary as two tables: what the items are doing, then the money."""
    m = C.money

    def est(group):
        lo, hi, _ = C.estimates(group)
        return f"${m(C._sum(lo))}-{m(C._sum(hi))}" if lo else "-"

    def ask(group):
        return m(C._sum(C.dec(r["ask"]) for r in group)) if any(r["ask"] for r in group) else "-"
    listed = f["listed"]
    local = [r for r in listed if _local(r)]
    ebay = [r for r in listed if not _local(r)]
    hand = [r for r in f["hand"] if not _held(r)]
    held = [r for r in f["hand"] if _held(r)]
    sold, ex = f["sold"], f["excluded"]
    gone = f["by_state"]["RETURNED"] + f["by_state"]["DISPOSED"]
    share = C._sum(C.sold_share(r, f["split"], f["loss_rule"]) for r in sold)
    groups = [("Sold", sold, "-", "-", m(f["sold_gross"]), m(f["sold_net"]), m(share)),
              ("Listed on eBay", ebay, est(ebay), ask(ebay), "-", "-", "-"),
              ("Available locally", local, est(local), ask(local), "-", "-", "-"),
              ("On hand", hand, est(hand), "-", "-", "-", "-"),
              ("Held back", held, "-", "-", "-", "-", "-"),
              ("Bought by us", ex, "-", "-", "-", "-",
               m(C._sum(C.dec(r["buyout_price"]) for r in ex))),
              ("Returned / disposed", [None] * gone, "-", "-", "-", "-", "-")]
    hdr = f"{'GROUP':<22}{'ITEMS':>6}  {'ESTIMATE':<20}{'LIST PRICE':>11}{'GROSS':>10}{'NET':>10}{'CLIENT':>10}"
    L = ["", "OVERVIEW", hdr, "-" * len(hdr)]
    for name, g, e, a, gross, net, cl in groups:
        if g:
            L.append(f"{name:<22}{len(g):>6}  {e:<20}{a:>11}{gross:>10}{net:>10}{cl:>10}")
    hdr = f"{'MONEY':<26}{'AMOUNT':>10}  NOTE"
    L += ["", "CLIENT MONEY", hdr, "-" * len(hdr)]
    lines = [("Share owed now", f["owed"], "sold, cleared, not paid"),
             ("Buyouts owed", C._sum(C.dec(r["buyout_price"]) for r in f["buyouts_owed"]),
              f"{len(f['buyouts_owed'])} items we bought, not paid"),
             ("Share pending", f["pending"], "sold, return window open"
              + (" (upper bound)" if f["pending_upper"] else "")),
             ("Handling kept", f["handling"], f"{f['handling_n']} sales, before the split"),
             ("Paid to date", f["paid_total"], "")]
    if f["adjustments"]:
        lines.insert(3, ("Adjustments pending", C._sum(a for _, a in f["adjustments"]), ""))
    for name, amt, note in lines:
        L.append(f"{name:<26}{m(amt):>10}  {note or '-'}")
    notes = []
    if f["incomplete"]:
        notes.append(f"{len(f['incomplete'])} sales provisional: ad fee or postage not known yet")
    if f["flagged"]:
        notes.append(f"{len(f['flagged'])} rows flagged by sync (see `consign sync`)")
    return L + ([""] + notes if notes else [])


def _local(r: dict) -> bool:
    """In the GAM case: LISTED on the mall channel with its GAM SKU and placed_at."""
    return (r.get("state") == "LISTED" and (r.get("channel") or "").strip().lower() == "mall"
            and bool(r.get("placed_at")) and bool(r.get("listing_id")))


def consign_account(lot: Path, client: str, today: date) -> tuple[str, list[dict]]:
    import consign as C
    rows, _ = C.read_ledger(lot / C.LEDGER_NAME)
    C.migrate(rows, lot, client)
    registry = C.load_registry()
    terms = C.client_terms(registry, client)
    settlements = C.read_settlements(client)
    figs = C.summary_figures([(r.get("intake") or lot.name, r) for r in rows],
                             terms["split"], settlements, terms["loss_rule"], today)
    figs.update(split=terms["split"], loss_rule=terms["loss_rule"])
    name = (registry.get(client) or {}).get("name") or client
    plan = {"LOT": "in a lot", "LIST": "single", "RETURN": "return"}

    def val(r: dict) -> str:
        if r.get("state") == "EXCLUDED":
            return "kept $" + (r.get("buyout_price") or "0")
        # The client reads this: an unsold row shows its estimate
        # (Conservative-Recommended), not the push-high ask it lists at, so the
        # ask is never read as a promise. A listed row with no estimate shows
        # its ask labelled as the list price.
        state = r.get("state") or ""
        lo, hi = r.get("est_low") or r.get("est_high"), r.get("est_high") or r.get("est_low")
        if state == "LISTED":
            return f"${lo}-{hi}" if lo else ("list $" + r["ask"] if r.get("ask") else "-")
        if r.get("ask") and (state in C.SOLD_STATES or not r.get("est_low")):
            return "ask $" + r["ask"]
        if r.get("est_low"):
            return f"${r['est_low']}-{r.get('est_high') or '?'}"
        return "-"

    L = [f"LOT ACCOUNT - {label(lot)}   consignment, client {client} {name}   (as of {today.isoformat()})",
         f"Received: {C.folder_date(lot) or C.default_intake(lot) or 'not recorded'}   Split {terms['split'] * 100:.0f}% of net to client",
         "States as of the last `consign sync --apply`."]
    L += _overview(C, figs, rows)
    # Held rows go last, one short line each, after the ledger and fees: they
    # are counted in the totals but are not for sale yet.
    held = [r for r in rows if _held(r)]
    rows = [r for r in rows if not _held(r)] + held
    L += ["", "INVENTORY LEDGER"]
    hdr = f"{'#':<10}{'ITEM':<54}{'QTY':<12}{'PLAN':<10}{'STATE':<19}{'EST / ASK':<13}{'SOLD':>9}{'CLIENT':>9}{'FB':>5}"
    L += [hdr, "-" * len(hdr)]
    out, fee_out, fees = [], [], []
    fee_lines = C.load_fee_lines()
    for r in rows:
        d = _short(r.get("description") or "", 52)
        if not _held(r):
            L.append(f"{r['item_id']:<10}{d:<54}{_report.ascii_text(r.get('qty') or '')[:10]:<12}"
                     f"{plan.get(r.get('cut') or '', '-'):<10}{'AVAILABLE LOCALLY' if _local(r) else r.get('state') or '':<19}{val(r):<13}"
                     f"{r.get('item_price') or '-':>9}{r.get('client_share') or '-':>9}"
                     f"{'yes' if r.get('fb_promoted') else '-':>5}")
        # fees go in their own section after the ledger, one block per sale
        bd = C.fee_breakdown(r, fee_lines)
        if bd:
            fees.append((r, d, bd))
        out.append({"line": "item", "item_id": r["item_id"], "item": _short(r.get("description") or "", 60),
                    "photos": r.get("photos") or "",
                    "qty": r.get("qty") or "", "plan": plan.get(r.get("cut") or "", ""),
                    "state": r.get("state") or "", "hold": "HOLD" if _held(r) else "",
                    "available_locally": "yes" if _local(r) else "", "fb_promoted": r.get("fb_promoted") or "", "est_low": r.get("est_low") or "",
                    "est_high": r.get("est_high") or "", "ask": r.get("ask") or "",
                    "buyout_price": r.get("buyout_price") or "", "store": r.get("store") or "",
                    "sku": r.get("sku") or "", "listing_id": r.get("listing_id") or "",
                    "sold_at": r.get("sold_at") or "", "item_price": r.get("item_price") or "",
                    "net": r.get("net") or "", "client_share": r.get("client_share") or "",
                    "folder": r.get("inventory_folder") or "",
                    "ebay_fee": r.get("ebay_fee") or "", "ad_fee": r.get("ad_fee") or "",
                    **{k: "" for k in C.FEE_COLUMNS}, "note": ""})
        blank = {k: "" for k in out[-1]}
        fee_out += [dict(blank, line="fee", item_id=r["item_id"], **f) for f in C.fee_rows(bd)]
    if fees:
        # the shared renderer (tools/consign.py fee_text) explains each sale's fees
        L += ["", "SOLD ITEM FEES (taken off the sale price before the split)"]
        for r, d, bd in fees:
            L += ["", f"{r['item_id']:<10}{d}",
                  f"{'':<10}sold ${r.get('item_price') or '-'}   net ${r.get('net') or '-'}"
                  f"   client ${r.get('client_share') or '-'}"]
            L += C.fee_text(bd, indent=" " * 10)
    if held:
        L += ["", f"HELD BACK ({len(held)} not for sale yet; counted in the totals above)"]
        for r in held:
            L.append(f"  {r['item_id']:<10}{_short(r.get('description') or '', 60)}")
    # the client reads this: no names, ours or anyone's
    return re.sub(r"\bRussell\b", "The Fair Hand", "\n".join(L)) + "\n", out + fee_out


# --------------------------------------------------------------------------- #

def write(lot: Path, text: str, rows: list[dict], today: date) -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = label(lot).replace("/", "_") + f"-{today.isoformat()}"
    txt, csv_path = OUT_DIR / f"{stem}.txt", OUT_DIR / f"{stem}.csv"
    # Plain ASCII (lib/report.py ascii_text): it prints on lp and the R2 link
    # serves it with no charset. Titles were folded before padding.
    txt.write_text(_report.ascii_text(text), encoding="ascii")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows({k: _report.ascii_text(v) if isinstance(v, str) else v
                         for k, v in r.items()} for r in rows)
    return txt, csv_path


def _split_columns(rows: list[str]) -> list[tuple[int, int]]:
    """Column spans of a fixed-width table: the runs between gaps of 2+
    positions that are blank on every row (header included)."""
    width = max(map(len, rows))
    blank = [all(len(r) <= i or r[i] == " " for r in rows) for i in range(width)]
    spans, start, gap = [], None, 0
    for i, b in enumerate(blank + [True, True]):
        if b:
            gap += 1
            if start is not None and gap == 2:
                spans.append((start, i - 1))
                start = None
        else:
            gap = 0
            if start is None:
                start = i
    return spans


def _photo_for(row: dict, found: dict[str, Path]) -> Optional[Path]:
    """The item's first listing photo: its own folder first, else the first
    file named in the row's photos column."""
    folder = row.get("folder") or ""
    for base in (REPO, INVENTORY):
        d = base / folder
        if folder and d.is_dir():
            shots = sorted(p for p in d.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
            if shots:
                return shots[0]
    names = (row.get("photos") or "").split()
    return found.get(names[0]) if names else None


def _thumbs(rows: list[dict]) -> list[str]:
    """One <img> data URI per row ("" when it has no photo), so the page and
    its PDF are one self-contained file."""
    from PIL import Image, ImageFilter, ImageOps
    wanted = {(r.get("photos") or "").split()[0] for r in rows if (r.get("photos") or "").split()}
    found: dict[str, Path] = {}
    if wanted:
        for root, _, files in os.walk(INVENTORY):
            for f in files:
                if f in wanted and f not in found:
                    found[f] = Path(root) / f
    out = []
    for r in rows:
        src = _photo_for(r, found)
        try:
            if src is None:
                raise OSError
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((THUMB_PX, THUMB_PX), Image.LANCZOS)
                im = im.filter(ImageFilter.UnsharpMask(radius=1, percent=80, threshold=2))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=85)
            out.append("data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode())
        except (OSError, ValueError):
            out.append("")
    return out


def _table_html(header: str, body: list[str], rows: Optional[list[dict]] = None) -> str:
    spans = _split_columns([header] + body)
    cut = lambda line: [html.escape(line[a:b].strip()) for a, b in spans]  # noqa: E731
    num = re.compile(r"^(?:[-$\d,.]+|pending)$")
    cells = [cut(r) for r in body]
    heads = cut(header)
    pics = [""] * len(cells)
    if rows and len(rows) == len(cells):
        # the text cuts titles to fit its columns; the page shows them whole
        if "ITEM" in heads:
            col = heads.index("ITEM")
            for c, r in zip(cells, rows):
                c[col] = html.escape(_report.ascii_text(r.get("item") or "")) or c[col]
        pics = _thumbs(rows)
    if any(pics):
        heads.insert(0, "")
        for c, pic in zip(cells, pics):
            c.insert(0, f'<img src="{pic}" alt="">' if pic else "")
        right_pad = [False]
    else:
        right_pad = []
    right = right_pad + [bool(cells) and all(num.match(c[i + len(right_pad)]) or not c[i + len(right_pad)]
                                            for c in cells) for i in range(len(spans))]
    th = "".join(f'<th{" class=n" if right[i] else ""}>{c}</th>' for i, c in enumerate(heads))
    trs = "".join("<tr>" + "".join(f'<td{" class=n" if right[i] else (" class=t" if heads[i] == "ITEM" else "")}>{c}</td>'
                                   for i, c in enumerate(row)) + "</tr>" for row in cells)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


STYLE = """
body{font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px;color:#222;background:#fff}
h1{font-size:1.25rem;margin:0 0 12px} h2{font-size:1rem;margin:22px 0 6px;border-bottom:1px solid #bbb}
pre{font:13px/1.4 ui-monospace,monospace;white-space:pre-wrap;margin:6px 0}
.scroll{overflow-x:auto} table{border-collapse:collapse;width:100%;font-size:13px}
img{display:block;width:80px;height:80px;object-fit:cover;border-radius:3px}
th,td{padding:3px 8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}
td{white-space:nowrap} td.t{white-space:normal;min-width:18em}
th{background:#f2f2f2} .n{text-align:right;white-space:nowrap}
@media print{@page{size:landscape;margin:12mm}body{max-width:none;padding:0;font-size:11px}
table{font-size:10px}tr{break-inside:avoid}thead{display:table-header-group}}
"""


def html_body(text: str, rows: Optional[list[dict]] = None) -> tuple[str, str]:
    """(title, body) of the account as HTML: every all-caps heading line
    starts a section; a header line over a row of dashes becomes a real table
    and everything else stays preformatted, so the figures are the text's."""
    lines = text.rstrip("\n").split("\n")
    title = html.escape(lines[0])
    # held rows are a plain list after the ledger table, not in it
    items = [r for r in (rows or []) if r.get("line", "item") == "item" and not r.get("hold")]
    out, pre, i = [], [], 1

    def flush():
        if any(x.strip() for x in pre):
            out.append("<pre>" + html.escape("\n".join(pre).strip("\n")) + "</pre>")
        pre.clear()

    while i < len(lines):
        line = lines[i]
        if (i + 1 < len(lines) and re.fullmatch(r"-{20,}", lines[i + 1])):
            flush()
            j = i + 2
            while j < len(lines) and lines[j].strip():
                j += 1
            body = lines[i + 2:j]
            out.append(_table_html(line, body, items if "ITEM" in line and len(body) == len(items) else None))
            i = j
            continue
        if re.match(r"^[A-Z][A-Z ()/,.\-]{6,}", line) and not line.startswith(" "):
            flush()
            out.append(f"<h2>{html.escape(line)}</h2>")
        else:
            pre.append(line)
        i += 1
    flush()
    return title, "".join(out).replace("<table", '<div class="scroll"><table').replace("</table>", "</table></div>")


def _page(title: str, body: str, style: str = "") -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{STYLE}{style}</style></head><body>
{body}
</body></html>
"""


def html_report(text: str, rows: Optional[list[dict]] = None) -> str:
    title, body = html_body(text, rows)
    return _page(title, f"<h1>{title}</h1>\n{body}")


def all_lots() -> list[Path]:
    """Every lot folder: the consignments first, then the estate buys."""
    pats = ["CONSIGN-*", "ES/*", "ESTATES/*"]
    return [d for p in pats for d in sorted(INVENTORY.glob(p)) if (d / "context.txt").is_file()]


# The picker shows one lot at a time; #<id> or the last lot picked opens first.
PICKER_JS = """
const sel = document.getElementById('lot');
function show(id) {
  if (!document.getElementById(id)) id = sel.options[0].value;
  for (const s of document.querySelectorAll('section.lot')) s.hidden = s.id !== id;
  sel.value = id;
  try { localStorage.setItem('lot', id); } catch (e) {}
}
sel.onchange = () => { location.hash = sel.value; };
addEventListener('hashchange', () => show(location.hash.slice(1)));
let first = location.hash.slice(1);
try { first = first || localStorage.getItem('lot'); } catch (e) {}
show(first);
"""


def lots_page(accounts: list[tuple[str, str, list[dict]]], today: date) -> str:
    """One page with every lot's account and a picker to switch between them.
    accounts: (lot label, account text, rows)."""
    opts, sections = [], []
    for name, text, rows in accounts:
        sid = re.sub(r"[^A-Za-z0-9-]", "_", name)
        title, body = html_body(_report.ascii_text(text), rows)
        opts.append(f'<option value="{sid}">{html.escape(name)}</option>')
        sections.append(f'<section class="lot" id="{sid}"><h1>{title}</h1>\n{body}</section>')
    bar = (f'<header><select id="lot" aria-label="Lot">{"".join(opts)}</select>'
           f'<span>as of {today.isoformat()}</span></header>')
    style = ("header{position:sticky;top:0;background:#fff;padding:8px 0;margin-bottom:8px;"
             "border-bottom:2px solid #222;display:flex;gap:12px;align-items:center;flex-wrap:wrap}"
             "header select{font-size:1.1rem;padding:6px;max-width:100%}"
             "@media print{header{display:none}}")
    return _page(f"Lot reports {today.isoformat()}",
                 bar + "\n" + "\n".join(sections) + f"\n<script>{PICKER_JS}</script>", style)


def render_pdf(html_path: Path, pdf_path: Path) -> Optional[str]:
    """Headless Chromium prints the page to PDF. Returns why it failed, or
    None once the PDF exists (Chromium can log errors and still write one)."""
    with tempfile.TemporaryDirectory() as prof:
        try:
            r = subprocess.run(["chromium", "--headless", "--disable-gpu", "--no-pdf-header-footer",
                                f"--user-data-dir={prof}", f"--print-to-pdf={pdf_path}",
                                html_path.as_uri()],
                               capture_output=True, text=True, timeout=PDF_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            return str(e)
    if pdf_path.exists() and pdf_path.stat().st_size:
        return None
    return (r.stderr or r.stdout or f"exit {r.returncode}").strip().splitlines()[-1]


def write_web(txt: Path, text: str, rows: Optional[list[dict]] = None) -> tuple[Path, Optional[Path], Optional[str]]:
    """Web page and printer-friendly PDF beside the .txt. The PDF is None,
    with the reason, when Chromium could not render it."""
    page = txt.with_suffix(".html")
    page.write_text(html_report(_report.ascii_text(text), rows), encoding="utf-8")
    pdf = txt.with_suffix(".pdf")
    pdf.unlink(missing_ok=True)
    why = render_pdf(page, pdf)
    return page, (None if why else pdf), why


def print_file(path: Path) -> str:
    """The PDF prints as rendered (landscape from its @page). Raises
    RuntimeError when lp fails, so a sheet that never printed can't be
    reported as printed."""
    res = subprocess.run(["lp", "-d", PRINTER, "-t", path.stem, str(path)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"lp exited {res.returncode}: {(res.stderr or res.stdout).strip()}")
    return res.stdout.strip()


def print_txt(path: Path) -> str:
    """Landscape at 12 cpi so the ~130-column ledger fits one line per item.
    Raises RuntimeError when lp fails, so a sheet that never printed can't be
    reported as printed."""
    res = subprocess.run(["lp", "-d", PRINTER, "-o", "landscape", "-o", "cpi=12",
                          "-o", "lpi=7", "-o", "page-left=36", "-o", "page-top=36",
                          "-t", path.stem, str(path)], capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"lp exited {res.returncode}: {(res.stderr or res.stdout).strip()}")
    return res.stdout.strip()


def account(lot: Path, today: date) -> tuple[str, list[dict]]:
    """(account text, rows) for one lot, consignment or estate."""
    client = extra_keys(lot).get("client")
    if client:
        with redirect_stdout(io.StringIO()):
            return consign_account(lot, client, today)
    keep, left_out = members(lot)
    items = estate_items(lot, keep, left_out)
    text = estate_text(lot, keep, left_out, items, today)
    return text, [{k: ("" if v is None else v) for k, v in i.items()} for i in items]


def write_all(today: date) -> int:
    """reports/lots/lots-<date>.html. A lot that fails shows its error in its
    own section, is named on stderr, and the run exits 1."""
    accounts, failed = [], []
    for lot in all_lots():
        try:
            text, rows = account(lot, today)
        except Exception as e:  # one broken lot must not hide the others
            failed.append(label(lot))
            print(f"FAILED {label(lot)}: {e!r}", file=sys.stderr)
            text, rows = f"LOT ACCOUNT - {label(lot)}\nFAILED: {e!r}\n", []
        accounts.append((label(lot), text, rows))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    page = OUT_DIR / f"lots-{today.isoformat()}.html"
    page.write_text(lots_page(accounts, today), encoding="utf-8")
    print(f"WROTE {page.relative_to(REPO)}  ({len(accounts)} lots)")
    return 1 if failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Full account of one lot: item ledger + summary.")
    ap.add_argument("lot", nargs="?", help="lot folder under inventory/, e.g. ES/<LOT> or CONSIGN-YYYYMMDD")
    ap.add_argument("--all", action="store_true", help="every lot on one page with a lot picker")
    ap.add_argument("--print", action="store_true", help=f"also print the PDF (the .txt if no PDF) on {PRINTER}")
    args = ap.parse_args(argv)
    if bool(args.lot) == args.all:
        ap.error("give a lot folder or --all")
    if args.all and args.print:
        ap.error("--all makes only the web page; print one lot at a time")
    today = date.today()
    if args.all:
        return write_all(today)
    lot = lot_dir(args.lot)
    if not (lot / "context.txt").is_file():
        print(f"no context.txt in {lot}; a lot is a folder with one", file=sys.stderr)
        return 2
    text, rows = account(lot, today)
    txt, csv_path = write(lot, text, rows, today)
    page, pdf, why = write_web(txt, text, rows)
    sys.stdout.write(text)
    print("\nWROTE " + "  ".join(str(p.relative_to(REPO)) for p in (txt, csv_path, page, pdf) if p))
    if why:
        print(f"FAILED pdf: {why}", file=sys.stderr)
    if args.print:
        try:
            print("PRINTED " + (print_file(pdf) if pdf else print_txt(txt)))
        except (RuntimeError, OSError) as e:
            print(f"FAILED print on {PRINTER}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
