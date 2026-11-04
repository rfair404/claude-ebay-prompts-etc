#!/usr/bin/env python3
"""lower — the /lower markdown queue: which live listings take their next
price step, by how much, and what stops one.

    python -m lib.cli lower scan [--date D] [--max-reprice 10]     # read-only
    python -m lib.cli lower apply --queue Q --yes-skus a,b [--confirm]
    python -m lib.cli lower backfill                               # once, idempotent

SCAN is read-only on eBay and on the ledgers. It pulls every active listing
(Trading GetMyeBaySelling, then GetItem for the Best Offer floors of the ones
that reach the cut), finds each one's last price change, and writes
reports/lower/<date>/queue.csv, queue.md and review.html. Nothing else.

APPLY touches only PROPOSED rows Russell said yes to. Without --confirm it runs
`listing --update <draft> --fields price[,bestoffer] --dry-run` per row and
writes nothing. With --confirm it re-reads the live price first and refuses a
row whose price moved since the scan, then runs the real update and appends
the change to reports/lower/price_changes.csv (source=lower). It never
confirms by itself. It replaces the per-run reports/*_apply.py scripts.

THE RULES (inventory-manager.md "The house pricing rules"; tested in
tests/test_lower.py):

* Due: the last price change (latest price_changes.csv row, else the eBay
  start date, whichever is later) is more than 15 days ago.
* Only asks of $36+ are cut. $30-35.99 and $25 or under are left alone.
* The step: new = max(Recommended + margin, current - (current - Recommended)/3),
  never more than 15% of current, whole dollars. margin = max($2, 5% of
  Recommended). The third-of-the-gap value rounds DOWN; the margin and 15%
  bounds round UP, so rounding never crosses them. A step under $2 is skipped.
  At or under Recommended + margin the listing is AT_TARGET: flagged, not cut.
* Best Offer floors scale by new/current, rounded down to whole dollars.
* Floors never crossed: Conservative tier, cost basis (item-level spend:),
  the consignment row's floor, and on solid gold or silver the melt value at
  LIVE spot (no cached spot; spot unavailable or weight unknown -> HELD).
* price.txt older than 15 days, missing, or without a Recommended tier ->
  STALE_PRICE; at most --max-reprice of them are queued for a PRICE re-run.
* KBD gold (KBD-C019/C020/C021) is HELD. K01 consignment is proposed with a
  "no ads" note. Listings in reports/lower/holds.csv are HELD until their date.
* Default-store prices are delivered (free shipping); shipping is never
  priced separately here. A price Russell set in Seller Hub isn't detectable,
  so every row shows its last-change source instead.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable, Optional

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "lib"), str(REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LOWER_DIR = REPO / "reports" / "lower"
LOG_PATH = LOWER_DIR / "price_changes.csv"
HOLDS_PATH = LOWER_DIR / "holds.csv"
LOG_FIELDS = ["sku", "item_id", "store", "date", "old_ask", "new_ask",
              "old_floors", "new_floors", "source"]
HOLD_FIELDS = ["sku", "item_id", "store", "until", "reason"]

DUE_DAYS = 15            # a change more than this many days ago is due again
PRICE_FRESH_DAYS = 15    # price.txt younger than this is reused
MAX_REPRICE = 10         # STALE_PRICE re-runs queued per scan
MIN_ASK = Decimal(36)    # aging cuts only touch $36+ (Russell's rule)
MIN_STEP = Decimal(2)
STEP_CAP = Decimal("0.15")
MARGIN_PCT = Decimal("0.05")
MARGIN_MIN = Decimal(2)
KBD_GOLD = {"KBD-C019", "KBD-C020", "KBD-C021"}   # held until Russell says gold recovered
GRAMS_PER_OZT = Decimal("31.1034768")

PROPOSED, AT_TARGET, STALE_PRICE, HELD, SKIPPED = (
    "PROPOSED", "AT_TARGET", "STALE_PRICE", "HELD", "SKIPPED")
STATUSES = (PROPOSED, AT_TARGET, STALE_PRICE, HELD, SKIPPED)

QUEUE_FIELDS = [
    "store", "item_id", "sku", "title", "status", "reason", "note",
    "current_price", "new_price", "step", "recommended", "conservative", "push_high",
    "auto_decline", "new_auto_decline", "auto_accept", "new_auto_accept",
    "last_change", "last_change_source", "days_since_change", "start_date",
    "price_txt", "price_txt_age_days", "rerun", "melt", "shoot_dir",
    "gallery_url", "watchers",
]


# ---------------------------------------------------------------------------
# money
# ---------------------------------------------------------------------------

def dec(v) -> Optional[Decimal]:
    """'$1,949.00' / 49.0 / '' -> Decimal or None."""
    if v is None:
        return None
    s = str(v).replace(",", "").replace("$", "").strip()
    if not s or s.lower() in ("none", "null", "n/a"):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def dollars_down(x: Decimal) -> Decimal:
    return x.to_integral_value(rounding=ROUND_FLOOR)


def dollars_up(x: Decimal) -> Decimal:
    return x.to_integral_value(rounding=ROUND_CEILING)


def money(v: Optional[Decimal]) -> str:
    return "" if v is None else f"{v:.2f}"


# ---------------------------------------------------------------------------
# cut math
# ---------------------------------------------------------------------------

def margin(rec: Decimal) -> Decimal:
    """How close to Recommended counts as there: $2 or 5%, whichever is more."""
    return max(MARGIN_MIN, rec * MARGIN_PCT)


@dataclass
class Cut:
    status: str
    new: Optional[Decimal] = None
    step: Optional[Decimal] = None
    new_decline: Optional[Decimal] = None
    new_accept: Optional[Decimal] = None
    reason: str = ""


def scale_floor(floor: Optional[Decimal], new: Decimal, current: Decimal) -> Optional[Decimal]:
    """A Best Offer floor moved in proportion to the ask, whole dollars down."""
    if floor is None or floor <= 0:
        return None
    return dollars_down(floor * new / current)


def cut_math(current: Decimal, rec: Decimal, auto_decline: Optional[Decimal] = None,
             auto_accept: Optional[Decimal] = None) -> Cut:
    """The next step down from `current` toward Recommended `rec`."""
    m = margin(rec)
    target = rec + m
    if current <= target:
        return Cut(AT_TARGET, reason=f"at target: within ${m:.2f} of Recommended ${rec:.2f}")
    third = current - (current - rec) / 3
    new = max(dollars_down(third), dollars_up(target),
              dollars_up(current * (1 - STEP_CAP)))
    step = current - new
    if step < MIN_STEP:
        return Cut(SKIPPED, reason=f"step ${step:.2f} is under $2")
    return Cut(PROPOSED, new=new, step=step,
               new_decline=scale_floor(auto_decline, new, current),
               new_accept=scale_floor(auto_accept, new, current))


def ask_rule(current: Decimal) -> Optional[str]:
    """Why an ask is too low for an aging cut, or None when it may be cut."""
    if current >= MIN_ASK:
        return None
    if current >= 30:
        return "$30-35.99: left alone (Russell's rule)"
    if current <= 25:
        return "$25 or under: left alone (Russell's rule)"
    return "under $36: aging cuts only touch $36+ (Russell's rule)"


# ---------------------------------------------------------------------------
# precious metal (specializations/jewelry.md: melt is a floor, never the price)
# ---------------------------------------------------------------------------

_GOLD_RE = re.compile(r"\b(10|14|18|22|24)\s?k(?:t|arat)?\b", re.I)
_GOLD_NOT_SOLID = re.compile(
    r"\b1/(?:10|20)\b|gold[\s-]?filled|\bG\.?F\.?\b|\bRGP\b|\bG\.?P\.?\b|\bHGE\b|"
    r"plated|plate\b|gold[\s-]?tone|vermeil|electroplat", re.I)
_SILVER_RE = re.compile(r"\bsterling\b|(?<![\d/])\.?925\b|\bcoin silver\b", re.I)
_SILVER_NOT = re.compile(r"silver[\s-]?plate|silverplate|silver[\s-]?tone|\bEPNS\b|"
                         r"nickel silver|german silver|\bplated\b", re.I)
_KARAT = {"10": Decimal("0.417"), "14": Decimal("0.585"), "18": Decimal("0.750"),
          "22": Decimal("0.917"), "24": Decimal("0.999")}
_STERLING = Decimal("0.925")
_WEIGHT_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s?(?:g|gr|grams?)\b", re.I)


def metal_of(title: str) -> Optional[tuple[str, Decimal]]:
    """('gold', purity) / ('silver', 0.925) for solid precious metal, else None.
    Mixed gold and silver takes gold: the higher floor."""
    t = title or ""
    m = _GOLD_RE.search(t)
    if m and not _GOLD_NOT_SOLID.search(t):
        return "gold", _KARAT[m.group(1)]
    if _SILVER_RE.search(t) and not _SILVER_NOT.search(t):
        return "silver", _STERLING
    return None


def weight_of(title: str, meta: Optional[dict] = None) -> Optional[Decimal]:
    """Grams from draft meta.weight_g, else a '3.73g' in the title."""
    w = dec((meta or {}).get("weight_g"))
    if w and w > 0:
        return w
    m = _WEIGHT_RE.search(title or "")
    return dec(m.group(1)) if m else None


def melt_value(weight_g: Decimal, purity: Decimal, spot_per_ozt: Decimal) -> Decimal:
    return weight_g * purity * spot_per_ozt / GRAMS_PER_OZT


# ---------------------------------------------------------------------------
# price.txt
# ---------------------------------------------------------------------------

def read_tiers(text: str) -> dict:
    """Conservative / Recommended / Push-high from price.txt's current block
    (above the first `=== END`, the way review_card_html reads it), with the
    tier regexes tools/price_vs_actual.py already proved on the files."""
    import price_vs_actual as pva
    cur = text.split("\n=== END", 1)[0]
    out = {}
    for key, name in (("floor", "conservative"), ("recommended", "recommended"),
                      ("ceiling", "push_high")):
        m = pva.TIER_RE[key].search(cur)
        out[name] = dec(m.group(1)) if m else None
    return out


@dataclass
class PriceInfo:
    path: Optional[Path]
    age_days: Optional[int]
    tiers: dict = field(default_factory=dict)
    stale: str = ""          # why it can't be used, "" when it can


def price_info(shoot: Optional[Path], today: date) -> PriceInfo:
    p = shoot / "price.txt" if shoot else None
    if not p or not p.exists():
        return PriceInfo(None, None, stale="no price.txt on disk")
    age = (today - datetime.fromtimestamp(p.stat().st_mtime).date()).days
    tiers = read_tiers(p.read_text(encoding="utf-8", errors="replace"))
    info = PriceInfo(p, age, tiers)
    if age > PRICE_FRESH_DAYS:
        info.stale = f"price.txt is {age} days old"
    elif not tiers.get("recommended"):
        info.stale = "price.txt has no Recommended tier"
    return info


# ---------------------------------------------------------------------------
# the price-change log
# ---------------------------------------------------------------------------

def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _log_key(r: dict) -> tuple:
    return ((r.get("sku") or "").strip(), (r.get("item_id") or "").strip(),
            (r.get("date") or "").strip(), (r.get("source") or "").strip())


def append_log(path: Path, entries: Iterable[dict]) -> int:
    """Append rows not already in the log (same sku, item_id, date, source).
    Append-only: existing rows are never rewritten. Returns rows added."""
    have = {_log_key(r) for r in read_csv(path)}
    new = []
    for e in entries:
        k = _log_key(e)
        if k in have:
            continue
        have.add(k)
        new.append({f: e.get(f, "") for f in LOG_FIELDS})
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if fresh:
            w.writeheader()
        w.writerows(new)
    return len(new)


def _iso(s: str) -> Optional[date]:
    try:
        return date.fromisoformat((s or "").strip()[:10])
    except ValueError:
        return None


def _same_listing(r: dict, *, sku: str, item_id: str, store: str) -> bool:
    rs = (r.get("store") or "").strip()
    if rs and store and rs != store:
        return False
    return bool((sku and (r.get("sku") or "").strip() == sku)
                or (item_id and (r.get("item_id") or "").strip() == item_id))


def last_change(log_rows: list[dict], *, sku: str, item_id: str, store: str,
                start: Optional[date]) -> tuple[Optional[date], str]:
    """(date, source) of the listing's last price change: the latest log row,
    or the start date when that is later (a relist starts the clock again)."""
    best: tuple[Optional[date], str] = (None, "")
    for r in log_rows:
        if not _same_listing(r, sku=sku, item_id=item_id, store=store):
            continue
        d = _iso(r.get("date", ""))
        if d and (best[0] is None or d >= best[0]):
            best = (d, r.get("source", "") or "log")
    if start and (best[0] is None or start > best[0]):
        return start, "listed"
    return best


def is_due(last: Optional[date], today: date) -> bool:
    return last is not None and (today - last).days > DUE_DAYS


# ---------------------------------------------------------------------------
# backfill from the old aging reports
# ---------------------------------------------------------------------------

_FILE_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_LOG_LINE = re.compile(
    r"^== (\S+) (\S+)(?: (default|junk))? \$([\d.,]+) -> \$([\d.,]+)"
    r"(?:\s+floor -> \$([\d.,]+))?")


def parse_last_cut(value: str, year: int) -> Optional[tuple[date, str, str]]:
    """A `last_cut` / `cut_last_time` cell -> (date, old, new), or None.
    '2026-10-11 -5% (30+d rule) 39.99->38.00', 'cut10-22', 'reverted10-22'
    are changes; 'never ...', 'in10-22:' (only listed in that table) and
    'new since 10-22' are not."""
    v = (value or "").strip()
    if not v or v.lower().startswith("never"):
        return None
    d = None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    else:
        m = re.search(r"(?:cut|reverted)(\d{1,2})-(\d{1,2})", v)
        if m:
            d = date(year, int(m.group(1)), int(m.group(2)))
    if d is None:
        return None
    pair = re.search(r"([\d.]+)->([\d.]+)", v)
    return d, (pair.group(1) if pair else ""), (pair.group(2) if pair else "")


def entries_from_aging_csv(path: Path) -> list[dict]:
    """Price changes recorded in an aging table's last_cut column."""
    fd = _FILE_DATE.search(path.name)
    if not fd:
        return []
    year = int(fd.group(1)[:4])
    out = []
    for r in read_csv(path):
        cell = r.get("last_cut", r.get("cut_last_time", ""))
        hit = parse_last_cut(cell, year)
        if not hit:
            continue
        d, old, new = hit
        out.append({"sku": r.get("sku", ""), "item_id": r.get("listing_id", ""),
                    "store": r.get("store", ""), "date": d.isoformat(),
                    "old_ask": old, "new_ask": new, "old_floors": "", "new_floors": "",
                    "source": "backfill:last_cut"})
    return out


