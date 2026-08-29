"""
Function 6b — LIST / EDIT (multi-variation "CHOICE" listings via inventory item groups).

Companion to list_edit.py. Reads a GROUP draft (`draft_group.md`,
`listing_kind: multi_variation`), uploads each variation's photos to EPS,
creates/updates one InventoryItem per variation SKU, ties them together in an
inventoryItemGroup (varies_by → a variation aspect, e.g. MPN), creates an
UNPUBLISHED offer per SKU, and — only on an explicit, --confirm'd publish —
calls publishOfferByInventoryItemGroup to put ONE multi-variation listing live.

================================================================
PUBLISHING IS EXPLICIT, MANUAL, AND CONFIRMATION-GATED (same firewall as list_edit).
================================================================
--sync NEVER publishes. It creates inventory items + an item group + UNPUBLISHED
offers and stops. Publishing is a separate command and requires --confirm.

Why a separate module: the single-item path (list_edit) builds ONE inventory
item + ONE offer and PUTs them. Variation listings need N items + a group object
+ publish-by-group, a different shape — but they reuse list_edit's builders
(_body_to_html, _build_weight_lb, aspects, policy/location resolution, EPS upload)
so titles/descriptions/aspects render identically.

Variation facts this encodes (verified live against the eBay Metadata API):
  • The category MUST allow variations (Sell Metadata getListingStructurePolicies
    → variationsSupported). Antiques>Silverplate>Flatware (20099) = False;
    Kitchen&Home>Other Flatware (261680) = True.
  • varies_by must be a VARIATION-ENABLED aspect for the category. "Type" is not;
    MPN / Item Length / Material / Color / Finish are. This draft varies by MPN
    (free text → unique piece names, no collisions).

----- CLI -----

    python list_edit_group.py --validate <group-draft>            # no creds needed
    python list_edit_group.py --sync     <group-draft>            # create items+group+UNPUBLISHED offers
    python list_edit_group.py --publish  <group-draft>            # DRY RUN (shows what would go live)
    python list_edit_group.py --publish  <group-draft> --confirm  # actually publishes the group
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from draft_io import parse_draft, update_meta  # noqa: E402
import list_edit as le  # noqa: E402
from ebay_client import (  # noqa: E402
    EbayCredentials, EbayAPIError, EbayAuthError, load_credentials, api_send,
)

MARKETPLACE = le.DEFAULT_MARKETPLACE
CURRENCY = le.CURRENCY
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tiff", ".bmp"}


# ---------------------------------------------------------------------------
# Parse / validate
# ---------------------------------------------------------------------------

def _resolve_group_path(target: str | Path) -> Path:
    p = Path(target)
    if p.is_dir():
        cand = p / "draft_group.md"
        if not cand.exists():
            raise FileNotFoundError(f"no draft_group.md in {p}")
        return cand
    if not p.exists():
        raise FileNotFoundError(f"group draft not found: {p}")
    return p


def _variations(draft) -> list[dict]:
    return list(draft.frontmatter.get("variations") or [])


def _variation_photo_paths(shoot_dir: Path, var: dict) -> list[Path]:
    out: list[Path] = []
    for rel in var.get("photos") or []:
        out.append((shoot_dir / str(rel)).resolve())
    return out


def validate_group(group_path: Path) -> list[str]:
    """Return a list of blocking issues (empty = ready to sync)."""
    draft = parse_draft(group_path)
    fm = draft.frontmatter
    issues: list[str] = []

    if str(fm.get("listing_kind") or "") != "multi_variation":
        issues.append("listing_kind must be 'multi_variation'")
    title = str(draft.get("title") or "").strip()
    if not title:
        issues.append("title: missing")
    elif len(title) > 80:
        issues.append(f"title: {len(title)} chars (max 80)")
    if not str(draft.get("category_id") or "").strip():
        issues.append("category_id: missing (must be a variations-enabled category)")

    # eBay REJECTS Best Offer on any SKU in an inventory item group (error
    # 25737) — see the NOTE in _variation_offer. _variation_offer never emits
    # bestOfferTerms, so a best_offer.enabled left in a group draft is not a
    # money-path risk (it can't leak into the payload), but it is silently
    # ignored, which reads as a bug to whoever set it expecting Best Offer to
    # be live. Flag it here so the draft gets cleaned up instead.
    if draft.get("best_offer.enabled"):
        issues.append(
            "best_offer.enabled is set but eBay does not allow Best Offer on "
            "variation/grouped listings (API error 25737 on any SKU in an "
            "inventory item group) — remove best_offer from this draft; it "
            "will otherwise be silently dropped rather than applied.")

    varies_by = str(fm.get("varies_by") or "").strip()
    if not varies_by:
        issues.append("varies_by: missing (the variation aspect name, e.g. MPN)")

    vkey = _varies_key(varies_by)
    vars_ = _variations(draft)
    if not vars_:
        issues.append("variations: none listed")
    shoot_dir = group_path.parent
    seen_val: set[str] = set()
    seen_sku: set[str] = set()
    for i, v in enumerate(vars_):
        tag = f"variation[{i}]"
        sku = str(v.get("sku") or "").strip()
        if not sku:
            issues.append(f"{tag}: sku missing")
        elif sku in seen_sku:
            issues.append(f"{tag}: duplicate sku {sku!r}")
        else:
            seen_sku.add(sku)
        val = str(v.get(vkey) or "").strip()
        if not val:
            issues.append(f"{tag} ({sku or '?'}): missing {vkey!r} (the varies_by value)")
        elif val in seen_val:
            issues.append(f"{tag}: duplicate {varies_by} value {val!r} — variation values must be unique")
        else:
            seen_val.add(val)
        if le._to_decimal_str(v.get("price")) is None:
            issues.append(f"{tag} ({sku or '?'}): price missing/nonnumeric")
        photos = _variation_photo_paths(shoot_dir, v)
        if not photos:
            issues.append(f"{tag} ({sku or '?'}): no photos listed")
        for ph in photos:
            if not ph.exists():
                issues.append(f"{tag}: photo missing on disk: {ph.name}")
            elif ph.suffix.lower() not in _IMAGE_EXTS:
                issues.append(f"{tag}: not an image: {ph.name}")
    return issues


def _varies_key(varies_by: str) -> str:
    """The frontmatter key holding each variation's varies_by value.

    We accept an explicit lowercased alias (e.g. 'MPN' -> 'mpn') so the draft
    reads naturally; fall back to the aspect name itself.
    """
    return varies_by.strip().lower().replace(" ", "_")


# ---------------------------------------------------------------------------
# Builders (reuse list_edit for shared bits)
# ---------------------------------------------------------------------------

# Everything in the draft body AT/AFTER this sentinel line is INTERNAL (assessment,
# notes) and must NEVER reach the buyer-facing listing description.
_BUYER_BODY_SENTINEL = "<!-- END BUYER DESCRIPTION -->"


def _buyer_body(draft) -> str:
    """The buyer-facing portion of the draft body (everything before the sentinel)."""
    body = draft.body or ""
    idx = body.find(_BUYER_BODY_SENTINEL)
    return body[:idx].rstrip() if idx != -1 else body


def _shared_aspects(draft) -> dict[str, list[str]]:
    return le._build_aspects(draft)


def _variation_inventory_item(draft, var: dict, varies_by: str,
                              image_urls: list[str]) -> dict:
    aspects = dict(_shared_aspects(draft))
    vkey = _varies_key(varies_by)
    aspects[varies_by] = [str(var.get(vkey)).strip()]
    # optional per-piece Item Length (non-varying spec, nice for buyers/search)
    ilen = str(var.get("item_length") or "").strip()
    if ilen:
        aspects.setdefault("Item Length", [ilen])

    item: dict = {
        "availability": {"shipToLocationAvailability": {"quantity": int(var.get("quantity") or 1)}},
        "condition": str(draft.get("condition")),
        "product": {
            # Grouped items show the GROUP title; keep the per-item title = group
            # title (clean, <=80) rather than an appended piece name that truncates
            # mid-word. eBay requires non-varying data consistent across variants.
            "title": str(draft.get("title")),
            "description": le._body_to_html(_buyer_body(draft)),
            "imageUrls": image_urls,
            "aspects": aspects,
        },
    }
    cond_desc = str(draft.get("condition_description") or "").strip()
    note = str(var.get("condition_note") or "").strip()
    full_cd = (cond_desc + (" " + note if note else "")).strip()
    if full_cd and item["condition"] != "NEW":
        item["conditionDescription"] = full_cd[:1000]

    weight = le._build_weight_lb(draft)
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
        # packageType is REQUIRED for CALCULATED shipping. The group path only
        # ever ran on free flat-rate policies (tubes, Magic Rose), so this was
        # missing until the Flair NOS lot moved onto the BUYER-PAID calculated
        # policy; without it publish fails 25002 / err:216314 "Please provide a
        # valid Shipping Package type". Same helper the single-item path uses.
        pkg["packageType"] = le._package_type(draft)
        item["packageWeightAndSize"] = pkg
    return item


def _item_group_body(draft, varies_by: str, variant_skus: list[str],
                     var_values: list[str], group_image_urls: list[str]) -> dict:
    return {
        "title": str(draft.get("title")),
        "description": le._body_to_html(_buyer_body(draft)),
        "imageUrls": group_image_urls,
        "aspects": _shared_aspects(draft),
        "variantSKUs": variant_skus,
        "variesBy": {
            "aspectsImageVariesBy": [varies_by],   # images swap per variation
            "specifications": [{"name": varies_by, "values": var_values}],
        },
    }


def _variation_offer(draft, var: dict, category_id: str, location_key: str,
                     policies: dict) -> dict:
    price = le._to_decimal_str(var.get("price"))
    offer: dict = {
        "sku": str(var.get("sku")),
        "marketplaceId": MARKETPLACE,
        "format": "FIXED_PRICE",
        "availableQuantity": int(var.get("quantity") or 1),
        "categoryId": str(category_id),
        "listingDescription": le._body_to_html(_buyer_body(draft)),
        "merchantLocationKey": location_key,
        "listingPolicies": {
            "fulfillmentPolicyId": policies["fulfillment"],
            "paymentPolicyId": policies["payment"],
            "returnPolicyId": policies["return"],
        },
        "pricingSummary": {"price": {"value": price, "currency": CURRENCY}},
    }
    # NOTE: eBay REJECTS Best Offer on any SKU in an inventory item group
    # (API error 25737). Variation/multi-SKU listings cannot offer Best Offer at
    # all — so, unlike the single-item path, we never set bestOfferTerms here.
    return offer


# ---------------------------------------------------------------------------
# Sync (create items + group + UNPUBLISHED offers) — never publishes
# ---------------------------------------------------------------------------

def sync_group(group_path: Path, creds: Optional[EbayCredentials] = None,
               dry_run: bool = False) -> dict:
    creds = creds or load_credentials()
    if not dry_run and not creds.has_user:
        raise EbayAuthError("Sync needs user-context OAuth. Run `python list_edit.py --setup-check`.")

    group_path = _resolve_group_path(group_path)
    issues = validate_group(group_path)
    if issues:
        raise ValueError("group draft is not sync-ready:\n  - " + "\n  - ".join(issues))

    draft = parse_draft(group_path)
    fm = draft.frontmatter
    varies_by = str(fm.get("varies_by")).strip()
    vkey = _varies_key(varies_by)
    category_id = str(draft.get("category_id")).strip()
    group_key = str(draft.get("meta.group_id") or group_path.parent.name)
    shoot_dir = group_path.parent

    if not dry_run:
        policies, location_key = le._resolve_policies_and_location(creds)
    else:
        policies, location_key = {"fulfillment": "<F>", "payment": "<P>", "return": "<R>"}, "<LOC>"

    # Per-draft shipping override: if the draft names a fulfillment policy (e.g. a
    # buyer-paid Calculated policy), use it instead of the account default. This is
    # how a group listing opts into buyer-pays-shipping.
    fpid = str(draft.get("shipping.fulfillment_policy_id") or "").strip()
    if fpid:
        policies = {**policies, "fulfillment": fpid}
        print(f"  [shipping] using fulfillment policy {fpid} (from draft)")

    variant_skus: list[str] = []
    var_values: list[str] = []
    group_images: list[str] = []
    built = {"items": [], "offers": [], "group": None}

    # Optional explicit group hero image(s) for the DEFAULT gallery, decoupled from
    # any single variation. Without this, the group default falls back to the first
    # variation's photos (which would make that variation duplicate the hero).
    hero_rel = draft.frontmatter.get("hero_photos") or []
    if hero_rel:
        hero_paths = [(shoot_dir / str(r)).resolve() for r in hero_rel]
        if dry_run:
            group_images = [f"EPS<{p.name}>" for p in hero_paths]
        else:
            group_images = le.upload_photos_to_eps(hero_paths, creds=creds)

    for v in _variations(draft):
        sku = str(v.get("sku"))
        paths = _variation_photo_paths(shoot_dir, v)
        if dry_run:
            urls = [f"EPS<{p.name}>" for p in paths]
        else:
            urls = le.upload_photos_to_eps(paths, creds=creds)
        if not group_images:
            group_images = list(urls)   # fallback: group hero = first variation's images

        item_body = _variation_inventory_item(draft, v, varies_by, urls)
        built["items"].append({sku: item_body})
        if not dry_run:
            api_send("PUT", f"/sell/inventory/v1/inventory_item/{sku}", item_body, creds=creds)

        variant_skus.append(sku)
        var_values.append(str(v.get(vkey)).strip())

    group_body = _item_group_body(draft, varies_by, variant_skus, var_values, group_images)
    built["group"] = {group_key: group_body}
    if not dry_run:
        api_send("PUT", f"/sell/inventory/v1/inventory_item_group/{group_key}", group_body, creds=creds)

    # Offers per SKU (unpublished). Update if one already exists for the SKU.
    for v in _variations(draft):
        sku = str(v.get("sku"))
        offer_body = _variation_offer(draft, v, category_id, location_key, policies)
        built["offers"].append({sku: offer_body})
        if dry_run:
            continue
        offer_id = le._find_offer_id_for_sku(sku, creds)
        if offer_id:
            api_send("PUT", f"/sell/inventory/v1/offer/{offer_id}", offer_body, creds=creds)
        else:
            api_send("POST", "/sell/inventory/v1/offer", offer_body, creds=creds)

    if not dry_run:
        update_meta(group_path, {
            "ebay_inventory_item_group_key": group_key,
            "last_synced": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })

    built["group_key"] = group_key
    built["variant_skus"] = variant_skus
    return built


def publish_group(group_path: Path, creds: Optional[EbayCredentials] = None,
                  confirm: bool = False) -> dict:
    """Publish the whole variation listing live. Requires --confirm."""
    creds = creds or load_credentials()
    group_path = _resolve_group_path(group_path)
    draft = parse_draft(group_path)
    group_key = str(draft.get("meta.ebay_inventory_item_group_key")
                    or draft.get("meta.group_id") or group_path.parent.name)
    if not confirm:
        return {"dry_run": True, "group_key": group_key,
                "would_call": "POST /sell/inventory/v1/offer/publish_by_inventory_item_group",
                "body": {"inventoryItemGroupKey": group_key, "marketplaceId": MARKETPLACE}}
    if not creds.has_user:
        raise EbayAuthError("Publish needs user-context OAuth.")
    resp = api_send("POST", "/sell/inventory/v1/offer/publish_by_inventory_item_group",
                    {"inventoryItemGroupKey": group_key, "marketplaceId": MARKETPLACE}, creds=creds)
    listing_id = str(resp.get("listingId") or "")
    if listing_id:
        update_meta(group_path, {
            "ebay_listing_id": listing_id,
            "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
    return {"published": True, "listing_id": listing_id, "response": resp}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Multi-variation (CHOICE) eBay listing from a group draft.")
    ap.add_argument("--validate", metavar="GROUP", help="Check a group draft (no creds)")
    ap.add_argument("--dry-run", metavar="GROUP", dest="dry_run", help="Build + print all API bodies, no calls")
    ap.add_argument("--sync", metavar="GROUP", help="Create items + group + UNPUBLISHED offers")
    ap.add_argument("--publish", metavar="GROUP", help="Publish the group live (needs --confirm)")
    ap.add_argument("--confirm", action="store_true", help="Actually publish (with --publish)")
    args = ap.parse_args()

    try:
        if args.validate:
            issues = validate_group(_resolve_group_path(args.validate))
            if issues:
                print("NOT sync-ready:")
                for m in issues:
                    print("  -", m)
                sys.exit(1)
            print("[OK] group draft is sync-ready.")
        elif args.dry_run:
            import json
            built = sync_group(_resolve_group_path(args.dry_run), dry_run=True)
            print(json.dumps(built, indent=2, default=str))
        elif args.sync:
            built = sync_group(_resolve_group_path(args.sync))
            print(f"[OK] synced group '{built['group_key']}' with "
                  f"{len(built['variant_skus'])} variations (UNPUBLISHED): {', '.join(built['variant_skus'])}")
            print("  Review, then: python list_edit_group.py --publish <group> --confirm")
        elif args.publish:
            res = publish_group(_resolve_group_path(args.publish), confirm=args.confirm)
            if res.get("dry_run"):
                print("DRY RUN — would publish group:", res["group_key"])
                print("  ", res["would_call"], res["body"])
                print("  Re-run with --confirm to go live.")
            else:
                print(f"[LIVE] published listing {res.get('listing_id')}")
        else:
            ap.print_help()
    except (ValueError, FileNotFoundError, EbayAuthError, EbayAPIError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
