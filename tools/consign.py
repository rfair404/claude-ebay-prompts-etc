#!/usr/bin/env python3
"""consign — the consignment ("client") ledger: what we hold for each client,
the state of each item, what it is worth and what we owe.

    python -m lib.cli consign import CONSIGN-K01 roster.csv --client K01 --intake 2026-11-05
    python -m lib.cli consign import CONSIGN-K01 -             # CSV/TSV on stdin
    python -m lib.cli consign sync                             # dry run, every ledger
    python -m lib.cli consign sync CONSIGN-K01 --apply
    python -m lib.cli consign report --client K01 --csv
    python -m lib.cli consign settle K01 --through 2026-11-30  # dry run; --apply writes
    python -m lib.cli consign settle K01 --through 2026-11-30 --final  # agreement ended

Order of work: the finance sync first (`python lib/sync_actuals.py
--all-stores --apply`), then `consign sync`. This tool reads the local
ledgers only. It never calls eBay.

Files
  inventory/CONSIGN-<client>/client_ledger.csv   one ledger per client, one row
                                                 per sellable unit, every intake
  inventory/CONSIGN-YYYYMMDD/client_ledger.csv   the older one-folder-per-intake
                                                 layout; its context.txt says
                                                 `client: K01`. Still read.
  inventory/_consign/clients.csv                 the client registry; the only
                                                 place a name or contact lives
  inventory/_consign/<client>/settlements.csv    one row per payout
  reports/consign/<client>/<settlement_id>.csv   a payout's statement, written once
  reports/consign/<client>/<client>-<date>.csv   `report --csv`, no buyer data
  reports/fee_lines*.csv                         per-line eBay fees (sync_actuals),
                                                 read for the fee breakdown

Fee breakdown. The statement, the report and tools/report_lot.py show each
sale's fees line by line, every % marked SOURCED (eBay's campaign bid, or the
agreement's mall terms), DERIVED (fee / base, worked out here) or FLAT. An
eBay sale whose fee lines are missing or don't add up to its ebay_fee and
ad_fee is held back from settle until they do.

States. Hand-entered: INTAKE (the default), PREP (in hand, being prepped,
not listed), HOLD (in hand, held back on purpose), RETURNED, DISPOSED
(needs a state_note) and EXCLUDED (Russell buys it; needs a buyout_price,
which is the client's share, no split; a 0.00 buyout is closed with nothing
owed).
Derived by `sync`, each one upgrading the last: SHOT (the folder exists),
DRAFTED (draft.md exists), LISTED (live on eBay: the SKU or listing id has
a listings-ledger row PUBLISHED, OUT_OF_STOCK, SOLD or SHIPPED; DRAFTED and
SYNCED are not live, and an ENDED row, or a DELETED one that was published,
leaves the item in its on-hand state; off eBay, placed_at is typed: on the
mall floor), SOLD (a sales_ledger row), CLEARED
(sold, ad fee and postage known, and past the later of sold_at + 40 days and
delivered_at + 30 days), SETTLED (paid_to_client_date set, copied from a
paid settlement). RETURNED, DISPOSED and EXCLUDED win over anything
derived; PREP stands in for SHOT and DRAFTED but gives way to LISTED and
later. HOLD (needs a state_note) stands in like PREP, ignores an ENDED
listing, stays HOLD with a flag if a live listing matches, and gives way only
to a sale. Two cases are flagged in state_note rather than given a state: a
listing that ended without a sale, and a sale that has left the sales ledger
(refund or cancel). Once
paid_to_client_date is set the row's client_share is frozen; a
recalculation that differs is flagged and settled as an adjustment.

Money. net = net_before_postage - ad_fee - actual_postage - handling -
other_costs, and client_share = split x net. handling is a flat fee per
sale for packing materials (clients.csv handling_fee; blank = no fee),
stamped on the row when it sells and kept from then on, like split_locked.
A loss (net below zero) follows clients.csv loss_rule: split, client_never_owes
or client_bears_loss (the client carries all of it).

Channels. A blank channel is eBay, read from the sales ledgers. Any other
channel is a sale typed by hand (sold_at, item_price, ebay_fee for that
channel's fee, actual_postage) that sync never overwrites or calls vanished;
it clears at the end of the month it sold, once funds_received (typed when
the money reaches us) is set, and never before. channel `mall` is the antique-mall booth:
type sold_at, item_price and card_paid, and sync works out the fee (10% plus
3% of card_paid), with no postage. Every sold row pays handling, whatever
the channel. Off eBay, listing_id is the channel's own item id (the mall's
GAM SKU) and is never looked up in the eBay ledgers; type it with placed_at
(the date it went on the floor) and the row is LISTED until it sells.

Set B flags (no state change): an ask or sale below the client's floor (a
lot's floor is its rows' total), a listing live longer than listing_days
(180), and an unsold item past return_notice_sent + pickup_days (30).
settle holds a balance under clients.csv min_payout, except buyouts, which
are paid in the next run (§7.2); --final pays it all.

Lots. Rows with the same lot_id are one listing and share one
inventory_folder. The lead row (lowest item_id) carries the sale and the
client share; its lot-mates follow its state with blank money fields.

There is no `set` command: edit the CSV by hand, and `sync` validates it.
"""
from __future__ import annotations

import argparse
import calendar
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
import offsite  # noqa: E402  data_root(): the main checkout
import stores  # noqa: E402  the one store model (#156)
import sync_actuals as SA  # noqa: E402  scan_drafts / load_listings_ledger
from report import ascii_text  # noqa: E402  report text is plain ASCII

INVENTORY = REPO / "inventory"
LEDGER_NAME = "client_ledger.csv"
SETTLEMENTS_NAME = "settlements.csv"
INTAKE_PREFIX = "CONSIGN-"
INTAKE_GLOB = INTAKE_PREFIX + "*"

COLUMNS = [
    "item_id", "bin", "description", "qty", "cut", "why", "photos",
    "inventory_folder", "state", "state_note", "est_low", "est_high",
    "est_source", "store", "sku", "listing_id", "ask", "order_id", "sold_at",
    "item_price", "buyer_shipping", "refunded", "gross", "ebay_fee",
    "net_before_postage", "ad_fee", "actual_postage", "other_costs",
    "other_costs_note", "buyout_price", "net", "client_share", "actuals_complete",
    "cleared_on", "settlement_id", "paid_to_client_date",
    # added for the per-client ledger (#284); an older file without them loads
    # with them blank, and the next write fills them
    "client_code", "intake", "lot_id", "split_locked", "delivered_at",
    # the per-order handling fee (boxes, fill, tape, label), set when it sells
    "handling",
    # added for the Set B terms (PR #286, 2026-10-23); optional, blank on an
    # older file. channel: blank = ebay, anything else is a sale typed by hand.
    # floor: the client's lowest price (Exhibit A). return_notice_sent: the
    # date the client was told to pick an unsold item up, typed by hand.
    # card_paid: on a mall sale, the part the buyer paid by card.
    # funds_received: off eBay, the date the channel's money reached us.
    "channel", "floor", "return_notice_sent", "card_paid", "funds_received",
    # placed_at: off eBay, the date the item went on the channel's floor (the
    # mall booth), with that channel's own item id (the GAM SKU) in listing_id.
    "placed_at",
]
MONEY = ("est_low", "est_high", "ask", "item_price", "buyer_shipping", "refunded",
         "gross", "ebay_fee", "net_before_postage", "ad_fee", "actual_postage",
         "other_costs", "handling", "buyout_price", "net", "client_share", "floor",
         "card_paid")
# Blank on a lot-mate: the lead row carries the lot's sale. The estimates and
# the floor stay, since they describe the item itself.
SALE_MONEY = tuple(k for k in MONEY
                   if k not in ("est_low", "est_high", "buyout_price", "floor"))
DATES = ("sold_at", "cleared_on", "paid_to_client_date", "intake", "delivered_at",
         "return_notice_sent", "funds_received", "placed_at")
# Copied from the sales ledger row on every sync. ad_fee / actual_postage are
# copied only when the sales ledger knows them: a blank there means "not read
# yet", and a figure typed into the client ledger by hand is kept instead.
SALE_FIELDS = ("order_id", "sold_at", "item_price", "buyer_shipping", "refunded",
               "ebay_fee", "net_before_postage")
SALE_OPTIONAL = ("ad_fee", "actual_postage")
# What a lot-mate copies from its lead, besides the state.
LOT_COPY = ("store", "sku", "listing_id", "sold_at", "cleared_on", "channel",
            "placed_at")
# A row's channel: blank is eBay, matched against the sales ledgers. Any other
# channel (booth, dealer, another marketplace) is a sale typed by hand:
# channel, sold_at, item_price, ebay_fee (that channel's fee), actual_postage
# and other_costs, plus order_id if it has one. sync never overwrites those
# from the eBay ledgers. net_before_postage = gross - ebay_fee there.
EBAY = "ebay"
CHANNEL_RE = re.compile(r"^[a-z0-9_-]+$")
# The antique-mall booth, where every consigned antique sells. Its fee is
# worked out, not typed: MALL_COMMISSION of item_price plus MALL_CARD_RATE of
# card_paid (the part paid by card; 0.00 for cash, blank = unknown). No
# postage; the handling fee still applies (labels and data entry, Russell
# 2026-10-23). These must match the consignment agreement
# §4.2(b) (docs/client-onboarding/consignment-agreement.md): change both together.
# The fee breakdown labels a mall % from these, citing AGREEMENT_CITE;
# tests/test_consign.py reads the agreement and fails if the two drift apart.
MALL = "mall"
MALL_COMMISSION = Decimal("0.10")
MALL_CARD_RATE = Decimal("0.03")
AGREEMENT_CITE = "consignment agreement sec. 4.2(b)"

TERMINAL = ("RETURNED", "DISPOSED", "EXCLUDED")
# Listings-ledger statuses (lib/list_edit.upsert_listing, lib/sync_actuals)
# that mean the item has been live on eBay. DRAFTED and SYNCED never were.
# DELETED counts only with a published_at, and then as ENDED (listing_status).
LIVE_LISTING = ("PUBLISHED", "OUT_OF_STOCK", "SOLD", "SHIPPED", "ENDED")
STATES = ("INTAKE", "PREP", "HOLD", "SHOT", "DRAFTED", "LISTED", "SOLD",
          "CLEARED", "SETTLED") + TERMINAL