def entries_from_apply_log(path: Path, table: Optional[Path] = None) -> list[dict]:
    """Rows an *_apply.log says were sent. Only a log that ends APPLIED
    counts (a DRY RUN log changed nothing). `table` is the run's CSV, read for
    the store, item number and old floor when the log line lacks them."""
    fd = _FILE_DATE.search(path.name)
    text = path.read_text(encoding="utf-8", errors="replace")
    if not fd or not re.search(r"(?m)^APPLIED", text):
        return []
    by_sku = {r.get("sku", ""): r for r in (read_csv(table) if table else []) if r.get("sku")}
    out = []
    for ln in text.splitlines():
        m = _LOG_LINE.match(ln)
        if not m:
            continue
        a, b, store, old, new, floor = m.groups()
        item_id, sku = (a, b) if a.isdigit() else ("", b)
        row = by_sku.get(sku, {})
        out.append({"sku": sku, "item_id": item_id or row.get("listing_id", ""),
                    "store": store or row.get("store", ""), "date": fd.group(1),
                    "old_ask": old.replace(",", ""), "new_ask": new.replace(",", ""),
                    "old_floors": row.get("auto_decline", "") if floor else "",
                    "new_floors": floor or "",
                    "source": f"backfill:{path.name}"})
    return out


def holds_from_aging_csv(path: Path) -> list[dict]:
    """Seasonal holds and Russell's own prices from an aging table."""
    out = []
    for r in read_csv(path):
        until = (r.get("hold_until") or "").strip()
        why = (r.get("excluded_reason") or "").strip()
        # K01 is proposed with a note, never held for being K01 (its timing
        # rows say "Russell's timing" but are a past cut, not a standing hold)
        russell = ("Russell" in why and "Russell's rule" not in why
                   and not why.startswith("K01"))
        if until or russell:
            out.append({"sku": r.get("sku", ""), "item_id": r.get("listing_id", ""),
                        "store": r.get("store", ""), "until": until,
                        "reason": why or "hold"})
    return out


