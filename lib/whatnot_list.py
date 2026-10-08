"""
LIST on Whatnot — the Whatnot side of the side-by-side publish (lib/publish.py).

Same shape as the eBay path in lib/list_edit.py, deliberately:

    sync_to_whatnot(draft)            productCreate / productUpdate, listing
                                      UNPUBLISHED  (eBay: --sync)
    publish_to_whatnot(draft, confirm) listingPublish — DRY RUN unless
                                      confirm=True (eBay: --publish)
    end_on_whatnot(draft, confirm)    listingUnpublish — DRY RUN unless
                                      confirm=True (eBay: --end)

The draft.md is the single source of truth for both channels. Whatnot-only
choices go in an optional `whatnot:` block in the frontmatter:

    whatnot:
      taxonomy_id: 574           # `ebz whatnot --taxonomy vintage advertisement`
      price: 29.99               # optional — defaults to the draft's price
      format: buy_it_now         # or: auction
      starting_price: 5.00       # auction only
      offerable: true            # defaults to best_offer.enabled
      shipping_profile_id: "..." # defaults to whatnot.shipping_profile_id in config

Whatnot IDs are written back into `meta:` beside the eBay ones (meta.whatnot_*),
and lifecycle rows go to whatnot_ledger.csv — never into listings_ledger.csv,
whose rows are eBay offers that `ebz reconcile` checks against eBay.

----- Photos -----

Whatnot takes media by URL only; it has no upload endpoint. Two sources, set
by `whatnot.media.source` in config:

    eps       (default) upload the PREP-approved photos to eBay Picture
              Services with the draft's eBay store credentials and hand
              Whatnot those URLs. Uses the same PREP gate as the eBay path.
    base_url  `whatnot.media.base_url` + "/<shoot folder>/<photo path>" — for
              a static host (an R2/S3 bucket) you sync listing/ photos to.

Either way the URLs are cached in meta.whatnot_media_urls so a re-sync does
not re-upload.

----- Cross-listing -----

Nothing here ends the eBay listing when the Whatnot one sells, or vice versa.
publish_to_whatnot() therefore refuses a single-quantity item that is already
live on eBay unless `crosslist=True` — the operator is accepting that a
double sale has to be cancelled by hand.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

_LIB = Path(__file__).resolve().parent
for _p in (str(_LIB.parent), str(_LIB)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import ConfigError                                     # noqa: E402
from draft_io import Draft, parse_draft, resolve_photo_paths, update_meta  # noqa: E402
from whatnot_client import (                                       # noqa: E402
    WhatnotAPIError, WhatnotCredentials, get_product_attributes, get_shipping_profiles,
    graphql, load_credentials, nodes, raise_user_errors, search_taxonomy,
    taxonomy_global_id, whatnot_setting, whoami,
)

ROOT = _LIB.parent
CURRENCY = "USD"
TITLE_MAX = 999
QTY_MAX = 999

# eBay condition enum (draft.condition) -> the Whatnot condition attribute's
# option text. Whatnot's options vary by category; `ebz whatnot --attributes
# <taxonomy_id>` lists the real ones, and whatnot.condition_values in config
# overrides any of these.
DEFAULT_CONDITION_VALUES = {
    "NEW": "Brand New",
    "LIKE_NEW": "Like New",
    "NEW_OTHER": "Like New",
    "NEW_WITH_DEFECTS": "Like New",
    "USED_EXCELLENT": "Used - Excellent",
    "USED_VERY_GOOD": "Used - Very Good",
    "USED_GOOD": "Used - Good",
    "USED_ACCEPTABLE": "Used - Acceptable",
    "FOR_PARTS_OR_NOT_WORKING": "For Parts or Not Working",
}

# Fields selected back from productCreate/productUpdate. Listing is an
# interface, so the concrete types are spelled out.
_LISTING_FIELDS = ("... on BuyItNowListing { id url status published } "
                   "... on AuctionListing { id url status published }")
_PRODUCT_SELECTION = (
    "product { id variants(first: 5) { edges { node { id sku "
    f"listings(first: 5) {{ edges {{ node {{ {_LISTING_FIELDS} }} }} }} }} }} }} }}"
    " userErrors { field message }")


@dataclass
class WhatnotSyncResult:
    operation: str                 # "created" | "updated"
    product_id: str
    variant_id: str
    listing_id: str
    listing_url: str
    media_count: int
    environment: str


@dataclass
class WhatnotPublishResult:
    dry_run: bool
    listing_id: str
    title: str
    price: str
    status_before: str
    listing_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_draft_path(target: str | Path) -> Path:
    p = Path(target)
    if p.is_dir():
        p = p / "draft.md"
    if not p.exists():
        raise FileNotFoundError(f"No draft.md found at {p}")
    return p


def _cents(value: object) -> Optional[int]:
    try:
        d = Decimal(str(value).strip().lstrip("$"))
    except (InvalidOperation, AttributeError):
        return None
    cents = int((d * 100).quantize(Decimal("1")))
    return cents if cents > 0 else None


def _money(cents: int) -> dict:
    return {"amount": cents, "currencyCode": CURRENCY}


def _wn(draft: Draft, key: str, default=None):
    """A value from the draft's optional `whatnot:` block."""
    block = draft.frontmatter.get("whatnot") or {}
    return block.get(key, default) if isinstance(block, dict) else default


