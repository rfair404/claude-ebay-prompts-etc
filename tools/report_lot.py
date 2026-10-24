#!/usr/bin/env python3
"""report_lot — the full account of one lot: every item it brought in, where
each one stands, and a money summary.

    python tools/report_lot.py ES/MON1020-M            # estate buy
    python tools/report_lot.py CONSIGN-20261022        # consignment intake
    python tools/report_lot.py ES/MON1020-M --print    # also print on CannonBW

Writes reports/lots/<lot>-<date>.txt (plain text, for printing) and .csv (one
row per item) and prints the .txt. Reads local ledgers only, never eBay, so
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
ENDED, QUEUED (an offer on eBay, not published), DRAFT, PHOTOS ONLY.
Net kept per sale = net_before_postage - ad_fee - actual_postage, counted only
once both actuals are in (the accountant's 2026-10-22 ruling); until then the
sale shows as "actuals pending".
"""
from __future__ import annotations

import argparse
import csv
import io
import re
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
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".nef", ".orf", ".heic"}
_LINE_RE = re.compile(r"^\s*(client|part_of|note)\s*:\s*(.*?)\s*$", re.I)
STATUS_ORDER = ["SOLD", "LIVE", "ENDED", "QUEUED", "DRAFT", "PHOTOS ONLY"]


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
    """Shallowest folders under `bucket` holding a draft or photos. Doesn't
    descend into another member or left-out bucket (`stop`)."""
    def has_item(d: Path) -> bool:
        try:
            return any(f.is_file() and (f.name in ("draft.md", "draft_group.md")
                                         or f.suffix.lower() in PHOTO_EXT)
                       for f in d.iterdir())
        except OSError:
            return False

    if has_item(bucket):
        return [bucket]
    out = []
    for d in sorted(p for p in bucket.iterdir() if p.is_dir()):
        if _skip(d) or d in stop:
            continue
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
            status = "PHOTOS ONLY"
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
        t = i["item"] if len(i["item"]) <= 52 else i["item"][:51] + "~"
        kept_s = "pending" if i["actuals_pending"] else _m(i["net_kept"])
        L.append(f"{n:<4}{t:<54}{i['status']:<13}{i['store']:<7}{_m(i['ask']):>10}"
                 f"{_m(i['gross']):>10}{kept_s:>11}  {i['sku']:<10}{i['listing_id']:<14}")
    L += ["", "QUEUED = offer on eBay, not published. Net kept counts a sale once its ad fee and postage are in."]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- #
# consignment account
# --------------------------------------------------------------------------- #

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
        if r.get("ask"):
            return "ask $" + r["ask"]
        if r.get("est_low"):
            return f"${r['est_low']}-{r.get('est_high') or '?'}"
        return "-"

    L = [f"LOT ACCOUNT - {label(lot)}   consignment, client {client} {name}   (as of {today.isoformat()})",
         f"Received: {C.folder_date(lot) or C.default_intake(lot) or 'not recorded'}   Split {terms['split'] * 100:.0f}% of net to client",
         "", "SUMMARY (tools/consign.py report; states as of the last `consign sync --apply`)"]
    L += summary
    L += ["", "INVENTORY LEDGER"]
    hdr = f"{'#':<10}{'ITEM':<54}{'QTY':<12}{'PLAN':<10}{'STATE':<10}{'EST / ASK':<13}{'SOLD':>9}{'CLIENT':>9}"
    L += [hdr, "-" * len(hdr)]
    out = []
    for r in rows:
        d = (r.get("description") or "").split(";")[0].replace("\n", " ")
        d = d if len(d) <= 52 else d[:51] + "~"
        L.append(f"{r['item_id']:<10}{d:<54}{(r.get('qty') or '')[:11]:<12}"
                 f"{plan.get(r.get('cut') or '', '-'):<10}{r.get('state') or '':<10}{val(r):<13}"
                 f"{r.get('item_price') or '-':>9}{r.get('client_share') or '-':>9}")
        out.append({"item_id": r["item_id"], "item": r.get("description") or "",
                    "qty": r.get("qty") or "", "plan": plan.get(r.get("cut") or "", ""),
                    "state": r.get("state") or "", "est_low": r.get("est_low") or "",
                    "est_high": r.get("est_high") or "", "ask": r.get("ask") or "",
                    "buyout_price": r.get("buyout_price") or "", "store": r.get("store") or "",
                    "sku": r.get("sku") or "", "listing_id": r.get("listing_id") or "",
                    "sold_at": r.get("sold_at") or "", "item_price": r.get("item_price") or "",
                    "net": r.get("net") or "", "client_share": r.get("client_share") or "",
                    "folder": r.get("inventory_folder") or ""})
    return "\n".join(L) + "\n", out


# --------------------------------------------------------------------------- #

def write(lot: Path, text: str, rows: list[dict], today: date) -> tuple[Path, Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = label(lot).replace("/", "_") + f"-{today.isoformat()}"
    txt, csv_path = OUT_DIR / f"{stem}.txt", OUT_DIR / f"{stem}.csv"
    txt.write_text(text, encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        if rows:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return txt, csv_path


def print_txt(path: Path) -> str:
    """Landscape at 12 cpi so the ~130-column ledger fits one line per item."""
    res = subprocess.run(["lp", "-d", PRINTER, "-o", "landscape", "-o", "cpi=12",
                          "-o", "lpi=7", "-o", "page-left=36", "-o", "page-top=36",
                          "-t", path.stem, str(path)], capture_output=True, text=True)
    return (res.stdout or res.stderr).strip()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Full account of one lot: item ledger + summary.")
    ap.add_argument("lot", help="lot folder under inventory/, e.g. ES/MON1020-M or CONSIGN-20261022")
    ap.add_argument("--print", action="store_true", help=f"also print the .txt on {PRINTER}")
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
    sys.stdout.write(text)
    print(f"\nWROTE {txt.relative_to(REPO)}  {csv_path.relative_to(REPO)}")
    if args.print:
        print("PRINTED " + print_txt(txt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
