"""
Function 6 — LIST / EDIT (sync local DRAFT file → eBay DRAFT listing).

Reads a `draft.md`, uploads its photos to eBay Picture Services (EPS),
creates/updates an InventoryItem + an UNPUBLISHED Offer, and writes the
eBay IDs back into the draft. The listing ends in eBay's DRAFT state;
the user reviews and publishes manually in Seller Hub.

================================================================
PUBLISHING IS EXPLICIT, MANUAL, AND CONFIRMATION-GATED.
================================================================
--sync NEVER publishes. It creates an UNPUBLISHED offer (a draft) and
stops. Nothing in the build/sync path — not a config flag, env var, or
automated chain — can make a listing go live.

Publishing is a SEPARATE, deliberate command:

    python list_edit.py --publish <shoot-dir>            # DRY RUN (shows what would go live)
    python list_edit.py --publish <shoot-dir> --confirm  # actually publishes
    python list_edit.py --list    <shoot-dir> --confirm  # sync THEN publish (post review-gate)

Without --confirm it is a dry run. `publish_offer()` is the ONLY place
this module calls the publishOffer endpoint, it runs only on a draft you
already synced (has an ebay_offer_id), and it requires --confirm. It is
never invoked by --sync and never triggered automatically.

The firewall's intent — no ACCIDENTAL or AUTOMATIC publication — is
preserved: nothing in the IDENTIFY→DRAFT pipeline publishes, and every
publish path still requires the explicit --confirm guard. What changed
(v3 review-gate): publishing is no longer categorically forbidden to the
agent. After DRAFT, the REVIEW phase (prompts/review.md) presents a
review card and STOPS; only an explicit human approval at that gate lets
the agent run `--list <dir> --confirm` (one step: sync then publish).

----- Why the eBay Sell API (vs the Chrome stand-in) -----

The Chrome stand-in (prompts/list_edit_chrome.md) drives the seller UI
and hits two environment walls for photos: file_upload is sandboxed to
session-shared files, and browsers are granted read-only OS-automation
tier (can't type into the native file dialog). This API path bypasses the
UI entirely — photos POST to EPS server-side, the description is one HTTP
field (no rich-text editor), and everything is headless and repeatable in
any environment, including a scheduled run.

----- One-time setup (see lib/SETUP_EBAY_API.md) -----

Needs in config.yaml under `ebay:`  app_id, cert_id, dev_id, redirect_uri,
user_refresh_token, plus account-specific:  merchant_location_key,
fulfillment_policy_id, payment_policy_id, return_policy_id.
`python list_edit.py --setup-check` verifies them and lists your account's
policy IDs / locations to paste in.

----- CLI -----

    python list_edit.py --validate <shoot-dir|draft.md>   # no creds needed
    python list_edit.py --preflight <shoot-dir|draft.md>  # check/auto-fix condition+shipping vs category
    python list_edit.py --setup-check                      # verify creds + policies
    python list_edit.py --sync <shoot-dir|draft.md>        # create/update eBay DRAFT (runs preflight)
    python list_edit.py --list <shoot-dir|draft.md> --confirm  # sync + publish (post review-gate)
    python list_edit.py --offers                           # query ALL offers on the account
    python list_edit.py --withdraw-offer <id> --confirm    # end a live offer (keeps it, UNPUBLISHED)
    python list_edit.py --delete-offer <id> --confirm      # delete an offer (ends listing if live)
    python list_edit.py --delete-item <sku> --confirm      # delete inventory item + ALL its offers
    python list_edit.py --check                             # legacy stub-status report
"""

from __future__ import annotations

import re
import sys
import time
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
    delete_inventory_item,
    delete_offer,
    get_allowed_condition_ids,
    get_category_suggestions,
    get_fulfillment_policies,
    get_inventory_locations,
    get_offer,
    get_offers_for_sku,
    get_payment_policies,
    get_return_policies,
    get_user_access_token,
    iter_inventory_items,
    load_credentials,
    upload_site_hosted_picture,
    withdraw_offer,
)