def _quantity(draft: Draft) -> int:
    try:
        return int(draft.get("quantity") or 1)
    except (TypeError, ValueError):
        return 1


def _price_cents(draft: Draft) -> Optional[int]:
    return _cents(_wn(draft, "price") if _wn(draft, "price") is not None else draft.get("price"))


def _format(draft: Draft) -> str:
    fmt = str(_wn(draft, "format") or "buy_it_now").strip().lower().replace("-", "_")
    return "auction" if fmt == "auction" else "buy_it_now"


def _taxonomy_id(draft: Draft) -> Optional[str]:
    tid = _wn(draft, "taxonomy_id") or whatnot_setting("default_taxonomy_id")
    return str(tid).strip() if tid not in (None, "") else None


def _ebay_sku(draft: Draft) -> str:
    """Reuse the eBay SKU so one item has one label in both channels."""
    from list_edit import _sku_for
    return _sku_for(draft)


def _is_live_on_ebay(draft: Draft) -> bool:
    return bool(str(draft.get("meta.ebay_listing_id") or "").strip())


def is_live_on_whatnot(draft: Draft) -> bool:
    return bool(str(draft.get("meta.whatnot_published_at") or "").strip())


def _plain_description(draft: Draft) -> str:
    """Whatnot renders the description as plain text: drop markdown markers,
    keep the line structure, and lead with the condition disclosure."""
    lines = []
    for ln in draft.body.splitlines():
        ln = re.sub(r"^\s{0,3}#{1,6}\s*", "", ln)          # headings
        ln = re.sub(r"^\s*[*-]\s+", "• ", ln)             # bullets
        ln = re.sub(r"\*\*(.+?)\*\*", r"\1", ln)          # bold
        ln = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1", ln)  # links: eBay URLs don't belong on Whatnot
        lines.append(ln.rstrip())
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    cond = str(draft.get("condition_description") or "").strip()
    return f"Condition: {cond}\n\n{body}" if cond else body


def _condition_value(draft: Draft) -> Optional[str]:
    enum = str(draft.get("condition") or "").strip().upper()
    overrides = whatnot_setting("condition_values") or {}
    return (overrides.get(enum) if isinstance(overrides, dict) else None) or \
        DEFAULT_CONDITION_VALUES.get(enum)


def _build_attributes(draft: Draft) -> list[dict]:
    """Condition and brand, when their attribute IDs are configured
    (whatnot.attributes.condition / .brand — `ebz whatnot --attributes`)."""
    ids = whatnot_setting("attributes") or {}
    if not isinstance(ids, dict):
        return []
    out = []
    cond = _condition_value(draft)
    if ids.get("condition") and cond:
        out.append({"id": str(ids["condition"]), "value": cond})
    brand = str(draft.get("item_specifics.brand") or "").strip()
    if ids.get("brand") and brand:
        out.append({"id": str(ids["brand"]), "value": brand})
    return out


def _weight_oz(draft: Draft) -> Optional[float]:
    try:
        lb = float(draft.get("shipping.weight.major_lb") or 0)
        oz = float(draft.get("shipping.weight.minor_oz") or 0)
    except (TypeError, ValueError):
        return None
    total = lb * 16 + oz
    return round(total, 2) if total > 0 else None


def _dimensions(draft: Draft) -> Optional[dict]:
    try:
        l, w, d = (float(draft.get(f"shipping.package_in.{k}") or 0) for k in ("l", "w", "d"))
    except (TypeError, ValueError):
        return None
    if min(l, w, d) <= 0:
        return None
    return {"length": l, "width": w, "height": d, "unit": "INCH"}


