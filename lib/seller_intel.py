#!/usr/bin/env python3
"""Seller intelligence — join eBay SOLD comps to the live Browse API.

The bridge between our two data sources:
  • Apify sold-comp data carries `sellerName` (+ feedback) for each realized sale
    — so we can mine the comps to find WHO actually sells a collectable type.
  • The eBay Browse API (lib/ebay_browse.py) then pulls that same seller's LIVE
    ACTIVE listings + current feedback.

Together: "these are the sellers moving Akro/Peltier marbles, here's their
realized-sale footprint, and here's what they're offering right now."

  rank    --from <sold.json...> [--by comps|realized] [--top 20] [--min-comps 2]
          Rank sellers by how much they sell in the comp data: # of sold comps,
          total + median realized $, point-in-time feedback, and the top
          marble types they sell (attributed via the top-100 taxonomy).

  profile <username> --from <sold.json...> [--max-active 50] [--q ...] [--category ...]
          One seller: their SOLD-comp footprint (from the data) PLUS their LIVE
          active listings and current feedback (Browse API). The full picture.

Sold-comp JSON must include `sellerName` (re-fetch Apify datasets with that
field). Honesty: sold prices are realized; active prices are ASKING. Type tags
are keyword buckets, not authenticated IDs.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ebay_browse  # noqa: E402

TAXONOMY = Path(__file__).resolve().parent.parent / "kb" / "taxonomies" / "marble-types-top100.md"


# --- taxonomy matcher (shared with the visual library curation) --------------
def load_taxonomy():
    """Parse (keyword -> type) pairs from the top-100 taxonomy, longest first."""
    pairs = []
    if not TAXONOMY.exists():
        return pairs
    for ln in TAXONOMY.read_text(encoding="utf-8").splitlines():
        if not ln.lstrip().startswith("|"):
            continue
        cols = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cols) < 4 or not cols[0].isdigit():
            continue
        typ, kws = cols[1], cols[3]
        for kw in kws.split(";"):
            kw = kw.strip().lower()
            if kw:
                pairs.append((kw, typ))
    pairs.sort(key=lambda x: len(x[0]), reverse=True)
    return pairs


def type_for(title, pairs):
    t = (title or "").lower()
    for kw, lab in pairs:
        if kw in t:
            return lab
    return None


# --- data --------------------------------------------------------------------
def load_comps(paths):
    """Read sold comps from a bare list, an Apify dump ({items}), or the standard
    saved-run JSON from lib/apify_ebay.py ({raw_items} — carries sellerName)."""
    items = []
    for f in paths:
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        if isinstance(data, list):
            items.extend(data)
        else:
            items.extend(data.get("raw_items") or data.get("items") or [])
    return items


def _fnum(s):
    """Parse a feedback-count string like '5.8K' or '106' to an int."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    try:
        if s.lower().endswith("k"):
            return int(float(s[:-1]) * 1000)
        if s.lower().endswith("m"):
            return int(float(s[:-1]) * 1_000_000)
        return int(float(s))
    except ValueError:
        return None


def aggregate(items, pairs):
    sellers = defaultdict(lambda: {"prices": [], "types": Counter(), "fb_pct": None,
                                   "fb_count": None, "titles": []})
    for it in items:
        name = it.get("sellerName")
        if not name:
            continue
        s = sellers[name]
        pr = it.get("soldPrice")
        if isinstance(pr, (int, float)):
            s["prices"].append(float(pr))
        lab = type_for(it.get("title"), pairs)
        if lab:
            s["types"][lab] += 1
        if it.get("sellerFeedbackPercent") and s["fb_pct"] is None:
            s["fb_pct"] = it["sellerFeedbackPercent"]
        fc = _fnum(it.get("sellerFeedbackCount"))
        if fc is not None and (s["fb_count"] is None or fc > s["fb_count"]):
            s["fb_count"] = fc
        if len(s["titles"]) < 3:
            s["titles"].append((it.get("title") or "")[:50])
    return sellers