def aging_tables(reports: Path) -> list[Path]:
    return sorted(list(reports.glob("aging_price_cut_*.csv"))
                  + list(reports.glob("markdowns/*-aging.csv")),
                  key=lambda p: (_FILE_DATE.search(p.name).group(1)
                                 if _FILE_DATE.search(p.name) else "", p.name))


def backfill(reports: Path, log_path: Path, holds_path: Path) -> tuple[int, int, list[str]]:
    """Seed price_changes.csv (and holds.csv) from the old aging reports.
    Safe to run again: rows already in either file are not added twice.
    Returns (log rows added, hold rows added, notes)."""
    entries: list[dict] = []
    notes: list[str] = []
    for log in sorted(reports.glob("**/*_apply.log")):
        table = log.with_name(log.name.replace("_apply.log", ".csv"))
        got = entries_from_apply_log(log, table if table.exists() else None)
        entries += got
        notes.append(f"{log.name}: {len(got)} applied rows")
    tables = aging_tables(reports)
    for t in tables:
        entries += entries_from_aging_csv(t)
    # one change per listing per day, whatever file said it
    seen, uniq = set(), []
    for e in entries:
        k = (e["sku"] or e["item_id"], e["date"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    added = append_log(log_path, uniq)
    holds_added = 0
    if tables:
        have = {(h.get("sku", ""), h.get("item_id", ""), h.get("until", ""))
                for h in read_csv(holds_path)}
        new = [h for h in holds_from_aging_csv(tables[-1])
               if (h["sku"], h["item_id"], h["until"]) not in have]
        if new:
            holds_path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not holds_path.exists() or holds_path.stat().st_size == 0
            with holds_path.open("a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=HOLD_FIELDS)
                if fresh:
                    w.writeheader()
                w.writerows(new)
            holds_added = len(new)
        notes.append(f"holds from {tables[-1].name}: {holds_added} added")
    return added, holds_added, notes


def active_hold(holds: list[dict], *, sku: str, item_id: str, store: str,
                today: date) -> Optional[str]:
    for h in holds:
        if not _same_listing(h, sku=sku, item_id=item_id, store=store):
            continue
        until = _iso(h.get("until", ""))
        if until and until < today:
            continue
        return (h.get("reason") or "hold") + (f" (until {until})" if until else "")
    return None


# ---------------------------------------------------------------------------
# one listing -> one queue row
# ---------------------------------------------------------------------------

@dataclass
class Listing:
    store: str
    item_id: str
    sku: str
    title: str
    price: Decimal
    start: Optional[date] = None
    listing_type: str = "FixedPriceItem"
    gallery_url: str = ""
    watchers: str = ""


@dataclass
class Ctx:
    """Everything evaluate() reads, injected so tests need no eBay or disk."""
    today: date
    log_rows: list = field(default_factory=list)
    holds: list = field(default_factory=list)
    consign_row: Callable[[Listing], Optional[dict]] = lambda l: None
    shoot_dir: Callable[[Listing], Optional[Path]] = lambda l: None
    meta: Callable[[Listing], dict] = lambda l: {}
    cost: Callable[[Listing], Optional[Decimal]] = lambda l: None
    floors: Callable[[Listing], tuple] = lambda l: (None, None)
    spot: Callable[[str], Optional[Decimal]] = lambda metal: None


def evaluate(l: Listing, ctx: Ctx) -> dict:
    row = {f: "" for f in QUEUE_FIELDS}
    row.update(store=l.store, item_id=l.item_id, sku=l.sku, title=l.title,
               current_price=money(l.price), gallery_url=l.gallery_url,
               watchers=l.watchers, start_date=l.start.isoformat() if l.start else "")
    notes = []

    def done(status, reason=""):
        row["status"], row["reason"], row["note"] = status, reason, "; ".join(notes)
        return row

    last, src = last_change(ctx.log_rows, sku=l.sku, item_id=l.item_id,
                            store=l.store, start=l.start)
    row["last_change"] = last.isoformat() if last else ""
    row["last_change_source"] = src
    row["days_since_change"] = str((ctx.today - last).days) if last else ""

    crow = ctx.consign_row(l)
    if crow:
        code = (crow.get("client_code") or "").strip()
        if (crow.get("item_id") or "").strip() in KBD_GOLD:
            return done(HELD, "KBD gold hold (until Russell says gold recovered)")
        if code == "K01":
            notes.append("K01 consignment (no ads)")
    if not l.sku:
        notes.append("no SKU: Seller Hub by hand")

    if l.listing_type and l.listing_type != "FixedPriceItem":
        return done(SKIPPED, f"{l.listing_type} (not fixed price)")
    hold = active_hold(ctx.holds, sku=l.sku, item_id=l.item_id, store=l.store,
                       today=ctx.today)
    if hold:
        return done(HELD, hold)
    if last is None:
        return done(HELD, "no start date or price change on record")
    if not is_due(last, ctx.today):
        return done(SKIPPED, f"changed {(ctx.today - last).days} days ago ({src})")
    low = ask_rule(l.price)
    if low:
        return done(HELD, low)

    shoot = ctx.shoot_dir(l)
    row["shoot_dir"] = str(shoot.relative_to(REPO)) if shoot and shoot.is_relative_to(REPO) \
        else (str(shoot) if shoot else "")
    info = price_info(shoot, ctx.today)
    row["price_txt"] = row["shoot_dir"] + "/price.txt" if info.path else ""
    row["price_txt_age_days"] = "" if info.age_days is None else str(info.age_days)
    t = info.tiers
    row["recommended"], row["conservative"], row["push_high"] = (
        money(t.get("recommended")), money(t.get("conservative")), money(t.get("push_high")))
    if info.stale:
        return done(STALE_PRICE, info.stale)

    decline, accept = ctx.floors(l)
    row["auto_decline"], row["auto_accept"] = money(decline), money(accept)
    cut = cut_math(l.price, t["recommended"], decline, accept)
    if cut.status != PROPOSED:
        return done(cut.status, cut.reason)
    row.update(new_price=money(cut.new), step=money(cut.step),
               new_auto_decline=money(cut.new_decline), new_auto_accept=money(cut.new_accept))

    # floors never crossed
    cons = t.get("conservative")
    if cons and cut.new < cons:
        return done(HELD, f"cut to ${cut.new} would go under Conservative ${cons:.2f}")
    cost = ctx.cost(l)
    if cost and cut.new < cost:
        return done(HELD, f"cut to ${cut.new} would go under cost ${cost:.2f}")
    cfloor = dec((crow or {}).get("floor"))
    if cfloor and cut.new < cfloor:
        return done(HELD, f"cut to ${cut.new} would go under the consignment floor ${cfloor:.2f}")
    metal = metal_of(l.title)
    if metal:
        kind, purity = metal
        w = weight_of(l.title, ctx.meta(l))
        if not w:
            return done(HELD, f"solid {kind}: weight not on disk, melt floor unproven")
        spot = ctx.spot(kind)
        if not spot:
            return done(HELD, "spot unavailable")
        melt = melt_value(w, purity, spot)
        row["melt"] = money(dollars_up(melt))
        if cut.new < melt:
            return done(HELD, f"cut to ${cut.new} would go under melt ${melt:.2f}")
        if cut.new_decline is not None and cut.new_decline < melt:
            return done(HELD, f"Best Offer floor ${cut.new_decline} would go under "
                              f"melt ${melt:.2f}")
        notes.append(f"{kind} melt ${melt:.2f} at live spot ${spot:.2f}/ozt")
    return done(PROPOSED)


def cap_reruns(rows: list[dict], max_reprice: int) -> None:
    """Queue the oldest STALE_PRICE rows for a PRICE re-run, `max_reprice` max."""
    stale = [r for r in rows if r["status"] == STALE_PRICE]
    stale.sort(key=lambda r: -int(r["days_since_change"] or 0))
    for i, r in enumerate(stale):
        if i < max_reprice:
            r["rerun"] = "yes"
        else:
            r["rerun"] = "no"
            r["reason"] += f"; over this scan's re-run cap ({max_reprice})"


def scan_rows(listings: list[Listing], ctx: Ctx, max_reprice: int = MAX_REPRICE) -> list[dict]:
    rows = [evaluate(l, ctx) for l in listings]
    cap_reruns(rows, max_reprice)
    order = {s: i for i, s in enumerate(STATUSES)}
    rows.sort(key=lambda r: (order[r["status"]], r["store"], -int(r["days_since_change"] or 0)))
    return rows


# ---------------------------------------------------------------------------
# reports
# ---------------------------------------------------------------------------

def write_queue(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=QUEUE_FIELDS)
        w.writeheader()
        w.writerows(rows)


def summary_md(rows: list[dict], today: date, *, stores_scanned: list[str],
               spot_seen: dict, max_reprice: int) -> str:
    from collections import Counter
    by = Counter((r["store"], r["status"]) for r in rows)
    prop = [r for r in rows if r["status"] == PROPOSED]
    drop = sum((dec(r["step"]) or Decimal(0)) for r in prop)
    out = [f"# /lower queue {today.isoformat()} (proposal only, nothing applied)", "",
           f"Stores scanned: {', '.join(stores_scanned)}. {len(rows)} active listings. "
           f"Due = last price change more than {DUE_DAYS} days ago.", ""]
    for s in stores_scanned:
        out.append(f"- **{s}**: " + ", ".join(f"{st} {by[(s, st)]}" for st in STATUSES))
    out += ["", f"## Proposed: {len(prop)} listings, ${drop:.2f} off list in total", ""]
    for r in prop:
        fl = (f", floor ${r['auto_decline']} -> ${r['new_auto_decline']}"
              if r["new_auto_decline"] else "")
        out.append(f"- {r['item_id']} {r['sku'] or '(no SKU)'} {r['title'][:50]}: "
                   f"${r['current_price']} -> ${r['new_price']}{fl} "
                   f"(Rec ${r['recommended']}, {r['days_since_change']} d)"
                   + (f" [{r['note']}]" if r["note"] else ""))
    at = [r for r in rows if r["status"] == AT_TARGET]
    out += ["", f"## At target: {len(at)} (Russell's call, no cut)", ""]
    out += [f"- {r['item_id']} {r['title'][:50]}: ${r['current_price']} vs Rec "
            f"${r['recommended']}" for r in at]
    st = [r for r in rows if r["status"] == STALE_PRICE]
    q = [r for r in st if r["rerun"] == "yes"]
    out += ["", f"## STALE_PRICE: {len(st)}; {len(q)} queued for a PRICE re-run "
            f"(cap {max_reprice}; one price-researcher run each, no per-run cost on record)", ""]
    out += [f"- {r['item_id']} {r['sku']} {r['title'][:50]}: {r['reason']} "
            f"-> {r['shoot_dir'] or '(no shoot dir)'}" for r in q]
    held = [r for r in rows if r["status"] == HELD]
    def _group(reason: str) -> str:
        if reason.startswith(("$", "under $36")):
            return reason  # the price-band rules read whole
        return re.sub(r"\$[\d.,]+|\(until [^)]*\)", "", reason).strip()[:70]

    reasons = Counter(_group(r["reason"]) for r in held)
    out += ["", f"## Held: {len(held)}", ""]
    out += [f"- {n} x {why}" for why, n in reasons.most_common()]
    named = [r for r in held if not r["reason"].startswith(("$", "under $36"))]
    if named:
        out += ["", "Held by name:"]
        out += [f"- {r['item_id']} {r['sku']} {r['title'][:45]}: {r['reason']}" for r in named]
    if spot_seen:
        out += ["", "Live spot used: " + ", ".join(f"{k} ${v:.2f}/ozt" if v else f"{k} unavailable"
                                                  for k, v in spot_seen.items())]
    out += ["", "## Apply (Russell's yes; the listing manager runs it)", "",
            "```bash",
            f"python -m lib.cli lower apply --queue reports/lower/{today.isoformat()}/queue.csv "
            "--yes-skus <skus>            # dry run",
            f"python -m lib.cli lower apply --queue reports/lower/{today.isoformat()}/queue.csv "
            "--yes-skus <skus> --confirm",
            "```", ""]
    return "\n".join(out)


def review_html(rows: list[dict], today: date) -> str:
    """One card per proposed or at-target listing: photo, title, ask, new ask,
    Recommended, days since change, and the one yes/no."""
    e = html.escape
    cards = []
    n = 0
    for r in rows:
        if r["status"] not in (PROPOSED, AT_TARGET):
            continue
        n += 1
        img = (f'<img src="{e(r["gallery_url"])}" alt="{e(r["title"])}" width="160" '
               f'height="160" loading="lazy">' if r["gallery_url"] else
               '<div class="noimg">no photo</div>')
        if r["status"] == PROPOSED:
            ask = f"Cut ${e(r['current_price'])} to <strong>${e(r['new_price'])}</strong>?"
            q = "yes / no"
        else:
            ask = f"${e(r['current_price'])} is at Recommended. Leave it?"
            q = "yes / no"
        note = f'<p class="note">{e(r["note"])}</p>' if r["note"] else ""
        cards.append(
            f'<li class="card">{img}<div><h2>{n}. {e(r["title"])}</h2>'
            f'<p>{ask}</p>'
            f'<p class="meta">Recommended ${e(r["recommended"])} &middot; '
            f'{e(r["days_since_change"])} days since last change ({e(r["last_change_source"])}) '
            f'&middot; {e(r["store"])} {e(r["item_id"])} {e(r["sku"])}</p>{note}'
            f'<p class="q">{q}</p></div></li>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Price steps {today.isoformat()}</title>
<style>
body{{font:16px/1.4 system-ui,sans-serif;margin:0 auto;max-width:760px;padding:12px;color:#111;background:#fff}}
h1{{font-size:1.3em}} ol{{list-style:none;padding:0}}
.card{{display:flex;gap:12px;border-bottom:1px solid #ccc;padding:12px 0}}
.card img,.noimg{{width:160px;height:160px;object-fit:contain;flex:none;background:#f3f3f3}}
.noimg{{display:flex;align-items:center;justify-content:center;color:#555}}
h2{{font-size:1em;margin:0 0 6px}} .meta,.note{{color:#444;font-size:.9em;margin:4px 0}}
.q{{font-weight:700}}
</style></head><body>
<h1>Price steps, {today.isoformat()}: {n} listings</h1>
<ol>{''.join(cards)}</ol>
</body></html>
"""


# ---------------------------------------------------------------------------
# eBay reads (Trading API; read calls only)
# ---------------------------------------------------------------------------

READ_CALLS = frozenset({"GetMyeBaySelling", "GetItem"})


def trading_read(call: str, inner_xml: str, creds) -> str:
    """POST one Trading READ call and return the XML. Any call not in
    READ_CALLS is refused here, so scan can't revise a listing by mistake."""
    if call not in READ_CALLS:
        raise PermissionError(f"lower: {call} is not a read call")
    import ebay_client as ec
    body = (f'<?xml version="1.0" encoding="utf-8"?><{call}Request '
            f'xmlns="urn:ebay:apis:eBLBaseComponents">{inner_xml}</{call}Request>')
    headers = {
        "X-EBAY-API-COMPATIBILITY-LEVEL": ec._TRADING_COMPAT_LEVEL,
        "X-EBAY-API-CALL-NAME": call,
        "X-EBAY-API-SITEID": ec._TRADING_SITE_ID,
        "X-EBAY-API-DEV-NAME": creds.dev_id or "",
        "X-EBAY-API-APP-NAME": creds.app_id,
        "X-EBAY-API-CERT-NAME": creds.cert_id,
        "X-EBAY-API-IAF-TOKEN": ec.get_user_access_token(creds),
        "Content-Type": "text/xml",
    }
    req = urllib.request.Request(ec._trading_endpoint(creds), data=body.encode("utf-8"),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    ack = ec._xml_tag(text, "Ack") or ""
    if ack.lower() not in ("success", "warning"):
        raise RuntimeError(f"{call} returned {ack or 'no Ack'}: "
                           f"{ec._xml_tag(text, 'LongMessage') or text[:200]}")
    return text


def _tag(xml: str, tag: str) -> str:
    m = re.search(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", xml, re.DOTALL)
    return html.unescape(m.group(1).strip()) if m else ""


def parse_active(xml: str, store: str) -> list[Listing]:
    out = []
    active = _tag(xml, "ActiveList")
    for it in re.findall(r"<Item>(.*?)</Item>", active, re.DOTALL):
        price = dec(_tag(_tag(it, "SellingStatus"), "CurrentPrice")) or dec(_tag(it, "BuyItNowPrice"))
        start = _tag(_tag(it, "ListingDetails"), "StartTime")
        out.append(Listing(
            store=store, item_id=_tag(it, "ItemID"), sku=_tag(it, "SKU"),
            title=_tag(it, "Title"), price=price or Decimal(0),
            start=_iso(start), listing_type=_tag(it, "ListingType") or "FixedPriceItem",
            gallery_url=_tag(_tag(it, "PictureDetails"), "GalleryURL"),
            watchers=_tag(it, "WatchCount")))
    return out


def fetch_active(store: str, creds, call=trading_read) -> list[Listing]:
    out, page, pages = [], 1, 1
    while page <= pages:
        xml = call("GetMyeBaySelling",
                   "<ActiveList><Include>true</Include><Pagination>"
                   f"<EntriesPerPage>200</EntriesPerPage><PageNumber>{page}</PageNumber>"
                   "</Pagination></ActiveList><DetailLevel>ReturnAll</DetailLevel>", creds)
        out += parse_active(xml, store)
        pages = int(_tag(_tag(_tag(xml, "ActiveList"), "PaginationResult"),
                         "TotalNumberOfPages") or 1)
        page += 1
    return out


def parse_item(xml: str) -> dict:
    """GetItem -> current price and Best Offer floors."""
    item = _tag(xml, "Item")
    det = _tag(item, "ListingDetails")
    enabled = _tag(_tag(item, "BestOfferDetails"), "BestOfferEnabled").lower() == "true"
    return {"price": dec(_tag(_tag(item, "SellingStatus"), "CurrentPrice")),
            "title": _tag(item, "Title"),
            "best_offer": enabled,
            "auto_decline": dec(_tag(det, "MinimumBestOfferPrice")) if enabled else None,
            "auto_accept": dec(_tag(det, "BestOfferAutoAcceptPrice")) if enabled else None}


def fetch_item(item_id: str, creds, call=trading_read) -> dict:
    return parse_item(call("GetItem", f"<ItemID>{html.escape(item_id)}</ItemID>", creds))


def live_spot(metal: str) -> Optional[Decimal]:
    """Live spot per troy ounce from gold-api.com (GH #298's provider), or None.
    Never cached on disk; a quote more than 15 minutes old is refused, so a
    caller fails closed. lib/spot.py (#298) should replace this when it lands."""
    sym = {"gold": "XAU", "silver": "XAG"}[metal]
    try:
        with urllib.request.urlopen(f"https://api.gold-api.com/price/{sym}", timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    price = dec(data.get("price"))
    when = data.get("updatedAt") or ""
    try:
        ts = datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError:
        return None
    if not price or price <= 0 or (datetime.now(timezone.utc) - ts).total_seconds() > 900:
        return None
    return price


# ---------------------------------------------------------------------------
# disk lookups
# ---------------------------------------------------------------------------

_SKU_RE = re.compile(r'^\s*ebay_inventory_sku:\s*["\']?([^"\'\n]+)["\']?', re.M)


def shoot_dirs_by_sku(inventory: Path) -> dict[str, Path]:
    """sku -> the shoot dir of its draft.md, preferring one with a price.txt."""
    from draft_io import iter_drafts
    out: dict[str, Path] = {}
    for d in iter_drafts(inventory):
        m = _SKU_RE.search(d.read_text(encoding="utf-8", errors="replace"))
        if not m:
            continue
        sku = m.group(1).strip()
        if sku not in out or (not (out[sku] / "price.txt").exists()
                              and (d.parent / "price.txt").exists()):
            out[sku] = d.parent
    return out


def consign_rows() -> list[dict]:
    import consign
    rows = []
    for d in consign.all_intakes():
        got, _ = consign.read_ledger(d / consign.LEDGER_NAME)
        rows += got
    return rows


def _draft_meta(shoot: Optional[Path]) -> dict:
    if not shoot or not (shoot / "draft.md").exists():
        return {}
    try:
        from draft_io import parse_draft
        return (parse_draft(shoot / "draft.md").frontmatter.get("meta") or {})
    except Exception:  # noqa: BLE001 — an unreadable draft is just no meta
        return {}


def _item_cost(shoot: Optional[Path]) -> Optional[Decimal]:
    """Cost basis only when the item's context says spend_unit: item."""
    if not shoot:
        return None
    import dir_context
    keys = dir_context.load(shoot).keys
    return dec(keys.get("spend")) if keys.get("spend_unit") == "item" else None


def _ledger_start(store: str) -> dict[str, date]:
    import stores
    out = {}
    for r in read_csv(stores.readable(stores.paths(store).listings_ledger)):
        d = _iso(r.get("published_at", ""))
        if d and r.get("listing_id"):
            out[r["listing_id"]] = d
    return out


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def set_draft_prices(text: str, price: Decimal, decline: Optional[Decimal],
                     accept: Optional[Decimal]) -> str:
    """The draft with only price and Best Offer amounts changed."""
    new = re.sub(r'(?m)^price: .*$', f'price: "{price:.2f}"', text, count=1)
    if decline is not None:
        new = re.sub(r'(?m)^  auto_decline_amount: .*$',
                     f'  auto_decline_amount: "{decline:.2f}"', new, count=1)
    if accept is not None:
        new = re.sub(r'(?m)^  auto_accept_amount: .*$',
                     f'  auto_accept_amount: "{accept:.2f}"', new, count=1)
    return new


def _title(text: str) -> str:
    m = re.search(r"(?m)^title: *(.*)$", text)
    return m.group(1).strip() if m else ""


@dataclass
class Applied:
    item_id: str
    sku: str
    outcome: str     # dry-run ok | applied | refused | failed | manual
    detail: str = ""


def apply_rows(rows: list[dict], yes: set[str], *, confirm: bool,
               live_price: Callable[[dict], Optional[Decimal]],
               draft_for: Callable[[dict], Optional[Path]],
               run_update: Callable[[dict, Path, str, bool], int],
               log_path: Path, today: date) -> list[Applied]:
    """Push the yes'd PROPOSED rows. Dry run unless `confirm`; only a
    confirmed, successful update is logged."""
    out = []
    for r in rows:
        if r["status"] != PROPOSED or not ({r["sku"], r["item_id"]} & yes):
            continue
        new, cur = dec(r["new_price"]), dec(r["current_price"])
        nd, na = dec(r["new_auto_decline"]), dec(r["new_auto_accept"])
        if not r["sku"]:
            out.append(Applied(r["item_id"], "", "manual",
                               f"no SKU: Seller Hub ${cur} -> ${new}"))
            continue
        if confirm:
            live = live_price(r)
            if live is None or abs(live - cur) > Decimal("0.005"):
                out.append(Applied(r["item_id"], r["sku"], "refused",
                                   f"live price {live} is not the queue's {cur}: re-scan"))
                continue
        d = draft_for(r)
        if not d or not (d / "draft.md").exists():
            out.append(Applied(r["item_id"], r["sku"], "failed", "no draft (draft_rebuild failed)"))
            continue
        f = d / "draft.md"
        orig = f.read_text(encoding="utf-8")
        edited = set_draft_prices(orig, new, nd, na)
        assert _title(edited) == _title(orig), "title_unchanged"
        fields = "price,bestoffer" if (nd is not None or na is not None) else "price"
        f.write_text(edited, encoding="utf-8")
        ok = False
        try:
            rc = run_update(r, d, fields, confirm)
            ok = rc == 0
        finally:
            if not (confirm and ok):
                f.write_text(orig, encoding="utf-8")
        if not ok:
            out.append(Applied(r["item_id"], r["sku"], "failed", f"listing --update exit {rc}"))
            continue
        if confirm:
            append_log(log_path, [{
                "sku": r["sku"], "item_id": r["item_id"], "store": r["store"],
                "date": today.isoformat(), "old_ask": r["current_price"],
                "new_ask": r["new_price"],
                "old_floors": "/".join(x for x in (r["auto_decline"], r["auto_accept"]) if x),
                "new_floors": "/".join(x for x in (r["new_auto_decline"], r["new_auto_accept"]) if x),
                "source": "lower"}])
            out.append(Applied(r["item_id"], r["sku"], "applied", f"${cur} -> ${new}"))
        else:
            out.append(Applied(r["item_id"], r["sku"], "dry-run ok", f"${cur} -> ${new}"))
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _stores(arg: Optional[str]) -> list[str]:
    import stores
    return [stores.validate_store_name(arg)] if arg else stores.configured_stores()


def cmd_scan(a) -> int:
    import stores
    from ebay_client import load_credentials
    today = date.fromisoformat(a.date) if a.date else date.today()
    inv = REPO / "inventory"
    shoots = shoot_dirs_by_sku(inv)
    crows = consign_rows()
    by_sku = {r["sku"]: r for r in crows if r.get("sku")}
    by_lid = {r["listing_id"]: r for r in crows if r.get("listing_id")}
    folder_dir = {r["sku"]: REPO / r["inventory_folder"] for r in crows
                  if r.get("sku") and r.get("inventory_folder")}
    spot_seen: dict[str, Optional[Decimal]] = {}

    def spot(metal):           # fetched live, once per run
        if metal not in spot_seen:
            spot_seen[metal] = live_spot(metal)
        return spot_seen[metal]

    listings, creds_by = [], {}
    names = _stores(a.store)
    for s in names:
        creds_by[s] = load_credentials(store=s)
        got = fetch_active(s, creds_by[s])
        starts = _ledger_start(s)
        for l in got:
            l.start = l.start or starts.get(l.item_id)
        listings += got
        print(f"{s}: {len(got)} active listings", file=sys.stderr)

    def shoot(l):
        return shoots.get(l.sku) or folder_dir.get(l.sku)

    def floors(l):
        try:
            it = fetch_item(l.item_id, creds_by[l.store])
        except Exception as e:  # noqa: BLE001 — a failed read holds nothing up but its row
            print(f"  ! GetItem {l.item_id}: {e}", file=sys.stderr)
            return None, None
        return it["auto_decline"], it["auto_accept"]

    ctx = Ctx(today=today, log_rows=read_csv(a.log), holds=read_csv(a.holds),
              consign_row=lambda l: by_sku.get(l.sku) or by_lid.get(l.item_id),
              shoot_dir=shoot, meta=lambda l: _draft_meta(shoot(l)),
              cost=lambda l: _item_cost(shoot(l)), floors=floors, spot=spot)
    rows = scan_rows(listings, ctx, a.max_reprice)
    out = Path(a.out) if a.out else LOWER_DIR / today.isoformat()
    write_queue(rows, out / "queue.csv")
    (out / "queue.md").write_text(summary_md(rows, today, stores_scanned=names,
                                             spot_seen=spot_seen, max_reprice=a.max_reprice),
                                  encoding="utf-8")
    (out / "review.html").write_text(review_html(rows, today), encoding="utf-8")
    from collections import Counter
    c = Counter(r["status"] for r in rows)
    print(" ".join(f"{s} {c[s]}" for s in STATUSES))
    print(f"wrote {out}/queue.csv, queue.md, review.html")
    return 0


def cmd_apply(a) -> int:
    from ebay_client import load_credentials
    rows = read_csv(Path(a.queue))
    if a.store:
        rows = [r for r in rows if r["store"] == a.store]
    yes = {s.strip() for s in a.yes_skus.split(",") if s.strip()}
    shoots = shoot_dirs_by_sku(REPO / "inventory")
    creds: dict = {}

    def live_price(r):
        c = creds.setdefault(r["store"], load_credentials(store=r["store"]))
        return fetch_item(r["item_id"], c)["price"]

    def draft_for(r):
        d = shoots.get(r["sku"])
        if d:
            return d
        subprocess.run([sys.executable, "tools/draft_rebuild.py", "--store", r["store"],
                        "--force", "--sku", r["sku"]], cwd=REPO, check=False)
        d = REPO / "inventory" / "_rebuilt" / r["sku"]
        return d if (d / "draft.md").exists() else None

    def run_update(r, d, fields, confirm):
        cmd = [sys.executable, "-m", "lib.cli", "--store", r["store"], "listing", "--update",
               str(d), "--fields", fields] + ([] if confirm else ["--dry-run"])
        print(f"\n== {r['item_id']} {r['sku']} ${r['current_price']} -> ${r['new_price']}"
              f"  {r['title'][:50]}", flush=True)
        return subprocess.run(cmd, cwd=REPO).returncode

    res = apply_rows(rows, yes, confirm=a.confirm, live_price=live_price, draft_for=draft_for,
                     run_update=run_update, log_path=Path(a.log), today=date.today())
    for x in res:
        print(f"  [{x.outcome}] {x.item_id} {x.sku} {x.detail}")
    unknown = yes - {r["sku"] for r in rows} - {r["item_id"] for r in rows}
    for u in sorted(unknown):
        print(f"  [not in queue] {u}")
    print(f"{'APPLIED' if a.confirm else 'DRY RUN'}: "
          + ", ".join(f"{k} {sum(1 for x in res if x.outcome == k)}"
                      for k in ("applied", "dry-run ok", "refused", "failed", "manual")))
    return 1 if any(x.outcome in ("failed", "refused") for x in res) else 0


def cmd_backfill(a) -> int:
    added, holds, notes = backfill(Path(a.reports), Path(a.log), Path(a.holds))
    for n in notes:
        print(f"  {n}")
    print(f"price_changes.csv: {added} rows added; holds.csv: {holds} added")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    import stores
    ap = argparse.ArgumentParser(prog="ebz lower", description=__doc__.split("\n\n")[0])
    ap.add_argument("--store", dest="store_top", metavar="NAME", default=None,
                    help="which store (also accepted after scan/apply); scan "
                         "without it reads every configured store")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan", help="read-only: build reports/lower/<date>/queue.csv")
    sc.add_argument("--date", help="scan date YYYY-MM-DD (default today)")
    sc.add_argument("--max-reprice", type=int, default=MAX_REPRICE)
    sc.add_argument("--out", help="output dir (default reports/lower/<date>)")
    ap_ = sub.add_parser("apply", help="push yes'd PROPOSED rows (dry run unless --confirm)")
    ap_.add_argument("--queue", required=True)
    ap_.add_argument("--yes-skus", required=True, help="comma list of SKUs or item numbers")
    ap_.add_argument("--confirm", action="store_true", help="really write to eBay")
    bf = sub.add_parser("backfill", help="seed price_changes.csv from the old aging reports")
    bf.add_argument("--reports", default=str(REPO / "reports"))
    for p in (sc, ap_, bf):
        p.add_argument("--log", default=str(LOG_PATH))
        p.add_argument("--holds", default=str(HOLDS_PATH))
        if p is not bf:
            stores.add_store_args(p)
    a = ap.parse_args(argv)
    if a.cmd != "backfill":
        a.store = a.store or a.store_top
    a.log, a.holds = Path(a.log), Path(a.holds)
    return {"scan": cmd_scan, "apply": cmd_apply, "backfill": cmd_backfill}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
