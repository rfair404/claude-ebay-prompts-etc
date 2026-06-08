"""
Function 6 — LIST / EDIT (sync local DRAFT file → eBay DRAFT listing).

Reads a `draft.md`, uploads its photos to eBay Picture Services (EPS),
creates/updates an InventoryItem + an UNPUBLISHED Offer, and writes the
eBay IDs back into the draft. The listing ends in eBay's DRAFT state;
the user reviews and publishes manually in Seller Hub.

================================================================
THIS MODULE NEVER PUBLISHES AN EBAY LISTING. THERE IS NO CODE PATH IN
THIS FILE THAT CALLS THE eBay PUBLISH ENDPOINT
(POST /sell/inventory/v1/offer/{offerId}/publish OR ANY EQUIVALENT).
================================================================

The no-publish firewall is enforced by ABSENCE: no function here, and no
helper in ebay_client.api_send's callers, targets the publish path.
Adding one is a deliberate user-initiated refactor of this file. No
config flag, CLI argument, env var, or chat instruction can cause
publication. See PLAN.md "No-publish firewall".

----- Why the eBay Sell API (vs the Chrome stand-in) -----

The Chrome stand-in (v3/prompts/list_edit_chrome.md) drives the seller UI
and hits two environment walls for photos: file_upload is sandboxed to
session-shared files, and browsers are granted read-only OS-automation
tier (can't type into the native file dialog). This API path bypasses the
UI entirely — photos POST to EPS server-side, the description is one HTTP
field (no rich-text editor), and everything is headless and repeatable in
any environment, including a scheduled run.

----- One-time setup (see v2/lib/SETUP_EBAY_API.md) -----

Needs in config.yaml under `ebay:`  app_id, cert_id, dev_id, redirect_uri,
user_refresh_token, plus account-specific:  merchant_location_key,
fulfillment_policy_id, payment_policy_id, return_policy_id.
`python list_edit.py --setup-check` verifies them and lists your account's
policy IDs / locations to paste in.

----- CLI -----

    python list_edit.py --validate <shoot-dir|draft.md>   # no creds needed
    python list_edit.py --setup-check                      # verify creds + policies
    python list_edit.py --sync <shoot-dir|draft.md>        # create/update eBay DRAFT
    python list_edit.py --check                             # legacy stub-status report
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

from config import ConfigError, config_path, load_config
from draft_io import Draft, parse_draft, resolve_photo_paths, update_meta
from ebay_client import (
    DEFAULT_MARKETPLACE,
    EbayAPIError,
    EbayAuthError,
    EbayCredentials,
    api_send,
    get_category_suggestions,
    get_fulfillment_policies,
    get_inventory_locations,
    get_payment_policies,
    get_return_policies,
    get_user_access_token,
    load_credentials,
    upload_site_hosted_picture,
)


FIREWALL_NO_PUBLISH = True
STUB_MESSAGE = "LIST/EDIT is stubbed — awaiting eBay developer key. See PLAN.md Function 6."

CURRENCY = "USD"
_VALID_CONDITIONS = {
    "NEW", "LIKE_NEW", "NEW_OTHER", "NEW_WITH_DEFECTS",
    "MANUFACTURER_REFURBISHED", "CERTIFIED_REFURBISHED", "EXCELLENT_REFURBISHED",
    "VERY_GOOD_REFURBISHED", "GOOD_REFURBISHED", "SELLER_REFURBISHED",
    "USED_EXCELLENT", "USED_VERY_GOOD", "USED_GOOD", "USED_ACCEPTABLE",
    "FOR_PARTS_OR_NOT_WORKING",
}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tiff", ".bmp"}

# draft item_specifics key -> eBay aspect display name
_ASPECT_NAMES = {
    "brand": "Brand", "type": "Type", "material": "Material", "color": "Color",
    "country_of_origin": "Country/Region of Manufacture", "pattern": "Pattern",
    "theme": "Theme", "style": "Style", "size": "Size", "subject": "Subject",
    "collection": "Collection", "character_family": "Character Family",
    "occasion": "Occasion", "time_period_manufactured": "Time Period Manufactured",
    "finish": "Finish", "department": "Department",
}


@dataclass
class SyncResult:
    offer_id: str
    inventory_sku: str
    operation: str            # "created" or "updated"
    photo_eps_urls: list[str]
    category_id: str
    eb_seller_hub_url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_draft_path(target: str | Path) -> Path:
    """Accept a draft.md or a shoot directory; return the draft.md path."""
    p = Path(target)
    if p.is_dir():
        p = p / "draft.md"
    if not p.exists():
        raise FileNotFoundError(f"No draft.md found at {p}")
    return p


def _sku_for(draft: Draft) -> str:
    # Reuse an already-synced SKU for idempotency; otherwise derive from the
    # shoot FOLDER name (unique per item), NOT meta.item_id — sibling drafts
    # in a batch often share a generic item_id and would collide on one SKU.
    existing = draft.get("meta.ebay_inventory_sku")
    if existing:
        return str(existing)
    basis = draft.path.parent.name or str(draft.get("meta.item_id") or "item")
    sku = re.sub(r"[^A-Za-z0-9_-]+", "-", f"ebaybiz-{basis}").strip("-")
    return sku[:50] or "ebaybiz-item"


def _to_decimal_str(val: object) -> Optional[str]:
    if val is None or val == "":
        return None
    try:
        d = Decimal(str(val))
    except (InvalidOperation, ValueError):
        return None
    return f"{d:.2f}"


def _ebay_extra(field: str) -> Optional[str]:
    """Read an account-specific eBay setting for the ACTIVE environment.

    Prefers ebay.<environment>.<field>; falls back to flat ebay.<field>.
    """
    section = (load_config().get("ebay") or {})
    env = section.get("environment") or "sandbox"
    env_section = section.get(env) or {}
    v = env_section.get(field)
    if v is None:
        v = section.get(field)
    return str(v) if v else None


# ---------------------------------------------------------------------------
# Pre-flight validation (NO credentials needed)
# ---------------------------------------------------------------------------

def validate_draft_for_sync(draft_path: Path) -> list[str]:
    """Return a list of issues that would block a sync. Empty list = ready.

    Runs entirely offline. Catches the problems the user can fix in the
    local file before any eBay call — including the V1 empty-description
    failure mode.
    """
    issues: list[str] = []
    try:
        draft = parse_draft(draft_path)
    except Exception as e:  # parse failure is itself the blocking issue
        return [f"draft parse error: {e}"]

    title = str(draft.get("title") or "")
    if not title:
        issues.append("title: required, empty")
    elif len(title) > 80:
        issues.append(f"title: {len(title)}/80 — exceeds 80 chars")

    price = _to_decimal_str(draft.get("price"))
    if price is None:
        issues.append("price: required, missing or non-numeric")
    elif Decimal(price) <= 0:
        issues.append(f"price: {price} — must be positive")

    qty = draft.get("quantity")
    if not isinstance(qty, int) or qty < 1:
        issues.append(f"quantity: {qty!r} — must be an integer >= 1")

    cond = str(draft.get("condition") or "")
    if not cond:
        issues.append("condition: required, empty")
    elif cond not in _VALID_CONDITIONS:
        issues.append(f"condition: {cond!r} — not a recognized eBay condition enum")

    if not str(draft.get("item_specifics.type") or "").strip():
        issues.append("item_specifics.type: required, empty")

    if len(draft.body.strip()) < 20:
        issues.append("description body: empty/too short (V1 missing-description guard)")

    photos = resolve_photo_paths(draft)
    if not photos:
        issues.append("photos: none listed")
    for ph in photos:
        if not ph.exists():
            issues.append(f"photo missing on disk: {ph.name}")
        elif ph.suffix.lower() not in _IMAGE_EXTS:
            issues.append(f"photo not an image type: {ph.name}")

    # numeric structural caps
    for dotpath, cap in (("shipping.weight.major_lb", 3), ("shipping.weight.minor_oz", 2),
                         ("shipping.package_in.length", 5), ("shipping.package_in.width", 5),
                         ("shipping.package_in.depth", 5)):
        v = draft.get(dotpath)
        if v not in (None, "") and len(str(v)) > cap:
            issues.append(f"{dotpath}: {v!r} exceeds maxLen {cap}")

    return issues


# ---------------------------------------------------------------------------
# Photo upload
# ---------------------------------------------------------------------------

def upload_photos_to_eps(photo_paths: list[Path],
                         creds: Optional[EbayCredentials] = None) -> list[str]:
    """Upload each photo to EPS (in order); return EPS URLs in the same order."""
    creds = creds or load_credentials()
    urls: list[str] = []
    for ph in photo_paths:
        data = Path(ph).read_bytes()
        urls.append(upload_site_hosted_picture(data, picture_name=Path(ph).stem, creds=creds))
    return urls


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def _build_aspects(draft: Draft) -> dict[str, list[str]]:
    aspects: dict[str, list[str]] = {}
    spec = draft.frontmatter.get("item_specifics") or {}
    for key, name in _ASPECT_NAMES.items():
        val = spec.get(key)
        if isinstance(val, str) and val.strip():
            aspects[name] = [val.strip()]
    for k, v in (spec.get("extra") or {}).items():
        if isinstance(v, str) and v.strip():
            aspects[str(k)] = [v.strip()]
    return aspects


def _build_weight_lb(draft: Draft) -> Optional[float]:
    lb = draft.get("shipping.weight.major_lb")
    oz = draft.get("shipping.weight.minor_oz") or 0
    try:
        total = float(lb) + float(oz) / 16.0
    except (TypeError, ValueError):
        return None
    return round(total, 2) if total > 0 else None


def _build_inventory_item(draft: Draft, image_urls: list[str]) -> dict:
    item: dict = {
        "availability": {"shipToLocationAvailability": {"quantity": int(draft.get("quantity") or 1)}},
        "condition": str(draft.get("condition")),
        "product": {
            "title": str(draft.get("title")),
            "description": draft.body,
            "imageUrls": image_urls,
            "aspects": _build_aspects(draft),
        },
    }
    cond_desc = str(draft.get("condition_description") or "").strip()
    if cond_desc and item["condition"] != "NEW":
        item["conditionDescription"] = cond_desc[:1000]
    upc = str(draft.get("item_specifics.upc") or "").strip()
    if upc:
        item["product"]["upc"] = [upc]

    weight = _build_weight_lb(draft)
    length = draft.get("shipping.package_in.length")
    width = draft.get("shipping.package_in.width")
    depth = draft.get("shipping.package_in.depth")
    pkg: dict = {}
    if weight:
        pkg["weight"] = {"value": weight, "unit": "POUND"}
    if all(x not in (None, "", 0) for x in (length, width, depth)):
        pkg["dimensions"] = {"length": float(length), "width": float(width),
                             "height": float(depth), "unit": "INCH"}
    if pkg:
        item["packageWeightAndSize"] = pkg
    return item


def _build_offer(draft: Draft, sku: str, category_id: str,
                 location_key: str, policies: dict) -> dict:
    price = _to_decimal_str(draft.get("price"))
    offer: dict = {
        "sku": sku,
        "marketplaceId": DEFAULT_MARKETPLACE,
        "format": "FIXED_PRICE",
        "availableQuantity": int(draft.get("quantity") or 1),
        "categoryId": str(category_id),
        "listingDescription": draft.body,
        "pricingSummary": {"price": {"value": price, "currency": CURRENCY}},
        "merchantLocationKey": location_key,
        "listingPolicies": {
            "fulfillmentPolicyId": policies["fulfillment"],
            "paymentPolicyId": policies["payment"],
            "returnPolicyId": policies["return"],
        },
    }
    if draft.get("best_offer.enabled"):
        terms: dict = {"bestOfferEnabled": True}
        decline = _to_decimal_str(draft.get("best_offer.auto_decline_amount"))
        accept = _to_decimal_str(draft.get("best_offer.auto_accept_amount"))
        if decline:
            terms["autoDeclinePrice"] = {"value": decline, "currency": CURRENCY}
        if accept:
            terms["autoAcceptPrice"] = {"value": accept, "currency": CURRENCY}
        offer["listingPolicies"]["bestOfferTerms"] = terms
    return offer


# ---------------------------------------------------------------------------
# Account prerequisites
# ---------------------------------------------------------------------------

def _resolve_policies_and_location(creds: EbayCredentials) -> tuple[dict, str]:
    """Read the required policy IDs + merchant location from config.

    These are account-specific and captured once (see --setup-check).
    Raises EbayAuthError with guidance if any is missing.
    """
    missing = []
    policies = {
        "fulfillment": _ebay_extra("fulfillment_policy_id"),
        "payment": _ebay_extra("payment_policy_id"),
        "return": _ebay_extra("return_policy_id"),
    }
    location = _ebay_extra("merchant_location_key")
    for k, v in policies.items():
        if not v:
            missing.append(f"ebay.{k}_policy_id")
    if not location:
        missing.append("ebay.merchant_location_key")
    if missing:
        raise EbayAuthError(
            "Missing account-specific settings in config.yaml: "
            + ", ".join(missing)
            + "\n  Run `python list_edit.py --setup-check` to list your account's "
              "policy IDs and locations, then paste them under `ebay:` in config."
        )
    return policies, location


def _find_offer_id_for_sku(sku: str, creds: EbayCredentials) -> Optional[str]:
    """Return the existing API offerId for this SKU, or None.

    Authoritative source for update-vs-create — do NOT trust
    meta.ebay_offer_id, which the Chrome stand-in may have set to a UI
    draftId (a different ID space than the Sell API offerId).
    """
    try:
        d = api_send("GET", f"/sell/inventory/v1/offer?sku={urllib.parse.quote(sku)}", creds=creds)
    except EbayAPIError as e:
        if e.status == 404:
            return None
        raise
    offers = d.get("offers") or []
    return str(offers[0]["offerId"]) if offers and offers[0].get("offerId") else None


def _resolve_category_id(draft: Draft, creds: EbayCredentials) -> str:
    explicit = str(draft.get("category_id") or "").strip()
    if explicit:
        return explicit
    title = str(draft.get("title") or "")
    data = get_category_suggestions(title, creds=creds)
    suggestions = data.get("categorySuggestions") or []
    if not suggestions:
        raise EbayAPIError(0, f"No category suggestion for title {title!r}; set category_id in draft.md", None)
    return str(suggestions[0]["category"]["categoryId"])


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def create_or_update_listing(draft_path: Path,
                             creds: Optional[EbayCredentials] = None) -> SyncResult:
    """Sync a draft.md into eBay as an UNPUBLISHED (draft) offer.

    CREATE flow when frontmatter has no ebay_offer_id; EDIT flow otherwise.
    The offer is never published — that is a manual user action in Seller Hub.
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError("Sync needs user-context OAuth. Run `python list_edit.py --setup-check`.")

    draft_path = _resolve_draft_path(draft_path)
    issues = validate_draft_for_sync(draft_path)
    if issues:
        raise ValueError("draft is not sync-ready:\n  - " + "\n  - ".join(issues))

    draft = parse_draft(draft_path)
    sku = _sku_for(draft)
    policies, location_key = _resolve_policies_and_location(creds)
    category_id = _resolve_category_id(draft, creds)

    # 1) photos -> EPS
    image_urls = upload_photos_to_eps(resolve_photo_paths(draft), creds=creds)

    # 2) InventoryItem (create-or-replace; PUT is idempotent on the SKU)
    item_body = _build_inventory_item(draft, image_urls)
    api_send("PUT", f"/sell/inventory/v1/inventory_item/{sku}", item_body, creds=creds)

    # 3) Offer — update if one already exists for this SKU, else create.
    #    Look it up by SKU (idempotent); do not trust meta.ebay_offer_id,
    #    which may be a Chrome UI draftId rather than an API offerId.
    offer_body = _build_offer(draft, sku, category_id, location_key, policies)
    offer_id = _find_offer_id_for_sku(sku, creds)
    if offer_id:
        api_send("PUT", f"/sell/inventory/v1/offer/{offer_id}", offer_body, creds=creds)
        operation = "updated"
    else:
        resp = api_send("POST", "/sell/inventory/v1/offer", offer_body, creds=creds)
        offer_id = str(resp.get("offerId") or "")
        if not offer_id:
            raise EbayAPIError(0, f"createOffer returned no offerId: {resp}", None)
        operation = "created"

    # 4) write the eBay IDs back into the draft frontmatter
    update_meta(draft_path, {
        "ebay_offer_id": offer_id,
        "ebay_inventory_sku": sku,
        "last_synced": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })

    hub = "https://www.ebay.com/sh/lst/drafts"
    return SyncResult(offer_id=offer_id, inventory_sku=sku, operation=operation,
                      photo_eps_urls=image_urls, category_id=category_id,
                      eb_seller_hub_url=hub)