def _stats(prices):
    if not prices:
        return (0, 0.0, 0.0, 0.0)
    return (len(prices), sum(prices), statistics.median(prices), max(prices))


# --- commands ----------------------------------------------------------------
def cmd_rank(args):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    pairs = load_taxonomy()
    sellers = aggregate(load_comps(args.from_), pairs)
    rows = []
    for name, s in sellers.items():
        n, total, med, mx = _stats(s["prices"])
        n_comps = max(n, sum(s["types"].values()), len(s["titles"]))
        if n_comps < args.min_comps:
            continue
        rows.append((name, n_comps, total, med, mx, s))
    key = (lambda r: r[2]) if args.by == "realized" else (lambda r: r[1])
    rows.sort(key=key, reverse=True)
    print(f"\nTop {min(args.top, len(rows))} marble sellers by "
          f"{'total realized $' if args.by=='realized' else 'sold-comp count'} "
          f"(of {len(sellers)} sellers in {sum(len(s['prices']) for s in sellers.values())} comps):\n")
    print(f"  {'seller':<22} {'comps':>5} {'realized$':>10} {'median$':>8} {'fb%':>6} {'fb#':>7}  top types")
    for name, n_comps, total, med, mx, s in rows[: args.top]:
        tt = ", ".join(f"{t}({c})" for t, c in s["types"].most_common(3))
        fb = s["fb_pct"] or "?"
        fc = s["fb_count"] if s["fb_count"] is not None else "?"
        print(f"  {name:<22} {n_comps:>5} {total:>10.0f} {med:>8.0f} {fb:>6} {str(fc):>7}  {tt}")


def cmd_profile(args):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    pairs = load_taxonomy()
    name = args.username
    sellers = aggregate(load_comps(args.from_), pairs)
    s = sellers.get(name)

    print(f"\n=== Seller profile: {name} ===\n")
    print("— SOLD-comp footprint (realized, from comp data) —")
    if not s:
        print(f"  (no sold comps for {name} in the provided data)")
    else:
        n, total, med, mx = _stats(s["prices"])
        print(f"  sold comps: {max(n, sum(s['types'].values()))}   "
              f"realized total: ${total:.0f}   median: ${med:.0f}   high: ${mx:.0f}")
        print(f"  feedback (point-in-time): {s['fb_pct'] or '?'}  ({s['fb_count'] if s['fb_count'] is not None else '?'} ratings)")
        print(f"  sells: {', '.join(f'{t} x{c}' for t,c in s['types'].most_common(6)) or '(untagged)'}")

    print("\n— LIVE active listings (asking, Browse API) —")
    try:
        active = ebay_browse.seller_items(name, q=args.q, category_ids=args.category,
                                          max_items=args.max_active)
    except Exception as e:
        print(f"  ! Browse API error: {e}")
        active = []
    if active:
        fb = next((a.get("sellerFeedbackPct") for a in active if a.get("sellerFeedbackPct")), None)
        fs = next((a.get("sellerFeedbackScore") for a in active if a.get("sellerFeedbackScore")), None)
        print(f"  live feedback: {fb or '?'}%  ({fs if fs is not None else '?'} score)   "
              f"active listings: {len(active)}")
        prices = [a["askingPrice"] for a in active if isinstance(a.get("askingPrice"), float)]
        if prices:
            print(f"  asking range: ${min(prices):.0f}–${max(prices):.0f}  (median ${statistics.median(prices):.0f})")
        print()
        for a in active[: args.show]:
            price = f"${a['askingPrice']:.0f}" if isinstance(a.get("askingPrice"), float) else "?"
            print(f"    {price:>7}  {(a.get('title') or '')[:58]}")
            print(f"            {a.get('url')}")
    else:
        print("  (no active listings found — they may have sold out, or list outside the default category)")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {"seller": name, "active": active}, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")