SOLD_STATES = ("SOLD", "CLEARED", "SETTLED")
ON_HAND = ("INTAKE", "PREP", "HOLD", "SHOT", "DRAFTED")
CUTS = ("", "LIST", "LOT", "RETURN")
# A sale clears at the later of RETURN_WINDOW_DAYS after it sold and
# DELIVERY_WINDOW_DAYS after delivery (eBay's 30-day return window runs from
# delivery). The sales ledger has no delivery date, so delivered_at is typed
# by hand; without it, 40 days from the sale (10 for handling and transit).
RETURN_WINDOW_DAYS = 40
DELIVERY_WINDOW_DAYS = 30
# Set B: 180 days listed, then 30 days to pick up (clients.csv listing_days
# and pickup_days set them per client).
DEFAULT_LISTING_DAYS = 180
DEFAULT_PICKUP_DAYS = 30
DEFAULT_SPLIT = Decimal("0.5")
# Taken from net before the split, like postage, once per sale (a lot is one
# sale), on every channel: the mall booth too (labels and data entry). The
# standard fee is $2.00 (agreement §4.6): packing materials run about $1.48
# for a small box and $2.63 for a medium one at 2026-10 Uline/Brother prices,
# and packing time is paid by our side of the split. It is opt-in: a client
# pays it only once clients.csv handling_fee is set, which happens when their
# signed agreement names the fee. A figure typed into a row's handling column
# is kept (an oversize box).
DEFAULT_HANDLING = Decimal("0")
CENT = Decimal("0.01")
FLAG = "FLAG: "
NOTE_SEP = " | "

REGISTRY_COLUMNS = ["client_code", "name", "contact", "split", "terms", "opened",
                    "agreement_signed", "w9_on_file", "payee_type", "payout_method",
                    "payout_cadence", "floor_main", "floor_junk", "loss_rule",
                    "abandon_election", "handling_fee", "min_payout", "listing_days",
                    "pickup_days"]
# How a sale whose net is below zero is shared:
# split: the client carries their split of the loss too (the default).
# client_never_owes: the sale pays the client $0, never less.
# client_bears_loss: the client carries the whole loss, not split (Set B: "a
#   below-zero sale is charged back to the client in full").
LOSS_RULES = ("split", "client_never_owes", "client_bears_loss")
CODE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_CLIENT_RE = re.compile(r"^\s*client\s*:\s*(\S+)\s*$", re.I | re.M)
_ACQUIRED_RE = re.compile(r"^\s*acquired\s*:\s*(\d{4}-\d{2}-\d{2})\b", re.I | re.M)
_KEY_LINE_RE = re.compile(r"^\s*[A-Za-z_]+\s*:\s*\S")
_DATE_NAME_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})$")
_ID_RE = re.compile(r"^(?:(?P<code>.+)-)?C(?P<n>\d+)$")
_LOT_NOTE_RE = re.compile(r"^in lot \S+ with \S+$")
_FORMAT_RE = re.compile(r'^format:\s*"?([A-Za-z_]+)"?', re.M)

SETTLEMENT_COLUMNS = [
    "settlement_id", "client_code", "period_start", "period_end", "item_ids",
    "gross", "fees", "ad", "postage", "other", "net", "client_share",
    "adjustments", "amount_paid", "paid_date", "method", "receipt_ref", "client_ack",
    "handling",
]
# A line=fee row's own fields. Its amount goes in fee_amount, never in
# ebay_fee or ad_fee: those are the sale row's totals, and a fee row there
# would count the fee twice when the column is summed.
FEE_COLUMNS = ["fee_type", "fee_amount", "fee_base", "fee_rate", "rate_source"]
STATEMENT_COLUMNS = [
    "settlement_id", "client_code", "line", "item_id", "intake", "description",
    "lot_id", "sold_at", "delivered_at", "cleared_on", "item_price", "buyer_shipping",
    "refunded", "gross", "ebay_fee", "ad_fee", "actual_postage", "handling",
    "other_costs", "net", "split", "client_share", "amount", "note", "channel",
    # line=fee rows: the per-item fee breakdown (fee_breakdown)
    *FEE_COLUMNS,
]

# What `import` accepts beyond the ledger's own column names (roster.csv).
IMPORT_ALIASES = {"item": "description", "note": "why", "notes": "why"}

EXPORT_COLUMNS = [
    "client_code", "intake", "item_id", "lot_id", "bin", "description", "qty", "cut",
    "state", "note", "est_low", "est_high", "listing_id", "ask", "sold_at",
    "delivered_at", "item_price", "buyer_shipping", "refunded", "gross", "ebay_fee",
    "net_before_postage", "ad_fee", "actual_postage", "handling", "other_costs",
    "other_costs_note", "buyout_price", "net", "split_locked", "client_share",
    "actuals_complete", "cleared_on", "settlement_id", "paid_to_client_date",
    "channel", "floor", "return_notice_sent", "card_paid", "funds_received",
    "placed_at",
    # line is "item", or "fee" for a row of the item's fee breakdown
    "line", *FEE_COLUMNS,
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
    """state_note without the parts sync writes (FLAGs and the lot-mate note);
    it rewrites those."""
    parts = [p.strip() for p in (note or "").split(NOTE_SEP.strip())]
    return NOTE_SEP.join(p for p in parts if p and not p.startswith(FLAG.strip())
                         and not _LOT_NOTE_RE.match(p))


def norm_folder(folder: str) -> str:
    """Repo-relative, forward slashes, `inventory/` first — the same shape as
    sales_ledger.shoot_dir and scan_drafts()'s `dir`."""
    f = (folder or "").strip().replace("\\", "/").strip("/")
    if f and not f.startswith("inventory/"):
        f = "inventory/" + f
    return f


def canonical_id(item_id: str, code: str) -> str:
    """`C001` -> `K01-C001`. Ids are client-prefixed so they stay unique when
    one client's intakes share a ledger; a bare id from an older ledger is
    rewritten on its next write."""
    return f"{code}-{item_id}" if code and re.fullmatch(r"C\d+", item_id) else item_id


def id_num(item_id: str) -> Optional[int]:
    m = _ID_RE.match(item_id or "")
    return int(m.group("n")) if m else None


def id_key(item_id: str) -> tuple:
    n = id_num(item_id)
    return (n is None, n or 0, item_id)


def is_active(r: dict) -> bool:
    return (r["state"] or "").upper() not in TERMINAL


def channel_of(r: dict) -> str:
    """The row's sales channel, lower case; blank is eBay."""
    return (r.get("channel") or "").strip().lower() or EBAY


def off_ebay(r: dict) -> bool:
    return channel_of(r) != EBAY


def month_end(d: date) -> date:
    return date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])


def iso(v: str) -> Optional[date]:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def intake_dir(name: str) -> Path:
    p = Path(name)
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == "inventory":
        return REPO / p
    return INVENTORY / p


def _name_suffix(d: Path) -> str:
    return d.name[len(INTAKE_PREFIX):] if d.name.startswith(INTAKE_PREFIX) else ""


def folder_date(d: Path) -> str:
    """CONSIGN-20261022 -> 2026-10-22; '' for a per-client CONSIGN-K01."""
    m = _DATE_NAME_RE.match(_name_suffix(d))
    if not m:
        return ""
    s = "-".join(m.groups())
    return s if iso(s) else ""


def folder_code(d: Path) -> str:
    """CONSIGN-K01 -> K01; '' for a dated CONSIGN-YYYYMMDD folder."""
    s = _name_suffix(d)
    return "" if not s or _DATE_NAME_RE.match(s) else s


def all_intakes() -> list[Path]:
    """Every consignment ledger folder: a CONSIGN-* folder with a ledger, or
    one that names its client (context.txt `client:`, or a CONSIGN-<code>
    folder name)."""
    return sorted(p for p in INVENTORY.glob(INTAKE_GLOB) if p.is_dir()
                  and ((p / LEDGER_NAME).exists() or intake_client(p)))


def client_dirs(code: str) -> list[Path]:
    return [d for d in all_intakes() if intake_client(d) == code]


def find_item(*, sku: str = "", listing_id: str = "",
              folder: str = "") -> Optional[tuple[str, str]]:
    """(client code, intake folder name) of the consignment item with this
    SKU, listing id or inventory folder, or None when it is not consignment.
    A folder inside a CONSIGN-* intake counts before it has a ledger row, and
    the folder catches a fresh publish whose SKU `sync` hasn't copied yet.
    Read-only; lib/auto_promote and tools/promote ask it before adding an ad."""
    folder = norm_folder(folder)
    for d in all_intakes():
        rows, _ = read_ledger(d / LEDGER_NAME)
        for r in rows:
            if ((sku and r["sku"] == sku)
                    or (listing_id and r["listing_id"] == listing_id)
                    or (folder and norm_folder(r["inventory_folder"]) == folder)):
                return r["client_code"] or intake_client(d), d.name
        if folder.startswith(norm_folder(d.name) + "/"):
            return intake_client(d), d.name
    return None


def registry_path() -> Path:
    return INVENTORY / "_consign" / "clients.csv"


def settlements_path(code: str) -> Path:
    return INVENTORY / "_consign" / code / SETTLEMENTS_NAME


def reports_dir(code: str) -> Path:
    return REPO / "reports" / "consign" / code


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


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> Optional[Path]:
    """Write `rows`, after copying the current file to a timestamped backup
    beside it. Returns the backup path (None for a new file)."""
    backup = None
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
        backup.write_bytes(path.read_bytes())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return backup


def write_ledger(path: Path, rows: list[dict]) -> Optional[Path]:
    return _write_csv(path, COLUMNS, rows)


def _context(d: Path) -> str:
    ctx = d / "context.txt"
    return ctx.read_text(encoding="utf-8", errors="ignore") if ctx.exists() else ""


def context_client(d: Path) -> str:
    m = _CLIENT_RE.search(_context(d))
    return m.group(1) if m else ""


def intake_client(d: Path) -> str:
    """The folder's client: context.txt `client:`, else a CONSIGN-<code> name."""
    return context_client(d) or folder_code(d)


def default_intake(d: Path) -> str:
    """The intake date for a row that has none: context.txt `acquired:`, else
    the date in a CONSIGN-YYYYMMDD folder name."""
    m = _ACQUIRED_RE.search(_context(d))
    if m and iso(m.group(1)):
        return m.group(1)
    return folder_date(d)


def set_intake_client(d: Path, code: str) -> None:
    """Put `client: <code>` in the intake's context.txt, after its leading
    `key: value` lines. Refuses to change a different client already there. A
    CONSIGN-<code> folder already names its client and needs no line."""
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


def migrate(rows: list[dict], d: Path, code: str) -> None:
    """Fill the per-client columns an older ledger lacks: client_code, the
    intake date, and the client prefix on bare `C###` ids."""
    fallback = default_intake(d)
    for r in rows:
        if code:
            r["client_code"] = code
            r["item_id"] = canonical_id(r["item_id"], code)
        r["intake"] = r["intake"] or fallback


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