# ---------------------------------------------------------------------------
# Validation + payload building (pure — no network)
# ---------------------------------------------------------------------------

def validate_draft_for_whatnot(draft_path: Path) -> list[str]:
    """Offline checks. Empty list == ready to sync to Whatnot."""
    draft = parse_draft(_resolve_draft_path(draft_path))
    issues = []
    title = str(draft.get("title") or "").strip()
    if not title:
        issues.append("title is empty")
    elif len(title) > TITLE_MAX:
        issues.append(f"title is {len(title)} chars; Whatnot allows {TITLE_MAX}")
    if not draft.body.strip():
        issues.append("description (draft body) is empty")
    if _format(draft) == "auction":
        if _cents(_wn(draft, "starting_price")) is None:
            issues.append("whatnot.format is auction but whatnot.starting_price is missing or <= 0")
    elif _price_cents(draft) is None:
        issues.append("price is missing or <= 0")
    qty = _quantity(draft)
    if not 0 <= qty <= QTY_MAX:
        issues.append(f"quantity {qty} is outside Whatnot's 0..{QTY_MAX}")
    if not _taxonomy_id(draft):
        issues.append("no Whatnot category: set whatnot.taxonomy_id in the draft "
                      "(find one with `ebz whatnot --taxonomy <words>`) or "
                      "whatnot.default_taxonomy_id in config")
    if not resolve_photo_paths(draft):
        issues.append("no photos listed")
    if not (_wn(draft, "shipping_profile_id") or whatnot_setting("shipping_profile_id")
            or _weight_oz(draft)):
        issues.append("no shipping: set whatnot.shipping_profile_id, or a shipping.weight "
                      "so Whatnot can pick a profile (an unset profile blocks activation)")
    return issues


def build_listing_input(draft: Draft, *, published: Optional[bool] = None,
                        listing_id: Optional[str] = None) -> dict:
    listing: dict = {}
    if listing_id:
        listing["id"] = listing_id
    if _format(draft) == "auction":
        listing["auction"] = {"startingPrice": _money(_cents(_wn(draft, "starting_price")))}
    else:
        offerable = _wn(draft, "offerable")
        if offerable is None:
            offerable = bool(draft.get("best_offer.enabled"))
        listing["buyItNow"] = {"price": _money(_price_cents(draft)), "offerable": bool(offerable)}
    # ListingInput.inventoryLevel is marked deprecated in favour of a variant
    # inventoryLevels field the published docs don't define yet; it is the one
    # documented way to set quantity today.
    listing["inventoryLevel"] = {"quantity": _quantity(draft)}
    if published is not None:
        listing["published"] = published
    return listing


def build_product_input(draft: Draft, media_urls: list[str], *,
                        product_id: Optional[str] = None,
                        variant_id: Optional[str] = None,
                        listing_id: Optional[str] = None) -> dict:
    """ProductInput for productCreate (no ids) or productUpdate (ids given).
    A new listing is always created with published=False: publishing is a
    separate, confirm-gated call."""
    category = {"taxonomyId": taxonomy_global_id(_taxonomy_id(draft))}
    ebay_cat = str(draft.get("category_id") or "").strip()
    if ebay_cat:
        category.update({"externalCategoryID": ebay_cat, "externalCategorySource": "ebay"})

    variant: dict = {"sku": _ebay_sku(draft)}
    if _format(draft) == "buy_it_now":
        variant["price"] = _money(_price_cents(draft))
    if variant_id:
        variant["id"] = variant_id
    else:
        variant["mediaSources"] = list(media_urls)
    variant["listings"] = [build_listing_input(
        draft, published=None if listing_id else False, listing_id=listing_id)]

    product: dict = {
        "title": str(draft.get("title") or "").strip(),
        "description": _plain_description(draft),
        "productCategory": category,
        "variants": [variant],
    }
    if product_id:
        product["id"] = product_id
    attrs = _build_attributes(draft)
    if attrs:
        product["attributes"] = attrs

    profile = _wn(draft, "shipping_profile_id") or whatnot_setting("shipping_profile_id")
    if profile:
        product["shippingProfileId"] = str(profile)
    else:
        oz = _weight_oz(draft)
        if oz:
            product.update({"weight": oz, "weightUnit": "OUNCE",
                            "autoCreateShippingProfile": True})
            dims = _dimensions(draft)
            if dims:
                product["dimensions"] = dims
    if draft.get("item_id") or draft.get("meta.item_id"):
        product["externalId"] = str(draft.get("meta.item_id") or draft.get("item_id"))
    return product


