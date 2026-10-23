#!/usr/bin/env python3
"""consign — the consignment ("client") ledger: what we hold for each client,
the state of each item, what it is worth and what we owe.

    python -m lib.cli consign import CONSIGN-20261022 roster.csv --client K01
    python -m lib.cli consign import CONSIGN-20261022 -        # CSV/TSV on stdin
    python -m lib.cli consign sync                             # dry run, every intake
    python -m lib.cli consign sync CONSIGN-20261022 --apply
    python -m lib.cli consign report --client K01 --csv

Order of work: the finance sync first (`python lib/sync_actuals.py
--all-stores --apply`), then `consign sync`. This tool reads the local
ledgers only. It never calls eBay.

Files
  inventory/CONSIGN-YYYYMMDD/client_ledger.csv   one row per sellable unit
  inventory/CONSIGN-YYYYMMDD/context.txt         `client: K01` names the client
  inventory/_consign/clients.csv                 the client registry; the only
                                                 place a name or contact lives
  reports/consign/<client>-<YYYY-MM-DD>.csv      `report --csv`, no buyer data

States. Hand-entered: INTAKE (the default), PREP (in hand, being prepped,
not listed), RETURNED, DISPOSED (needs a state_note) and EXCLUDED (Russell
buys it; needs a buyout_price, which is the client's share, no split).
Derived by `sync`, each one upgrading the last: SHOT (the folder exists),
DRAFTED (draft.md exists), LISTED (SKU or listing id in a listings ledger),
SOLD (a sales_ledger row), CLEARED (sold, ad fee and postage known, return
window passed), SETTLED (paid_to_client_date set). RETURNED, DISPOSED and
EXCLUDED win over anything derived; PREP stands in for SHOT and DRAFTED but
gives way to LISTED and later. Two cases are flagged in state_note rather
than given a state: a listing that ended unsold, and a sale that has left
the sales ledger (refund or cancel). Once paid_to_client_date is set the
row's client_share is frozen; a recalculation that differs is flagged.

There is no `set` command: edit the CSV by hand, and `sync` validates it.
"""
from __future__ import annotations

import argparse
import csv
import io
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))
import stores  # noqa: E402  the one store model (#156)
import sync_actuals as SA  # noqa: E402  scan_drafts / load_listings_ledger

INVENTORY = REPO / "inventory"
LEDGER_NAME = "client_ledger.csv"
INTAKE_GLOB = "CONSIGN-*"

COLUMNS = [
    "item_id", "bin", "description", "qty", "cut", "why", "photos",
    "inventory_folder", "state", "state_note", "est_low", "est_high",
    "est_source", "store", "sku", "listing_id", "ask", "order_id", "sold_at",
    "item_price", "buyer_shipping", "refunded", "gross", "ebay_fee",
    "net_before_postage", "ad_fee", "actual_postage", "other_costs",
    "other_costs_note", "buyout_price", "net", "client_share", "actuals_complete",
    "cleared_on", "settlement_id", "paid_to_client_date",
]
MONEY = ("est_low", "est_high", "ask", "item_price", "buyer_shipping", "refunded",
         "gross", "ebay_fee", "net_before_postage", "ad_fee", "actual_postage",
         "other_costs", "buyout_price", "net", "client_share")
DATES = ("sold_at", "cleared_on", "paid_to_client_date")
# Copied from the sales ledger row on every sync. ad_fee / actual_postage are
# copied only when the sales ledger knows them: a blank there means "not read
# yet", and a figure typed into the client ledger by hand is kept instead.
SALE_FIELDS = ("order_id", "sold_at", "item_price", "buyer_shipping", "refunded",
               "ebay_fee", "net_before_postage")
SALE_OPTIONAL = ("ad_fee", "actual_postage")

TERMINAL = ("RETURNED", "DISPOSED", "EXCLUDED")
STATES = ("INTAKE", "PREP", "SHOT", "DRAFTED", "LISTED", "SOLD", "CLEARED",
          "SETTLED") + TERMINAL
