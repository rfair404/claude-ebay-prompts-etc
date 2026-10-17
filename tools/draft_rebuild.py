#!/usr/bin/env python3
"""Rebuild a draft.md for a LIVE listing from eBay, so `list_edit --update` can
edit it.

Every field-scoped edit (`lib/list_edit.py --update TARGET --fields ...`) reads
from a draft.md. The Linux migration (2026-10) left no inventory/ history, so
the ~240 listings that were live then have no draft and cannot be edited by the
pipeline at all. This writes one back from what eBay holds: the inventory item
(title, condition, aspects, package) and the offer (price, Best Offer, category,
description, policies).

    python tools/draft_rebuild.py --sku 5a568d66
    python tools/draft_rebuild.py --listing-id 206454286267 206455950753
    python tools/draft_rebuild.py --sku 5a568d66 --print        # stdout only
    python tools/draft_rebuild.py --sku 5a568d66 --store junk

Writes inventory/_rebuilt/<sku>/draft.md. Read-only against eBay — it never
writes to a listing. Then edit the draft and push one group at a time, dry run
first:

    python lib/list_edit.py --update inventory/_rebuilt/<sku> --fields aspects --dry-run

WHAT A REBUILT DRAFT CAN AND CAN'T DO

* Photos are eBay-hosted, so `photos:` is empty and the URLs sit in
  `meta.ebay_image_urls`. `--fields photos` refuses an empty list rather than
  strip the live gallery. New photos go through PREP like any other shoot.
* The description is converted from eBay's HTML back to the markdown subset
  `_body_to_html` writes. `meta.description_roundtrip` says whether converting
  it back reproduces the live HTML exactly. When it is false, check the
  `--dry-run` diff before you push `--fields description`.
* Fulfillment routing (`shipping.fulfillment_mode`, `international`, `us_only`)
  is inferred from the live policy ID against config. An unrecognised policy is
  recorded in meta and flagged. Check it before you push `--fields policies`.
* Item specifics with several values come back as YAML lists, and
  `_build_aspects` sends them back as lists.
* A draft already on disk for the SKU is never overwritten. The tool points at
  the existing one instead, or overwrites a previous rebuild with --force.
* Listings with no SKU, or a non-canonical one, are refused. A listing made
  outside the Inventory API (web form or Trading API) has no inventory item to
  rebuild from. A legacy slug SKU would be re-hashed by `_sku_for` and miss the
  live item.
"""
from __future__ import annotations

import argparse
import csv
import html
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
sys.path.insert(0, str(REPO / "tools"))

import yaml                                                            # noqa: E402

import stores                                                          # noqa: E402
from draft_io import parse_draft                                       # noqa: E402
from ebay_client import (EbayAPIError, api_send, get_offers_for_sku,   # noqa: E402
                         load_credentials)
import list_edit as L                                                  # noqa: E402

TEMPLATE = REPO / "templates" / "listing-v1.md"
OUT_ROOT = REPO / "inventory" / "_rebuilt"
_ASPECT_KEYS = {v: k for k, v in L._ASPECT_NAMES.items()}


# ---------------------------------------------------------------------------
# HTML description -> the markdown subset _body_to_html writes
# ---------------------------------------------------------------------------