def build_media_input(media_urls: list[str], title: str) -> list[dict]:
    return [{"source": u, "mediaContentType": "IMAGE", "alt": title[:120]} for u in media_urls]


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------

def _media_urls(draft: Draft) -> list[str]:
    cached = [u.strip() for u in str(draft.get("meta.whatnot_media_urls") or "").splitlines()
              if u.strip()]
    if cached:
        return cached
    from list_edit import _assert_photos_cleared
    photos = resolve_photo_paths(draft)
    _assert_photos_cleared(photos)
    media = whatnot_setting("media") or {}
    source = str((media.get("source") if isinstance(media, dict) else None) or "eps").lower()
    if source == "base_url":
        base = str(media.get("base_url") or "").rstrip("/")
        if not base:
            raise ConfigError("whatnot.media.source is base_url but whatnot.media.base_url is unset")
        shoot = draft.path.parent
        return [f"{base}/{shoot.name}/{p.relative_to(shoot).as_posix()}" for p in photos]
    if source != "eps":
        raise ConfigError(f"whatnot.media.source must be 'eps' or 'base_url', not {source!r}")
    from ebay_client import load_credentials as load_ebay_credentials
    from list_edit import _draft_store, upload_photos_to_eps
    creds = load_ebay_credentials(store=_draft_store(str(draft.path)))
    return upload_photos_to_eps(photos, creds=creds)


# ---------------------------------------------------------------------------
# Ledger — whatnot_ledger.csv, one row per SKU
# ---------------------------------------------------------------------------

_LEDGER_FIELDS = ["sku", "status", "title", "price", "product_id", "listing_id", "url",
                  "environment", "synced_at", "published_at", "ended_at", "updated_at"]
_LEDGER_TS_FOR = {"SYNCED": "synced_at", "PUBLISHED": "published_at", "ENDED": "ended_at"}


def _ledger_path() -> Path:
    override = os.environ.get("EBAYBIZ_WHATNOT_LEDGER")
    return Path(override) if override else ROOT / "whatnot_ledger.csv"


def upsert_whatnot_ledger(sku: str, status: str, **fields: str) -> Optional[str]:
    """Same contract as list_edit.upsert_listing: SYNCED never demotes a
    PUBLISHED row, only provided fields are written, never raises."""
    if not sku:
        return None
    try:
        path = _ledger_path()
        rows: list[dict] = []
        if path.exists():
            with path.open(newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        row = next((r for r in rows if r.get("sku") == sku), None)
        if row is None:
            row = {k: "" for k in _LEDGER_FIELDS}
            row["sku"] = sku
            rows.append(row)
        for k, v in fields.items():
            if v and k in _LEDGER_FIELDS:
                row[k] = str(v)
        cur = row.get("status") or ""
        row["status"] = "PUBLISHED" if (status == "SYNCED" and cur == "PUBLISHED") else status
        ts = _now()
        tsf = _LEDGER_TS_FOR.get(status)
        if tsf and not row.get(tsf):
            row[tsf] = ts
        row["updated_at"] = ts
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_LEDGER_FIELDS, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in _LEDGER_FIELDS})
        return str(path)
    except (OSError, csv.Error):
        return None


# ---------------------------------------------------------------------------
# Sync / publish / end
# ---------------------------------------------------------------------------

def _first_ids(product: dict) -> tuple[str, str, dict]:
    variant = (nodes(product.get("variants")) or [{}])[0]
    listing = (nodes(variant.get("listings")) or [{}])[0]
    return str(variant.get("id") or ""), str(listing.get("id") or ""), listing


def _check_environment(draft: Draft, creds: WhatnotCredentials) -> None:
    """IDs from staging mean nothing on production (and vice versa)."""
    env = str(draft.get("meta.whatnot_environment") or "").strip()
    if env and env != creds.environment and draft.get("meta.whatnot_product_id"):
        raise ValueError(
            f"this draft's Whatnot product was created on {env!r} but the configured "
            f"environment is {creds.environment!r}. Clear meta.whatnot_* to list it "
            f"fresh on {creds.environment}.")