# No AUTOMATIC/ACCIDENTAL publish: --sync never publishes, the pipeline
# never publishes, and every publish path requires the explicit --confirm
# guard. Publishing is reachable by the agent only after a human approves
# the REVIEW gate (prompts/review.md) — it is no longer categorically refused.
FIREWALL_NO_AUTO_PUBLISH = True
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
    # Optional: a Media Mail fulfillment policy used for media items (books,
    # magazines, comics, music, movies). Not required — falls back to default.
    policies["fulfillment_media"] = _ebay_extra("fulfillment_policy_id_media")
    location = _ebay_extra("merchant_location_key")
    for k, v in policies.items():
        if k == "fulfillment_media":
            continue  # optional
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
# Pre-publish preflight — validate condition + shipping against the category
# ---------------------------------------------------------------------------

# eBay condition enum <-> numeric conditionId (the metadata API speaks ids).
_COND_ENUM_TO_ID = {
    "NEW": 1000, "LIKE_NEW": 2750, "NEW_OTHER": 1500, "NEW_WITH_DEFECTS": 1750,
    "MANUFACTURER_REFURBISHED": 2000, "CERTIFIED_REFURBISHED": 2000,
    "EXCELLENT_REFURBISHED": 2010, "VERY_GOOD_REFURBISHED": 2020,
    "GOOD_REFURBISHED": 2030, "SELLER_REFURBISHED": 2500,
    "USED_EXCELLENT": 3000, "USED_VERY_GOOD": 4000, "USED_GOOD": 5000,
    "USED_ACCEPTABLE": 6000, "FOR_PARTS_OR_NOT_WORKING": 7000,
}
_COND_ID_TO_ENUM = {
    1000: "NEW", 1500: "NEW_OTHER", 1750: "NEW_WITH_DEFECTS", 2750: "LIKE_NEW",
    2000: "CERTIFIED_REFURBISHED", 2010: "EXCELLENT_REFURBISHED",
    2020: "VERY_GOOD_REFURBISHED", 2030: "GOOD_REFURBISHED", 2500: "SELLER_REFURBISHED",
    3000: "USED_EXCELLENT", 4000: "USED_VERY_GOOD", 5000: "USED_GOOD",
    6000: "USED_ACCEPTABLE", 7000: "FOR_PARTS_OR_NOT_WORKING",
}
_COND_FAMILIES = (
    [1000, 1500, 1750, 2750],          # new-ish
    [2000, 2010, 2020, 2030, 2500],    # refurbished
    [3000, 4000, 5000, 6000],          # used grades
    [7000],                            # for parts
)


def _remap_condition_for_category(enum: str, allowed_ids: set[int]) -> tuple[str, Optional[str]]:
    """Return (condition_enum, change_reason). reason is None if unchanged.

    Picks the closest accepted condition in the SAME family (used->used,
    new->new). Raises if the category accepts nothing in that family (a real
    mismatch the user must resolve — e.g. listing a used item in a new-only
    category).
    """
    cid = _COND_ENUM_TO_ID.get(enum)
    if cid is None or not allowed_ids or cid in allowed_ids:
        return enum, None  # unknown enum, no metadata, or already valid
    family = next((f for f in _COND_FAMILIES if cid in f), [])
    candidates = [i for i in family if i in allowed_ids]
    if not candidates:
        allowed_names = ", ".join(_COND_ID_TO_ENUM.get(i, str(i)) for i in sorted(allowed_ids))
        raise EbayAPIError(
            0, f"condition {enum} is invalid for this category and no same-grade "
               f"alternative is accepted. Category accepts: {allowed_names}. "
               f"Fix the draft's condition or category_id.", None)
    # Prefer generic "Used" (3000) for used items; else the nearest accepted id.
    target = 3000 if (3000 in candidates and cid in _COND_FAMILIES[2]) else \
        min(candidates, key=lambda i: abs(i - cid))
    new_enum = _COND_ID_TO_ENUM[target]
    return new_enum, (f"condition {enum} not accepted by category "
                      f"(accepts {sorted(allowed_ids)}) -> remapped to {new_enum}")