def client_terms(registry: dict, code: str) -> dict:
    """The client's split and loss rule, after checking the registry row's
    optional fields. Blank fields stay blank; a bad one stops the run."""
    reg = {k: (v or "").strip() for k, v in (registry.get(code) or {}).items() if k}
    where = f"[X] {registry_path().name}: client {code}"
    loss = reg.get("loss_rule", "").lower() or "split"
    if loss not in LOSS_RULES:
        raise SystemExit(f"{where} loss_rule {reg['loss_rule']!r} is not one of "
                         f"{', '.join(LOSS_RULES)}")
    if reg.get("w9_on_file", "").lower() not in ("", "yes", "no"):
        raise SystemExit(f"{where} w9_on_file {reg['w9_on_file']!r} is not yes or no")
    if reg.get("agreement_signed") and not iso(reg["agreement_signed"]):
        raise SystemExit(f"{where} agreement_signed {reg['agreement_signed']!r} "
                         f"is not a YYYY-MM-DD date")
    # Never a full account or card number: the registry is backed up offsite.
    if re.search(r"\d{5,}", re.sub(r"[\s.-]", "", reg.get("payout_method", ""))):
        raise SystemExit(f"{where} payout_method holds more than 4 digits: keep "
                         f"the last 4 only (e.g. 'check' or 'bank 1234')")
    for k in ("floor_main", "floor_junk", "handling_fee"):
        try:
            dec(reg.get(k, ""))
        except ValueError:
            raise SystemExit(f"{where} {k} {reg[k]!r} is not an amount") from None
    handling = dec(reg.get("handling_fee", ""))
    if handling is not None and handling < 0:
        raise SystemExit(f"{where} handling_fee {reg['handling_fee']!r} is below zero")
    try:
        min_payout = dec(reg.get("min_payout", "")) or Decimal(0)
        if min_payout < 0:
            raise ValueError
    except ValueError:
        raise SystemExit(f"{where} min_payout {reg['min_payout']!r} is not an amount "
                         f"of 0 or more") from None
    days = {}
    for k, default in (("listing_days", DEFAULT_LISTING_DAYS),
                       ("pickup_days", DEFAULT_PICKUP_DAYS)):
        raw = reg.get(k, "")
        if not raw:
            days[k] = default
        elif raw.isdigit() and int(raw) > 0:
            days[k] = int(raw)
        else:
            raise SystemExit(f"{where} {k} {raw!r} is not a whole number of days")
    return {"split": client_split(registry, code), "loss_rule": loss,
            "handling": DEFAULT_HANDLING if handling is None else handling,
            "agreement_signed": reg.get("agreement_signed", ""),
            "w9_on_file": reg.get("w9_on_file", "").lower(),
            "min_payout": min_payout, **days}


# --------------------------------------------------------------------------- #
# settlements
# --------------------------------------------------------------------------- #
def read_settlements(code: str) -> list[dict]:
    p = settlements_path(code) if code else None
    if not p or not p.exists():
        return []
    with p.open(newline="", encoding="utf-8-sig") as f:
        return [{c: (row.get(c) or "").strip() for c in SETTLEMENT_COLUMNS}
                for row in csv.DictReader(f)]


def parse_adjustments(text: str) -> list[tuple[str, Decimal]]:
    """`K01-C008=-0.90 K01-C010=-8.10` -> [(id, amount), ...]. ValueError when bad."""
    out = []
    for tok in (text or "").replace(";", " ").split():
        iid, sep, amt = tok.partition("=")
        d = dec(amt) if sep else None
        if not iid or d is None:
            raise ValueError(tok)
        out.append((iid, d))
    return out


def settlement_due(s: dict) -> Decimal:
    return ((dec(s["client_share"]) or Decimal(0))
            + sum((a for _, a in parse_adjustments(s["adjustments"])), Decimal(0)))


def settlement_errors(settlements: list[dict]) -> list[str]:
    errs, seen = [], set()
    for s in settlements:
        sid = s["settlement_id"] or "(no settlement_id)"
        if sid in seen:
            errs.append(f"{SETTLEMENTS_NAME}: {sid} used twice")
        seen.add(sid)
        for k in ("period_start", "period_end", "paid_date"):
            if s[k] and not iso(s[k]):
                errs.append(f"{SETTLEMENTS_NAME}: {sid} {k} {s[k]!r} is not a YYYY-MM-DD date")
        for k in ("gross", "fees", "ad", "postage", "handling", "other", "net",
                  "client_share", "amount_paid"):
            try:
                dec(s[k])
            except ValueError:
                errs.append(f"{SETTLEMENTS_NAME}: {sid} {k} {s[k]!r} is not an amount")
        try:
            parse_adjustments(s["adjustments"])
        except ValueError as e:
            errs.append(f"{SETTLEMENTS_NAME}: {sid} adjustment {e} is not ID=amount")
    return errs


def settlement_notes(settlements: list[dict]) -> list[str]:
    """Paid settlements whose recorded payment doesn't match what was due."""
    out = []
    for s in settlements:
        if not s["paid_date"]:
            continue
        due = money(settlement_due(s))
        if not s["amount_paid"]:
            out.append(f"{s['settlement_id']} paid {s['paid_date']} but amount_paid is "
                       f"blank (due ${due})")
        elif money(dec(s["amount_paid"])) != due:
            out.append(f"{s['settlement_id']} amount_paid ${money(dec(s['amount_paid']))} "
                       f"is not the ${due} due")
    return out


def settled_adjustments(settlements: list[dict]) -> dict[str, Decimal]:
    """Adjustments already put on a settlement (paid or not), per item."""
    out: dict[str, Decimal] = {}
    for s in settlements:
        for iid, amt in parse_adjustments(s["adjustments"]):
            out[iid] = out.get(iid, Decimal(0)) + amt
    return out


def in_open_settlement(settlements: list[dict]) -> set[str]:
    """Item ids on a settlement that isn't paid yet."""
    return {i for s in settlements if not s["paid_date"] for i in s["item_ids"].split()}


def apply_paid_settlements(rows: list[dict], settlements: list[dict]) -> None:
    """Copy each paid settlement's paid_date and id onto its rows."""
    by_id = {r["item_id"]: r for r in rows}
    for s in settlements:
        if not s["paid_date"]:
            continue
        for iid in s["item_ids"].split():
            r = by_id.get(iid)
            if r is not None and not r["paid_to_client_date"]:
                r["paid_to_client_date"] = s["paid_date"]
                r["settlement_id"] = s["settlement_id"]


# --------------------------------------------------------------------------- #
# lots
# --------------------------------------------------------------------------- #
def lot_plan(rows: list[dict]) -> dict[str, tuple[str, dict]]:
    """Lot-mate item_id -> (lot_id, lead row). The lead is the lot's active
    row with the lowest item_id; a RETURNED/DISPOSED/EXCLUDED row has left
    the lot."""
    lots: dict[str, list[dict]] = {}
    for r in rows:
        if r["lot_id"] and is_active(r):
            lots.setdefault(r["lot_id"], []).append(r)
    mates = {}
    for lot, rs in lots.items():
        rs = sorted(rs, key=lambda r: id_key(r["item_id"]))
        for m in rs[1:]:
            mates[m["item_id"]] = (lot, rs[0])
    return mates


def unify_lot_folders(rows: list[dict]) -> None:
    """Every active row in a lot takes the lot's folder (validate() has
    already refused a lot on two folders)."""
    folders: dict[str, str] = {}
    for r in rows:
        if r["lot_id"] and is_active(r) and r["inventory_folder"]:
            folders[r["lot_id"]] = norm_folder(r["inventory_folder"])
    for r in rows:
        if r["lot_id"] and is_active(r) and r["lot_id"] in folders:
            r["inventory_folder"] = folders[r["lot_id"]]


def unit_floor(r: dict, rows: list[dict]) -> Optional[Decimal]:
    """The floor for the unit `r` sells as: its own, or for a lot the total of
    its active rows' floors. None when no row in it has one."""
    unit = ([m for m in rows if m["lot_id"] == r["lot_id"] and is_active(m)]
            if r["lot_id"] else [r])
    floors = [f for m in unit if (f := dec(m["floor"])) is not None]
    return sum(floors, Decimal(0)) if floors else None


def follow_lead(m: dict, lot: str, lead: dict) -> None:
    """A lot-mate takes its lead's state, copies the listing it is part of,
    and holds no money of its own."""
    note = hand_note(m["state_note"])
    for k in SALE_MONEY:
        m[k] = ""
    m["actuals_complete"] = m["split_locked"] = ""
    for k in LOT_COPY:
        m[k] = lead[k]
    m["state"] = lead["state"]
    m["state_note"] = NOTE_SEP.join(([note] if note else [])
                                    + [f"in lot {lot} with {lead['item_id']}"])