def sync_to_whatnot(draft_path: Path,
                    creds: Optional[WhatnotCredentials] = None) -> WhatnotSyncResult:
    """Create (or update) the Whatnot product with its listing UNPUBLISHED."""
    draft_path = _resolve_draft_path(draft_path)
    issues = validate_draft_for_whatnot(draft_path)
    if issues:
        raise ValueError("draft is not Whatnot-ready:\n  - " + "\n  - ".join(issues))
    creds = creds or load_credentials()
    draft = parse_draft(draft_path)
    _check_environment(draft, creds)

    product_id = str(draft.get("meta.whatnot_product_id") or "").strip()
    variant_id = str(draft.get("meta.whatnot_variant_id") or "").strip()
    listing_id = str(draft.get("meta.whatnot_listing_id") or "").strip()
    title = str(draft.get("title") or "")

    if product_id:
        # Update in place. Media are not resent: productUpdate's media argument
        # adds images, so resending would duplicate every photo.
        q = ("mutation($input: ProductInput!) { productUpdate(input: $input) { "
             + _PRODUCT_SELECTION + " } }")
        inp = build_product_input(draft, [], product_id=product_id,
                                  variant_id=variant_id or None, listing_id=listing_id or None)
        payload = raise_user_errors(graphql(q, {"input": inp}, creds).get("productUpdate"),
                                    "productUpdate")
        operation, media = "updated", []
    else:
        media = _media_urls(draft)
        q = ("mutation($input: ProductInput!, $media: [CreateMediaInput!]!) { "
             "productCreate(input: $input, media: $media) { " + _PRODUCT_SELECTION + " } }")
        inp = build_product_input(draft, media)
        payload = raise_user_errors(
            graphql(q, {"input": inp, "media": build_media_input(media, title)}, creds)
            .get("productCreate"), "productCreate")
        operation = "created"

    product = payload.get("product") or {}
    product_id = str(product.get("id") or product_id)
    v_id, l_id, listing = _first_ids(product)
    variant_id, listing_id = v_id or variant_id, l_id or listing_id
    if not product_id or not listing_id:
        raise WhatnotAPIError(f"{operation} product but Whatnot returned no product/listing id: "
                              f"{payload}")
    url = str(listing.get("url") or draft.get("meta.whatnot_url") or "")

    meta = {"whatnot_product_id": product_id, "whatnot_variant_id": variant_id,
            "whatnot_listing_id": listing_id, "whatnot_environment": creds.environment,
            "whatnot_last_synced": _now()}
    if url:
        meta["whatnot_url"] = url
    if media:
        meta["whatnot_media_urls"] = "\n".join(media)
    update_meta(draft_path, meta)

    sku = _ebay_sku(draft)
    if upsert_whatnot_ledger(sku, "SYNCED", title=title, price=_price_str(draft),
                             product_id=product_id, listing_id=listing_id, url=url,
                             environment=creds.environment):
        print(f"  [whatnot ledger] {sku} -> SYNCED")
    return WhatnotSyncResult(operation=operation, product_id=product_id, variant_id=variant_id,
                             listing_id=listing_id, listing_url=url, media_count=len(media),
                             environment=creds.environment)


def _price_str(draft: Draft) -> str:
    c = _cents(_wn(draft, "starting_price")) if _format(draft) == "auction" else _price_cents(draft)
    return f"{c / 100:.2f}" if c else "?"


def get_listing(listing_id: str, creds: Optional[WhatnotCredentials] = None) -> dict:
    q = f"query($id: ID!) {{ listing(id: $id) {{ {_LISTING_FIELDS} }} }}"
    return graphql(q, {"id": listing_id}, creds).get("listing") or {}