class _HtmlToMd(HTMLParser):
    """The inverse of list_edit._body_to_html for the tags it emits: h2-h4,
    p, ul/li, strong, a. Anything else keeps its text and loses its markup,
    and the round-trip check reports that."""

    _HEAD = {"h1": "#", "h2": "#", "h3": "##", "h4": "###", "h5": "###", "h6": "###"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self.buf: list[str] = []
        self.prefix = ""
        self.href: Optional[str] = None

    def _flush(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if text:
            self.blocks.append(self.prefix + text)
        self.buf, self.prefix = [], ""

    def handle_starttag(self, tag, attrs):
        if tag in self._HEAD:
            self._flush(); self.prefix = self._HEAD[tag] + " "
        elif tag in ("p", "div", "li"):
            self._flush(); self.prefix = "- " if tag == "li" else ""
        elif tag == "br":
            self.buf.append(" ")
        elif tag in ("strong", "b"):
            self.buf.append("**")
        elif tag == "a":
            self.href = dict(attrs).get("href")
            self.buf.append("[")

    def handle_endtag(self, tag):
        if tag in self._HEAD or tag in ("p", "div", "li", "ul", "ol"):
            self._flush()
            if tag in ("ul", "ol"):
                self.blocks.append("")      # a list ends at a blank line
        elif tag in ("strong", "b"):
            self.buf.append("**")
        elif tag == "a":
            self.buf.append(f"]({self.href})" if self.href else "]")
            self.href = None

    def handle_data(self, data):
        self.buf.append(data)


def html_to_md(src: str) -> str:
    p = _HtmlToMd()
    p.feed(src or "")
    p.close()
    p._flush()
    # Blank line between blocks, except consecutive bullets stay one list.
    out: list[str] = []
    for b in p.blocks:
        if b == "":
            if out and out[-1] != "":
                out.append("")
            continue
        if out and out[-1] != "" and not (b.startswith("- ") and out[-1].startswith("- ")):
            out.append("")
        out.append(b)
    return "\n".join(out).strip("\n")


def _norm_html(s: str) -> str:
    s = html.unescape(s or "")
    s = re.sub(r">\s+<", "><", s)
    return re.sub(r"\s+", " ", s).strip()


def description_roundtrips(src_html: str, md: str) -> bool:
    return _norm_html(L._body_to_html(md)) == _norm_html(src_html)


# ---------------------------------------------------------------------------
# eBay -> draft frontmatter
# ---------------------------------------------------------------------------

def _money(node: Optional[dict]) -> Optional[str]:
    v = (node or {}).get("value")
    return str(Decimal(str(v)).quantize(Decimal("0.01"))) if v not in (None, "") else None


def _weight(pkg: dict) -> tuple[int, int]:
    w = pkg.get("weight") or {}
    try:
        val = float(w.get("value") or 0)
    except (TypeError, ValueError):
        return 0, 0
    unit = str(w.get("unit") or "POUND").upper()
    oz = {"POUND": val * 16, "OUNCE": val, "KILOGRAM": val * 35.274,
          "GRAM": val / 28.3495}.get(unit, 0)
    lb, rem = divmod(round(oz), 16)
    return int(lb), int(rem)


def _dims(pkg: dict) -> dict:
    d = pkg.get("dimensions") or {}
    k = {"INCH": 1.0, "CENTIMETER": 1 / 2.54}.get(str(d.get("unit") or "INCH").upper())
    if not d or k is None:
        return {"length": 0, "width": 0, "depth": 0}
    r = lambda x: round(float(x or 0) * k, 1)                       # noqa: E731
    return {"length": r(d.get("length")), "width": r(d.get("width")),
            "depth": r(d.get("height"))}


def _routing(fulfillment_id: Optional[str], store: str) -> tuple[dict, Optional[str]]:
    """Map the live fulfillment policy back to the draft flags that select it."""
    known = {
        L._ebay_extra("fulfillment_policy_id", store=store): {},
        L._ebay_extra("fulfillment_policy_id_media", store=store): {},
        L._ebay_extra("fulfillment_policy_id_local_pickup", store=store): {"fulfillment_mode": "LOCAL_PICKUP"},
        L._ebay_extra("fulfillment_policy_id_international", store=store): {"international": True},
        L._ebay_extra("fulfillment_policy_id_us_only", store=store): {"us_only": True},
    }
    known.pop(None, None)
    if fulfillment_id in known:
        return known[fulfillment_id], None
    return {}, (f"fulfillment policy {fulfillment_id} is not one config.yaml names — "
                f"check shipping flags before pushing --fields policies")


def build_draft(item: dict, offer: dict, sku: str, store: str,
                ledger_row: Optional[dict] = None) -> tuple[dict, str, list[str]]:
    """(frontmatter, markdown body, warnings) for one live SKU."""
    warnings: list[str] = []
    product = item.get("product") or {}
    lp = offer.get("listingPolicies") or {}

    spec: dict = {k: "" for k in L._ASPECT_NAMES}
    spec["upc"] = ""
    extra: dict = {}
    for name, vals in (product.get("aspects") or {}).items():
        vals = [str(v) for v in (vals or []) if str(v).strip()]
        if not vals:
            continue
        val = vals[0] if len(vals) == 1 else vals
        if name in _ASPECT_KEYS:
            spec[_ASPECT_KEYS[name]] = val
        else:
            extra[name] = val
    if product.get("upc"):
        spec["upc"] = str(product["upc"][0])
    spec["extra"] = extra

    src_html = offer.get("listingDescription") or product.get("description") or ""
    body = html_to_md(src_html)
    roundtrip = description_roundtrips(src_html, body)
    if not roundtrip:
        warnings.append("description does not round-trip exactly — check the "
                        "--dry-run diff before pushing --fields description")

    bot = lp.get("bestOfferTerms") or {}
    pkg = item.get("packageWeightAndSize") or {}
    lb, oz = _weight(pkg)
    flags, route_warn = _routing(lp.get("fulfillmentPolicyId"), store)
    if route_warn:
        warnings.append(route_warn)
    qty = ((item.get("availability") or {}).get("shipToLocationAvailability") or {}).get("quantity")

    listing = offer.get("listing") or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fm = {
        "template_version": "v1",
        "store": "" if stores.is_default(store) else store,
        "meta": {
            "item_id": sku,
            "shoot_dir": "",
            "drafted_at": (ledger_row or {}).get("drafted_at") or "",
            "rebuilt_from_ebay_at": now,
            "ebay_offer_id": str(offer.get("offerId") or ""),
            "ebay_listing_id": str(listing.get("listingId") or ""),
            "ebay_inventory_sku": sku,
            "last_synced": None,
            "ebay_image_urls": list(product.get("imageUrls") or []),
            "ebay_fulfillment_policy_id": lp.get("fulfillmentPolicyId") or "",
            "description_roundtrip": roundtrip,
            "notes": ("Rebuilt from eBay by tools/draft_rebuild.py. Photos are "
                      "eBay-hosted (meta.ebay_image_urls); there are no local files."),
        },
        "title": product.get("title") or "",
        "category_id": str(offer.get("categoryId") or ""),
        "category_path": "",
        "condition": item.get("condition") or "",
        "condition_description": item.get("conditionDescription") or "",
        "item_specifics": spec,
        "format": offer.get("format") or "FIXED_PRICE",
        "price": _money((offer.get("pricingSummary") or {}).get("price")) or "",
        "cost_of_goods": None,
        "quantity": int(qty if qty is not None else offer.get("availableQuantity") or 1),
        "best_offer": {
            "enabled": bool(bot.get("bestOfferEnabled")),
            "auto_decline_amount": _money(bot.get("autoDeclinePrice")),
            "auto_accept_amount": _money(bot.get("autoAcceptPrice")),
        },
        "shipping": {
            "fulfillment_mode": flags.get("fulfillment_mode", "SHIP"),
            "weight": {"major_lb": lb, "minor_oz": oz},
            "package_in": _dims(pkg),
            "international": flags.get("international", False),
            "us_only": flags.get("us_only", False),
        },
        "photos": [],
    }
    if fm["best_offer"]["enabled"] and Decimal(fm["price"] or 0) < 100:
        # validate_draft_for_sync (#140 A1) refuses Best Offer under $100
        # without a documented reason. This one is live already, so say so.
        fm["meta"]["notes"] += " best offer was already on when rebuilt."
    if not fm["condition"]:
        warnings.append("live inventory item has NO condition")
    return fm, body, warnings


def render_draft(fm: dict, body: str) -> str:
    constraints = parse_draft(TEMPLATE).frontmatter.get("_field_constraints") or {}
    head = ("# Rebuilt from the live eBay listing by tools/draft_rebuild.py.\n"
            "# Field meanings and constraints: templates/listing-v1.md.\n")
    dump = lambda d: yaml.safe_dump(d, sort_keys=False, allow_unicode=True,  # noqa: E731
                                    default_flow_style=False, width=1000)
    return (f"---\n{head}{dump(fm)}{dump({'_field_constraints': constraints})}---\n\n"
            f"{body}\n")


# ---------------------------------------------------------------------------
# Lookup + CLI
# ---------------------------------------------------------------------------

def _ledger(store: str) -> list[dict]:
    p = stores.paths(store).listings_ledger
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _existing_drafts() -> dict[str, Path]:
    """sku -> draft.md for every draft on disk, rebuilt ones included."""
    out: dict[str, Path] = {}
    rx = re.compile(r'ebay_inventory_sku:\s*"?([0-9a-f]{8})')
    for dm in (REPO / "inventory").rglob("draft.md"):
        try:
            m = rx.search(dm.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if m:
            out.setdefault(m.group(1), dm)
    return out


def rebuild(sku: str, creds, store: str, *, ledger: list[dict], existing: dict,
            force: bool = False, to_stdout: bool = False) -> tuple[str, str]:
    """Returns (status, message). status: written | printed | skipped | error."""
    if not L._CANONICAL_SKU_RE.match(sku or ""):
        return "error", (f"SKU {sku!r} is not canonical 8-hex; _sku_for would re-hash it "
                         f"and miss the live item. Not rebuilt.")
    out = OUT_ROOT / sku / "draft.md"
    have = existing.get(sku)
    if have and not to_stdout and not (force and have.resolve() == out.resolve()):
        return "skipped", f"draft already on disk: {have.relative_to(REPO)}"
    try:
        item = api_send("GET", f"/sell/inventory/v1/inventory_item/{sku}", creds=creds)
    except EbayAPIError as e:
        return "error", f"no inventory item for {sku} ({e})"
    offers = get_offers_for_sku(sku, creds=creds)
    if not offers:
        return "error", f"{sku} has an inventory item but no offer"
    row = next((r for r in ledger if r.get("sku") == sku), None)
    fm, body, warns = build_draft(item, offers[0], sku, store, row)
    text = render_draft(fm, body)
    if to_stdout:
        print(text)
        return "printed", "; ".join(warns)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    msg = str(out.relative_to(REPO))
    status = offers[0].get("status")
    if status != "PUBLISHED":
        warns.append(f"offer status is {status} — --update will refuse it")
    return "written", msg + ("".join(f"\n      ! {w}" for w in warns))


def _skus_for_listing_ids(ids: list[str], ledger: list[dict], creds) -> dict[str, Optional[str]]:
    found = {i: next((r["sku"] for r in ledger if r.get("listing_id") == i), None) for i in ids}
    if any(v is None for v in found.values()):
        for o in L.list_account_offers(creds=creds):
            lid = str(o.get("listing_id") or "")
            if lid in found and not found[lid]:
                found[lid] = o.get("sku")
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("--sku", nargs="+", default=[], help="inventory SKU(s)")
    ap.add_argument("--listing-id", nargs="+", default=[], help="eBay item number(s)")
    ap.add_argument("--print", dest="to_stdout", action="store_true",
                    help="print the draft instead of writing it")
    ap.add_argument("--force", action="store_true",
                    help="overwrite a previous rebuild in inventory/_rebuilt/ (never a real draft)")
    stores.add_store_args(ap)
    args = ap.parse_args(argv)
    if not (args.sku or args.listing_id):
        ap.error("give --sku and/or --listing-id")

    store = stores.resolve_store_name(args.store)
    creds = load_credentials(store=store)
    ledger = _ledger(store)
    existing = _existing_drafts()

    targets: list[tuple[str, Optional[str]]] = [(s, s) for s in args.sku]
    if args.listing_id:
        for lid, sku in _skus_for_listing_ids(args.listing_id, ledger, creds).items():
            targets.append((lid, sku))

    bad = 0
    for label, sku in targets:
        if not sku:
            print(f"  [X] {label}: no SKU on this listing — made outside the Inventory API; "
                  f"edit it in Seller Hub")
            bad += 1
            continue
        status, msg = rebuild(sku, creds, store, ledger=ledger, existing=existing,
                              force=args.force, to_stdout=args.to_stdout)
        tag = {"written": "OK", "printed": "OK", "skipped": "--", "error": "X"}[status]
        if status != "printed":
            print(f"  [{tag}] {label if label == sku else f'{label} ({sku})'}: {msg}")
        elif msg:
            print(f"  ! {msg}", file=sys.stderr)
        bad += status == "error"
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
