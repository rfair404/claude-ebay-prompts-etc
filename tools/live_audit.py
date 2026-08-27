#!/usr/bin/env python3
"""LIVE AUDIT — reconcile the local files against what eBay actually shows.

Local state drifts from live state and nothing forces them back together: a
price edited in Seller Hub never reaches `draft.md`, an accepted Best Offer
never writes back to the ledger, and an ended listing leaves a folder still
marked PUBLISHED. This reads BOTH live sources, compares them to the local
files, and — with `--apply` — makes the local files say what eBay says.

Two live sources, because neither alone is enough:

  Sell API offers   authoritative for OUR side: sku -> offer -> listing id,
                    the price we set, the offer's own status.
  Browse actives    authoritative for the BUYER's side: what is actually
                    purchasable right now. A sold-out listing can still read
                    PUBLISHED as an offer, so an offer missing from Browse is
                    the signal that something sold or ended.

Live always wins. This tool never pushes local values to eBay — that is
`list_edit.py`'s job, behind the publish firewall. Here the flow is one-way:
eBay -> disk.

  audit            report the differences (default; writes nothing)
  audit --apply    update ledger + drafts, backing up every draft it rewrites

Backups go to `<shoot>/.history/draft-<timestamp>.md`, and every rewritten
draft gets `meta.notes` stamped with what changed and when.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "lib"))

INVENTORY = REPO / "inventory"
LEDGER = REPO / "listings_ledger.csv"

# Top-level categories to sweep for our own active listings. Browse rejects a
# bare seller filter and takes one category per call, so "the whole store" is a
# union. Same list the seller study uses.
from seller_style import DEFAULT_CATEGORIES  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(v) -> str | None:
    """Normalise a price to a comparable string.

    Without this the audit reports "$45.00 vs $45.0" as drift on nearly every
    listing: Browse renders two decimals, the offer record does not.
    """
    if v in (None, ""):
        return None
    try:
        return f"{Decimal(str(v)):.2f}"
    except (ArithmeticError, ValueError):
        return None


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


# --------------------------------------------------------------------------
# live
# --------------------------------------------------------------------------
def fetch_offers(verbose=True) -> list[dict]:
    import list_edit as L

    offers = L.list_account_offers()
    if verbose:
        print(f"  Sell API: {len(offers)} offers")
    return offers


def fetch_actives(seller: str, verbose=True) -> dict[str, dict]:
    import ebay_browse as B

    seen: dict[str, dict] = {}
    for cid, name in DEFAULT_CATEGORIES:
        try:
            total, recs = B.seller_active(seller, category_ids=cid, sample=200)
        except Exception as exc:
            if verbose:
                print(f"    {cid} {name}: {exc}", file=sys.stderr)
            continue
        for r in recs:
            lid = (r.get("url") or "").rstrip("/").split("/")[-1]
            r["listing_id"] = lid
            seen.setdefault(lid, r)
    if verbose:
        print(f"  Browse:   {len(seen)} active listings")
    return seen


# --------------------------------------------------------------------------
# local
# --------------------------------------------------------------------------
def scan_drafts() -> list[dict]:
    out = []
    # Recurse: drafts sit at any depth (inventory/more-mags-444/j-crew/3/draft.md
    # is three levels down, and a two-level glob silently misses every one of
    # them — which reads as "no local draft matches this sku").
    for dr in sorted(INVENTORY.rglob("draft.md")):
        if ".history" in dr.parts:
            continue
        try:
            t = dr.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        def grab(pat, text=t):
            m = re.search(pat, text, re.M)
            return m.group(1) if m else ""

        out.append({
            "path": dr,
            "dir": str(dr.parent.relative_to(REPO)).replace("\\", "/"),
            "title": grab(r'^title:\s*"(.*)"'),
            "price": grab(r'^price:\s*"(.*)"'),
            "sku": grab(r'ebay_inventory_sku:\s*"?([0-9a-zA-Z\-]{6,})"?'),
            "listing_id": grab(r'ebay_listing_id:\s*"?(\d+)"?'),
        })
    return out


def load_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    with LEDGER.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# --------------------------------------------------------------------------
# reconcile
# --------------------------------------------------------------------------
def reconcile(offers: list[dict], actives: dict, drafts: list[dict],
              ledger: list[dict]) -> list[dict]:
    by_sku = {d["sku"]: d for d in drafts if d["sku"]}
    by_lid = {d["listing_id"]: d for d in drafts if d["listing_id"]}
    led_by_sku = {r["sku"]: r for r in ledger if r.get("sku")}

    # A multi-variation (CHOICE) listing has many offers behind ONE listing id,
    # and Browse reports the cheapest variation as the listing's price. Comparing
    # that against each variation's own offer price manufactures drift on every
    # piece in the group, so those listings are compared on status only.
    lid_counts: dict[str, int] = {}
    for o in offers:
        if o.get("listing_id"):
            lid_counts[o["listing_id"]] = lid_counts.get(o["listing_id"], 0) + 1

    rows = []
    for o in offers:
        lid = o.get("listing_id") or ""
        d = by_sku.get(o["sku"]) or by_lid.get(lid)
        live_active = lid in actives
        act = actives.get(lid, {})
        issues = []

        # what a buyer sees right now beats what our offer record claims
        if o["status"] == "PUBLISHED" and not live_active:
            state = "GONE"
            issues.append("offer says PUBLISHED but the listing is not purchasable "
                          "(sold, ended, or out of stock)")
        elif o["status"] == "PUBLISHED":
            state = "LIVE"
        else:
            state = o["status"]

        variation_group = lid_counts.get(lid, 0) > 1
        live_price = _money(act.get("askingPrice"))
        offer_price = _money(o.get("price"))
        if variation_group:
            # the offer's own price is the truth for a variation; Browse's is
            # the group minimum
            live_price = offer_price
        elif live_price and offer_price and live_price != offer_price:
            issues.append(f"price: Browse ${live_price} vs offer ${offer_price}")

        row = {
            "sku": o["sku"], "listing_id": lid, "state": state,
            "live_title": act.get("title") or o.get("title") or "",
            "live_price": live_price or offer_price or "",
            "variation_group": variation_group,
            "dir": d["dir"] if d else "",
            "local_title": d["title"] if d else "",
            "local_price": d["price"] if d else "",
            "draft": d["path"] if d else None,
            "issues": issues,
        }
        if not d:
            # A CHOICE group's variations have no per-piece draft by design —
            # `list_edit_group.py` builds one listing from many SKUs. Saying
            # "no local draft" for each of them buries the real drift under 26
            # rows of noise.
            if variation_group:
                row["group"] = True
            else:
                row["issues"].append("no local draft folder matches this sku/listing id")
        else:
            if row["live_title"] and row["local_title"] and row["live_title"] != row["local_title"]:
                row["issues"].append("title differs from live")
            lp, dp = _money(row["live_price"]), _money(row["local_price"])
            if lp and dp and lp != dp:
                row["issues"].append(f"local price ${dp} != live ${lp}")
        led = led_by_sku.get(o["sku"])
        if led and led.get("status") == "PUBLISHED" and state == "GONE":
            row["issues"].append("ledger still says PUBLISHED")
        rows.append(row)

    # local drafts that think they are published but have no live offer at all
    live_skus = {o["sku"] for o in offers}
    for r in ledger:
        if r.get("status") == "PUBLISHED" and r.get("sku") and r["sku"] not in live_skus:
            rows.append({
                "sku": r["sku"], "listing_id": r.get("listing_id", ""), "state": "NO-OFFER",
                "live_title": "", "live_price": "",
                "dir": (by_sku.get(r["sku"]) or {}).get("dir", ""),
                "local_title": r.get("title", ""), "local_price": r.get("price", ""),
                "draft": (by_sku.get(r["sku"]) or {}).get("path"),
                "issues": ["ledger says PUBLISHED but eBay has no offer for this sku"],
            })
    return rows


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------
def _yaml_str(v: str) -> str:
    """Quote a value for YAML without altering it.

    The first version wrote `title: "%s" % value.replace('"', "'")`, which turned
    an inches mark into a foot mark: a plate measured 10.5" on eBay became 10.5'
    on disk. A title carrying a double quote gets single-quoted instead, with
    any internal single quotes doubled per YAML.
    """
    if '"' in v:
        return "'" + v.replace("'", "''") + "'"
    return '"' + v + '"'


def backup_draft(path: Path) -> Path:
    hist = path.parent / ".history"
    hist.mkdir(exist_ok=True)
    dst = hist / f"draft-{_stamp()}.md"
    shutil.copyfile(path, dst)
    return dst


def apply_row(row: dict, verbose=True) -> list[str]:
    """Make the local draft say what eBay says. Returns what changed."""
    path = row.get("draft")
    if not path or not Path(path).exists():
        return []
    text = Path(path).read_text(encoding="utf-8")
    changed = []

    if row["live_title"] and row["local_title"] and row["live_title"] != row["local_title"]:
        text = re.sub(r'^title:\s*".*"$', "title: " + _yaml_str(row["live_title"]),
                      text, count=1, flags=re.M)
        changed.append(f'title -> "{row["live_title"]}"')
    if row["live_price"] and row["live_price"] != (row["local_price"] or ""):
        text = re.sub(r'^price:\s*".*"$', 'price: "%s"' % row["live_price"],
                      text, count=1, flags=re.M)
        changed.append(f'price -> {row["live_price"]}')

    if not changed:
        return []
    backup_draft(Path(path))
    # The note lands inside an existing quoted YAML scalar, so it must not carry
    # a raw double quote. It did, and a title measured 10.5" broke the
    # frontmatter of every draft whose new title contained an inches mark.
    note = (f"live-audit {_now()}: " + "; ".join(changed)).replace('"', "'")
    if re.search(r'^\s+notes:\s*"', text, re.M):
        text = re.sub(r'^(\s+notes:\s*")(.*)"$',
                      lambda m: f'{m.group(1)}{(m.group(2) + " | " if m.group(2) else "")}{note}"',
                      text, count=1, flags=re.M)
    Path(path).write_text(text, encoding="utf-8")
    if verbose:
        print(f"    {row['dir']}: {'; '.join(changed)}")
    return changed


def apply_ledger(rows: list[dict], verbose=True) -> int:
    ledger = load_ledger()
    if not ledger:
        return 0
    by_sku = {r["sku"]: r for r in rows if r.get("sku")}
    n = 0
    for r in ledger:
        row = by_sku.get(r.get("sku", ""))
        if not row:
            continue
        new_status = {"LIVE": "PUBLISHED", "GONE": "NEEDS_CHECK",
                      "NO-OFFER": "NEEDS_CHECK"}.get(row["state"])
        if new_status and r.get("status") != new_status:
            r["status"] = new_status
            r["updated_at"] = _now()
            n += 1
        if row["live_price"] and r.get("price") != row["live_price"]:
            r["price"] = row["live_price"]
            r["updated_at"] = _now()
            n += 1
        if row["live_title"] and r.get("title") != row["live_title"]:
            r["title"] = row["live_title"]
            r["updated_at"] = _now()
            n += 1
    if n:
        backup = LEDGER.with_suffix(f".backup-{_stamp()}.csv")
        shutil.copyfile(LEDGER, backup)
        with LEDGER.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(ledger[0]))
            w.writeheader()
            w.writerows(ledger)
        if verbose:
            print(f"  ledger: {n} field(s) updated (backup {backup.name})")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seller", default="popsgames")
    ap.add_argument("--apply", action="store_true", help="write the local updates")
    ap.add_argument("--json", default=None, help="dump the reconciliation rows")
    ap.add_argument("--offers", default=None, help="use a cached offers JSON instead of the API")
    ap.add_argument("--actives", default=None, help="use a cached Browse JSON instead of the API")
    a = ap.parse_args()

    print("live:")
    if a.offers:
        offers = json.loads(Path(a.offers).read_text(encoding="utf-8"))
        print(f"  Sell API: {len(offers)} offers (cached)")
    else:
        offers = fetch_offers()
    if a.actives:
        recs = json.loads(Path(a.actives).read_text(encoding="utf-8"))
        actives = {}
        for r in recs:
            lid = (r.get("url") or "").rstrip("/").split("/")[-1]
            r["listing_id"] = lid
            actives[lid] = r
        print(f"  Browse:   {len(actives)} active listings (cached)")
    else:
        actives = fetch_actives(a.seller)

    drafts, ledger = scan_drafts(), load_ledger()
    print(f"local: {len(drafts)} drafts, {len(ledger)} ledger rows\n")

    rows = reconcile(offers, actives, drafts, ledger)
    live = [r for r in rows if r["state"] == "LIVE"]
    gone = [r for r in rows if r["state"] == "GONE"]
    noof = [r for r in rows if r["state"] == "NO-OFFER"]
    unpub = [r for r in rows if r["state"] == "UNPUBLISHED"]
    drift = [r for r in live if r["issues"]]

    print(f"LIVE (purchasable now):      {len(live)}")
    print(f"  of those, local drifted:   {len(drift)}")
    print(f"GONE (offer live, not buyable): {len(gone)}")
    print(f"UNPUBLISHED offers:          {len(unpub)}")
    print(f"ledger PUBLISHED, no offer:  {len(noof)}")

    if drift:
        print("\nlocal vs live differences:")
        for r in drift[:40]:
            print(f"  {r['dir'] or r['sku']}")
            for i in r["issues"]:
                print(f"      {i}")
        if len(drift) > 40:
            print(f"  ... and {len(drift) - 40} more")
    if gone:
        print("\nnot purchasable (check for a sale):")
        for r in gone[:25]:
            print(f"  {r['listing_id']}  {r['dir'] or r['sku']}  {r['live_title'][:52]}")
        if len(gone) > 25:
            print(f"  ... and {len(gone) - 25} more")

    if a.json:
        Path(a.json).write_text(json.dumps(
            [{k: (str(v) if isinstance(v, Path) else v) for k, v in r.items()} for r in rows],
            indent=1), encoding="utf-8")
        print(f"\nrows -> {a.json}")

    if a.apply:
        print("\napplying (live wins):")
        n = sum(bool(apply_row(r)) for r in rows if r["state"] in ("LIVE", "GONE"))
        print(f"  drafts: {n} rewritten (backups in each <shoot>/.history/)")
        apply_ledger(rows)
    else:
        print("\n(report only — re-run with --apply to update the local files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