def cmd_landscape(args):
    """Competitive landscape for a category: join realized comps (who sells, how
    much) to LIVE active inventory (who has the most for sale, who asks highest).
    """
    import time
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    pairs = load_taxonomy()
    sellers = aggregate(load_comps(args.from_), pairs)

    # the competitor set = sellers most active in the comp data
    ranked = sorted(sellers.items(),
                    key=lambda kv: max(len(kv[1]["prices"]), sum(kv[1]["types"].values())),
                    reverse=True)[: args.check]
    print(f"\nProbing live active inventory for the top {len(ranked)} comp sellers "
          f"(category {args.category or ebay_browse.DEFAULT_SELLER_CATEGORY}, Browse API)…\n")

    rows = []
    for name, s in ranked:
        n, total, med, mx = _stats(s["prices"])
        try:
            active_total, recs = ebay_browse.seller_active(
                name, q=args.q, category_ids=args.category, sample=200)
        except Exception as e:
            active_total, recs = -1, []
            print(f"  ! {name}: Browse error ({type(e).__name__})")
        asks = [r["askingPrice"] for r in recs if isinstance(r.get("askingPrice"), float)]
        rows.append({
            "seller": name, "comps": max(n, sum(s["types"].values())),
            "realized": total, "med_sold": med, "high_sold": mx,
            "active": active_total,
            "ask_med": statistics.median(asks) if asks else 0.0,
            "ask_max": max(asks) if asks else 0.0,
            "fb": s["fb_pct"] or "?",
            "top": ", ".join(t for t, _ in s["types"].most_common(2)),
        })
        time.sleep(args.delay)

    def show(title, key, fmt):
        print(f"\n=== {title} ===")
        print(f"  {'seller':<20} {'active':>6} {'sold':>5} {'realized$':>9} "
              f"{'medSold':>7} {'askMed':>7} {'askMax':>7} {'fb':>6}  top types")
        for r in sorted(rows, key=key, reverse=True)[: args.top]:
            at = r["active"] if r["active"] >= 0 else "err"
            print(f"  {r['seller']:<20} {str(at):>6} {r['comps']:>5} {r['realized']:>9.0f} "
                  f"{r['med_sold']:>7.0f} {r['ask_med']:>7.0f} {r['ask_max']:>7.0f} "
                  f"{str(r['fb']):>6}  {r['top']}")

    show("MOST FOR SALE NOW (active inventory leaders)", lambda r: r["active"], None)
    show("HIGHEST-PRICED (premium — by top active asking)", lambda r: r["ask_max"], None)
    show("TOP REALIZED (volume x price, from sold comps)", lambda r: r["realized"], None)
    print("\nReading it: high active + low median = volume players; few active + high "
          "ask/realized = premium specialists. 'active' is exact (Browse total); "
          "sold figures are from the comp sample, asking from a 200-item sample.")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("rank", help="rank sellers found in the sold comps")
    p.add_argument("--from", dest="from_", nargs="+", required=True)
    p.add_argument("--by", choices=["comps", "realized"], default="comps")
    p.add_argument("--top", type=int, default=20)
    p.add_argument("--min-comps", type=int, default=2)
    p.set_defaults(func=cmd_rank)

    p = sub.add_parser("profile", help="one seller: comps footprint + live active listings")
    p.add_argument("username")
    p.add_argument("--from", dest="from_", nargs="+", required=True)
    p.add_argument("--max-active", type=int, default=50)
    p.add_argument("--show", type=int, default=10)
    p.add_argument("--q", default=None)
    p.add_argument("--category", default=None)
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("landscape", help="category competitive map: active inventory + price leaders")
    p.add_argument("--from", dest="from_", nargs="+", required=True)
    p.add_argument("--category", default=None, help="category_ids (default 220 Toys)")
    p.add_argument("--q", default=None, help="optional keyword to scope the category")
    p.add_argument("--check", type=int, default=30, help="how many top comp-sellers to probe live")
    p.add_argument("--top", type=int, default=12, help="rows per ranking")
    p.add_argument("--delay", type=float, default=0.2, help="seconds between Browse calls")
    p.add_argument("--json", default=None)
    p.set_defaults(func=cmd_landscape)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
