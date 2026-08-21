#!/usr/bin/env python3
"""Read-only assessment of every listing: sellable on eBay, and photo state.

Answers two questions that must not be answered from the local ledger:

  * is this SKU actually still on sale?  (an accepted Best Offer never writes
    back to listings_ledger.csv, so the ledger lies by omission)
  * have its photos been orientation-checked, rendered, and pushed?

Writes nothing, anywhere. Every eBay call is a GET.
"""
from __future__ import annotations
import csv, json, re, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "lib"))
import list_edit as L                                        # noqa: E402


def main() -> int:
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    live_only = "--all" not in sys.argv
    creds = L.load_credentials()

    led = {r["sku"]: r for r in csv.DictReader(open(ROOT / "listings_ledger.csv", encoding="utf-8"))}
    sold = {r["sku"] for r in csv.DictReader(open(ROOT / "sales_ledger.csv", encoding="utf-8")) if r.get("sku")}

    rows = []
    for d in sorted(ROOT.joinpath("inventory").rglob("draft.md")):
        s = d.parent
        m = re.search(r'ebay_inventory_sku:\s*"?([0-9a-f]{8})"?', d.read_text(encoding="utf-8", errors="ignore"))
        if not m: continue
        sku = m.group(1); r = led.get(sku, {})
        if live_only and r.get("status") not in ("PUBLISHED",): continue
        mf = s / ".prep" / "prep.json"
        pm = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
        ph = pm.get("photos", {})
        rows.append(dict(
            shoot=s.relative_to(ROOT).as_posix(), sku=sku,
            title=(r.get("title") or "")[:44], url=r.get("url", ""),
            ledger=r.get("status", "?"), local_sold=(sku in sold) or (s / "SOLD.md").exists(),
            frames=len(ph),
            unresolved=sum(1 for x in ph.values() if x["orientation"]["needs_ask"]),
            rotated=sum(1 for x in ph.values() if x["orientation"]["applied"]),
            rendered=bool(pm.get("chosen_preset")), pushed=bool(pm.get("pushed_at")),
        ))

    print(f"querying eBay for {len(rows)} SKUs (read-only)...\n")
    for i, row in enumerate(rows, 1):
        try:
            st = L.offer_sellable_state(row["sku"], creds)
            row.update(sellable=st["sellable"], ebay=st["status"],
                       qty=st["quantity"], why=st["reason"])
        except Exception as e:                               # noqa: BLE001
            row.update(sellable=None, ebay="ERROR", qty=None, why=str(e)[:80])
        if i % 20 == 0:
            print(f"   ...{i}/{len(rows)}"); sys.stdout.flush()
        time.sleep(0.05)

    json.dump(rows, open(ROOT / ".prep_assess.json", "w"), indent=1)

    sell   = [r for r in rows if r["sellable"] is True]
    nosell = [r for r in rows if r["sellable"] is False]
    err    = [r for r in rows if r["sellable"] is None]

    print(f"\n{'='*78}\nASSESSMENT — {len(rows)} listings the ledger calls PUBLISHED\n{'='*78}")
    print(f"  still sellable on eBay        : {len(sell)}")
    print(f"  NOT sellable (sold/ended)     : {len(nosell)}")
    print(f"  could not be checked          : {len(err)}")

    ghost = [r for r in nosell if not r["local_sold"]]
    print(f"\n  of the not-sellable, the ledger still shows live: {len(ghost)}")
    for r in ghost:
        print(f"     {r['sku']}  {r['ebay']:12} qty={r['qty']}  {r['title'][:40]:42} {r['url']}")

    pushed_dead = [r for r in nosell if r["pushed"]]
    print(f"\n  PUSHED BY ME AND NOW NOT SELLABLE: {len(pushed_dead)}")
    for r in pushed_dead:
        print(f"     {r['sku']}  {r['ebay']:12} qty={r['qty']}  {r['title'][:40]:42} {r['url']}")

    print(f"\n{'-'*78}\nORIENTATION — across the {len(sell)} still-sellable listings\n{'-'*78}")
    nocheck = [r for r in sell if r["frames"] == 0]
    unres   = [r for r in sell if r["unresolved"]]
    norend  = [r for r in sell if r["frames"] and not r["rendered"]]
    nopush  = [r for r in sell if r["rendered"] and not r["pushed"]]
    print(f"  checked                        : {len(sell)-len(nocheck)}")
    print(f"  frames rotated                 : {sum(r['rotated'] for r in sell)} of {sum(r['frames'] for r in sell)}")
    print(f"  NEED an orientation re-run     : {len(nocheck)+len(unres)}")
    for r in nocheck: print(f"     no PREP at all   {r['shoot']:46} {r['title'][:34]}")
    for r in unres:   print(f"     {r['unresolved']:2} unresolved   {r['shoot']:46} {r['title'][:34]}")
    print(f"  checked but NOT rendered       : {len(norend)}")
    for r in norend:  print(f"     {r['shoot']:46} {r['title'][:34]}")
    print(f"  rendered but NOT pushed        : {len(nopush)}")
    for r in nopush:  print(f"     {r['shoot']:46} {r['title'][:34]}")
    print(f"\nwrote .prep_assess.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
