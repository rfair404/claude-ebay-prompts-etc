#!/usr/bin/env python3
"""PRICE AUDIT — which listings are still asking above their own comp evidence.

Every draft's `meta.notes` records what PRICE concluded at drafting time: the
ceiling we listed at, and the **Recommended** (clear/median) figure that comps
actually supported. We list at the ceiling on purpose — push-high is house
policy for silver, antiques and anything where the exact comp is thin — but a
ceiling ask is a bet with a clock on it. After a month with no sale, the ask
is just a number the market has already declined.

This finds those: live listings older than `--days` whose current ask is still
above their own Recommended figure. It proposes the drop; it never makes it.
Repricing is a decision, and it goes through the same review as the copy.

  price_audit.py [--days 30] [--json out.json]

Two honesty notes. The Recommended figure is as old as the draft — a comp read
from June is not a comp today, and a flagged item may deserve a re-hunt rather
than a mechanical drop to a stale number. And "above Recommended" is not
"overpriced": a genuinely rare piece can sit for months and then sell at the
ceiling to the one buyer who wanted it. The flag says look, not cut.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INVENTORY = REPO / "inventory"
LEDGER = REPO / "listings_ledger.csv"

# "Recommended $76", "Recommended (median …) = $76", "Recommended/clear ref $295",
# "Recommended $32 / ceiling $48" — take the FIRST dollar figure that follows the
# word, within a short window so a later unrelated price cannot be captured.
REC = re.compile(r"Recommended[^$\n]{0,80}?\$\s?([0-9][0-9,]*(?:\.\d{1,2})?)", re.I)
# A few drafts write it the other way round: "clear/median price of $76 (Recommended)".
REC_ALT = re.compile(r"\$\s?([0-9][0-9,]*(?:\.\d{1,2})?)[^$\n]{0,30}\(?Recommended", re.I)


def _dec(s) -> Decimal | None:
    if s in (None, ""):
        return None
    try:
        return Decimal(str(s).replace(",", "").replace("$", ""))
    except Exception:
        return None


def recommended_for(text: str) -> Decimal | None:
    m = REC.search(text) or REC_ALT.search(text)
    return _dec(m.group(1)) if m else None


def days_since(iso: str) -> int | None:
    if not iso:
        return None
    try:
        d = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - d).days


def scan(audit_rows: list[dict], days: int) -> list[dict]:
    led = {r["sku"]: r for r in csv.DictReader(LEDGER.open(encoding="utf-8-sig"))}
    out = []
    for r in audit_rows:
        if r.get("state") != "LIVE" or r.get("group") or not r.get("dir"):
            continue
        p = REPO / r["dir"] / "draft.md"
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        rec = recommended_for(text)
        ask = _dec(r.get("live_price"))
        age = days_since((led.get(r["sku"]) or {}).get("published_at", ""))
        if ask is None or age is None:
            continue
        row = {"dir": r["dir"], "sku": r["sku"], "listing_id": r.get("listing_id", ""),
               "title": r.get("live_title", ""), "ask": str(ask),
               "recommended": str(rec) if rec is not None else None, "days": age}
        if rec is None:
            row["flag"] = "no-recommended-recorded"
        elif age >= days and ask > rec:
            row["flag"] = "above-recommended"
            row["over_pct"] = round(float((ask - rec) / rec) * 100, 1)
            row["drop_to"] = str(rec)
        else:
            row["flag"] = "ok"
        out.append(row)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--audit", required=True, help="rows JSON from tools/live_audit.py")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    rows = scan(json.loads(Path(a.audit).read_text(encoding="utf-8")), a.days)
    flagged = sorted((r for r in rows if r["flag"] == "above-recommended"),
                     key=lambda r: -r["days"])
    missing = [r for r in rows if r["flag"] == "no-recommended-recorded"]
    ok = [r for r in rows if r["flag"] == "ok"]

    print(f"live listings priced:        {len(rows)}")
    print(f"  at or below Recommended:   {len(ok)}")
    print(f"  ABOVE Recommended, {a.days}d+:  {len(flagged)}")
    print(f"  no Recommended in notes:   {len(missing)}\n")

    if flagged:
        tot_ask = sum(Decimal(r["ask"]) for r in flagged)
        tot_rec = sum(Decimal(r["drop_to"]) for r in flagged)
        print(f"{'days':>5} {'ask':>9} {'rec':>9} {'over':>7}  listing")
        for r in flagged:
            print(f"{r['days']:5} {r['ask']:>9} {r['drop_to']:>9} {r['over_pct']:>6}%  {r['dir']}")
            print(f"{'':33}{r['title'][:60]}")
        print(f"\ntotal ask ${tot_ask} vs total Recommended ${tot_rec} "
              f"(${tot_ask - tot_rec} of ask above the comp evidence)")
    if a.json:
        Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"\nrows -> {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