# --------------------------------------------------------------------------- #
# validation (hand edits)
# --------------------------------------------------------------------------- #
def validate(rows: list[dict], unknown_columns: list[str], code: str = "") -> list[str]:
    errs = []
    if unknown_columns:
        errs.append(f"unknown column(s) {', '.join(unknown_columns)} — the ledger "
                    f"columns are listed in tools/consign.py COLUMNS")
    seen: set[str] = set()
    for i, r in enumerate(rows, start=2):        # line 1 is the header
        tag = r["item_id"] or f"line {i}"
        cid = canonical_id(r["item_id"], code)
        if not r["item_id"]:
            errs.append(f"line {i}: no item_id")
        elif cid in seen:
            errs.append(f"{tag}: item_id used twice")
        seen.add(cid)
        m = _ID_RE.match(r["item_id"])
        if code and m and m.group("code") and m.group("code") != code:
            errs.append(f"{tag}: item_id is client {m.group('code')}'s, not {code}'s")
        if code and r["client_code"] and r["client_code"] != code:
            errs.append(f"{tag}: client_code {r['client_code']!r} in client {code}'s ledger")
        st = r["state"].upper()
        if st and st not in STATES:
            errs.append(f"{tag}: state {r['state']!r} is not one of {', '.join(STATES)}")
        if st == "DISPOSED" and not hand_note(r["state_note"]):
            errs.append(f"{tag}: DISPOSED needs a state_note saying how and why")
        if st == "HOLD" and not hand_note(r["state_note"]):
            errs.append(f"{tag}: HOLD needs a state_note saying why")
        if st == "EXCLUDED" and not r["buyout_price"]:
            errs.append(f"{tag}: EXCLUDED needs a buyout_price (0.00 is allowed)")
        if r["cut"].upper() not in CUTS:
            errs.append(f"{tag}: cut {r['cut']!r} is not one of LIST, LOT, RETURN")
        if r["channel"] and not CHANNEL_RE.match(r["channel"].lower()):
            errs.append(f"{tag}: channel {r['channel']!r} is not one word of letters, "
                        f"digits, - and _ (blank is ebay)")
        if r["placed_at"] and not off_ebay(r):
            errs.append(f"{tag}: placed_at is for an off-eBay channel (the {MALL} booth); "
                        f"an eBay listing's date comes from the listings ledger")
        elif r["placed_at"] and not r["listing_id"]:
            errs.append(f"{tag}: placed_at needs the {channel_of(r)} item id in listing_id "
                        f"(the mall's GAM SKU)")
        if r["card_paid"] and channel_of(r) != MALL:
            errs.append(f"{tag}: card_paid is for a {MALL} sale only (channel is "
                        f"{channel_of(r)})")
        try:
            card, price = dec(r["card_paid"]), dec(r["item_price"])
        except ValueError:
            card = price = None                  # reported as a bad amount below
        if card is not None and (card < 0 or price is None or card > price):
            errs.append(f"{tag}: card_paid {r['card_paid']} must be between 0.00 and "
                        f"item_price")
        for k in MONEY:
            try:
                dec(r[k])
            except ValueError:
                errs.append(f"{tag}: {k} {r[k]!r} is not an amount")
        for k in DATES:
            if r[k] and not iso(r[k]):
                errs.append(f"{tag}: {k} {r[k]!r} is not a YYYY-MM-DD date")
        if (iso(r["delivered_at"]) and iso(r["sold_at"])
                and iso(r["delivered_at"]) < iso(r["sold_at"])):
            errs.append(f"{tag}: delivered_at {r['delivered_at']} is before sold_at")
        try:
            s = dec(r["split_locked"])
            if s is not None and not Decimal(0) <= s <= Decimal(1):
                raise ValueError
        except ValueError:
            errs.append(f"{tag}: split_locked {r['split_locked']!r} is not a fraction "
                        f"between 0 and 1")
    # One folder, one sellable unit: a sale matched on the folder would land
    # on every row that shares it. A lot is one unit, so its rows may share
    # it. Terminal rows no longer sell, so they may too.
    sharing: dict[str, list[dict]] = {}
    lot_folders: dict[str, set[str]] = {}
    for r in rows:
        if r["inventory_folder"] and is_active(r):
            f = norm_folder(r["inventory_folder"])
            sharing.setdefault(f, []).append(r)
            if r["lot_id"]:
                lot_folders.setdefault(r["lot_id"], set()).add(f)
    for f, rs in sharing.items():
        lots = {r["lot_id"] for r in rs}
        if len(rs) > 1 and (len(lots) > 1 or "" in lots):
            errs.append(f"{', '.join(r['item_id'] for r in rs)} share {f}: give each its "
                        f"own folder, put them in one lot_id, or blank all but one")
    for lot, fs in lot_folders.items():
        if len(fs) > 1:
            errs.append(f"lot {lot} is on {len(fs)} folders ({', '.join(sorted(fs))}): "
                        f"a lot is one listing, in one folder")
    # A lot's sale lives on its lead. A lot-mate that already sold or was
    # paid would hand its sale to the lead and be owed (or paid) again.
    for iid, (lot, lead) in lot_plan(rows).items():
        m = next(r for r in rows if r["item_id"] == iid)
        held = [k for k in ("order_id", "client_share", "settlement_id",
                            "paid_to_client_date") if m[k]]
        if off_ebay(m) and m["item_price"]:      # a typed sale follow_lead would wipe
            held.append("item_price")
        if held:
            errs.append(f"{iid}: in lot {lot} but not its lead ({lead['item_id']}) and has "
                        f"{', '.join(held)}: make the sold row the lowest item_id in the "
                        f"lot, or take it out of the lot")
    # One order, one row. A hand-typed order_id on another channel is keyed
    # by that channel, so it only clashes with the same channel's orders.
    orders: dict[tuple[str, str], list[str]] = {}
    for r in rows:
        if r["order_id"] and is_active(r):
            orders.setdefault((channel_of(r), r["order_id"]), []).append(r["item_id"])
    for (channel, order), ids in orders.items():
        if len(ids) > 1:
            on = "" if channel == EBAY else f" on {channel}"
            errs.append(f"{', '.join(ids)} all carry order {order}{on}: one sale, one row")
    return errs


def validate_all(dirs: list[Path]) -> list[str]:
    return [msg for msg, _ in cross_errors(dirs)]


def cross_errors(dirs: list[Path]) -> list[tuple[str, frozenset[str]]]:
    """Checks across ledgers, each with the ledger folders it involves: a
    folder claimed from two ledgers (unless the rows are one client's lot), a
    lot on two folders, one client's item id in two of its ledgers, and a
    folder name that disagrees with its context.txt."""
    errs: list[tuple[str, frozenset[str]]] = []
    claims: dict[str, list[tuple[str, str, str, str]]] = {}
    lots: dict[tuple[str, str], set[tuple[str, str]]] = {}
    ids: dict[tuple[str, str], set[str]] = {}
    for d in dirs:
        rows, _ = read_ledger(d / LEDGER_NAME)
        code = intake_client(d)
        for r in rows:
            cid = canonical_id(r["item_id"], code)
            if cid and code:                     # unassigned ledgers number on their own
                ids.setdefault((code, cid), set()).add(d.name)
            if not r["inventory_folder"] or not is_active(r):
                continue
            f = norm_folder(r["inventory_folder"])
            claims.setdefault(f, []).append((d.name, code, r["lot_id"], cid))
            if r["lot_id"]:
                lots.setdefault((code, r["lot_id"]), set()).add((d.name, f))
    for (code, cid), names in sorted(ids.items()):
        if len(names) > 1:
            errs.append((f"{cid} is in {' and '.join(sorted(names))}: one item, one ledger",
                         frozenset(names)))
    for f, cl in sorted(claims.items()):
        names = frozenset(c[0] for c in cl)
        if len(names) < 2:
            continue                             # one ledger: validate() covers it
        units = {(c[1], c[2]) for c in cl}
        if len(units) > 1 or any(not lot for _, lot in units):
            errs.append((f"{f} is claimed by {', '.join(c[3] for c in cl)} in "
                         f"{' and '.join(sorted(names))}: one folder is one "
                         f"sellable unit, unless the rows are one client's lot", names))
    for (code, lot), placed in sorted(lots.items()):
        names = frozenset(n for n, _ in placed)
        if len({f for _, f in placed}) > 1 and len(names) > 1:
            errs.append((f"client {code} lot {lot} is on {len({f for _, f in placed})} "
                         f"folders across {' and '.join(sorted(names))}", names))
    for d in dirs:
        have, named = context_client(d), folder_code(d)
        if have and named and have != named:
            errs.append((f"{d.name}: context.txt says client {have}, the folder name "
                         f"{named}", frozenset([d.name])))
    return errs


# --------------------------------------------------------------------------- #
# the books: every store's ledgers and the local drafts, read once
# --------------------------------------------------------------------------- #
def listing_status(r: dict) -> str:
    """A listings-ledger row's status. A DELETED row that was published once
    was live and is gone: ENDED. One never published was never live."""
    st = (r.get("status") or "").strip().upper()
    if st == "DELETED" and (r.get("published_at") or "").strip():
        return "ENDED"
    return st


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
        """The listings-ledger row for this item, if it has been live on eBay
        (LIVE_LISTING); a live or sold row beats an ENDED one (an item ended on
        one store and relisted on another). A DRAFTED or SYNCED row is not a
        listing yet and is ignored."""
        hits = [(s, r) for s, r in self.listings
                if listing_status(r) in LIVE_LISTING
                and ((listing_id and r.get("listing_id") == listing_id)
                     or (sku and r.get("sku") == sku))]
        if not hits:
            return None
        return sorted(hits, key=lambda h: (listing_status(h[1]) != "ENDED",
                                           h[1].get("updated_at") or ""))[-1]

    def first_published(self, sku: str, listing_id: str) -> Optional[date]:
        """The first day this item went live: the earliest published_at on its
        live listings-ledger rows, so a relist doesn't restart the clock. None
        when no row has one."""
        days = [iso((r.get("published_at") or "").strip()[:10]) for _, r in self.listings
                if listing_status(r) in LIVE_LISTING
                and ((listing_id and r.get("listing_id") == listing_id)
                     or (sku and r.get("sku") == sku))]
        days = [d for d in days if d]
        return min(days) if days else None

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
def share_of(net: Decimal, split: Decimal, loss_rule: str = "split") -> Decimal:
    if loss_rule == "client_bears_loss" and net < 0:
        return net                               # charged back in full, not split
    share = net * split
    if loss_rule == "client_never_owes" and share < 0:
        return Decimal(0)
    return share


def row_split(r: dict, split: Decimal) -> Decimal:
    """The split locked when the row sold, else the client's current one."""
    return dec(r["split_locked"]) if r["split_locked"] else split


def settle_money(r: dict, split: Decimal, loss_rule: str = "split") -> None:
    """gross, net, client_share and actuals_complete from the row's own
    figures. A blank ad_fee or actual_postage is unknown, not zero: the row
    stays actuals_complete=no with net and client_share blank. A blank
    handling is zero (a row that sold before the fee existed)."""
    price = dec(r["item_price"])
    if price is None:
        r["gross"] = r["net"] = r["client_share"] = r["actuals_complete"] = ""
        return
    z = lambda k: dec(r[k]) or Decimal(0)  # noqa: E731
    gross = price + z("buyer_shipping") - z("refunded")
    r["gross"] = money(gross)
    if channel_of(r) == MALL:
        # The mall's fee is worked out (§4.2(b)); nothing is shipped.
        card = dec(r["card_paid"])
        r["ebay_fee"] = "" if card is None else money(
            MALL_COMMISSION * price + MALL_CARD_RATE * card)
        r["actual_postage"] = "0.00"
    if off_ebay(r):
        # A sale typed by hand: net before postage is gross less that
        # channel's fee (blank = unknown). Off eBay there is no ad fee.
        fee = dec(r["ebay_fee"])
        r["net_before_postage"] = "" if fee is None else money(gross - fee)
        r["ad_fee"] = r["ad_fee"] or "0.00"
    nbp, ad, post = dec(r["net_before_postage"]), dec(r["ad_fee"]), dec(r["actual_postage"])
    if nbp is None or ad is None or post is None:
        r["net"] = r["client_share"] = ""
        r["actuals_complete"] = "no"
        return
    net = nbp - ad - post - z("handling") - z("other_costs")
    r["net"] = money(net)
    r["client_share"] = money(share_of(net, split, loss_rule))
    r["actuals_complete"] = "yes"


def provisional_net(r: dict) -> Optional[Decimal]:
    """Net with the unknown costs left out: an upper bound, for the report."""
    nbp = dec(r["net_before_postage"])
    if nbp is None:
        return None
    return nbp - sum((dec(r[k]) or Decimal(0)
                      for k in ("ad_fee", "actual_postage", "handling", "other_costs")),
                     Decimal(0))


def recalculated_share(r: dict, split: Decimal, loss_rule: str) -> Optional[Decimal]:
    """What a paid row's client share should be now, from the row as last
    synced: the buyout for EXCLUDED, nothing once the sale is gone or the
    item came back, else the split of net (None while net is unknown)."""
    st = r["state"].upper()
    if st == "EXCLUDED":
        return dec(r["buyout_price"]) or Decimal(0)
    if st not in SOLD_STATES:
        return Decimal(0)
    net = dec(r["net"])
    return None if net is None else share_of(net, row_split(r, split), loss_rule)


def pending_adjustments(rows: list[dict], settlements: list[dict], split: Decimal,
                        loss_rule: str) -> list[tuple[str, Decimal]]:
    """(item_id, amount) still to put on a settlement: for each paid row, the
    recalculated share less what was paid and what is already adjusted."""
    done = settled_adjustments(settlements)
    out = []
    for r in rows:
        paid = dec(r["client_share"]) if r["paid_to_client_date"] else None
        if paid is None:
            continue
        target = recalculated_share(r, split, loss_rule)
        if target is None:
            continue
        adj = (target - paid - done.get(r["item_id"], Decimal(0))).quantize(
            CENT, rounding=ROUND_HALF_UP)
        if adj:
            out.append((r["item_id"], adj))
    return out