def _set_draft_condition(draft_path: Path, new_enum: str) -> None:
    """Rewrite the top-level `condition:` line in the draft file."""
    text = draft_path.read_text(encoding="utf-8")
    new_text = re.sub(r'(?m)^(condition:\s*).*$', f'condition: "{new_enum}"', text, count=1)
    if new_text != text:
        draft_path.write_text(new_text, encoding="utf-8")


def _is_media_service(code: str) -> bool:
    """True if a shipping service code denotes USPS Media Mail."""
    c = (code or "").lower()
    return "media" in c  # USPSMedia / USPSMediaMail


def _resolve_shipping_policy(draft: Draft, policies: dict,
                             creds: EbayCredentials) -> tuple[str, list[str]]:
    """Choose the fulfillment policy for THIS item and validate it.

    Routing: if the draft's `primary_service` is Media Mail (DRAFT sets this
    for books/magazines/comics/music/movies) AND a media policy is configured,
    use it; otherwise use the default (USPS Ground) policy. Returns
    (chosen_fulfillment_id, messages).
    """
    default_fid = str(policies.get("fulfillment") or "")
    media_fid = str(policies.get("fulfillment_media") or "")
    want = str(draft.get("shipping.primary_service") or "").strip()
    msgs: list[str] = []

    if _is_media_service(want):
        if media_fid:
            chosen, label = media_fid, "Media Mail (media policy)"
        else:
            chosen, label = default_fid, "default ground (NO media policy configured)"
            msgs.append("shipping: item wants Media Mail but ebay.fulfillment_policy_id_media "
                        "is unset — using default ground policy. Add a Media Mail policy to save on media.")
    else:
        chosen, label = default_fid, "default ground policy"

    # Validate the chosen policy actually exists and ships.
    try:
        pols = get_fulfillment_policies(creds=creds)
        pol = next((p for p in pols if str(p.get("fulfillmentPolicyId")) == chosen), None)
        if not pol:
            msgs.append(f"shipping: chosen fulfillment_policy_id {chosen} not found on this account")
        else:
            offered = [s.get("shippingServiceCode")
                       for opt in (pol.get("shippingOptions") or [])
                       for s in (opt.get("shippingServices") or []) if s.get("shippingServiceCode")]
            if not offered:
                msgs.append(f"shipping: policy '{pol.get('name')}' offers no services — publish may fail")
            else:
                msgs.insert(0, f"shipping: using {label} -> '{pol.get('name')}' ({', '.join(offered)})")
    except (EbayAPIError, EbayAuthError) as e:
        msgs.append(f"shipping: could not verify fulfillment policy ({e})")
    return chosen, msgs


def _insurance_notes(draft: Draft) -> list[str]:
    """Remind to insure high-value items — only $100 is auto-included, and the
    eBay API can't set insurance (it's bought at label time via ShipCover)."""
    price = _to_decimal_str(draft.get("price"))
    try:
        if price and Decimal(price) > Decimal("100"):
            return [f"insurance: price ${price} > $100 — only $100 ships included; "
                    f"add ShipCover/added coverage when buying the label (Seller Hub -> "
                    f"Get shipping label -> Additional liability coverage)."]
    except InvalidOperation:
        pass
    return []


