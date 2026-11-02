#!/usr/bin/env python3
"""report_lot — the full account of one lot: every item it brought in, where
each one stands, and a money summary.

    python tools/report_lot.py ES/<LOT>                # estate buy
    python tools/report_lot.py CONSIGN-YYYYMMDD        # consignment intake
    python tools/report_lot.py ES/<LOT> --print        # also print on CannonBW

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
no photos yet).
Net kept per sale = net_before_postage - ad_fee - actual_postage, counted only
once both actuals are in (the accountant's 2026-10-22 ruling); until then the
sale shows as "actuals pending".
"""
from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import re
import os
import tempfile
import textwrap
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
THUMB_PX = 120
GALLERY_PX = 300
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".nef", ".orf", ".heic"}
_LINE_RE = re.compile(r"^\s*(client|part_of|note)\s*:\s*(.*?)\s*$", re.I)
STATUS_ORDER = ["SOLD", "LIVE", "ENDED", "QUEUED", "DRAFT", "PHOTOS ONLY", "NOT SHOT"]


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


def _all_listings() -> list[dict]:
    rows = []
    for name in stores.configured_stores():
        for r in _report.collect(name):
            rows.append(dict(r, _store=name))
    return rows


def _all_sales() -> list[dict]:
    rows = []
    for name in stores.configured_stores():
        for r in SR._sales_rows(name):
            rows.append(dict(r, _store=name))
    return rows


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
        gross = sum(_f(s.get("gross")) or 0.0 for s in sales_here)
        nbp = sum(_f(s.get("net_before_postage")) or 0.0 for s in sales_here)
        actuals = all((s.get("ad_fee") or "").strip() and (s.get("actual_postage") or "").strip()
                      for s in sales_here)
        kept = (nbp - sum(_f(s.get("ad_fee")) or 0.0 for s in sales_here)
                - sum(_f(s.get("actual_postage")) or 0.0 for s in sales_here)) if sales_here else None
        out.append({
            "item": best.get("title") or _title_from(d),
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
    ctx = SR.load_context(lot)
    spends = [(m, SR.load_context(m)["spend"]) for m in keep]
    paid = sum(s for _, s in spends if s is not None)
    L = [f"LOT ACCOUNT - {label(lot)}   (as of {today.isoformat()})",
         f"Acquired: {ctx['acquired'] or 'not recorded'}   Paid: ${paid:,.2f}", ""]
    L.append("MEMBERS (folders counted in this lot)")
    for m, s in spends:
        note = extra_keys(m).get("note")
        L.append(f"  {label(m):<44} spend {'not set' if s is None else f'${s:,.2f}'}"
                 + (f"   note: {note}" if note else ""))
    for d, why in left_out:
        L.append(f"  {label(d):<44} LEFT OUT: {why}")
    L.append("")

    counts = {s: sum(1 for i in items if i["status"] == s) for s in STATUS_ORDER}
    live_ask = sum(i["ask"] or 0 for i in items if i["status"] == "LIVE")
    pend_ask = sum(i["ask"] or 0 for i in items if i["status"] in ("QUEUED", "DRAFT"))
    sold = [i for i in items if i["status"] == "SOLD"]
    gross = sum(i["gross"] or 0 for i in sold)
    nbp = sum(i["net_before_postage"] or 0 for i in sold)
    kept = sum(i["net_kept"] or 0 for i in sold if i["net_kept"] is not None)
    pending = sum(1 for i in sold if i["actuals_pending"])
    L += ["SUMMARY",
          f"  Items: {len(items)}   " + "   ".join(f"{s.lower()} {n}" for s, n in counts.items() if n),
          f"  Live asking total:      ${live_ask:,.2f}",
          f"  Queued/draft asking:    ${pend_ask:,.2f}",
          f"  Sold: {len(sold)}   gross ${gross:,.2f}   net before postage ${nbp:,.2f}",
          f"  Net kept (actuals in):  ${kept:,.2f}" + (f"   ({pending} sale(s) actuals pending)" if pending else ""),
          f"  Recovered vs paid:      ${kept - paid:,.2f}" + ("  (paid back)" if kept >= paid else ""),
          ""]

    L.append("INVENTORY LEDGER")
    hdr = f"{'#':<4}{'ITEM':<54}{'STATUS':<13}{'STORE':<7}{'ASK':>10}{'SOLD':>10}{'NET KEPT':>11}  {'SKU':<10}{'LISTING':<14}"
    L += [hdr, "-" * len(hdr)]
    for n, i in enumerate(items, 1):
        t = _report.ascii_text(i["item"])
        t = t if len(t) <= 52 else t[:51] + "~"
        kept_s = "pending" if i["actuals_pending"] else _m(i["net_kept"])
        L.append(f"{n:<4}{t:<54}{i['status']:<13}{i['store']:<7}{_m(i['ask']):>10}"
                 f"{_m(i['gross']):>10}{kept_s:>11}  {i['sku']:<10}{i['listing_id']:<14}")
    L += ["", "QUEUED = offer on eBay, not published. Net kept counts a sale once its ad fee and postage are in."]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# consignment account
# --------------------------------------------------------------------------- #

def _held(r: dict) -> bool:
    """HOLD state, or [HOLD] written in the row's why or state_note."""
    return r.get("state") == "HOLD" or any("[HOLD]" in (r.get(k) or "") for k in ("why", "state_note"))


def consign_account(lot: Path, client: str, today: date) -> tuple[str, list[dict]]:
    import consign as C
    rows, _ = C.read_ledger(lot / C.LEDGER_NAME)
    C.migrate(rows, lot, client)
    registry = C.load_registry()
    terms = C.client_terms(registry, client)
    settlements = C.read_settlements(client)
    summary = C.client_summary([(r.get("intake") or lot.name, r) for r in rows],
                               terms["split"], settlements, terms["loss_rule"], today)
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
         "", "SUMMARY (tools/consign.py report; states as of the last `consign sync --apply`)"]
    L += summary
    held = [r for r in rows if _held(r)]
    if held:
        L += ["", "HELD BACK (marked HOLD; counted in the ledger and totals below)"]
        for r in held:
            why = re.sub(r"\s+", " ", r.get("state_note") or r.get("why") or "")
            L += textwrap.wrap(_report.ascii_text(why), 118, initial_indent=f"  {r['item_id']:<10}",
                               subsequent_indent=" " * 12)
    L += ["", "INVENTORY LEDGER"]
    hdr = f"{'#':<10}{'ITEM':<54}{'QTY':<12}{'PLAN':<10}{'STATE':<14}{'EST / ASK':<13}{'SOLD':>9}{'CLIENT':>9}"
    L += [hdr, "-" * len(hdr)]
    out, fee_out, fees = [], [], []
    fee_lines = C.load_fee_lines()
    for r in rows:
        d = _report.ascii_text((r.get("description") or "").split(";")[0].replace("\n", " "))
        d = d if len(d) <= 52 else d[:51] + "~"
        L.append(f"{r['item_id']:<10}{d:<54}{_report.ascii_text(r.get('qty') or '')[:11]:<12}"
                 f"{plan.get(r.get('cut') or '', '-'):<10}{(r.get('state') or '') + (' HOLD' if _held(r) and r.get('state') != 'HOLD' else ''):<14}{val(r):<13}"
                 f"{r.get('item_price') or '-':>9}{r.get('client_share') or '-':>9}")
        # fees go in their own section after the ledger, one block per sale
        bd = C.fee_breakdown(r, fee_lines)
        if bd:
            fees.append((r, d, bd))
        out.append({"line": "item", "item_id": r["item_id"], "item": r.get("description") or "",
                    "photos": r.get("photos") or "", "why": r.get("why") or "",
                    "state_note": r.get("state_note") or "",
                    "qty": r.get("qty") or "", "plan": plan.get(r.get("cut") or "", ""),
                    "state": r.get("state") or "", "hold": "HOLD" if _held(r) else "", "est_low": r.get("est_low") or "",
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
    return "\n".join(L) + "\n", out + fee_out


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


def _images(row: dict, found: dict[str, Path]) -> list[Path]:
    """Every photo of the item: its own folder first, else the files named in
    the row's photos column."""
    folder = row.get("folder") or ""
    for base in (REPO, INVENTORY):
        d = base / folder
        if folder and d.is_dir():
            shots = sorted(p for p in d.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})
            if shots:
                return shots
    return [found[n] for n in (row.get("photos") or "").split() if n in found]


def _data_uri(src: Path, px: int, quality: int) -> str:
    from PIL import Image, ImageOps
    try:
        with Image.open(src) as im:
            im.draft("RGB", (px * 2, px * 2))
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((px, px))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality)
    except (OSError, ValueError):
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _pictures(rows: list[dict], gallery: dict[str, str]) -> tuple[list[str], list[str]]:
    """Per row: a thumbnail data URI ("" without a photo) and the keys of its
    photos in `gallery`, which holds each distinct photo once so the page and
    its PDF are one self-contained file."""
    wanted = {n for r in rows for n in (r.get("photos") or "").split()}
    found: dict[str, Path] = {}
    if wanted:
        for root, _, files in os.walk(INVENTORY):
            for f in files:
                if f in wanted and f not in found:
                    found[f] = Path(root) / f
    ids: dict[Path, str] = {}
    thumbs, keys = [], []
    for r in rows:
        imgs = _images(r, found)
        thumbs.append(_data_uri(imgs[0], THUMB_PX, 70) if imgs else "")
        ks = []
        for p in imgs:
            if p not in ids:
                uri = _data_uri(p, GALLERY_PX, 60)
                ids[p] = f"i{len(ids)}" if uri else ""
                if uri:
                    gallery[ids[p]] = uri
            if ids[p]:
                ks.append(ids[p])
        keys.append(" ".join(ks))
    return thumbs, keys


def _pop_html(r: dict) -> str:
    """The hover card for one item: every filled field, then its photos."""
    skip = {"line", "item", "photos"}
    dl = "".join(f"<dt>{html.escape(k.replace('_', ' '))}</dt><dd>{html.escape(_report.ascii_text(str(v)))}</dd>"
                 for k, v in r.items() if k not in skip and v not in ("", None))
    return (f'<div class="pop"><b>{html.escape(_report.ascii_text(r.get("item") or ""))}</b>'
            f'<dl>{dl}</dl><div class="g"></div></div>')


def _table_html(header: str, body: list[str], rows: Optional[list[dict]] = None,
                gallery: Optional[dict[str, str]] = None) -> str:
    spans = _split_columns([header] + body)
    cut = lambda line: [html.escape(line[a:b].strip()) for a, b in spans]  # noqa: E731
    num = re.compile(r"^(?:[-$\d,.]+|pending)$")
    cells = [cut(r) for r in body]
    heads = cut(header)
    pics, keys = [""] * len(cells), [""] * len(cells)
    if rows and len(rows) == len(cells):
        # the text cuts titles to fit its columns; the page shows them whole
        if "ITEM" in heads:
            col = heads.index("ITEM")
            for c, r in zip(cells, rows):
                c[col] = html.escape(_report.ascii_text(r.get("item") or "")) or c[col]
        pics, keys = _pictures(rows, gallery if gallery is not None else {})
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
    if rows and len(rows) == len(cells):
        for c, r in zip(cells, rows):
            c[0] += _pop_html(r)
    trs = ""
    for n, row in enumerate(cells):
        trs += f'<tr data-i="{keys[n]}">' if rows and len(rows) == len(cells) else "<tr>"
        trs += "".join(f'<td{" class=n" if right[i] else (" class=t" if heads[i] == "ITEM" else "")}>{c}</td>'
                       for i, c in enumerate(row)) + "</tr>"
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


def html_report(text: str, rows: Optional[list[dict]] = None) -> str:
    """The account as a web page: every all-caps heading line starts a
    section; a header line over a row of dashes becomes a real table and
    everything else stays preformatted, so the figures are the text's."""
    lines = text.rstrip("\n").split("\n")
    title = html.escape(lines[0])
    items = [r for r in (rows or []) if r.get("line", "item") == "item"]
    out, pre, i, gallery = [], [], 1, {}

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
            out.append(_table_html(line, lines[i + 2:j], items if len(lines[i + 2:j]) == len(items) else None, gallery))
            i = j
            continue
        if re.match(r"^[A-Z][A-Z ()/,.\-]{6,}", line) and not line.startswith(" "):
            flush()
            out.append(f"<h2>{html.escape(line)}</h2>")
        else:
            pre.append(line)
        i += 1
    flush()
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body{{font:15px/1.45 system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px;color:#222;background:#fff}}
h1{{font-size:1.25rem;margin:0 0 12px}} h2{{font-size:1rem;margin:22px 0 6px;border-bottom:1px solid #bbb}}
pre{{font:13px/1.4 ui-monospace,monospace;white-space:pre-wrap;margin:6px 0}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:13px}}
td>img{{display:block;width:60px;height:60px;object-fit:cover;border-radius:3px}}
tbody tr[data-i]:hover{{background:#fff8e1}}
.pop{{display:none;position:fixed;z-index:9;width:min(640px,92vw);max-height:80vh;overflow:auto;box-sizing:border-box;
background:#fff;border:1px solid #888;border-radius:6px;box-shadow:0 6px 24px #0004;padding:10px;white-space:normal;text-align:left;font-weight:400}}
tr:hover .pop{{display:block}} .pop dl{{display:grid;grid-template-columns:max-content 1fr;gap:2px 10px;margin:8px 0;font-size:12px}}
.pop dt{{color:#666}} .pop dd{{margin:0;overflow-wrap:anywhere}} .g{{display:flex;flex-wrap:wrap;gap:4px}}
.g img{{height:150px;width:auto;border-radius:3px}}
th,td{{padding:3px 8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}
td{{white-space:nowrap}} td.t{{white-space:normal;min-width:18em}}
th{{background:#f2f2f2}} .n{{text-align:right;white-space:nowrap}}
@media print{{@page{{size:landscape;margin:12mm}}body{{max-width:none;padding:0;font-size:11px}}
table{{font-size:10px}}.pop{{display:none!important}}tr{{break-inside:avoid}}thead{{display:table-header-group}}}}
</style></head><body>
<h1>{title}</h1>
{"".join(out).replace("<table", '<div class="scroll"><table').replace("</table>", "</table></div>")}
<script>
const IMG={json.dumps(gallery)};
document.querySelectorAll("tr[data-i]").forEach(tr=>{{let built=0;tr.addEventListener("mouseenter",()=>{{
const p=tr.querySelector(".pop");if(!p)return;
if(!built){{built=1;const g=p.querySelector(".g");tr.dataset.i.split(" ").forEach(k=>{{if(IMG[k]){{const i=new Image();i.src=IMG[k];g.appendChild(i)}}}})}}
const r=tr.getBoundingClientRect();p.style.left=Math.max(8,Math.min(r.left+70,innerWidth-p.offsetWidth-8))+"px";
p.style.top=Math.max(8,Math.min(r.top,innerHeight-p.offsetHeight-8))+"px"}})}});
</script>
</body></html>
"""


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


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Full account of one lot: item ledger + summary.")
    ap.add_argument("lot", help="lot folder under inventory/, e.g. ES/<LOT> or CONSIGN-YYYYMMDD")
    ap.add_argument("--print", action="store_true", help=f"also print the PDF (the .txt if no PDF) on {PRINTER}")
    args = ap.parse_args(argv)
    today = date.today()
    lot = lot_dir(args.lot)
    if not (lot / "context.txt").is_file():
        print(f"no context.txt in {lot}; a lot is a folder with one", file=sys.stderr)
        return 2
    client = extra_keys(lot).get("client")
    if client:
        with redirect_stdout(io.StringIO()):
            text, rows = consign_account(lot, client, today)
    else:
        keep, left_out = members(lot)
        items = estate_items(lot, keep, left_out)
        text = estate_text(lot, keep, left_out, items, today)
        rows = [{k: ("" if v is None else v) for k, v in i.items()} for i in items]
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