# --------------------------------------------------------------------------- #
# sync: derive state + fill eBay facts
# --------------------------------------------------------------------------- #
def _freeze_paid(r: dict, paid_share: str, target: Optional[Decimal], adjusted: Decimal,
                 flags: list[str], gone: bool = False) -> None:
    """Once the client is paid, client_share is the amount paid. A
    recalculation that differs, less any adjustment already on a settlement,
    is flagged with the adjustment, not written."""
    if not paid_share:
        return
    r["client_share"] = paid_share
    adj = (target or Decimal(0)) - dec(paid_share) - adjusted
    if money(adj) == "0.00":
        return
    paid = f"client was paid ${paid_share} on {r['paid_to_client_date']}"
    if gone:
        flags.append(f"{paid}: needs a negative adjustment, adjust by ${money(adj)}")
    else:
        flags.append(f"{paid}; recalculated share is ${money(target) or '?'}: "
                     f"adjust by ${money(adj)}")


def draft_format(fdir: Path) -> str:
    p = fdir / "draft.md"
    if not p.is_file():
        return ""
    m = _FORMAT_RE.search(p.read_text(encoding="utf-8", errors="ignore"))
    return m.group(1).upper() if m else ""


def _amount(v: str) -> Optional[Decimal]:
    """A figure sync copied in (a draft's price, say): None when blank or bad."""
    try:
        return dec(v)
    except ValueError:
        return None


def sync_row(r: dict, books: Books, split: Decimal, today: date,
             loss_rule: str = "split", adjusted: Decimal = Decimal(0),
             handling: Decimal = DEFAULT_HANDLING, *,
             floor: Optional[Decimal] = None, listing_days: int = DEFAULT_LISTING_DAYS,
             pickup_days: int = DEFAULT_PICKUP_DAYS) -> list[str]:
    """Update `r` in place from the books. Returns the flags raised. `floor`
    is the unit's floor: the row's own, or a lot's total for its lead."""
    r["state"] = r["state"].upper() or "INTAKE"
    r["cut"] = r["cut"].upper()
    note = hand_note(r["state_note"])
    flags: list[str] = []
    paid_share = r["client_share"] if r["paid_to_client_date"] else ""
    if r["state"] in TERMINAL:                   # hand-entered terminal wins
        if r["state"] == "EXCLUDED":             # Russell's buyout, no split
            r["client_share"] = money(dec(r["buyout_price"]))
            _freeze_paid(r, paid_share, dec(r["buyout_price"]), adjusted, flags)
        else:                                    # paid, then it came back
            _freeze_paid(r, paid_share, Decimal(0), adjusted, flags, gone=True)
        r["state_note"] = NOTE_SEP.join(([note] if note else []) + [FLAG + f for f in flags])
        return flags
    hand = r["state"] if r["state"] in ("PREP", "HOLD") else ""
    # Off eBay, listing_id is the channel's own id (the mall's GAM SKU): the
    # eBay ledgers are never searched for it, and never write an eBay id over it.
    off = off_ebay(r)
    ebay_id = "" if off else r["listing_id"]
    folder = r["inventory_folder"] = norm_folder(r["inventory_folder"])
    state = "INTAKE"
    fmt = ""
    if folder:
        fdir = REPO / folder
        if fdir.is_dir():
            state = "SHOT"
        else:
            flags.append(f"folder not found: {folder}")
        if (fdir / "draft.md").is_file():
            state = "DRAFTED"
            fmt = draft_format(fdir)
        d = books.drafts.get(folder)
        if d:
            r["sku"] = r["sku"] or d.get("sku", "")
            if not off:
                r["listing_id"] = r["listing_id"] or d.get("listing_id", "")
            r["ask"] = r["ask"] or d.get("price", "")
            if not r["store"] and d.get("store"):
                r["store"] = d["store"]
    listing = books.listing_for(r["sku"], ebay_id)
    if hand == "HOLD" and listing and listing_status(listing[1]) == "ENDED":
        listing = None                           # held: an ended listing is history
    if listing:
        lstore, lrow = listing
        if listing_status(lrow) != "ENDED":      # an ended listing keeps its on-hand state
            state = "LISTED"
        r["store"] = lstore
        if not off:
            r["listing_id"] = r["listing_id"] or lrow.get("listing_id", "")
        r["ask"] = lrow.get("price") or r["ask"]
    placed = off and bool(r["placed_at"])        # on the channel's floor (validated)
    if placed:
        state = "LISTED"

    sales = books.sales_for(folder, r["sku"], ebay_id)
    channel = channel_of(r)
    hand_sold = False
    vanished = False
    if channel != EBAY:
        # Sold somewhere else: the sale is typed by hand and the eBay sales
        # ledger is never read into it. An eBay sale on the same item means it
        # sold twice, or the channel is wrong.
        if sales:
            flags.append(f"channel is {channel} but eBay sale "
                         f"{sales[0][1].get('order_id')} matches this item: one item "
                         f"sells once, fix the channel or the sale")
        sales = []
        hand_sold = bool(r["sold_at"])
    elif r["order_id"] and r["order_id"] not in {s.get("order_id") for _, s in sales}:
        vanished = True
    if vanished:
        # It was sold, and that sale is gone: refunded or cancelled. Keep the
        # old figures as the record; don't adopt any other sale on our own.
        flags.append(f"sale {r['order_id']} is no longer in the sales ledger "
                     f"(refund or cancel?)")
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
        if not r["split_locked"]:                # the split at the moment it sold
            r["split_locked"] = str(split)
        if not r["handling"] and not r["paid_to_client_date"]:  # likewise the fee
            r["handling"] = money(handling)
        fmt = (srow.get("sold_format") or "").strip().upper() or fmt
    elif hand_sold:
        state = "SOLD"
        if not r["split_locked"]:
            r["split_locked"] = str(split)
        if not r["handling"] and not r["paid_to_client_date"]:  # every channel pays it
            r["handling"] = money(handling)
    elif listing and listing_status(listing[1]) == "ENDED":
        ended = (listing[1].get("ended_at") or "").strip()[:10]
        flags.append(f"listing {r['listing_id'] or r['sku']} ended"
                     f"{' ' + ended if ended else ''}: relist, return or dispose")
    elif listing and listing_status(listing[1]) == "SOLD" and not vanished:
        flags.append("the listings ledger says SOLD but no sales ledger row matches: "
                     "run the finance sync (lib/sync_actuals.py --all-stores --apply)")
    if fmt == "AUCTION":
        flags.append("auction format: consigned items stay fixed-price until the "
                     "Georgia auctioneer-licence question is answered")

    sold = bool(sale) or hand_sold
    settle_money(r, row_split(r, split), loss_rule)
    if hand_sold and r["actuals_complete"] != "yes":
        if channel == MALL:
            flags.append(f"{MALL} sale: type item_price and card_paid (0.00 if paid in "
                         f"cash) so it can clear")
        else:
            flags.append(f"{channel} sale: type item_price, ebay_fee (the {channel} fee, "
                         f"0.00 if none) and actual_postage (0.00 if none) so it can "
                         f"clear")
    target = Decimal(0) if vanished else dec(r["client_share"])
    _freeze_paid(r, paid_share, target, adjusted, flags, gone=vanished)
    r["cleared_on"] = ""
    if sale and not r["paid_to_client_date"] and not r["delivered_at"]:
        flags.append(f"no delivery date: clears {RETURN_WINDOW_DAYS} days after the sale")
    # Off eBay (agreement §5.1(b)): cleared at the end of the month it sold,
    # once its money has reached us (funds_received, typed by hand), and paid
    # on the 15th after; funds that come later roll to the next payout run.
    if hand_sold and not r["funds_received"] and not r["paid_to_client_date"]:
        flags.append(f"{channel} sale: type funds_received so it can clear (at the "
                     f"end of the month it sold, once the money is in)")
    window_end = None
    if hand_sold and r["actuals_complete"] == "yes" and r["funds_received"]:
        window_end = month_end(date.fromisoformat(r["sold_at"]))
    elif sale and r["actuals_complete"] == "yes" and r["sold_at"]:
        window_end = date.fromisoformat(r["sold_at"]) + timedelta(days=RETURN_WINDOW_DAYS)
        if r["delivered_at"]:
            window_end = max(window_end, date.fromisoformat(r["delivered_at"])
                             + timedelta(days=DELIVERY_WINDOW_DAYS))
    if window_end is not None:
        if window_end <= today:
            state = "CLEARED"
            r["cleared_on"] = window_end.isoformat()
    if sold and r["paid_to_client_date"]:
        state = "SETTLED"
    if hand and state in ("INTAKE", "SHOT", "DRAFTED"):
        state = hand                             # the on-hand label until listed
    if hand == "HOLD" and state == "LISTED":     # live on eBay, or placed off it
        state = "HOLD"
        flags.append(f"on hold but listing {r['listing_id'] or r['sku']} is live: "
                     f"end it, or clear the hold")

    # Exhibit A floors: the client's price protection, on the ask and the sale.
    ask, price = _amount(r["ask"]), _amount(r["item_price"])
    if floor is not None and state == "LISTED" and ask is not None and ask < floor:
        flags.append(f"ask ${money(ask)} is below the client's floor ${money(floor)}: "
                     f"raise it, or get the client's written OK")
    if floor is not None and sold and price is not None and price < floor:
        flags.append(f"sold at ${money(price)}, below the client's floor "
                     f"${money(floor)}: needs the client's written OK")
    # 180 days listed, then the client is told and has 30 days to pick it up.
    first = None
    if state == "LISTED" and placed:
        first = iso(r["placed_at"])
    elif state == "LISTED" and listing and listing_status(listing[1]) != "ENDED":
        first = books.first_published(r["sku"], ebay_id)
        if first is None:
            flags.append("listing window not checked: no published_at for this "
                         "listing in the listings ledger")
    if first is not None and (today - first).days > listing_days:
        flags.append(f"listing window over: end it and notify client (live since "
                     f"{first}, {(today - first).days} days; the limit is "
                     f"{listing_days})")
    notice = iso(r["return_notice_sent"])
    if notice and state not in SOLD_STATES:
        deadline = notice + timedelta(days=pickup_days)
        if today > deadline:
            flags.append(f"pickup deadline passed: abandonment election applies "
                         f"(notice sent {notice}, {pickup_days} days ran out {deadline})")
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
    settlements = read_settlements(code)
    errs = validate(rows, unknown, code) + settlement_errors(settlements)
    if errs:
        for e in errs:
            print(f"  [X] {e}")
        print(f"  [X] fix {path.name} (or {SETTLEMENTS_NAME}) by hand; nothing synced "
              f"for this ledger")
        return 1
    terms = client_terms(registry, code)
    before = [dict(r) for r in rows]
    migrate(rows, d, code)
    apply_paid_settlements(rows, settlements)
    adjusted = settled_adjustments(settlements)
    unify_lot_folders(rows)
    mates = lot_plan(rows)
    nflags = 0
    for r in rows:
        if r["item_id"] not in mates:
            nflags += len(sync_row(r, books, terms["split"], today, terms["loss_rule"],
                                   adjusted.get(r["item_id"], Decimal(0)),
                                   terms["handling"],
                                   floor=unit_floor(r, rows),
                                   listing_days=terms["listing_days"],
                                   pickup_days=terms["pickup_days"]))
    for r in rows:
        if r["item_id"] in mates:
            follow_lead(r, *mates[r["item_id"]])
    changed = 0
    for old, new in zip(before, rows):
        diff = [k for k in COLUMNS if old[k] != new[k] and k not in ("state", "state_note")]
        flags = [p for p in new["state_note"].split(NOTE_SEP) if p.startswith(FLAG)]
        if old != new:
            changed += 1
            move = (f"{old['state'] or 'INTAKE'} -> {new['state']}"
                    if old["state"] != new["state"] else new["state"])
            print(f"  {new['item_id']:<10} {move}"
                  + (f"  ({', '.join(diff)})" if diff else ""))
        for fl in flags:
            print(f"  ! {new['item_id']:<10} {fl}")
    for s in settlement_notes(settlements):
        print(f"  ! {s}")
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
        if k in COLUMNS and k not in ("item_id", "client_code"):
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
    """The next item number for a client: one past the highest in `rows`,
    which should be every row in all of that client's ledgers."""
    return max((n for r in rows if (n := id_num(r["item_id"])) is not None), default=0) + 1