def preflight_listing(draft_path: Path, creds: Optional[EbayCredentials] = None,
                      apply: bool = True) -> list[str]:
    """Check (and, if apply=True, auto-correct) a draft against its eBay
    category BEFORE sync/publish: condition validity + shipping. Returns a
    list of human-readable messages. Safe to run repeatedly (idempotent)."""
    creds = creds or load_credentials()
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    category_id = _resolve_category_id(draft, creds)
    policies, _loc = _resolve_policies_and_location(creds)
    msgs: list[str] = [f"category: {category_id}"]

    # Condition vs category
    allowed, required = get_allowed_condition_ids(category_id, creds=creds)
    cur = str(draft.get("condition") or "")
    if not allowed:
        msgs.append(f"condition: {cur} (category condition metadata unavailable — left as-is)")
    else:
        new_enum, reason = _remap_condition_for_category(cur, allowed)
        if reason:
            msgs.append(reason)
            if apply:
                _set_draft_condition(draft_path, new_enum)
                draft.frontmatter["condition"] = new_enum
                notes = str(draft.get("meta.notes") or "")
                update_meta(draft_path, {"notes": (notes + f" | PREFLIGHT: {reason}").strip(" |")})
        else:
            msgs.append(f"condition: {cur} OK for category")

    # Shipping policy selection (media vs ground) + validation
    _chosen, ship = _resolve_shipping_policy(draft, policies, creds)
    msgs.extend(ship)
    # Insurance reminder for high-value items
    msgs.extend(_insurance_notes(draft))
    return msgs


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

    # 0.5) Preflight: make the condition valid for THIS category (auto-remap)
    #      and sanity-check shipping. Prevents the publish-time 25021
    #      "condition invalid for category" failure (and surfaces shipping
    #      mismatches) before any photo upload or offer write.
    allowed_conditions, _req = get_allowed_condition_ids(category_id, creds=creds)
    if allowed_conditions:
        new_enum, reason = _remap_condition_for_category(
            str(draft.get("condition") or ""), allowed_conditions)
        if reason:
            _set_draft_condition(draft_path, new_enum)
            draft.frontmatter["condition"] = new_enum
            notes = str(draft.get("meta.notes") or "")
            update_meta(draft_path, {"notes": (notes + f" | PREFLIGHT: {reason}").strip(" |")})
            print(f"  [preflight] {reason}")
    # Shipping: pick the right fulfillment policy (Media Mail for media items,
    # else default ground) and use it for this offer.
    chosen_fulfillment, ship_msgs = _resolve_shipping_policy(draft, policies, creds)
    policies = {**policies, "fulfillment": chosen_fulfillment}
    for _m in ship_msgs + _insurance_notes(draft):
        print(f"  [preflight] {_m}")

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
# PUBLISH — explicit, manual, confirmation-gated (the one publish path)
# ---------------------------------------------------------------------------

@dataclass
class PublishResult:
    dry_run: bool
    offer_id: str
    title: str
    price: str
    status_before: str
    listing_id: Optional[str] = None   # set only when actually published
    listing_url: Optional[str] = None