# ---------------------------------------------------------------------------
# Status / setup-check
# ---------------------------------------------------------------------------

def stub_status() -> dict:
    try:
        creds = load_credentials()
        err = None
    except Exception as e:  # pylint: disable=broad-except
        creds, err = None, str(e)
    return {
        "module": "list_edit",
        "implementation_status": "implemented (needs credentials + sandbox test)",
        "firewall_no_publish": FIREWALL_NO_PUBLISH,
        "publish_function_exists": False,
        "credentials": {
            "load_error": err,
            "app_id_set": bool(creds and creds.app_id),
            "cert_id_set": bool(creds and creds.cert_id),
            "dev_id_set": bool(creds and creds.dev_id),
            "user_refresh_token_set": bool(creds and creds.user_refresh_token),
        },
        "ready": bool(creds and creds.has_user and creds.dev_id),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_setup_check() -> None:
    creds = load_credentials()
    print(f"environment: {creds.environment}   config: {config_path()}")
    print(f"  app_id:             {'set' if creds.app_id else '(missing)'}")
    print(f"  cert_id:            {'set' if creds.cert_id else '(missing)'}")
    print(f"  dev_id:             {'set' if creds.dev_id else '(missing — REQUIRED for EPS photo upload)'}")
    print(f"  user_refresh_token: {'set' if creds.user_refresh_token else '(missing — REQUIRED for writes)'}")
    for f in ("merchant_location_key", "fulfillment_policy_id", "payment_policy_id", "return_policy_id"):
        print(f"  {f}: {_ebay_extra(f) or '(missing)'}")
    if not creds.has_user:
        print("\n[--] Cannot list account policies until app + user credentials are set.")
        return
    print("\nYour account's business policies + locations (paste IDs into config.yaml `ebay:`):")
    try:
        for label, items, idkey, namekey in (
            ("fulfillment_policy_id", get_fulfillment_policies(creds=creds), "fulfillmentPolicyId", "name"),
            ("payment_policy_id", get_payment_policies(creds=creds), "paymentPolicyId", "name"),
            ("return_policy_id", get_return_policies(creds=creds), "returnPolicyId", "name"),
        ):
            print(f"  {label}:")
            for it in items:
                print(f"    - {it.get(idkey)}   ({it.get(namekey)})")
        print("  merchant_location_key:")
        for loc in get_inventory_locations(creds=creds):
            print(f"    - {loc.get('merchantLocationKey')}   ({loc.get('name')})")
    except (EbayAuthError, EbayAPIError) as e:
        print(f"  [X] {e}")


def _cli() -> None:
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass

    ap = argparse.ArgumentParser(
        description="ebaybiz — LIST/EDIT (Function 6): sync draft.md -> eBay DRAFT (never publishes).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--validate", metavar="TARGET", help="Validate a draft.md or shoot dir (no creds).")
    ap.add_argument("--sync", metavar="TARGET", help="Create/update the eBay DRAFT from a draft.md or shoot dir.")
    ap.add_argument("--setup-check", action="store_true", help="Verify creds and list account policy IDs.")
    ap.add_argument("--check", action="store_true", help="Print module/credential status.")
    args = ap.parse_args()

    try:
        if args.validate:
            path = _resolve_draft_path(args.validate)
            issues = validate_draft_for_sync(path)
            if not issues:
                print(f"[OK] {path} is sync-ready.")
            else:
                print(f"[X] {path} has {len(issues)} issue(s):")
                for i in issues:
                    print(f"  - {i}")
                sys.exit(1)
            return
        if args.setup_check:
            _print_setup_check()
            return
        if args.sync:
            res = create_or_update_listing(Path(args.sync))
            print(f"[OK] {res.operation} eBay DRAFT (not published).")
            print(f"  offer_id:  {res.offer_id}")
            print(f"  sku:       {res.inventory_sku}")
            print(f"  category:  {res.category_id}")
            print(f"  photos:    {len(res.photo_eps_urls)} uploaded to EPS")
            print(f"  review at: {res.eb_seller_hub_url}  (publish manually in Seller Hub)")
            return
        if args.check:
            s = stub_status()
            for k, v in s.items():
                print(f"{k}: {v}")
            return
        ap.print_help()
    except (EbayAuthError, EbayAPIError, ConfigError, ValueError, FileNotFoundError) as e:
        print(f"[X] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