SOLD_STATES = ("SOLD", "CLEARED", "SETTLED")
ON_HAND = ("INTAKE", "PREP", "SHOT", "DRAFTED")
CUTS = ("", "LIST", "LOT", "RETURN")
# eBay's 30-day return window runs from DELIVERY, but we only know sold_at;
# 10 days covers handling plus transit, so a sale clears 40 days after it sold.
RETURN_WINDOW_DAYS = 40
DEFAULT_SPLIT = Decimal("0.5")
CENT = Decimal("0.01")
FLAG = "FLAG: "
NOTE_SEP = " | "

REGISTRY_COLUMNS = ["client_code", "name", "contact", "split", "terms", "opened"]
CODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CLIENT_RE = re.compile(r"^\s*client\s*:\s*(\S+)\s*$", re.I | re.M)
_KEY_LINE_RE = re.compile(r"^\s*[A-Za-z_]+\s*:\s*\S")

# What `import` accepts beyond the ledger's own column names (roster.csv).
IMPORT_ALIASES = {"item": "description", "note": "why", "notes": "why"}

EXPORT_COLUMNS = [
    "client_code", "intake", "item_id", "bin", "description", "qty", "cut",
    "state", "note", "est_low", "est_high", "listing_id", "ask", "sold_at",
    "item_price", "buyer_shipping", "refunded", "gross", "ebay_fee",
    "net_before_postage", "ad_fee", "actual_postage", "other_costs",
    "other_costs_note", "buyout_price", "net", "client_share", "actuals_complete",
    "cleared_on", "settlement_id", "paid_to_client_date",
]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def dec(v: str) -> Optional[Decimal]:
    """A money cell as Decimal; None when blank. Raises ValueError when bad."""
    s = (v or "").strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(v) from None
    if not d.is_finite():
        raise ValueError(v)
    return d


def money(d: Optional[Decimal]) -> str:
    return "" if d is None else str(d.quantize(CENT, rounding=ROUND_HALF_UP))


def hand_note(note: str) -> str:
    """state_note without the FLAG parts sync writes (it rewrites those)."""
    parts = [p.strip() for p in (note or "").split(NOTE_SEP.strip())]
    return NOTE_SEP.join(p for p in parts if p and not p.startswith(FLAG.strip()))


def norm_folder(folder: str) -> str:
    """Repo-relative, forward slashes, `inventory/` first — the same shape as
    sales_ledger.shoot_dir and scan_drafts()'s `dir`."""
    f = (folder or "").strip().replace("\\", "/").strip("/")
    if f and not f.startswith("inventory/"):
        f = "inventory/" + f
    return f


def intake_dir(name: str) -> Path:
    p = Path(name)
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == "inventory":
        return REPO / p
    return INVENTORY / p


def all_intakes() -> list[Path]:
    return sorted(p.parent for p in INVENTORY.glob(f"{INTAKE_GLOB}/{LEDGER_NAME}"))


def registry_path() -> Path:
    return INVENTORY / "_consign" / "clients.csv"


# --------------------------------------------------------------------------- #
# files
# --------------------------------------------------------------------------- #
def read_ledger(path: Path) -> tuple[list[dict], list[str]]:
    """(rows with every COLUMN present, column names the file has that the
    ledger doesn't know — an old-format file)."""
    if not path.exists():
        return [], []
    with path.open(newline="", encoding="utf-8-sig") as f:
        r = csv.DictReader(f)
        unknown = [c for c in (r.fieldnames or []) if c not in COLUMNS]
        rows = [{c: (row.get(c) or "").strip() for c in COLUMNS} for row in r]
    return rows, unknown