def import_rows(d: Path, incoming: list[dict], client: Optional[str],
                intake: Optional[str] = None) -> tuple[int, int]:
    """Append `incoming` to the folder's ledger. Returns (added, skipped)."""
    if client:
        if not CODE_RE.match(client):
            raise SystemExit(f"[X] client code {client!r}: letters, digits, - and _ only")
        d.mkdir(parents=True, exist_ok=True)
        set_intake_client(d, client)
    elif not intake_client(d):
        raise SystemExit(f"[X] {d.name} has no `client:` yet; pass --client K01")
    if intake and not iso(intake):
        raise SystemExit(f"[X] --intake {intake!r} is not a YYYY-MM-DD date")
    code = intake_client(d)
    path = d / LEDGER_NAME
    rows, unknown = read_ledger(path)
    if unknown:
        raise SystemExit(f"[X] {path} has unknown column(s) {', '.join(unknown)}")
    migrate(rows, d, code)
    received = intake or default_intake(d) or date.today().isoformat()
    seen = {(r["intake"], r["bin"], r["description"]) for r in rows}
    others = [r for o in client_dirs(code) if o.resolve() != d.resolve()
              for r in read_ledger(o / LEDGER_NAME)[0]]
    n = next_id(rows + others)
    added = skipped = 0
    for rec in incoming:
        rec["intake"] = rec["intake"] or received
        key = (rec["intake"], rec["bin"], rec["description"])
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        rec["item_id"] = f"{code}-C{n:03d}"
        rec["client_code"] = code
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


# --------------------------------------------------------------------------- #
# fee breakdown: one renderer for the settle statement, the report and
# tools/report_lot.py. Every % is marked by where it came from:
#   SOURCED  a rate eBay or the contract states (the listing's campaign bid
#            from reports/ebay_ads.json; the mall terms, MALL_COMMISSION and
#            MALL_CARD_RATE, citing the agreement)
#   DERIVED  fee / base, worked out here and never stored; never "eBay's rate"
#   FLAT     a fixed fee: the amount only
# The ad fee is a selling fee and comes off gross before the split (Russell):
# net = net_before_postage - ad_fee - ... already takes it there.
# --------------------------------------------------------------------------- #
FEES_MISSING = "FEE LINES MISSING"
FEES_DONT_TIE = "DON'T TIE"
AD_GAP = Decimal("0.1")          # SOURCED vs DERIVED ad %, in points, before a flag
AD_NOTE = "selling fee, taken off gross before the split"


def load_fee_lines() -> list[dict]:
    """Every store's fee_lines file (lib/sync_actuals.py writes them). An
    order id is unique across accounts, so one list is safe to match on."""
    out: list[dict] = []
    for s in stores.configured_stores():
        p = stores.paths(s).fee_lines
        if p.exists():
            with p.open(encoding="utf-8-sig", newline="") as f:
                out += list(csv.DictReader(f))
    return out


def _pct(d: Decimal) -> str:
    return f"{(d * 100).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


def _fee(fee_type: str, amount: Decimal, base: Optional[Decimal] = None, rate: str = "",
         source: str = "", note: str = "", flag: bool = False) -> dict:
    return {"fee_type": fee_type, "amount": amount, "base": base, "rate": rate,
            "source": source, "note": note, "flag": flag}


def _derived(amount: Decimal, base: Optional[Decimal], what: str = "") -> tuple[str, str]:
    if not base:
        return "", "no base from eBay: amount only"
    return _pct(amount / base), f"DERIVED (fee / ${money(base)} {what}base)"


def _ad_fee(x: dict, amount: Decimal, base: Optional[Decimal], what: str) -> dict:
    bid = dec(x.get("bid_pct") or "")
    d_rate, d_src = _derived(amount, base, what)
    if bid is None:
        return _fee("AD", amount, base, d_rate, d_src, AD_NOTE)
    s_rate = f"{bid.quantize(Decimal('0.1'))}%"
    s_src = f"SOURCED (eBay campaign bid, read {(x.get('bid_pct_as_of') or '?')[:10]})"
    if base and abs(amount / base * 100 - bid) > AD_GAP:
        return _fee("AD", amount, base, f"{s_rate} / {d_rate}", f"{s_src} / {d_src}",
                    AD_NOTE, flag=True)
    return _fee("AD", amount, None, s_rate, s_src, AD_NOTE)


def _same_item(x: dict, r: dict) -> bool:
    for k in ("listing_id", "sku"):
        if x.get(k) and r.get(k):
            return x[k] == r[k]
    return True


def _mall_breakdown(r: dict) -> dict:
    fee, price, card = dec(r["ebay_fee"]), dec(r["item_price"]), dec(r["card_paid"])
    if fee is None:
        return {"status": "OK", "detail": "", "lines": []}
    if card is None:
        return {"status": "OK", "detail": "", "lines": [_fee(
            "MALL_FEE", fee, source="card_paid not recorded: amount only")]}
    src = f"SOURCED ({AGREEMENT_CITE})"
    lines = [_fee("MALL_COMMISSION", (MALL_COMMISSION * price).quantize(CENT), price,
                  _pct(MALL_COMMISSION), src),
             _fee("MALL_CARD_FEE", (MALL_CARD_RATE * card).quantize(CENT), card,
                  _pct(MALL_CARD_RATE), src)]
    total = _sum(ln["amount"] for ln in lines)
    if abs(total - fee) > CENT:
        return {"status": FEES_DONT_TIE, "lines": lines,
                "detail": f"agreement rates give ${money(total)}, ebay_fee is ${money(fee)}"}
    return {"status": "OK", "detail": "", "lines": lines}


def fee_breakdown(r: dict, fee_lines: list[dict]) -> Optional[dict]:
    """A sold row's fees, one entry per fee line, each % labelled SOURCED,
    DERIVED or FLAT. None for a row with no sale of its own (unsold, or a
    lot-mate). `status` is OK, FEES_MISSING (no fee lines for an eBay sale)
    or FEES_DONT_TIE (the non-AD lines don't add up to ebay_fee, or the AD
    lines to ad_fee, within $0.01); settle holds back anything not OK."""
    if r["state"] not in SOLD_STATES or dec(r["item_price"]) is None:
        return None
    channel = channel_of(r)
    if channel == MALL:
        return _mall_breakdown(r)
    if channel != EBAY:
        fee = dec(r["ebay_fee"])
        return {"status": "OK", "detail": "", "lines": [] if fee is None else [
            _fee(f"{channel.upper()}_FEE", fee, source=f"typed by hand ({channel})")]}
    mine = [x for x in fee_lines
            if r["order_id"] and x.get("order_id") == r["order_id"] and _same_item(x, r)]
    if not mine:
        return {"status": FEES_MISSING, "detail": "run the finance sync", "lines": []}
    fvf_base = {x.get("line_item_id"): dec(x.get("basis") or "") for x in mine
                if x.get("fee_type") != "AD" and dec(x.get("basis") or "")}
    lines = []
    for x in mine:
        amount, base = dec(x.get("amount") or "") or Decimal(0), dec(x.get("basis") or "")
        ftype = x.get("fee_type") or ""
        if ftype == "AD":
            own = base is not None
            lines.append(_ad_fee(x, amount, base if own else fvf_base.get(x.get("line_item_id")),
                                 "" if own else "FVF "))
        elif "FIXED" in ftype:
            lines.append(_fee(ftype, amount, None, "", "FLAT (fixed fee: amount only)"))
        else:
            lines.append(_fee(ftype, amount, base, *_derived(amount, base)))
    non_ad = _sum(ln["amount"] for ln in lines if ln["fee_type"] != "AD")
    ad = _sum(ln["amount"] for ln in lines if ln["fee_type"] == "AD")
    want_fee, want_ad = dec(r["ebay_fee"]) or Decimal(0), dec(r["ad_fee"]) or Decimal(0)
    if abs(non_ad - want_fee) > CENT or abs(ad - want_ad) > CENT:
        return {"status": FEES_DONT_TIE, "lines": lines,
                "detail": f"lines ${money(non_ad)} + ad ${money(ad)}; ledger ebay_fee "
                          f"${money(want_fee)} + ad_fee ${money(want_ad)}"}
    return {"status": "OK", "detail": "", "lines": lines}


def fee_text(bd: Optional[dict], indent: str = "      ") -> list[str]:
    """The breakdown as plain-text lines, for the report and report_lot."""
    if not bd:
        return []
    out = []
    for ln in bd["lines"]:
        rate = f"{ln['rate']} " if ln["rate"] else ""
        out.append(f"{indent}fee {ln['fee_type']:<32} {'$' + money(ln['amount']):>9}  "
                   f"{rate}{ln['source']}"
                   + (f"  ({ln['note']})" if ln["note"] else "")
                   + ("  FLAG: SOURCED and DERIVED differ" if ln["flag"] else ""))
    if bd["status"] != "OK":
        out.append(f"{indent}{bd['status']}" + (f": {bd['detail']}" if bd["detail"] else ""))
    return out


def fee_rows(bd: Optional[dict]) -> list[dict]:
    """The breakdown as CSV fields (FEE_COLUMNS and a note), for the
    statement, the report --csv and report_lot. ebay_fee and ad_fee stay
    blank: the sale's own row already totals them. A sale that is not OK gets
    one more row naming why."""
    if not bd:
        return []
    out = []
    for ln in bd["lines"]:
        out.append({"fee_type": ln["fee_type"], "fee_amount": money(ln["amount"]),
                    "fee_base": money(ln["base"]), "fee_rate": ln["rate"],
                    "rate_source": ln["source"] + (" FLAG: SOURCED and DERIVED differ"
                                                   if ln["flag"] else ""),
                    "note": ln["note"]})
    if bd["status"] != "OK":
        out.append({"rate_source": bd["status"], "note": bd["detail"]})
    return out