def publish_to_whatnot(draft_path: Path, creds: Optional[WhatnotCredentials] = None,
                       confirm: bool = False, crosslist: bool = False) -> WhatnotPublishResult:
    """Make the synced Whatnot listing live. DRY RUN unless confirm=True — the
    only function that calls listingPublish."""
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    listing_id = str(draft.get("meta.whatnot_listing_id") or "").strip()
    if not listing_id:
        raise ValueError("draft has no meta.whatnot_listing_id — sync it to Whatnot first.")
    creds = creds or load_credentials()
    _check_environment(draft, creds)
    title, price = str(draft.get("title") or ""), _price_str(draft)

    live = get_listing(listing_id, creds)
    status = str(live.get("status") or "UNKNOWN")
    if live.get("published") and status == "ACTIVE":
        return WhatnotPublishResult(dry_run=False, listing_id=listing_id, title=title,
                                    price=price, status_before="PUBLISHED",
                                    listing_url=live.get("url"))
    if status in ("SOLD", "SOLD_OUT"):
        raise ValueError(f"Whatnot listing {listing_id} is {status} — not re-publishing a sold item.")
    if _is_live_on_ebay(draft) and _quantity(draft) <= 1 and not crosslist:
        raise ValueError(
            "this one-of-a-kind item is already live on eBay "
            f"(listing {draft.get('meta.ebay_listing_id')}). Nothing ends one listing when the "
            "other sells, so publishing on Whatnot risks selling it twice. Re-run with "
            "--crosslist to accept that, or end the eBay listing first.")
    if not confirm:
        return WhatnotPublishResult(dry_run=True, listing_id=listing_id, title=title,
                                    price=price, status_before=status, listing_url=live.get("url"))

    q = ("mutation($input: ListingPublishInput!) { listingPublish(input: $input) { "
         f"listing {{ {_LISTING_FIELDS} }} userErrors {{ field message }} }} }}")
    payload = raise_user_errors(graphql(q, {"input": {"id": listing_id}}, creds)
                                .get("listingPublish"), "listingPublish")
    url = str((payload.get("listing") or {}).get("url") or live.get("url") or "")
    update_meta(draft_path, {"whatnot_published_at": _now(), **({"whatnot_url": url} if url else {})})
    upsert_whatnot_ledger(_ebay_sku(draft), "PUBLISHED", title=title, price=price,
                          listing_id=listing_id, url=url, environment=creds.environment)
    return WhatnotPublishResult(dry_run=False, listing_id=listing_id, title=title, price=price,
                                status_before=status, listing_url=url or None)


def end_on_whatnot(draft_path: Path, creds: Optional[WhatnotCredentials] = None,
                   confirm: bool = False) -> WhatnotPublishResult:
    """Take the Whatnot listing down (unpublish, not delete — it can be
    re-published). DRY RUN unless confirm=True."""
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    listing_id = str(draft.get("meta.whatnot_listing_id") or "").strip()
    if not listing_id:
        raise ValueError("draft has no meta.whatnot_listing_id — nothing to end on Whatnot.")
    creds = creds or load_credentials()
    _check_environment(draft, creds)
    title, price = str(draft.get("title") or ""), _price_str(draft)
    if not confirm:
        return WhatnotPublishResult(dry_run=True, listing_id=listing_id, title=title,
                                    price=price, status_before="?")
    q = ("mutation($input: ListingPublishInput!) { listingUnpublish(input: $input) { "
         "userErrors { field message } } }")
    raise_user_errors(graphql(q, {"input": {"id": listing_id}}, creds).get("listingUnpublish"),
                      "listingUnpublish")
    # Mirrors list_edit.end_listing clearing ebay_listing_id: an empty
    # whatnot_published_at is how the cross-list guard knows it is not live.
    update_meta(draft_path, {"whatnot_published_at": "", "whatnot_ended_at": _now()})
    upsert_whatnot_ledger(_ebay_sku(draft), "ENDED", listing_id=listing_id,
                          environment=creds.environment)
    return WhatnotPublishResult(dry_run=False, listing_id=listing_id, title=title,
                                price=price, status_before="PUBLISHED")


# ---------------------------------------------------------------------------
# CLI — `ebz whatnot`
# ---------------------------------------------------------------------------

def _setup_check() -> None:
    try:
        creds = load_credentials()
    except ConfigError as e:
        print(f"[X] {e}")
        return
    print(f"environment: {creds.environment}  ({creds.endpoint})")
    try:
        me = whoami(creds)
        print(f"[OK] token works — seller {me.get('username') or me.get('id')}")
    except Exception as e:                                   # noqa: BLE001
        print(f"[X] token check failed: {e}")
        return
    profile = whatnot_setting("shipping_profile_id")
    print(f"shipping_profile_id: {profile or '(unset — uses draft weight to auto-create)'}")
    attrs = whatnot_setting("attributes") or {}
    print(f"attributes: condition={attrs.get('condition') or '(unset)'} "
          f"brand={attrs.get('brand') or '(unset)'}")
    media = whatnot_setting("media") or {}
    print(f"media source: {(media.get('source') if isinstance(media, dict) else None) or 'eps'}")