def write_ledger(path: Path, rows: list[dict]) -> Optional[Path]:
    """Write `rows`, after copying the current file to a timestamped backup
    beside it. Returns the backup path (None for a new ledger)."""
    backup = None
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
        backup.write_bytes(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return backup


def intake_client(d: Path) -> str:
    ctx = d / "context.txt"
    if not ctx.exists():
        return ""
    m = _CLIENT_RE.search(ctx.read_text(encoding="utf-8", errors="ignore"))
    return m.group(1) if m else ""


def set_intake_client(d: Path, code: str) -> None:
    """Put `client: <code>` in the intake's context.txt, after its leading
    `key: value` lines. Refuses to change a different client already there."""
    have = intake_client(d)
    if have == code:
        return
    if have:
        raise SystemExit(f"[X] {d.name} already belongs to client {have}, not {code}")
    ctx = d / "context.txt"
    lines = ctx.read_text(encoding="utf-8").splitlines() if ctx.exists() else []
    at = 0
    while at < len(lines) and _KEY_LINE_RE.match(lines[at]):
        at += 1
    lines.insert(at, f"client: {code}")
    ctx.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_registry() -> dict[str, dict]:
    p = registry_path()
    if not p.exists():
        return {}
    with p.open(newline="", encoding="utf-8-sig") as f:
        return {(r.get("client_code") or "").strip(): r for r in csv.DictReader(f)
                if (r.get("client_code") or "").strip()}


def client_split(registry: dict, code: str) -> Decimal:
    raw = (registry.get(code) or {}).get("split") or ""
    bad = SystemExit(f"[X] {registry_path().name}: client {code} split {raw!r} "
                     f"is not a fraction between 0 and 1 (0.5 = 50/50)")
    try:
        s = dec(raw)
    except ValueError:
        raise bad from None
    if s is None:
        return DEFAULT_SPLIT
    if not Decimal(0) <= s <= Decimal(1):
        raise bad
    return s


# --------------------------------------------------------------------------- #
# validation (hand edits)
# --------------------------------------------------------------------------- #
def validate(rows: list[dict], unknown_columns: list[str]) -> list[str]:
    errs = []
    if unknown_columns:
        errs.append(f"unknown column(s) {', '.join(unknown_columns)} — the ledger "
                    f"columns are listed in tools/consign.py COLUMNS")
    seen: set[str] = set()
    for i, r in enumerate(rows, start=2):        # line 1 is the header
        tag = r["item_id"] or f"line {i}"
        if not r["item_id"]:
            errs.append(f"line {i}: no item_id")
        elif r["item_id"] in seen:
            errs.append(f"{tag}: item_id used twice")
        seen.add(r["item_id"])
        st = r["state"].upper()
        if st and st not in STATES:
            errs.append(f"{tag}: state {r['state']!r} is not one of {', '.join(STATES)}")
        if st == "DISPOSED" and not hand_note(r["state_note"]):
            errs.append(f"{tag}: DISPOSED needs a state_note saying how and why")
        if st == "EXCLUDED" and not r["buyout_price"]:
            errs.append(f"{tag}: EXCLUDED needs a buyout_price (0.00 is allowed)")
        if r["cut"].upper() not in CUTS:
            errs.append(f"{tag}: cut {r['cut']!r} is not one of LIST, LOT, RETURN")
        for k in MONEY:
            try:
                dec(r[k])
            except ValueError:
                errs.append(f"{tag}: {k} {r[k]!r} is not an amount")
        for k in DATES:
            if r[k]:
                try:
                    date.fromisoformat(r[k])
                except ValueError:
                    errs.append(f"{tag}: {k} {r[k]!r} is not a YYYY-MM-DD date")
    # One folder, one sellable unit: a sale matched on the folder would land
    # on every row that shares it. Terminal rows no longer sell, so they may.
    sharing: dict[str, list[str]] = {}
    for r in rows:
        if r["inventory_folder"] and r["state"].upper() not in TERMINAL:
            sharing.setdefault(norm_folder(r["inventory_folder"]), []).append(r["item_id"])
    for f, ids in sharing.items():
        if len(ids) > 1:
            errs.append(f"{', '.join(ids)} share {f}: give each its own folder, "
                        f"or blank all but one")
    return errs


# --------------------------------------------------------------------------- #
# the books: every store's ledgers and the local drafts, read once
# --------------------------------------------------------------------------- #
class Books:
    def __init__(self) -> None:
        self.drafts = {d["dir"]: d for d in SA.scan_drafts()}
        self.listings: list[tuple[str, dict]] = []
        self.sales: list[tuple[str, dict]] = []
        for s in stores.configured_stores():
            self.listings += [(s, r) for r in SA.load_listings_ledger(s)]
            self.sales += [(s, r) for r in
                           SA._load_sales_ledger(stores.paths(s).sales_ledger).values()]

    def listing_for(self, sku: str, listing_id: str) -> Optional[tuple[str, dict]]:
        """The listings-ledger row for this item; a live or sold row beats an
        ENDED one (an item ended on one store and relisted on another)."""
        hits = [(s, r) for s, r in self.listings
                if (listing_id and r.get("listing_id") == listing_id)
                or (sku and r.get("sku") == sku)]
        if not hits:
            return None
        return sorted(hits, key=lambda h: (h[1].get("status") != "ENDED",
                                           h[1].get("updated_at") or ""))[-1]

    def sales_for(self, folder: str, sku: str, listing_id: str) -> list[tuple[str, dict]]:
        """Sales ledger rows for this item, newest first. The folder is the
        primary key (shoot_dir); SKU and listing id cover the sales whose
        shoot_dir is blank (everything rebuilt after the 2026-10 wipe)."""
        hits = [(s, r) for s, r in self.sales
                if (folder and norm_folder(r.get("shoot_dir", "")) == folder)
                or (sku and r.get("sku") == sku)
                or (listing_id and r.get("listing_id") == listing_id)]
        return sorted(hits, key=lambda h: h[1].get("sold_at") or "", reverse=True)


# --------------------------------------------------------------------------- #
# money (accountant's rules)
# --------------------------------------------------------------------------- #
def settle_money(r: dict, split: Decimal) -> None:
    """gross, net, client_share and actuals_complete from the row's own
    figures. A blank ad_fee or actual_postage is unknown, not zero: the row
    stays actuals_complete=no with net and client_share blank."""
    price = dec(r["item_price"])
    if price is None:
        r["gross"] = r["net"] = r["client_share"] = r["actuals_complete"] = ""
        return
    z = lambda k: dec(r[k]) or Decimal(0)  # noqa: E731
    r["gross"] = money(price + z("buyer_shipping") - z("refunded"))
    nbp, ad, post = dec(r["net_before_postage"]), dec(r["ad_fee"]), dec(r["actual_postage"])
    if nbp is None or ad is None or post is None:
        r["net"] = r["client_share"] = ""
        r["actuals_complete"] = "no"
        return
    net = nbp - ad - post - z("other_costs")
    r["net"] = money(net)
    r["client_share"] = money(net * split)
    r["actuals_complete"] = "yes"


def provisional_net(r: dict) -> Optional[Decimal]:
    """Net with the unknown costs left out: an upper bound, for the report."""
    nbp = dec(r["net_before_postage"])
    if nbp is None:
        return None
    return nbp - sum((dec(r[k]) or Decimal(0)
                      for k in ("ad_fee", "actual_postage", "other_costs")), Decimal(0))


# --------------------------------------------------------------------------- #
# sync: derive state + fill eBay facts
# --------------------------------------------------------------------------- #
def _freeze_paid(r: dict, paid_share: str, flags: list[str]) -> None:
    """Once the client is paid, client_share is the amount paid. A
    recalculation that differs is flagged with the adjustment, not written."""
    if not paid_share:
        return
    new = r["client_share"]
    r["client_share"] = paid_share
    if dec(new) != dec(paid_share):
        adj = (dec(new) or Decimal(0)) - dec(paid_share)
        flags.append(f"client was paid ${paid_share} on {r['paid_to_client_date']}; "
                     f"recalculated share is ${new or '?'}: adjust by ${money(adj)}")


def sync_row(r: dict, books: Books, split: Decimal, today: date) -> list[str]:
    """Update `r` in place from the books. Returns the flags raised."""
    r["state"] = r["state"].upper() or "INTAKE"
    r["cut"] = r["cut"].upper()
    note = hand_note(r["state_note"])
    flags: list[str] = []
    paid_share = r["client_share"] if r["paid_to_client_date"] else ""
    if r["state"] in TERMINAL:                   # hand-entered terminal wins
        if r["state"] == "EXCLUDED":             # Russell's buyout, no split
            r["client_share"] = money(dec(r["buyout_price"]))
            _freeze_paid(r, paid_share, flags)
        r["state_note"] = NOTE_SEP.join(([note] if note else []) + [FLAG + f for f in flags])
        return flags
    was_prep = r["state"] == "PREP"
    folder = r["inventory_folder"] = norm_folder(r["inventory_folder"])
    state = "INTAKE"
    if folder:
        fdir = REPO / folder
        if fdir.is_dir():
            state = "SHOT"
        else:
            flags.append(f"folder not found: {folder}")
        if (fdir / "draft.md").is_file():
            state = "DRAFTED"
        d = books.drafts.get(folder)
        if d:
            r["sku"] = r["sku"] or d.get("sku", "")
            r["listing_id"] = r["listing_id"] or d.get("listing_id", "")
            r["ask"] = r["ask"] or d.get("price", "")
            if not r["store"] and d.get("store"):
                r["store"] = d["store"]
    listing = books.listing_for(r["sku"], r["listing_id"])
    if listing:
        state = "LISTED"
        lstore, lrow = listing
        r["store"] = lstore
        r["listing_id"] = r["listing_id"] or lrow.get("listing_id", "")
        r["ask"] = lrow.get("price") or r["ask"]

    sales = books.sales_for(folder, r["sku"], r["listing_id"])
    vanished = bool(r["order_id"]) and r["order_id"] not in {s.get("order_id")
                                                            for _, s in sales}
    if vanished:
        # It was sold, and that sale is gone: refunded or cancelled. Keep the
        # old figures as the record; don't adopt any other sale on our own.
        msg = (f"sale {r['order_id']} is no longer in the sales ledger "
               f"(refund or cancel?)")
        if r["paid_to_client_date"]:
            msg += (f"; client was paid ${r['client_share'] or '?'} on "
                    f"{r['paid_to_client_date']}: needs a negative adjustment")
        flags.append(msg)
        sales = []
    elif r["order_id"]:                          # already pinned to its sale
        sales = [h for h in sales if h[1].get("order_id") == r["order_id"]]
    elif len(sales) > 1:
        flags.append(f"{len(sales)} sales match this item; using the newest, "
                     f"{sales[0][1].get('order_id')}")
    sale = sales[0] if sales else None
    if sale:
        state = "SOLD"
        sstore, srow = sale
        r["store"] = sstore
        for k in SALE_FIELDS:
            r[k] = srow.get(k, "") or ""
        for k in SALE_OPTIONAL:
            if (srow.get(k) or "").strip():
                r[k] = srow[k].strip()
        r["listing_id"] = r["listing_id"] or srow.get("listing_id", "")
    elif listing and listing[1].get("status") == "ENDED":
        flags.append(f"listing {r['listing_id'] or r['sku']} ended unsold: "
                     f"relist, return or dispose")
    elif listing and listing[1].get("status") == "SOLD" and not vanished:
        flags.append("the listings ledger says SOLD but no sales ledger row matches: "
                     "run the finance sync (lib/sync_actuals.py --all-stores --apply)")

    settle_money(r, split)
    _freeze_paid(r, paid_share, flags)
    r["cleared_on"] = ""
    if sale and r["actuals_complete"] == "yes" and r["sold_at"]:
        window_end = date.fromisoformat(r["sold_at"]) + timedelta(days=RETURN_WINDOW_DAYS)
        if window_end <= today:
            state = "CLEARED"
            r["cleared_on"] = window_end.isoformat()
    if sale and r["paid_to_client_date"]:
        state = "SETTLED"
    if was_prep and state in ("INTAKE", "SHOT", "DRAFTED"):
        state = "PREP"                           # PREP is the on-hand label until listed
    r["state"] = state
    r["state_note"] = NOTE_SEP.join(([note] if note else []) + [FLAG + f for f in flags])
    return flags


def sync_intake(d: Path, books: Books, registry: dict, *, apply: bool,
                today: Optional[date] = None) -> int:
    today = today or date.today()
    path = d / LEDGER_NAME
    rows, unknown = read_ledger(path)
    code = intake_client(d)
    print(f"\n{d.name}  client {code or '(none: add `client:` to context.txt)'}"
          f"  {len(rows)} item(s)")
    errs = validate(rows, unknown)
    if errs:
        for e in errs:
            print(f"  [X] {e}")
        print(f"  [X] fix {path.name} by hand; nothing synced for this intake")
        return 1
    split = client_split(registry, code)
    before = [dict(r) for r in rows]
    nflags = 0
    for r in rows:
        nflags += len(sync_row(r, books, split, today))
    changed = 0
    for old, new in zip(before, rows):
        diff = [k for k in COLUMNS if old[k] != new[k] and k not in ("state", "state_note")]
        flags = [p for p in new["state_note"].split(NOTE_SEP) if p.startswith(FLAG)]
        if old != new:
            changed += 1
            move = (f"{old['state'] or 'INTAKE'} -> {new['state']}"
                    if old["state"] != new["state"] else new["state"])
            print(f"  {new['item_id']:<6} {move}"
                  + (f"  ({', '.join(diff)})" if diff else ""))
        for fl in flags:
            print(f"  ! {new['item_id']:<6} {fl}")
    print(f"  {changed} row(s) change, {nflags} flag(s)")
    if not apply:
        return 0
    if changed:
        backup = write_ledger(path, rows)
        print(f"  [OK] wrote {path.name}" + (f" (backup {backup.name})" if backup else ""))
    return 0


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #
def parse_table(text: str) -> list[dict]:
    """CSV or TSV (tab in the header line) -> rows keyed by ledger column."""
    text = text.lstrip("﻿")
    if not text.strip():
        return []
    head = text.splitlines()[0]
    reader = csv.DictReader(io.StringIO(text), delimiter="\t" if "\t" in head else ",")
    keymap = {}
    for raw in reader.fieldnames or []:
        k = (raw or "").strip().lower().replace(" ", "_")
        k = IMPORT_ALIASES.get(k, k)
        if k in COLUMNS and k != "item_id":
            keymap[raw] = k
    if "description" not in keymap.values():
        raise SystemExit("[X] the input needs a header row with a `description` "
                         "(or `item`) column")
    out = []
    for row in reader:
        rec = {c: "" for c in COLUMNS}
        for raw, k in keymap.items():
            rec[k] = (row.get(raw) or "").strip()
        if rec["description"]:
            out.append(rec)
    return out


def next_id(rows: list[dict]) -> int:
    nums = [int(m.group(1)) for r in rows if (m := re.fullmatch(r"C(\d+)", r["item_id"]))]
    return max(nums, default=0) + 1


def import_rows(d: Path, incoming: list[dict], client: Optional[str]) -> tuple[int, int]:
    """Append `incoming` to the intake's ledger. Returns (added, skipped)."""
    if client:
        if not CODE_RE.match(client):
            raise SystemExit(f"[X] client code {client!r}: letters, digits, - and _ only")
        d.mkdir(parents=True, exist_ok=True)
        set_intake_client(d, client)
    elif not intake_client(d):
        raise SystemExit(f"[X] {d.name} has no `client:` yet; pass --client K01")
    path = d / LEDGER_NAME
    rows, unknown = read_ledger(path)
    if unknown:
        raise SystemExit(f"[X] {path} has unknown column(s) {', '.join(unknown)}")
    seen = {(r["bin"], r["description"]) for r in rows}
    n = next_id(rows)
    added = skipped = 0
    for rec in incoming:
        key = (rec["bin"], rec["description"])
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        rec["item_id"] = f"C{n:03d}"
        rec["state"] = rec["state"] or "INTAKE"
        rec["inventory_folder"] = norm_folder(rec["inventory_folder"])
        rows.append(rec)
        n += 1
        added += 1
    if added:
        write_ledger(path, rows)
    return added, skipped


# --------------------------------------------------------------------------- #
# report
# --------------------------------------------------------------------------- #
def _sum(vals) -> Decimal:
    return sum((v for v in vals if v is not None), Decimal(0))


def client_summary(items: list[tuple[str, dict]], split: Decimal) -> list[str]:
    rows = [r for _, r in items]
    by_state = Counter(r["state"] or "INTAKE" for r in rows)
    out = ["  by state: " + " · ".join(f"{s} {by_state[s]}" for s in STATES if by_state[s])]

    sold = [r for r in rows if r["state"] in SOLD_STATES]
    incomplete = [r for r in sold if r["actuals_complete"] != "yes"]
    sold_net = _sum(dec(r["net"]) if r["actuals_complete"] == "yes" else provisional_net(r)
                    for r in sold)
    listed = [r for r in rows if r["state"] == "LISTED"]
    listed_ask = [dec(r["ask"]) for r in listed]
    hand = [r for r in rows if (r["state"] or "INTAKE") in ON_HAND]
    lows, highs, unpriced = [], [], 0
    for r in hand:
        lo, hi = dec(r["est_low"]), dec(r["est_high"])
        if lo is None and hi is None:
            unpriced += 1
            continue
        lows.append(lo if lo is not None else hi)
        highs.append(hi if hi is not None else lo)
    gone = by_state["RETURNED"] + by_state["DISPOSED"]
    excluded = [r for r in rows if r["state"] == "EXCLUDED"]

    out.append("  value:")
    out.append(f"    sold     {len(sold):>3}  net ${money(sold_net)}"
               + (f"  ({len(incomplete)} provisional: ad fee or postage not known yet)"
                  if incomplete else ""))
    out.append(f"    listed   {len(listed):>3}  at ask ${money(_sum(listed_ask))}"
               + (f"  (unpriced {sum(a is None for a in listed_ask)})"
                  if any(a is None for a in listed_ask) else ""))
    out.append(f"    on hand  {len(hand):>3}  est ${money(_sum(lows))}–${money(_sum(highs))}"
               + (f"  (unpriced {unpriced})" if unpriced else ""))
    if excluded:
        out.append(f"    excluded {len(excluded):>3}  Russell buys at "
                   f"${money(_sum(dec(r['buyout_price']) for r in excluded))}")
    if gone:
        out.append(f"    returned/disposed {gone}")

    owed = _sum(dec(r["client_share"]) for r in rows
                if r["state"] == "CLEARED" and not r["paid_to_client_date"])
    pending_rows = [r for r in rows if r["state"] == "SOLD"]
    def pending_share(r):
        if r["actuals_complete"] == "yes":
            return dec(r["client_share"])
        pn = provisional_net(r)
        return None if pn is None else pn * split
    pending = _sum(pending_share(r) for r in pending_rows)
    buyouts_owed = [r for r in excluded if not r["paid_to_client_date"]]
    paid = _sum(dec(r["client_share"]) for r in rows if r["paid_to_client_date"])
    out.append("  totals:")
    out.append(f"    sold gross ${money(_sum(dec(r['gross']) for r in sold))}"
               f"  ·  net ${money(sold_net)}" + ("  (provisional)" if incomplete else ""))
    out.append(f"    client share owed     ${money(owed)}  (CLEARED, not paid)")
    if excluded:
        out.append(f"    buyouts owed          "
                   f"${money(_sum(dec(r['client_share']) for r in buyouts_owed))}  "
                   f"(EXCLUDED, Russell buys, not paid: {len(buyouts_owed)})")
    out.append(f"    client share pending  ${money(pending)}  (SOLD, return window "
               f"or actuals still open)"
               + ("  (upper bound)" if any(r in incomplete for r in pending_rows) else ""))
    out.append(f"    paid to date          ${money(paid)}")
    flagged = [r["item_id"] for r in rows if FLAG in r["state_note"]]
    if flagged:
        out.append(f"  flagged ({len(flagged)}): {', '.join(flagged)}  — see `consign sync`")
    return out


def export_rows(code: str, items: list[tuple[str, dict]]) -> list[dict]:
    out = []
    for intake, r in items:
        rec = {"client_code": code, "intake": intake, "note": hand_note(r["state_note"])}
        for k in EXPORT_COLUMNS:
            if k in rec:
                continue
            v = r.get(k, "")
            rec[k] = money(dec(v)) if k in MONEY else v
        rec["state"] = rec["state"] or "INTAKE"
        out.append(rec)
    return out


def report(intakes: list[Path], client: Optional[str], write_csv: bool) -> int:
    registry = load_registry()
    groups: dict[str, list[tuple[str, dict]]] = {}
    names: dict[str, list[str]] = {}
    for d in intakes:
        code = intake_client(d) or "UNASSIGNED"
        if client and code != client:
            continue
        rows, _ = read_ledger(d / LEDGER_NAME)
        groups.setdefault(code, []).extend((d.name, r) for r in rows)
        names.setdefault(code, []).append(d.name)
    if not groups:
        print("no consignment intakes match" + (f" client {client}" if client else ""))
        return 1
    print("States are as of the last `consign sync --apply`.")
    for code in sorted(groups):
        reg = registry.get(code) or {}
        split = client_split(registry, code)
        items = groups[code]
        print(f"\n=== {code}  {reg.get('name') or '(not in the registry)'}  "
              f"split {split * 100:.0f}% to client  ·  {len(items)} item(s) in "
              f"{', '.join(names[code])}")
        for line in client_summary(items, split):
            print(line)
        if write_csv:
            out = REPO / "reports" / "consign" / f"{code}-{date.today().isoformat()}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
                w.writeheader()
                w.writerows(export_rows(code, items))
            print(f"  [OK] wrote {SA._rel(out)}")
    return 0


# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="ebz consign", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import", help="bulk-add rows from a CSV/TSV file, or - for stdin")
    p.add_argument("intake", help="intake folder, e.g. CONSIGN-20261022")
    p.add_argument("source", help="CSV or TSV file with a `description` column, or -")
    p.add_argument("--client", help="client code (e.g. K01); needed for a new intake")
    p = sub.add_parser("sync", help="derive states and fill eBay figures from the ledgers")
    p.add_argument("intake", nargs="?", help="one intake (default: every intake)")
    p.add_argument("--apply", action="store_true", help="write the ledger (backup first)")
    p = sub.add_parser("report", help="per-client account: states, value, money owed")
    p.add_argument("intake", nargs="?", help="one intake (default: every intake)")
    p.add_argument("--client", help="only this client")
    p.add_argument("--csv", action="store_true",
                   help="also write reports/consign/<client>-<date>.csv")
    args = ap.parse_args(argv)

    if args.cmd == "import":
        text = (sys.stdin.read() if args.source == "-"
                else Path(args.source).read_text(encoding="utf-8-sig"))
        d = intake_dir(args.intake)
        added, skipped = import_rows(d, parse_table(text), args.client)
        print(f"{d.name}: added {added} row(s), skipped {skipped} duplicate(s)")
        if args.client and args.client not in load_registry():
            print(f"  ! {args.client} is not in {SA._rel(registry_path())} yet: add a row "
                  f"(split defaults to 0.5 until then)")
        return 0

    intakes = [intake_dir(args.intake)] if args.intake else all_intakes()
    missing = [d for d in intakes if not (d / LEDGER_NAME).exists()]
    if missing:
        raise SystemExit(f"[X] no {LEDGER_NAME} in {', '.join(str(d) for d in missing)}")
    if args.cmd == "report":
        return report(intakes, args.client, args.csv)

    books, registry = Books(), load_registry()
    worst = max((sync_intake(d, books, registry, apply=args.apply) for d in intakes),
                default=0)
    if not args.apply:
        print(f"\n[DRY RUN] Nothing written. Re-run with --apply to write "
              f"{LEDGER_NAME} (a timestamped backup is made first).")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