def buyout_owed(r: dict) -> bool:
    """An unpaid EXCLUDED buyout. A 0.00 buyout is closed: nothing is owed."""
    return (r["state"] == "EXCLUDED" and not r["paid_to_client_date"]
            and (dec(r["buyout_price"]) or Decimal(0)) > 0)


def client_summary(items: list[tuple[str, dict]], split: Decimal,
                   settlements: Optional[list[dict]] = None, loss_rule: str = "split",
                   today: Optional[date] = None) -> list[str]:
    today = today or date.today()
    settlements = settlements or []
    rows = [r for _, r in items]
    mates = lot_plan(rows)
    units = [r for r in rows if r["item_id"] not in mates]   # a lot counts once
    by_state = Counter(r["state"] or "INTAKE" for r in rows)
    out = ["  by state: " + " | ".join(f"{s} {by_state[s]}" for s in STATES if by_state[s])]
    if mates:
        out.append(f"  lots: {len({lot for lot, _ in mates.values()})}, "
                   f"{len(mates)} lot-mate(s) valued with their lead row")

    sold = [r for r in units if r["state"] in SOLD_STATES]
    incomplete = [r for r in sold if r["actuals_complete"] != "yes"]
    sold_net = _sum(dec(r["net"]) if r["actuals_complete"] == "yes" else provisional_net(r)
                    for r in sold)
    listed = [r for r in units if r["state"] == "LISTED"]
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
    out.append(f"    on hand  {sum(r['item_id'] not in mates for r in hand):>3} unit(s), "
               f"{len(hand)} item(s)  est ${money(_sum(lows))}-${money(_sum(highs))}"
               + (f"  (unpriced {unpriced})" if unpriced else ""))
    if excluded:
        out.append(f"    excluded {len(excluded):>3}  Russell buys at "
                   f"${money(_sum(dec(r['buyout_price']) for r in excluded))}")
    if gone:
        out.append(f"    returned/disposed {gone}")

    owed = _sum(dec(r["client_share"]) for r in units
                if r["state"] == "CLEARED" and not r["paid_to_client_date"])
    pending_rows = [r for r in units if r["state"] == "SOLD"]
    def pending_share(r):
        if r["actuals_complete"] == "yes":
            return dec(r["client_share"])
        pn = provisional_net(r)
        return None if pn is None else share_of(pn, row_split(r, split), loss_rule)
    pending = _sum(pending_share(r) for r in pending_rows)
    buyouts_owed = [r for r in excluded if buyout_owed(r)]
    adjustments = pending_adjustments(rows, settlements, split, loss_rule)
    paid = [s for s in settlements if s["paid_date"]]
    def paid_amount(s):
        return dec(s["amount_paid"]) if s["amount_paid"] else settlement_due(s)
    paid_year = _sum(paid_amount(s) for s in paid if s["paid_date"][:4] == str(today.year))
    open_settlements = [s for s in settlements if not s["paid_date"]]
    out.append("  totals:")
    out.append(f"    sold gross ${money(_sum(dec(r['gross']) for r in sold))}"
               f"  |  net ${money(sold_net)}" + ("  (provisional)" if incomplete else ""))
    out.append(f"    handling kept         ${money(_sum(dec(r['handling']) for r in sold))}"
               f"  ({sum(bool(dec(r['handling'])) for r in sold)} sale(s), taken before "
               f"the split)")
    out.append(f"    client share owed     ${money(owed)}  (CLEARED, not paid)")
    if excluded:
        out.append(f"    buyouts owed          "
                   f"${money(_sum(dec(r['buyout_price']) for r in buyouts_owed))}  "
                   f"(EXCLUDED, Russell buys, not paid: {len(buyouts_owed)})")
    if adjustments:
        out.append(f"    adjustments pending   ${money(_sum(a for _, a in adjustments))}  "
                   f"({', '.join(i for i, _ in adjustments)})")
    out.append(f"    client share pending  ${money(pending)}  (SOLD, return window "
               f"or actuals still open)"
               + ("  (upper bound)" if any(r in incomplete for r in pending_rows) else ""))
    if open_settlements:
        out.append(f"    settlements unpaid    "
                   f"${money(_sum(settlement_due(s) for s in open_settlements))}  "
                   f"({', '.join(s['settlement_id'] for s in open_settlements)})")
    out.append(f"    paid in {today.year}          ${money(paid_year)}  ({SETTLEMENTS_NAME})")
    out.append(f"    paid to date          ${money(_sum(paid_amount(s) for s in paid))}")
    flagged = [r["item_id"] for r in rows if FLAG in r["state_note"]]
    if flagged:
        out.append(f"  flagged ({len(flagged)}): {', '.join(flagged)}  -- see `consign sync`")
    return out


def export_rows(code: str, items: list[tuple[str, dict]],
                breakdowns: Optional[dict] = None) -> list[dict]:
    """One line=item row per item, each sold one followed by its line=fee
    rows from `breakdowns` ({item_id: fee_breakdown})."""
    out = []
    for intake, r in items:
        rec = {"client_code": code, "intake": r["intake"] or intake,
               "note": hand_note(r["state_note"]), "line": "item"}
        for k in EXPORT_COLUMNS:
            if k in rec:
                continue
            v = r.get(k, "")
            rec[k] = money(dec(v)) if k in MONEY else ascii_text(v or "")
        rec["state"] = rec["state"] or "INTAKE"
        out.append(rec)
        for f in fee_rows((breakdowns or {}).get(r["item_id"])):
            out.append({**{k: "" for k in EXPORT_COLUMNS}, "client_code": code,
                        "intake": rec["intake"], "item_id": r["item_id"],
                        "lot_id": r["lot_id"], "line": "fee", **f})
    return out


def fee_section(items: list[tuple[str, dict]], breakdowns: dict) -> list[str]:
    """The report's per-item fee breakdown, one block per sale."""
    out = []
    for _, r in items:
        bd = breakdowns.get(r["item_id"])
        if not bd:
            continue
        out.append(f"    {r['item_id']}  sold {r['sold_at'] or '?'}  gross "
                   f"${r['gross'] or '?'}  fees ${r['ebay_fee'] or '?'}  ad "
                   f"${r['ad_fee'] or '?'}  net ${r['net'] or '?'}")
        out += fee_text(bd)
    return ["  fee breakdown (each % SOURCED, DERIVED or FLAT; ad fees come off "
            "gross before the split):"] + out if out else []


def report(intakes: list[Path], client: Optional[str], write_csv: bool,
           today: Optional[date] = None) -> int:
    today = today or date.today()
    registry = load_registry()
    groups: dict[str, list[tuple[str, dict]]] = {}
    names: dict[str, list[str]] = {}
    for d in intakes:
        code = intake_client(d) or "UNASSIGNED"
        if client and code != client:
            continue
        rows, _ = read_ledger(d / LEDGER_NAME)
        migrate(rows, d, intake_client(d))       # in memory only: ids and intake dates
        groups.setdefault(code, []).extend((r["intake"] or d.name, r) for r in rows)
        names.setdefault(code, []).append(d.name)
    if not groups:
        print("no consignment intakes match" + (f" client {client}" if client else ""))
        return 1
    print("States are as of the last `consign sync --apply`.")
    fee_lines = load_fee_lines()
    for code in sorted(groups):
        reg = registry.get(code) or {}
        terms = client_terms(registry, code)
        split = terms["split"]
        items = groups[code]
        print(f"\n=== {code}  {reg.get('name') or '(not in the registry)'}  "
              f"split {split * 100:.0f}% to client  |  {len(items)} item(s) in "
              f"{', '.join(names[code])}")
        print(f"  terms: agreement {terms['agreement_signed'] or 'not signed'}  |  "
              f"W-9 {terms['w9_on_file'] or 'not recorded'}  |  loss rule "
              f"{terms['loss_rule']}")
        settlements = read_settlements(code) if code != "UNASSIGNED" else []
        for line in client_summary(items, split, settlements, terms["loss_rule"], today):
            print(line)
        breakdowns = {r["item_id"]: fee_breakdown(r, fee_lines) for _, r in items}
        for line in fee_section(items, breakdowns):
            print(ascii_text(line))
        if write_csv:
            out = reports_dir(code) / f"{code}-{today.isoformat()}.csv"
            out.parent.mkdir(parents=True, exist_ok=True)
            with out.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
                w.writeheader()
                w.writerows(export_rows(code, items, breakdowns))
            print(f"  [OK] wrote {SA._rel(out)}")
    return 0


# --------------------------------------------------------------------------- #
# settle
# --------------------------------------------------------------------------- #
def _statement_line(sid: str, code: str, line: str, r: dict, **kw) -> dict:
    rec = {c: "" for c in STATEMENT_COLUMNS}
    rec.update(settlement_id=sid, client_code=code, line=line)
    for k in ("item_id", "intake", "description", "lot_id", "sold_at", "delivered_at",
              "cleared_on"):
        rec[k] = r.get(k, "")
    rec.update(kw)
    return rec