def publish_offer(draft_path: Path, creds: Optional[EbayCredentials] = None,
                  confirm: bool = False) -> PublishResult:
    """Publish a previously-synced offer to a LIVE eBay listing.

    Guarded: requires an ebay_offer_id (from --sync) AND confirm=True. With
    confirm=False it is a DRY RUN — it fetches the offer and reports what
    WOULD go live without calling publish. This is the only function that
    calls publishOffer; --sync never does.
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError("Publish needs user-context OAuth. Run `python list_edit.py --setup-check`.")
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    offer_id = str(draft.get("meta.ebay_offer_id") or "").strip()
    if not offer_id:
        raise ValueError("draft has no meta.ebay_offer_id — run `--sync` first to create the offer.")

    off = api_send("GET", f"/sell/inventory/v1/offer/{offer_id}", creds=creds)
    status = str(off.get("status") or "UNKNOWN")
    price = str(((off.get("pricingSummary") or {}).get("price") or {}).get("value") or "?")
    sku = str(off.get("sku") or draft.get("meta.ebay_inventory_sku") or "")
    title = str(draft.get("title") or "")
    if not title and sku:
        try:
            title = str((api_send("GET", f"/sell/inventory/v1/inventory_item/{sku}", creds=creds)
                         .get("product") or {}).get("title") or "")
        except EbayAPIError:
            pass

    if status == "PUBLISHED":
        lid = str((off.get("listing") or {}).get("listingId") or "")
        return PublishResult(dry_run=False, offer_id=offer_id, title=title, price=price,
                             status_before=status, listing_id=lid or None,
                             listing_url=(f"https://www.ebay.com/itm/{lid}" if lid else None))

    if not confirm:
        return PublishResult(dry_run=True, offer_id=offer_id, title=title,
                             price=price, status_before=status)

    # --- the single publish call (with transient-error retry) ---
    # Right after an offer is created, eBay sometimes 5xx's or rejects the
    # offer's condition (25021) while category validation propagates, then
    # accepts the identical request seconds later. Retry those transients.
    resp = None
    for attempt in range(4):
        try:
            resp = api_send("POST", f"/sell/inventory/v1/offer/{offer_id}/publish", {}, creds=creds)
            break
        except EbayAPIError as e:
            transient = e.status >= 500 or (e.status == 400 and "25021" in (e.body or ""))
            if transient and attempt < 3:
                time.sleep(2.0 * (attempt + 1)); continue
            raise
    listing_id = str(resp.get("listingId") or "")
    if listing_id:
        update_meta(draft_path, {
            "ebay_listing_id": listing_id,
            "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return PublishResult(dry_run=False, offer_id=offer_id, title=title, price=price,
                         status_before=status, listing_id=listing_id or None,
                         listing_url=(f"https://www.ebay.com/itm/{listing_id}" if listing_id else None))


# ---------------------------------------------------------------------------
# END — take a live listing down (withdraw), also confirmation-gated
# ---------------------------------------------------------------------------

@dataclass
class EndResult:
    dry_run: bool
    offer_id: str
    title: str
    status_before: str
    ended: bool = False
    listing_id: Optional[str] = None


def end_listing(draft_path: Path, creds: Optional[EbayCredentials] = None,
                confirm: bool = False) -> EndResult:
    """End (withdraw) a live listing. Dry run unless confirm=True.

    Withdraws the offer, which ends the public listing; the offer returns
    to UNPUBLISHED so it can be re-synced/re-published later. The inverse
    of publish — same guard (does nothing without --confirm).
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError("End needs user-context OAuth. Run `python list_edit.py --setup-check`.")
    draft_path = _resolve_draft_path(draft_path)
    draft = parse_draft(draft_path)
    offer_id = str(draft.get("meta.ebay_offer_id") or "").strip()
    if not offer_id:
        raise ValueError("draft has no meta.ebay_offer_id — nothing to end.")

    off = api_send("GET", f"/sell/inventory/v1/offer/{offer_id}", creds=creds)
    status = str(off.get("status") or "UNKNOWN")
    listing_id = str((off.get("listing") or {}).get("listingId") or "")
    title = str(draft.get("title") or "")

    if status != "PUBLISHED":
        return EndResult(dry_run=(not confirm), offer_id=offer_id, title=title,
                         status_before=status, ended=False, listing_id=listing_id or None)
    if not confirm:
        return EndResult(dry_run=True, offer_id=offer_id, title=title,
                         status_before=status, listing_id=listing_id or None)

    api_send("POST", f"/sell/inventory/v1/offer/{offer_id}/withdraw", {}, creds=creds)
    update_meta(draft_path, {
        "ebay_listing_id": "",
        "ended_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    return EndResult(dry_run=False, offer_id=offer_id, title=title,
                     status_before=status, ended=True, listing_id=listing_id or None)


# ---------------------------------------------------------------------------
# Account-level listing management — query / withdraw / delete by ID
# (works on ANY offer/SKU on the account, not just ones with a local draft)
# ---------------------------------------------------------------------------

def list_account_offers(creds: Optional[EbayCredentials] = None) -> list[dict]:
    """Enumerate every offer on the account (inventory items -> offers).

    Returns rows: sku, title, offer_id, status, listing_id, price, marketplace.
    """
    creds = creds or load_credentials()
    if not creds.has_user:
        raise EbayAuthError("Query needs user-context OAuth. Run `python list_edit.py --setup-check`.")
    rows: list[dict] = []
    for it in iter_inventory_items(creds=creds):
        sku = it.get("sku")
        title = str((it.get("product") or {}).get("title") or "")
        try:
            offers = get_offers_for_sku(sku, creds=creds)
        except EbayAPIError:
            offers = []
        if not offers:
            rows.append({"sku": sku, "title": title, "offer_id": None,
                         "status": "NO_OFFER", "listing_id": None,
                         "price": None, "marketplace": None})
        for off in offers:
            rows.append({
                "sku": sku, "title": title,
                "offer_id": off.get("offerId"),
                "status": off.get("status"),
                "listing_id": (off.get("listing") or {}).get("listingId"),
                "price": ((off.get("pricingSummary") or {}).get("price") or {}).get("value"),
                "marketplace": off.get("marketplaceId"),
            })
    return rows


@dataclass
class OfferActionResult:
    action: str               # "withdraw" | "delete-offer" | "delete-item"
    dry_run: bool
    done: bool
    target: str               # offer_id or sku
    status_before: Optional[str] = None
    title: Optional[str] = None
    listing_id: Optional[str] = None
    detail: str = ""


def withdraw_offer_by_id(offer_id: str, creds: Optional[EbayCredentials] = None,
                         confirm: bool = False) -> OfferActionResult:
    """Withdraw (end) a live offer by ID — keeps the offer (UNPUBLISHED)."""
    creds = creds or load_credentials()
    off = get_offer(offer_id, creds=creds)
    status = str(off.get("status") or "UNKNOWN")
    listing_id = str((off.get("listing") or {}).get("listingId") or "") or None
    if status != "PUBLISHED":
        return OfferActionResult("withdraw", not confirm, False, offer_id, status,
                                 detail="not live (nothing to withdraw)", listing_id=listing_id)
    if not confirm:
        return OfferActionResult("withdraw", True, False, offer_id, status, listing_id=listing_id)
    withdraw_offer(offer_id, creds=creds)
    return OfferActionResult("withdraw", False, True, offer_id, status,
                             detail="ended; offer is now UNPUBLISHED", listing_id=listing_id)


def delete_offer_by_id(offer_id: str, creds: Optional[EbayCredentials] = None,
                       confirm: bool = False) -> OfferActionResult:
    """Delete an offer by ID (permanent). If live, this also ends the listing."""
    creds = creds or load_credentials()
    off = get_offer(offer_id, creds=creds)
    status = str(off.get("status") or "UNKNOWN")
    listing_id = str((off.get("listing") or {}).get("listingId") or "") or None
    price = ((off.get("pricingSummary") or {}).get("price") or {}).get("value")
    if not confirm:
        return OfferActionResult("delete-offer", True, False, offer_id, status,
                                 detail=f"sku={off.get('sku')} price={price}", listing_id=listing_id)
    delete_offer(offer_id, creds=creds)
    return OfferActionResult("delete-offer", False, True, offer_id, status,
                             detail="offer deleted (inventory item/SKU kept)", listing_id=listing_id)


def delete_item_by_sku(sku: str, creds: Optional[EbayCredentials] = None,
                       confirm: bool = False) -> OfferActionResult:
    """Delete an inventory item (SKU) AND all its offers (permanent)."""
    creds = creds or load_credentials()
    try:
        offers = get_offers_for_sku(sku, creds=creds)
    except EbayAPIError:
        offers = []
    n_live = sum(1 for o in offers if str(o.get("status")) == "PUBLISHED")
    detail = f"{len(offers)} offer(s), {n_live} live — all will be removed"
    if not confirm:
        return OfferActionResult("delete-item", True, False, sku, detail=detail)
    delete_inventory_item(sku, creds=creds)
    return OfferActionResult("delete-item", False, True, sku,
                             detail=f"inventory item + {len(offers)} offer(s) deleted")


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
        "firewall_no_auto_publish": FIREWALL_NO_AUTO_PUBLISH,
        "publish_requires_confirm": True,
        "publish_requires_review_gate": True,
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
        description="ebaybiz — LIST/EDIT (Function 6): sync draft.md -> eBay DRAFT; publish only via --publish/--list --confirm (post review-gate).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--validate", metavar="TARGET", help="Validate a draft.md or shoot dir (no creds).")
    ap.add_argument("--preflight", metavar="TARGET", help="Check (and auto-correct) condition + shipping against the eBay category (needs creds).")
    ap.add_argument("--sync", metavar="TARGET", help="Create/update the eBay DRAFT (unpublished offer) from a draft.md or shoot dir.")
    ap.add_argument("--publish", metavar="TARGET", help="Publish a synced offer to a LIVE listing. DRY RUN unless --confirm is also given.")
    ap.add_argument("--list", metavar="TARGET", dest="list_target", help="Sync THEN publish in one step (post review-gate). DRY RUN unless --confirm is also given.")
    ap.add_argument("--end", metavar="TARGET", help="End (withdraw) a live listing from a draft. DRY RUN unless --confirm is also given.")
    ap.add_argument("--offers", action="store_true", help="Query ALL offers on the account (sku, offerId, status, listingId, price).")
    ap.add_argument("--withdraw-offer", metavar="OFFER_ID", help="Withdraw (end) a live offer by ID — keeps the offer. DRY RUN unless --confirm.")
    ap.add_argument("--delete-offer", metavar="OFFER_ID", help="Delete an offer by ID (permanent; ends listing if live; keeps SKU). DRY RUN unless --confirm.")
    ap.add_argument("--delete-item", metavar="SKU", help="Delete an inventory item (SKU) AND all its offers (permanent). DRY RUN unless --confirm.")
    ap.add_argument("--confirm", action="store_true", help="Required with --publish/--list/--end/--withdraw-offer/--delete-offer/--delete-item to actually act (otherwise dry runs).")
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
        if args.preflight:
            print(f"Preflight {args.preflight}:")
            for m in preflight_listing(Path(args.preflight)):
                print(f"  {m}")
            return
        if args.sync:
            res = create_or_update_listing(Path(args.sync))
            print(f"[OK] {res.operation} eBay DRAFT (not published).")
            print(f"  offer_id:  {res.offer_id}")
            print(f"  sku:       {res.inventory_sku}")
            print(f"  category:  {res.category_id}")
            print(f"  photos:    {len(res.photo_eps_urls)} uploaded to EPS")
            print(f"  status:    UNPUBLISHED (a draft; API offers don't appear in Seller Hub Drafts)")
            print(f"  to go live: python list_edit.py --publish {args.sync} --confirm")
            return
        if args.publish:
            res = publish_offer(Path(args.publish), confirm=args.confirm)
            if res.status_before == "PUBLISHED":
                print(f"[i] Already LIVE. listing {res.listing_id}")
                if res.listing_url: print(f"    {res.listing_url}")
            elif res.dry_run:
                print("[DRY RUN] Nothing published. This WOULD go live:")
                print(f"  offer:  {res.offer_id}")
                print(f"  title:  {res.title}")
                print(f"  price:  ${res.price}")
                print(f"  status: {res.status_before} -> would become PUBLISHED (a real, live listing)")
                print(f"\n  To actually publish: re-run with --confirm")
            else:
                print(f"[LIVE] Published offer {res.offer_id} -> listing {res.listing_id}")
                if res.listing_url: print(f"  {res.listing_url}")
                print("  This listing is now public and accepting buyers.")
            return
        if args.list_target:
            # One-step LIST = sync (create/update the offer) then publish it.
            # Same --confirm guard as --publish: without it, this syncs and then
            # shows a DRY RUN of what would go live. This is the path the agent
            # runs ONLY after a human approves the REVIEW card.
            sres = create_or_update_listing(Path(args.list_target))
            print(f"[OK] {sres.operation} eBay DRAFT (offer {sres.offer_id}, {len(sres.photo_eps_urls)} photos).")
            res = publish_offer(Path(args.list_target), confirm=args.confirm)
            if res.status_before == "PUBLISHED":
                print(f"[i] Already LIVE. listing {res.listing_id}")
                if res.listing_url: print(f"    {res.listing_url}")
            elif res.dry_run:
                print("[DRY RUN] Synced but NOT published. This WOULD go live:")
                print(f"  title:  {res.title}")
                print(f"  price:  ${res.price}")
                print(f"\n  To actually publish: re-run --list with --confirm")
            else:
                print(f"[LIVE] Published offer {res.offer_id} -> listing {res.listing_id}")
                if res.listing_url: print(f"  {res.listing_url}")
                print("  This listing is now public and accepting buyers.")
            return
        if args.end:
            res = end_listing(Path(args.end), confirm=args.confirm)
            if res.status_before != "PUBLISHED":
                print(f"[i] Nothing live to end (offer status: {res.status_before}).")
            elif res.dry_run:
                print("[DRY RUN] Nothing ended. This WOULD end a LIVE listing:")
                print(f"  offer:   {res.offer_id}")
                print(f"  listing: {res.listing_id}")
                print(f"  title:   {res.title}")
                print("\n  To actually end it: re-run with --confirm")
            else:
                print(f"[ENDED] Withdrew offer {res.offer_id} (listing {res.listing_id}) — no longer live.")
            return
        if args.offers:
            rows = list_account_offers()
            print(f"{len(rows)} offer(s) on the account:\n")
            print(f"  {'STATUS':12} {'OFFER_ID':16} {'LISTING_ID':14} {'PRICE':>8}  SKU / TITLE")
            for r in rows:
                price = f"${r['price']}" if r.get("price") else "-"
                print(f"  {str(r['status'] or '-'):12} {str(r['offer_id'] or '-'):16} "
                      f"{str(r['listing_id'] or '-'):14} {price:>8}  {r['sku']}  |  {r['title'][:48]}")
            return
        if args.withdraw_offer:
            res = withdraw_offer_by_id(args.withdraw_offer, confirm=args.confirm)
            if not res.done and res.status_before != "PUBLISHED":
                print(f"[i] Offer {res.target} is {res.status_before} — {res.detail}")
            elif res.dry_run:
                print("[DRY RUN] WOULD withdraw (end) this LIVE offer:")
                print(f"  offer:   {res.target}\n  listing: {res.listing_id}\n  status:  {res.status_before}")
                print("\n  To actually withdraw: re-run with --confirm")
            else:
                print(f"[ENDED] Withdrew offer {res.target} — {res.detail}")
            return
        if args.delete_offer:
            res = delete_offer_by_id(args.delete_offer, confirm=args.confirm)
            if res.dry_run:
                print("[DRY RUN] WOULD DELETE this offer (permanent):")
                print(f"  offer:   {res.target}\n  status:  {res.status_before}"
                      + (f" (LIVE — deleting ends the listing)" if res.status_before == "PUBLISHED" else ""))
                print(f"  {res.detail}")
                print("\n  To actually delete: re-run with --confirm")
            else:
                print(f"[DELETED] Offer {res.target} — {res.detail}")
            return
        if args.delete_item:
            res = delete_item_by_sku(args.delete_item, confirm=args.confirm)
            if res.dry_run:
                print("[DRY RUN] WOULD DELETE this inventory item AND its offers (permanent):")
                print(f"  sku: {res.target}\n  {res.detail}")
                print("\n  To actually delete: re-run with --confirm")
            else:
                print(f"[DELETED] Inventory item {res.target} — {res.detail}")
            return
        if args.check:
            s = stub_status()
            for k, v in s.items():
                print(f"{k}: {v}")
            return
        ap.print_help()
    except (EbayAuthError, EbayAPIError, ConfigError, ValueError, FileNotFoundError) as e:
        print(f"[X] {type(e).__name__}: {e}", file=sys.stderr)
        body = getattr(e, "body", None)
        if body:
            print(f"    eBay said: {body}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
