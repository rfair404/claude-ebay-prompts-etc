#!/usr/bin/env python3
"""source_report — cross-directory bucket ROI, as a first-class report (#56).

Provenance is a real dimension of this business — items live under
`ESTATES/<name>/`, `FREE/`, `THRIFT/`, `GARAGE-SALES/`, `MINE/`, and each is a
distinct acquisition with its own cost, its own sell-through, and its own
answer to "was that worth buying." Every number of this shape produced before
this file came from a throwaway scratchpad script — not reproducible, not
tested, and gone the moment the session ended. This makes it a committed report
in the house UI, following the `tools/sales_report.py` -> dashboard pattern:

    python -m lib.cli report --by-source              # terminal table
    python -m lib.cli report --by-source --html       # reports/source_report.html

WHAT A "BUCKET" IS

Not "the top two path segments of a shoot dir" — that grain broke the moment
items moved under ESTATES/<name>/: `ESTATES/SCJ` is a real acquisition,
`FREE/more-mags-444` is a sub-lot *inside* the `FREE` acquisition, and the two
are not peers. A bucket is the semantic thing: the nearest ancestor directory
that owns a `context.txt`. `bucket_for()` below is the one resolver — a re-org
of the folders under it changes the report's contents, never its correctness.

WHY COST BASIS COMES FROM context.txt KEYS, NOT PROSE

`context.txt` already carries the story of an acquisition in prose ("An estate
sale in Social Circle Georgia. ... Spend $575"), and that prose stays the
payload. But recovering the spend by pattern-matching English is exactly the
kind of number this file exists to stop producing. `spend:` / `kind:` /
`acquired:` are real, explicit keys a tool can branch on without guessing.
`kind` matters because the two axes aren't comparable: an `event` bucket (a
single purchase — SCJ, MAR) has a real ROI; a `channel` bucket (an ongoing
habit — FREE, THRIFT) does not, and treating it as one manufactures a number
that means nothing. A missing `spend:` on an `event` bucket is a *data gap* —
flagged. On a `channel` bucket it's *correct* — no ROI expected, no flag.

WHAT THIS PAGE DOES NOT CLAIM

`net_before_postage` is exactly that — before postage. `sales_ledger.csv` has
no actual-postage column. It is also before ADVERTISING: the fee it subtracts
comes from `totalMarketplaceFee`, which is the final value fee only, and
promoted-listing fees are billed separately and never appear in the order
payload (#115). So every "net" and "profit" figure here overstates what was
actually kept, by postage AND by the whole ad bill — on one measured month the
latter ran ~8% of item sales, about half the size of the final value fee. The
page says so plainly rather than printing a number that quietly isn't a
profit.
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))       # sibling lib/ modules
import report as _report                                        # noqa: E402  lib/report.py

REPO = Path(__file__).resolve().parent.parent
INVENTORY = REPO / "inventory"
SALES_LEDGER = REPO / "sales_ledger.csv"
REPORTS = REPO / "reports"
OUT_HTML = REPORTS / "source_report.html"

# Directories that exist only as a copy of something already counted elsewhere
# — a pre-touch backup or a prior run's leftovers, never a source of new items.
# Matched anywhere in the path so a nested backup (SCJ/item-9/_prepped) is
# caught the same as a top-level one.
_BACKUP_EXACT = {"_prepped", ".prior-run-bak"}


def _norm(path_str: str) -> str:
    return (path_str or "").replace("\\", "/").strip().strip("/")


def _is_backup_path(path_str: str) -> bool:
    """True if any component of `path_str` names a backup/generated dir.

    A dot-prefixed component is treated the same as the two named exceptions
    the issue calls out — every dot-dir this repo already produces under
    `inventory/` (`.orig/`, `.orig-rot/`, `.scratch/`, `.tonecheck/`, `.comps/`)
    is regenerable backup/scratch output, never a distinct source of items.
    """
    return any(p in _BACKUP_EXACT or p.startswith(".")
               for p in _norm(path_str).split("/") if p)


# --------------------------------------------------------------------------- #
# bucket resolution — the directory that owns a context.txt
# --------------------------------------------------------------------------- #

def bucket_for(item_dir: Path, root: Optional[Path] = None) -> Optional[Path]:
    """Nearest ancestor of `item_dir` (inclusive) that holds a context.txt.

    Bounded to `root` (default INVENTORY) so the walk cannot climb out of the
    inventory tree and accidentally match an unrelated context.txt higher up
    the filesystem. Returns None if `item_dir` is not under `root`, or no
    ancestor within it owns a context.txt.
    """
    root_r = (INVENTORY if root is None else Path(root)).resolve()
    try:
        cur = Path(item_dir).resolve()
    except OSError:
        return None
    if cur != root_r and root_r not in cur.parents:
        return None
    while True:
        if (cur / "context.txt").is_file():
            return cur
        if cur == root_r:
            return None
        cur = cur.parent


def bucket_label(bucket_dir: Path, root: Optional[Path] = None) -> str:
    """Display key for a bucket — its path relative to `root` (default INVENTORY)."""
    root_r = (INVENTORY if root is None else Path(root)).resolve()
    try:
        rel = Path(bucket_dir).resolve().relative_to(root_r)
        return rel.as_posix() if str(rel) != "." else bucket_dir.name
    except ValueError:
        return str(bucket_dir)


# --------------------------------------------------------------------------- #
# context.txt — tolerant of empty / prose-only / keyed-plus-prose
# --------------------------------------------------------------------------- #

_KEY_RE = re.compile(r"^\s*(kind|spend|acquired)\s*:\s*(.*?)\s*$", re.I)
_KINDS = {"event", "channel"}


def _to_float(v, default=None):
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def parse_context(text: str) -> dict:
    """Pull `kind:` / `spend:` / `acquired:` out of a context.txt body.

    Only an explicit `key: value` line counts — prose that happens to contain
    "Spend $575" is deliberately NOT read as the spend field (that is the whole
    point of #56: a real field, not English pattern-matching). Tolerates an
    empty file, a prose-only file, and a file mixing key lines with prose in
    any order — all three shapes already exist on disk under inventory/.
    First occurrence of a key wins; a bare `kind:` value outside {event,
    channel} is treated as unset rather than trusted verbatim.
    """
    kind = spend = acquired = None
    for line in (text or "").splitlines():
        m = _KEY_RE.match(line)
        if not m:
            continue
        key, val = m.group(1).lower(), m.group(2).strip()
        if not val:
            continue
        if key == "kind" and kind is None:
            v = val.lower()
            if v in _KINDS:
                kind = v
        elif key == "spend" and spend is None:
            spend = _to_float(val)
        elif key == "acquired" and acquired is None:
            acquired = val
    return {"kind": kind, "spend": spend, "acquired": acquired}


def load_context(bucket_dir: Path) -> dict:
    f = Path(bucket_dir) / "context.txt"
    if not f.is_file():
        return {"kind": None, "spend": None, "acquired": None}
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"kind": None, "spend": None, "acquired": None}
    return parse_context(text)


# --------------------------------------------------------------------------- #
# ROI / gap rules
# --------------------------------------------------------------------------- #

def roi_for(net: float, spend: Optional[float], kind: Optional[str]) -> Optional[float]:
    """net / spend for an `event` bucket with a positive recorded spend.

    Suppressed (None — never 0, never inf) whenever ROI would not mean
    anything: `kind` is not explicitly `event` (a `channel` bucket, or one
    with no `kind:` at all, is not a single acquisition with a payback), or
    spend is missing/zero (a real basis, not a divide-by-zero).
    """
    if kind != "event":
        return None
    if not spend or spend <= 0:
        return None
    return net / spend


def is_basis_gap(kind: Optional[str], spend: Optional[float]) -> bool:
    """True only for the case the issue calls a real data gap: an `event`
    bucket with no recorded spend. A `channel` bucket (or unspecified kind)
    missing spend is correct, not a gap — no ROI was ever expected there."""
    return kind == "event" and spend is None


def sell_through(sold_n: int, live_n: int) -> Optional[float]:
    total = sold_n + live_n
    return (sold_n / total * 100) if total > 0 else None


# --------------------------------------------------------------------------- #
# gather — read-only over sales_ledger.csv + local drafts/ledger
# --------------------------------------------------------------------------- #

def _sales_rows() -> list[dict]:
    if not SALES_LEDGER.exists():
        return []
    with SALES_LEDGER.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _collect_listings() -> list[dict]:
    """Local drafts + listings_ledger.csv, merged (disk wins) — see lib/report.py.

    Best-effort for LIVE/ask/pending: a CHOICE group listing (`draft_group.md`)
    never reaches `listings_ledger.csv` (a known, documented gap in
    lib/report.py, not introduced here), so its live/ended state is inferred
    from the draft's own `published_at` plus whether it also shows up as a
    sale — not from an authoritative eBay read. `tools/sales_report.py`'s
    "shop right now" panel (drawn from the eBay-synced inventory_sheet.csv) is
    the authoritative shop-wide LIVE count; this report's LIVE/ask columns are
    for comparing buckets against each other, not as a replacement for it.
    """
    try:
        return _report.collect()
    except Exception:                                            # noqa: BLE001
        return []


def _new_bucket(bucket_dir: Path) -> dict:
    ctx = load_context(bucket_dir)
    return {
        "path": bucket_dir, "key": bucket_label(bucket_dir),
        "kind": ctx["kind"], "spend": ctx["spend"], "acquired": ctx["acquired"],
        "sold_n": 0, "gross": 0.0, "fee": 0.0, "net": 0.0,
        "ask_total": 0.0, "live_n": 0, "pending_n": 0,
    }


def gather() -> dict:
    buckets: dict[Path, dict] = {}
    unattributed = {"key": "— unattributed (no matching folder) —",
                     "sold_n": 0, "gross": 0.0, "fee": 0.0, "net": 0.0}

    def _bucket(bdir: Path) -> dict:
        b = buckets.get(bdir)
        if b is None:
            b = _new_bucket(bdir)
            buckets[bdir] = b
        return b

    sales = _sales_rows()
    sold_listing_ids = {r.get("listing_id") for r in sales if r.get("listing_id")}

    # ---- sold line items -----------------------------------------------
    for r in sales:
        shoot = _norm(r.get("shoot_dir") or "")
        gross, fee, net = (_to_float(r.get("gross"), 0.0), _to_float(r.get("ebay_fee"), 0.0),
                          _to_float(r.get("net_before_postage"), 0.0))
        if shoot and _is_backup_path(shoot):
            continue                                    # backup dir — excluded from counts
        bdir = bucket_for(REPO / shoot) if shoot else None
        if bdir is None:
            unattributed["sold_n"] += 1
            unattributed["gross"] += gross
            unattributed["fee"] += fee
            unattributed["net"] += net
            continue
        b = _bucket(bdir)
        b["sold_n"] += 1
        b["gross"] += gross
        b["fee"] += fee
        b["net"] += net

    # ---- live + drafted/synced pending, from local drafts/ledger -------
    for r in _collect_listings():
        path = _norm(r.get("path") or "")
        if not path or _is_backup_path(path):
            continue
        bdir = bucket_for((REPO / path).parent)
        if bdir is None:
            continue
        b = _bucket(bdir)
        published = bool(r.get("published_at"))
        status = (r.get("status") or "").upper()
        if not published:
            b["pending_n"] += 1
        elif status not in ("ENDED", "DELETED") and r.get("listing_id") not in sold_listing_ids:
            b["live_n"] += 1
            b["ask_total"] += _to_float(r.get("price"), 0.0)

    rows = sorted(buckets.values(), key=lambda b: -b["net"])
    for b in rows:
        b["cost_known"] = b["spend"] is not None
        b["profit"] = (b["net"] - b["spend"]) if b["cost_known"] else None
        b["roi"] = roi_for(b["net"], b["spend"], b["kind"])
        b["gap"] = is_basis_gap(b["kind"], b["spend"])
        b["sell_through"] = sell_through(b["sold_n"], b["live_n"])

    known = [b for b in rows if b["cost_known"]]
    event_known = [b for b in known if b["kind"] == "event"]
    total = {
        "sold_n": sum(b["sold_n"] for b in rows) + unattributed["sold_n"],
        "gross": sum(b["gross"] for b in rows) + unattributed["gross"],
        "fee": sum(b["fee"] for b in rows) + unattributed["fee"],
        "net": sum(b["net"] for b in rows) + unattributed["net"],
        "ask_total": sum(b["ask_total"] for b in rows),
        "live_n": sum(b["live_n"] for b in rows),
        "pending_n": sum(b["pending_n"] for b in rows),
        "cost": sum(b["spend"] for b in known),
        "cost_bucket_n": len(known),
        "profit": sum(b["profit"] for b in known) if known else None,
    }
    ev_net, ev_cost = sum(b["net"] for b in event_known), sum(b["spend"] for b in event_known)
    total["roi"] = (ev_net / ev_cost) if ev_cost > 0 else None
    total["sell_through"] = sell_through(total["sold_n"], total["live_n"])

    return {
        "buckets": rows,
        "unattributed": unattributed,
        "total": total,
        "gaps": [b["key"] for b in rows if b["gap"]],
        "missing_basis_channel": [b["key"] for b in rows
                                  if b["cost_known"] is False and not b["gap"]],
    }


# --------------------------------------------------------------------------- #
# terminal table
# --------------------------------------------------------------------------- #

def _fmt_money(v) -> str:
    return f"${v:,.0f}" if v is not None else "—"


def _fmt_roi(v) -> str:
    return f"{v:.2f}x" if v is not None else "—"


def _fmt_pct(v) -> str:
    return f"{v:.0f}%" if v is not None else "—"


def render_table(d: dict) -> str:
    cols = ("BUCKET", "LIVE", "ASK $", "SOLD", "GROSS $", "FEES", "NET $",
            "COST", "PROFIT", "ROI", "SELL-THR", "PENDING")
    widths = [22, 4, 8, 4, 9, 7, 9, 8, 9, 6, 8, 7]
    lines = ["  ".join(c.ljust(w) for c, w in zip(cols, widths))]
    lines.append("-" * (sum(widths) + 2 * (len(widths) - 1)))

    def row(key, b) -> str:
        gap = " *" if b.get("gap") else "  "
        cost = _fmt_money(b["spend"]) + gap
        vals = [key[:22], str(b["live_n"]), _fmt_money(b["ask_total"]),
                str(b["sold_n"]), _fmt_money(b["gross"]), _fmt_money(b["fee"]),
                _fmt_money(b["net"]), cost, _fmt_money(b["profit"]),
                _fmt_roi(b["roi"]), _fmt_pct(b["sell_through"]), str(b["pending_n"])]
        return "  ".join(v.ljust(w) for v, w in zip(vals, widths))

    for b in d["buckets"]:
        lines.append(row(b["key"], b))
    u = d["unattributed"]
    if u["sold_n"]:
        lines.append(row(u["key"], {**u, "spend": None, "profit": None, "roi": None,
                                    "gap": False, "ask_total": 0.0, "live_n": 0,
                                    "sell_through": None, "pending_n": 0}))
    t = d["total"]
    lines.append("-" * (sum(widths) + 2 * (len(widths) - 1)))
    lines.append(row("TOTAL", {**t, "spend": t["cost"] or None, "gap": False}))

    out = ["\n".join(lines), ""]
    if d["gaps"]:
        out.append(f"* basis not recorded (event, missing spend:): {', '.join(d['gaps'])}")
    out.append(f"cost/profit/ROI totals cover {t['cost_bucket_n']} bucket(s) with a "
               f"recorded spend: — buckets missing a basis are excluded, never counted "
               f"as zero cost or pure profit.")
    out.append("ROI is shown only for `kind: event` buckets with a recorded spend: — "
               "a `channel` bucket (an ongoing habit, not a single purchase) has no ROI "
               "by design, not a missing one.")
    out.append("NET is net_before_postage — sales_ledger.csv carries no actual-postage "
               "column, and the fee it subtracts (totalMarketplaceFee) is the final value "
               "fee only, so ad spend is absent too. Every NET/PROFIT figure here is "
               "before postage AND before advertising, not a final take-home number.")
    if u["sold_n"]:
        out.append(f"{u['sold_n']} sale(s) have no matching local folder — reported as "
                   f"their own line, not dropped.")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# HTML — house style, mirroring tools/sales_report.py
# --------------------------------------------------------------------------- #

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
tr.total td{font-weight:600;border-top:2px solid var(--rule)}
tr:last-child td{border-bottom:0}
.num{font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums;
  text-align:right;white-space:nowrap}
.dim{color:var(--muted)}
.warn{color:var(--warn)}
.ok{color:var(--ok)}
a{color:var(--accent)}
.scroll{overflow-x:auto}
.note{font-size:12.5px;color:var(--muted);margin:14px 0 0;
  padding-top:12px;border-top:1px solid var(--rule)}
.pill{display:inline-block;font:600 10px/1 "IBM Plex Mono",monospace;
  letter-spacing:.08em;padding:4px 7px;border-radius:3px;border:1px solid var(--rule);
  color:var(--muted)}
.pill.warn{color:var(--warn);border-color:var(--warn)}
"""


def _e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def _money(v) -> str:
    return f"${v:,.2f}" if v is not None else "—"


def _stat(amt: str, lbl: str, sub: str = "") -> str:
    return (f'<div class="stat"><div class="amt">{_e(amt)}</div>'
            f'<div class="lbl">{_e(lbl)}</div>'
            + (f'<div class="sub">{_e(sub)}</div>' if sub else "") + "</div>")


def _bucket_row(key: str, b: dict, *, total: bool = False, is_bucket: bool = True) -> str:
    kind = b.get("kind") or "unspecified"
    gap_pill = ' <span class="pill warn">GAP</span>' if b.get("gap") else ""
    cost = f'{_money(b["spend"])}' if b.get("cost_known", b.get("spend") is not None) else \
        '<span class="dim">basis not recorded</span>'
    profit_cls = "warn" if (b.get("profit") is not None and b["profit"] < 0) else ""
    tr_open = '<tr class="total">' if total else '<tr>'
    return (
        f'{tr_open}<td>{_e(key)}'
        + (f'<div class="dim" style="font-size:11.5px">{_e(kind)}'
           + (f' · acquired {_e(b["acquired"])}' if b.get("acquired") else "") + '</div>'
           if is_bucket and not total else "")
        + gap_pill + '</td>'
        f'<td class="num">{b["live_n"]}</td>'
        f'<td class="num dim">{_money(b["ask_total"])}</td>'
        f'<td class="num">{b["sold_n"]}</td>'
        f'<td class="num">{_money(b["gross"])}</td>'
        f'<td class="num dim">{_money(b["fee"])}</td>'
        f'<td class="num">{_money(b["net"])}</td>'
        f'<td class="num">{cost}</td>'
        f'<td class="num {profit_cls}">{_money(b.get("profit"))}</td>'
        f'<td class="num">{_fmt_roi(b.get("roi"))}</td>'
        f'<td class="num">{_fmt_pct(b.get("sell_through"))}</td>'
        f'<td class="num dim">{b["pending_n"]}</td></tr>'
    )


def draw(d: dict) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    t = d["total"]
    rows_html = "".join(_bucket_row(b["key"], b) for b in d["buckets"])
    u = d["unattributed"]
    if u["sold_n"]:
        rows_html += _bucket_row(u["key"], {
            "kind": None, "spend": None, "cost_known": None, "profit": None, "roi": None,
            "gap": False, "sell_through": None, "ask_total": 0.0, "live_n": 0,
            "pending_n": 0, "sold_n": u["sold_n"], "gross": u["gross"], "fee": u["fee"],
            "net": u["net"],
        }, is_bucket=False)
    total_row = _bucket_row("TOTAL", {
        "kind": None, "spend": t["cost"], "cost_known": t["cost_bucket_n"] > 0,
        "profit": t["profit"], "roi": t["roi"], "gap": False,
        "sell_through": sell_through(t["sold_n"], t["live_n"]),
        "ask_total": t["ask_total"], "live_n": t["live_n"], "pending_n": t["pending_n"],
        "sold_n": t["sold_n"], "gross": t["gross"], "fee": t["fee"], "net": t["net"],
    }, total=True)

    gap_note = (f'<p class="note ok" style="border-top:0;padding-top:0">'
               f'{len(d["gaps"])} bucket(s) flagged GAP — an <code>event</code> '
               f'acquisition with no recorded <code>spend:</code>: '
               f'{_e(", ".join(d["gaps"]))}.</p>' if d["gaps"] else "")

    body = (
        f'<div class="card"><div class="hdr">'
        f'<p class="eyebrow">ebaybiz · source</p>'
        f'<h1>Bucket ROI — realised by acquisition</h1>'
        f'<div class="ct">built {_e(now)} local · read-only, local files only</div>'
        f'</div>'
        f'<div class="stats">'
        + _stat(str(t["cost_bucket_n"]), "buckets with a recorded basis",
                f'{len(d["buckets"]) - t["cost_bucket_n"]} without one')
        + _stat(_money(t["cost"]) if t["cost_bucket_n"] else "—", "cost, known buckets only",
                "excluded, not zeroed, where basis is missing")
        + _stat(_fmt_roi(t["roi"]), "ROI, event buckets w/ basis",
                "channel buckets carry no ROI by design")
        + _stat(str(len(d["gaps"])), "GAP: event, no spend recorded",
                "a real data gap, not free money")
        + '</div></div>'
    )

    body += (
        '<div class="card"><div class="pad"><h2>By bucket (context.txt ownership)</h2>'
        f'{gap_note}'
        '<div class="scroll"><table><tr><th>Bucket</th><th class="num">Live</th>'
        '<th class="num">Ask $</th><th class="num">Sold</th><th class="num">Gross $</th>'
        '<th class="num">Fees</th><th class="num">Net $</th><th class="num">Cost</th>'
        '<th class="num">Profit</th><th class="num">ROI</th><th class="num">Sell-thr %</th>'
        '<th class="num">Pending</th></tr>'
        f'{rows_html}{total_row}</table></div>'
        '<p class="note">A <strong>bucket</strong> is the nearest ancestor directory that '
        'owns a <code>context.txt</code> — not path depth, so a re-org changes this table\'s '
        'contents, never its correctness. <strong>NET is net_before_postage</strong>: '
        '<code>sales_ledger.csv</code> carries no actual-postage column, and the fee it '
        'subtracts (<code>totalMarketplaceFee</code>) is the final value fee only, so '
        'promoted-listing spend is absent as well. PROFIT here is before postage and '
        'before advertising, not a final take-home figure. <strong>ROI</strong> (net / cost) is '
        'shown only for a <code>kind: event</code> bucket with a recorded <code>spend:</code> '
        '— never 0, never infinite, and never computed for a <code>channel</code> bucket '
        '(an ongoing habit has no single payback to measure). A bucket with '
        '"basis not recorded" is a missing field, never rendered as pure profit. Backup/'
        'scratch directories (<code>_prepped/</code>, <code>.prior-run-bak/</code> and other '
        'dot-dirs) are excluded from every count.'
        + (f' {u["sold_n"]} sale(s) matched no local folder — shown as their own line above, '
           f'not dropped.' if u["sold_n"] else '')
        + '</p></div></div>'
    )

    return ('<meta charset="utf-8">\n<title>Source Report</title>\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            f'<style>{STYLE}</style>\n<div class="wrap">\n{body}\n</div>\n')


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="report", description="ebaybiz cross-directory source/ROI report (#56).")
    ap.add_argument("--by-source", action="store_true",
                    help="bucket ROI by context.txt-owning directory (the only report today)")
    ap.add_argument("--html", action="store_true",
                    help=f"also write {OUT_HTML.relative_to(REPO)} in the house style")
    ap.add_argument("--out", default=str(OUT_HTML), help="HTML output path (with --html)")
    args = ap.parse_args(argv)

    if not args.by_source:
        ap.error("choose a report: --by-source")

    d = gather()
    print(render_table(d))

    if args.html:
        REPORTS.mkdir(exist_ok=True)
        out = Path(args.out)
        out.write_text(draw(d), encoding="utf-8")
        print(f"\n[OK] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