def settle(code: str, through: date, *, apply: bool, final: bool = False) -> int:
    """Gather what is owed to `code` through `through` into one settlement:
    CLEARED rows not paid, unpaid buyouts above 0.00, and pending
    adjustments. --apply writes the statement (once; never overwritten) and
    appends the row to settlements.csv. Payment is recorded by hand:
    the accountant sets paid_date and amount_paid, and the next sync copies
    paid_date onto the rows. Below the client's min_payout nothing is
    written and the balance carries forward; final (the agreement has ended)
    pays the full balance whatever it is, a negative one included."""
    terms = client_terms(load_registry(), code)
    dirs = client_dirs(code)
    if not dirs:
        raise SystemExit(f"[X] no consignment ledger for client {code}")
    settlements = read_settlements(code)
    rows: list[dict] = []
    own = {d.name for d in dirs}
    errs = settlement_errors(settlements)
    for msg, names in cross_errors(all_intakes()):
        if names & own:
            errs.append(msg)
        else:                                    # another client's problem, not this payout's
            print(f"! {msg} (not {code}'s; it blocks that client's sync)")
    for d in dirs:
        rs, unknown = read_ledger(d / LEDGER_NAME)
        errs += [f"{d.name}: {e}" for e in validate(rs, unknown, code)]
        migrate(rs, d, code)
        rows += rs
    if errs:
        for e in errs:
            print(f"[X] {e}")
        print("[X] fix these by hand (then `consign sync`); nothing settled")
        return 1
    split, loss_rule = terms["split"], terms["loss_rule"]
    held = in_open_settlement(settlements)
    mates = lot_plan(rows)
    cleared = [r for r in rows if r["state"] == "CLEARED" and not r["paid_to_client_date"]
               and r["item_id"] not in held and r["item_id"] not in mates
               and r["cleared_on"] and iso(r["cleared_on"]) <= through]
    # A sale whose fee lines are missing or don't tie to its ledger fees waits:
    # the client's statement shows every fee, so it can't go out unexplained.
    fee_lines = load_fee_lines()
    breakdown = {r["item_id"]: fee_breakdown(r, fee_lines) for r in cleared}
    for r in cleared:
        bd = breakdown[r["item_id"]]
        if bd and bd["status"] != "OK":
            print(f"  held {r['item_id']}: {bd['status']} ({bd['detail']}); "
                  f"not settled until its fee lines tie")
    cleared = [r for r in cleared if not breakdown[r["item_id"]]
               or breakdown[r["item_id"]]["status"] == "OK"]
    buyouts = [r for r in rows if buyout_owed(r) and r["item_id"] not in held]
    adjustments = pending_adjustments(rows, settlements, split, loss_rule)
    if not (cleared or buyouts or adjustments):
        print(f"{code}: nothing to settle through {through.isoformat()}"
              + (f" ({len(held)} item(s) on an unpaid settlement)" if held else ""))
        return 0
    # Under the client's min_payout, sale shares and adjustments carry forward,
    # but a buyout is still paid in this run (agreement §7.2).
    owed = (_sum(dec(r["client_share"]) for r in cleared)
            + _sum(dec(r["buyout_price"]) for r in buyouts)
            + _sum(a for _, a in adjustments))
    if not final and terms["min_payout"] and owed < terms["min_payout"]:
        under = (f"${money(owed)} owed through {through.isoformat()} is under the "
                 f"${money(terms['min_payout'])} minimum payout (clients.csv min_payout)")
        if not buyouts:
            print(f"{code}: {under}: carried forward, nothing written. `settle --final` "
                  f"pays it when the agreement ends.")
            return 0
        print(f"{code}: {under}: sales and adjustments carry forward; the buyout(s) "
              f"are paid now (agreement §7.2)")
        cleared, adjustments = [], []

    nums = [int(m.group(1)) for s in settlements
            if (m := re.fullmatch(rf"{re.escape(code)}-S(\d+)", s["settlement_id"]))]
    sid = f"{code}-S{max(nums, default=0) + 1:03d}"
    ends = [iso(s["period_end"]) for s in settlements if iso(s["period_end"])]
    intakes = [iso(r["intake"]) for r in rows if iso(r["intake"])]
    start = (max(ends) + timedelta(days=1) if ends
             else min(intakes) if intakes else through)
    tot = lambda k: _sum(dec(r[k]) for r in cleared)  # noqa: E731
    share = tot("client_share") + _sum(dec(r["buyout_price"]) for r in buyouts)
    srow = {c: "" for c in SETTLEMENT_COLUMNS}
    srow.update(settlement_id=sid, client_code=code, period_start=start.isoformat(),
                period_end=through.isoformat(),
                item_ids=" ".join(r["item_id"] for r in cleared + buyouts),
                gross=money(tot("gross")), fees=money(tot("ebay_fee")),
                ad=money(tot("ad_fee")), postage=money(tot("actual_postage")),
                handling=money(tot("handling")),
                other=money(tot("other_costs")), net=money(tot("net")),
                client_share=money(share),
                adjustments=" ".join(f"{i}={money(a)}" for i, a in adjustments))
    due = settlement_due(srow)

    by_id = {r["item_id"]: r for r in rows}
    lines = []
    for r in cleared:
        lines.append(_statement_line(
            sid, code, "sale", r, **{k: money(dec(r[k])) for k in (
                "item_price", "buyer_shipping", "refunded", "gross", "ebay_fee", "ad_fee",
                "actual_postage", "handling", "other_costs", "net", "client_share")},
            split=str(row_split(r, split)), amount=money(dec(r["client_share"])),
            note=r["other_costs_note"]))
        lines += [_statement_line(sid, code, "fee", r, **f)
                  for f in fee_rows(breakdown[r["item_id"]])]
        for mid, (lot, lead) in mates.items():
            if lead is r:
                lines.append(_statement_line(sid, code, "lot item", by_id[mid],
                                             note=f"in lot {lot} with {r['item_id']}"))
    for r in buyouts:
        lines.append(_statement_line(sid, code, "buyout", r,
                                     amount=money(dec(r["buyout_price"])),
                                     note="bought by Pop's Games, no split"))
    for iid, amt in adjustments:
        r = by_id[iid]
        lines.append(_statement_line(
            sid, code, "adjustment", r, amount=money(amt),
            note=f"paid ${r['client_share']} on {r['paid_to_client_date']} "
                 f"({r['settlement_id'] or 'no settlement id'}); now "
                 f"${money(recalculated_share(r, split, loss_rule))}"))
    lines.append({**{c: "" for c in STATEMENT_COLUMNS}, "settlement_id": sid,
                  "client_code": code, "line": "total",
                  **{k: srow[k2] for k, k2 in (("gross", "gross"), ("ebay_fee", "fees"),
                                               ("ad_fee", "ad"), ("actual_postage", "postage"),
                                               ("handling", "handling"),
                                               ("other_costs", "other"), ("net", "net"),
                                               ("client_share", "client_share"))},
                  "amount": money(due),
                  "note": f"period {srow['period_start']} to {srow['period_end']}"})

    print(f"{sid}  client {code}  {srow['period_start']} to {srow['period_end']}"
          + ("  FINAL: full balance, no minimum" if final else ""))
    print(f"  sales {len(cleared)}  gross ${srow['gross']}  handling ${srow['handling']}  "
          f"net ${srow['net']}  share ${money(tot('client_share'))}")
    if buyouts:
        print(f"  buyouts {len(buyouts)}  ${money(_sum(dec(r['buyout_price']) for r in buyouts))}")
    for iid, amt in adjustments:
        print(f"  adjustment {iid}  ${money(amt)}")
    print(f"  amount due ${money(due)}")
    if due < 0:
        print("  ! the amount due is negative: the client owes this back (carry it "
              "forward or invoice it)")
    if terms["w9_on_file"] != "yes":
        print(f"  ! no W-9 on file for {code} (clients.csv w9_on_file)")
    if not terms["agreement_signed"]:
        print(f"  ! no signed agreement recorded for {code} (clients.csv agreement_signed)")
    stmt = reports_dir(code) / f"{sid}.csv"
    if not apply:
        print(f"\n[DRY RUN] Nothing written. --apply writes {SA._rel(stmt)} and adds "
              f"{sid} to {SA._rel(settlements_path(code))}.")
        return 0
    if stmt.exists():
        raise SystemExit(f"[X] {SA._rel(stmt)} already exists and a statement is never "
                         f"overwritten: check {SETTLEMENTS_NAME} for a missing {sid}")
    stmt.parent.mkdir(parents=True, exist_ok=True)
    with stmt.open("x", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=STATEMENT_COLUMNS)
        w.writeheader()
        w.writerows(lines)
    backup = _write_csv(settlements_path(code), SETTLEMENT_COLUMNS, settlements + [srow])
    print(f"  [OK] wrote {SA._rel(stmt)}")
    print(f"  [OK] added {sid} to {SA._rel(settlements_path(code))}"
          + (f" (backup {backup.name})" if backup else ""))
    print(f"  When it is paid: set paid_date and amount_paid on {sid}, then "
          f"`consign sync --apply` marks its rows SETTLED.")
    return 0


# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="ebz consign", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import", help="bulk-add rows from a CSV/TSV file, or - for stdin")
    p.add_argument("intake", help="ledger folder, e.g. CONSIGN-K01 (or an older "
                                  "CONSIGN-20261022)")
    p.add_argument("source", help="CSV or TSV file with a `description` column, or -")
    p.add_argument("--client", help="client code (e.g. K01); needed for a new folder "
                                    "not named CONSIGN-<code>")
    p.add_argument("--intake", dest="received", metavar="YYYY-MM-DD",
                   help="date the items were received (default: context.txt "
                        "`acquired:`, the folder's date, or today)")
    p = sub.add_parser("sync", help="derive states and fill eBay figures from the ledgers")
    p.add_argument("intake", nargs="?", help="one ledger folder (default: every one)")
    p.add_argument("--apply", action="store_true", help="write the ledger (backup first)")
    p = sub.add_parser("report", help="per-client account: states, value, money owed")
    p.add_argument("intake", nargs="?", help="one ledger folder (default: every one)")
    p.add_argument("--client", help="only this client")
    p.add_argument("--csv", action="store_true",
                   help="also write reports/consign/<client>/<client>-<date>.csv")
    p = sub.add_parser("settle", help="gather what is owed into one settlement and "
                                      "a fixed statement")
    p.add_argument("client", help="client code, e.g. K01")
    p.add_argument("--through", required=True, metavar="YYYY-MM-DD",
                   help="settle rows cleared on or before this date")
    p.add_argument("--apply", action="store_true",
                   help="write the statement and the settlements.csv row")
    p.add_argument("--final", action="store_true",
                   help="the agreement has ended: pay the full balance, ignoring "
                        "min_payout, even if it is negative")
    args = ap.parse_args(argv)

    if args.cmd == "import":
        text = (sys.stdin.read() if args.source == "-"
                else Path(args.source).read_text(encoding="utf-8-sig"))
        d = intake_dir(args.intake)
        added, skipped = import_rows(d, parse_table(text), args.client, args.received)
        print(f"{d.name}: added {added} row(s), skipped {skipped} duplicate(s)")
        if args.client and args.client not in load_registry():
            print(f"  ! {args.client} is not in {SA._rel(registry_path())} yet: add a row "
                  f"(split defaults to 0.5 until then)")
        return 0
    if args.cmd == "settle":
        through = iso(args.through)
        if through is None:
            raise SystemExit(f"[X] --through {args.through!r} is not a YYYY-MM-DD date")
        return settle(args.client, through, apply=args.apply, final=args.final)

    # Only sync is refused in a worktree: inventory/ links to main's real
    # ledgers but the listings/sales ledgers don't, so sync would regress every
    # sold row. import, report and settle are safe there, and agents run there.
    if args.cmd == "sync":
        main_root = offsite.data_root(REPO).resolve()
        if REPO.resolve() != main_root:
            raise SystemExit(f"[X] run consign sync from the main checkout ({main_root}): "
                             f"a worktree has no ledgers, and sync would regress "
                             f"every sold row")
    intakes = [intake_dir(args.intake)] if args.intake else all_intakes()
    missing = [d for d in intakes if args.intake and not (d / LEDGER_NAME).exists()]
    if missing:
        raise SystemExit(f"[X] no {LEDGER_NAME} in {', '.join(str(d) for d in missing)}")
    if args.cmd == "report":
        return report(intakes, args.client, args.csv)

    errs = validate_all(all_intakes())
    if errs:
        for e in errs:
            print(f"[X] {e}")
        print("[X] fix these by hand; nothing synced")
        return 1
    books, registry = Books(), load_registry()
    worst = max((sync_intake(d, books, registry, apply=args.apply) for d in intakes),
                default=0)
    if not args.apply:
        print(f"\n[DRY RUN] Nothing written. Re-run with --apply to write "
              f"{LEDGER_NAME} (a timestamped backup is made first).")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())
