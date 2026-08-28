#!/usr/bin/env python3
"""price_vs_actual — did the PRICE stage's justified band survive contact with buyers?

For every SOLD item that the pipeline actually tracks (a `shoot_dir` in
sales_ledger.csv), read that shoot's `price.txt` and line the numbers up:

    Conservative (floor)  ·  Recommended  ·  Push-high (ceiling)  ·  ASK  ·  SOLD

and report where the realised price landed against the band PRICE justified.

Three questions this answers that no single ledger can:

  * **Was the ask the ceiling?** Standing policy is push-high + Best Offer, so an
    ask below Push-high is a departure worth seeing.
  * **Did the floor hold?** A sale under Conservative means the floor was not a
    floor — either the comp read was wrong or Best Offer auto-decline was not set
    where the price file said it should be.
  * **Which way is the band biased?** Consistently selling at Recommended while
    asking Push-high is a healthy Best Offer funnel; consistently selling BELOW
    Conservative is a pricing model that needs refitting, not a sales problem.

    python tools/price_vs_actual.py            # table + summary
    python tools/price_vs_actual.py --csv out.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The price files are written by a prompt, not a serialiser, so the tier lines
# come in several shapes. Seen in the wild:
#     "  Conservative  $9.90    — no-objection floor"
#     "- Conservative: $169 — safe floor"
#     "- Recommended (max supported): $199 — exact anchor"
#     "  Push-high     $24.99   — vetted ceiling"
# One regex per tier, tolerant of the separator and of a parenthetical.
_NUM = r"\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
# Between the tier word and its price, allow only punctuation/space or a whole
# parenthetical — never a bare word. "Recommended (max supported): $199" is the
# tier; "Conservative estimate: $25-$50" is prose ABOUT a premium, and a looser
# rule read that $25 as the Burberry floor when the real floor two lines down
# was $225. A tier line names the tier and then the number, nothing between.
_SEP = r"(?:\s*\([^)\n]{0,40}\))?\s*[:\-–—]?\s*"
TIER_RE = {
    "floor": re.compile(rf"^[\s\-*•]*conservative\b{_SEP}{_NUM}", re.I | re.M),
    "recommended": re.compile(rf"^[\s\-*•]*recommended\b{_SEP}{_NUM}", re.I | re.M),
    "ceiling": re.compile(rf"^[\s\-*•]*push[\s\-]?high\b{_SEP}{_NUM}", re.I | re.M),
}
MAX_RE = re.compile(rf"max supported price[:\s]*{_NUM}", re.I)


def _f(s):
    try:
        return float(str(s).replace(",", "").replace("$", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def read_price(shoot: Path) -> dict:
    """The tiers from a shoot's price.txt. Missing file or tier -> None."""
    f = shoot / "price.txt"
    if not f.exists():
        return {}
    txt = f.read_text(encoding="utf-8", errors="replace")
    out = {k: (_f(m.group(1)) if (m := rx.search(txt)) else None)
           for k, rx in TIER_RE.items()}
    out["max_supported"] = _f(m.group(1)) if (m := MAX_RE.search(txt)) else None
    return out


def gather() -> list[dict]:
    rows = []
    ledger = REPO / "sales_ledger.csv"
    if not ledger.exists():
        return rows                            # fresh checkout, nothing sold yet
    with ledger.open(newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            shoot_rel = (r.get("shoot_dir") or "").strip()
            if not shoot_rel:
                continue                       # listed outside the pipeline
            shoot = REPO / shoot_rel
            p = read_price(shoot)
            if not p:
                continue                       # tracked, but never priced by PRICE
            ask, sold = _f(r.get("listed_price")), _f(r.get("gross"))
            if not sold:
                continue
            rows.append({
                "shoot": shoot_rel.replace("\\", "/").replace("inventory/", ""),
                "title": (r.get("title") or "")[:44],
                "listing_id": r.get("listing_id", ""),
                "floor": p.get("floor"), "rec": p.get("recommended"),
                "ceiling": p.get("ceiling"), "max": p.get("max_supported"),
                "ask": ask, "sold": sold,
                "net": _f(r.get("net_before_postage")),
            })
    return rows


def classify(r: dict) -> str:
    """Where the realised price landed against the justified band."""
    s = r["sold"]
    if r["ceiling"] and s >= r["ceiling"]:
        return "at/above ceiling"
    if r["rec"] and s >= r["rec"]:
        return "rec..ceiling"
    if r["floor"] and s >= r["floor"]:
        return "floor..rec"
    if r["floor"]:
        return "BELOW FLOOR"
    return "no band"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--csv", help="also write the rows to this file")
    a = ap.parse_args()

    rows = gather()
    if not rows:
        print("no tracked sale has a price.txt with tiers")
        return 1

    def fmt(v):
        return f"{v:,.0f}" if v is not None else "—"

    print(f"{'floor':>7} {'rec':>7} {'ceil':>7} {'ASK':>7} {'SOLD':>7}  "
          f"{'ask vs':>7} {'sold vs':>8}  where            item")
    print(f"{'':>7} {'':>7} {'':>7} {'':>7} {'':>7}  {'ceil':>7} {'rec':>8}")
    print("-" * 118)
    for r in sorted(rows, key=lambda r: -(r["sold"] or 0)):
        askc = f"{r['ask'] / r['ceiling'] * 100:.0f}%" if (r["ask"] and r["ceiling"]) else "—"
        srec = f"{r['sold'] / r['rec'] * 100:.0f}%" if r["rec"] else "—"
        print(f"{fmt(r['floor']):>7} {fmt(r['rec']):>7} {fmt(r['ceiling']):>7} "
              f"{fmt(r['ask']):>7} {fmt(r['sold']):>7}  {askc:>7} {srec:>8}  "
              f"{classify(r):<16} {r['title']}")

    n = len(rows)
    below = [r for r in rows if classify(r) == "BELOW FLOOR"]
    atceil = [r for r in rows if classify(r) == "at/above ceiling"]
    askceil = [r for r in rows if r["ask"] and r["ceiling"]]
    asked_at_ceiling = [r for r in askceil if r["ask"] >= r["ceiling"] * 0.995]
    print("-" * 118)
    print(f"{n} tracked sales carry a PRICE band.")
    print(f"  asked AT the ceiling      : {len(asked_at_ceiling)}/{len(askceil)} "
          f"(push-high policy followed)")
    print(f"  sold at/above ceiling     : {len(atceil)}")
    print(f"  sold below the floor      : {len(below)}"
          + (" — " + ", ".join(r["shoot"] for r in below) if below else ""))
    got = [r["sold"] / r["rec"] for r in rows if r["rec"]]
    if got:
        got.sort()
        print(f"  median realised / Recommended: {got[len(got) // 2] * 100:.0f}%")
    ceil_ratio = [r["sold"] / r["ceiling"] for r in rows if r["ceiling"]]
    if ceil_ratio:
        ceil_ratio.sort()
        print(f"  median realised / Push-high  : {ceil_ratio[len(ceil_ratio) // 2] * 100:.0f}%")

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]) + ["where"])
            w.writeheader()
            for r in rows:
                w.writerow({**r, "where": classify(r)})
        print(f"[OK] wrote {a.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