def _cli() -> None:
    ap = argparse.ArgumentParser(prog="ebz whatnot",
                                 description="LIST on Whatnot (Seller API). Writes are DRY RUN "
                                             "unless --confirm.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--validate", metavar="DRAFT", help="offline Whatnot-readiness check")
    g.add_argument("--sync", metavar="DRAFT", help="create/update the product, listing unpublished")
    g.add_argument("--publish", metavar="DRAFT", help="make the synced listing live (needs --confirm)")
    g.add_argument("--list", dest="list_target", metavar="DRAFT",
                   help="sync, then publish (needs --confirm to go live)")
    g.add_argument("--end", metavar="DRAFT", help="unpublish the listing (needs --confirm)")
    g.add_argument("--payload", metavar="DRAFT", help="print the ProductInput JSON, no network")
    g.add_argument("--taxonomy", nargs="+", metavar="WORD", help="search Whatnot's category taxonomy")
    g.add_argument("--attributes", metavar="TAXONOMY_ID",
                   help="list attribute ids/options (condition, brand) for a category")
    g.add_argument("--shipping-profiles", action="store_true", help="list shipping profiles")
    g.add_argument("--setup-check", action="store_true", help="check token + config")
    ap.add_argument("--confirm", action="store_true", help="actually perform the live write")
    ap.add_argument("--crosslist", action="store_true",
                    help="allow publishing a qty-1 item that is also live on eBay")
    args = ap.parse_args()

    try:
        if args.setup_check:
            _setup_check()
        elif args.taxonomy:
            for tid, path in search_taxonomy(" ".join(args.taxonomy)):
                print(f"{tid:>6}  {path}")
        elif args.attributes:
            for a in get_product_attributes(args.attributes):
                opts = ", ".join(a.get("options") or [])
                req = " (required)" if a.get("required") else ""
                print(f"{a.get('id')}  {a.get('key')} — {a.get('name')}{req}"
                      + (f"\n      options: {opts}" if opts else ""))
        elif args.shipping_profiles:
            for p in get_shipping_profiles():
                print(f"{p.get('id')}  {p.get('name')}  ({p.get('weight')} {p.get('weightUnit')})")
        elif args.validate:
            issues = validate_draft_for_whatnot(Path(args.validate))
            print("[OK] Whatnot-ready" if not issues else
                  "[X] not Whatnot-ready:\n  - " + "\n  - ".join(issues))
            sys.exit(1 if issues else 0)
        elif args.payload:
            import json
            d = parse_draft(_resolve_draft_path(args.payload))
            print(json.dumps(build_product_input(d, ["<photo urls at sync time>"]), indent=2))
        elif args.sync or args.list_target:
            target = Path(args.sync or args.list_target)
            r = sync_to_whatnot(target)
            print(f"[OK] {r.operation} Whatnot product {r.product_id} on {r.environment} "
                  f"(listing {r.listing_id}, unpublished, {r.media_count} photos sent)")
            if args.list_target:
                _print_publish(publish_to_whatnot(target, confirm=args.confirm,
                                                  crosslist=args.crosslist))
        elif args.publish:
            _print_publish(publish_to_whatnot(Path(args.publish), confirm=args.confirm,
                                              crosslist=args.crosslist))
        elif args.end:
            r = end_on_whatnot(Path(args.end), confirm=args.confirm)
            if r.dry_run:
                print(f"[DRY RUN] would unpublish Whatnot listing {r.listing_id} ({r.title}). "
                      f"Re-run with --confirm.")
            else:
                print(f"[ENDED] Whatnot listing {r.listing_id} unpublished.")
    except (ValueError, ConfigError, WhatnotAPIError, FileNotFoundError) as e:
        print(f"[X] {e}")
        sys.exit(1)


def _print_publish(r: WhatnotPublishResult) -> None:
    if r.status_before == "PUBLISHED" and not r.dry_run:
        print(f"[i] Already LIVE on Whatnot. listing {r.listing_id}")
    elif r.dry_run:
        print("[DRY RUN] Nothing published on Whatnot. This WOULD go live:")
        print(f"  listing: {r.listing_id}\n  title:   {r.title}\n  price:   ${r.price}")
        print("\n  To actually publish: re-run with --confirm")
    else:
        print(f"[LIVE] Whatnot listing {r.listing_id} is published.")
    if r.listing_url:
        print(f"  {r.listing_url}")


if __name__ == "__main__":
    _cli()
