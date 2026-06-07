---
# ===========================================================================
# ebaybiz V2 — Default Listing Template (v1)
# ===========================================================================
#
# Every per-item DRAFT (Function 5 output) is a copy of this file, with the
# blank fields filled in. The YAML frontmatter (between the ---) carries
# every structured eBay field; the markdown body below carries the listing
# description (the only genuinely free-text section eBay wants).
#
# Field constraints (maxLen, required, etc.) are taken directly from the
# eBay Add Item form capture at `ebay-fields-all.txt` (2026-05-26).
# Inline comments tag each field with its constraint. A machine-readable
# `_field_constraints:` block at the bottom of the frontmatter consolidates
# the same constraints so `lib/list_edit.py:validate_draft_for_sync()` can
# enforce them before any eBay API call.
#
# Do NOT exceed any maxLen. Validator will refuse to sync.
# Do NOT delete `_field_constraints` from a per-item draft — the validator
# uses it. (template_version: v1 also lets older drafts stay readable
# after the template evolves.)
# ===========================================================================

template_version: v1

# --- META (not pushed to eBay; for local bookkeeping only) ---------------
meta:
  item_id:              ""        # local stable ID for the item
  shoot_dir:            ""        # path to the shoot directory the draft was built from
  drafted_at:           ""        # ISO 8601 timestamp the DRAFT was rendered
  ebay_offer_id:        null      # filled by LIST/EDIT on first sync
  ebay_inventory_sku:   null      # filled by LIST/EDIT on first sync
  last_synced:          null      # ISO 8601 timestamp of last LIST/EDIT push
  notes:                ""        # free-form local notes; never pushed

# --- TITLE (eBay form field [8], maxLen=80, REQUIRED) ---------------------
title: ""

# --- CATEGORY ------------------------------------------------------------
# Either set category_id directly (from `python ebay_client.py --category-suggestions "..."`)
# or leave blank and let eBay auto-suggest from the title at LIST time.
category_id:            ""
category_path:          ""        # human-readable breadcrumb, for the user's reference

# --- CONDITION -----------------------------------------------------------
# One of CONDITION_ENUM from lib/ebay_schema.py:
#   NEW | LIKE_NEW | NEW_OTHER | NEW_WITH_DEFECTS |
#   MANUFACTURER_REFURBISHED | CERTIFIED_REFURBISHED |
#   EXCELLENT_REFURBISHED | VERY_GOOD_REFURBISHED | GOOD_REFURBISHED |
#   SELLER_REFURBISHED |
#   USED_EXCELLENT | USED_VERY_GOOD | USED_GOOD | USED_ACCEPTABLE |
#   FOR_PARTS_OR_NOT_WORKING
# Note: condition is selected in eBay's pre-list dialog, not on the main
# form — but the Sell API accepts it as a field on the inventory item.
condition: ""

# Free-text condition disclosure shown to buyers.
# eBay form field [26], maxLen=1000.
condition_description: ""

# --- ITEM SPECIFICS ------------------------------------------------------
# All values are strings. Leave blank ("") if not confidently known.
# Never invent — INVESTIGATE's defensible-claims section is the authority.
# Most fields are tag-select widgets backed by hidden inputs at maxLen=65;
# UPC is a visible free-text input at maxLen=20.
item_specifics:
  type:                       ""    # REQUIRED in form section, maxLen=65
  brand:                      ""    # maxLen=65; "Unbranded" is a valid value
  upc:                        ""    # maxLen=20, free-text
  collection:                 ""    # maxLen=65
  subject:                    ""    # maxLen=65
  material:                   ""    # maxLen=65
  character_family:           ""    # maxLen=65
  occasion:                   ""    # maxLen=65
  color:                      ""    # maxLen=65
  country_of_origin:          ""    # maxLen=65, lookup-only (controlled vocabulary)
  pattern:                    ""    # maxLen=65
  theme:                      ""    # maxLen=65
  style:                      ""    # maxLen=65
  size:                       ""    # maxLen=65
  department:                 ""    # maxLen=65, lookup-only (controlled vocabulary)
  time_period_manufactured:   ""    # maxLen=65 (e.g. "1970s")
  finish:                     ""    # maxLen=65
  # Additional category-specific specifics go under `extra:` as a flat
  # name→value map. The validator does not enforce maxLen on extras since
  # eBay returns the per-category limits at runtime via the Taxonomy API.
  # When LIST/EDIT runs, it fetches the live aspect schema for category_id
  # and validates extras against that.
  extra: {}

# --- PRICING -------------------------------------------------------------
# Numeric fields are kept as strings to preserve leading zeros / decimal
# precision and to avoid YAML coercing them. Validator parses as Decimal.
format: "FIXED_PRICE"          # FIXED_PRICE | AUCTION
price: ""                      # REQUIRED, maxLen=13 (e.g. "129.00")
cost_of_goods: null            # private to seller; maxLen=13; null when unknown
quantity: 1                    # maxLen=5; defaults to 1
best_offer:
  enabled: true                # eBay form field [43] — default ON per V1 strategy
  auto_decline_amount: null    # maxLen=13; null = no auto-decline floor
  auto_accept_amount: null     # maxLen=13; null = no auto-accept ceiling

# --- SHIPPING ------------------------------------------------------------
# Weight: pounds (maxLen=3) + ounces (maxLen=2). eBay form fields [55][56].
# Dimensions: inches, all maxLen=5. eBay form fields [57][58][59].
# Round UP. Use packed dims (including box + padding), not bare item dims.
shipping:
  weight:
    major_lb: 0                # maxLen=3
    minor_oz: 0                # maxLen=2
  package_in:
    length: 0                  # maxLen=5
    width:  0                  # maxLen=5
    depth:  0                  # maxLen=5
  free_shipping: true          # eBay form field [61] — default ON
  # CALCULATED | FLAT_RATE | FREE_FLAT_RATE
  # When free_shipping is true this is FREE_FLAT_RATE.
  domestic_shipping_type: "FREE_FLAT_RATE"
  # Primary service code (eBay Fulfillment policy). Common defaults:
  #   USPSGroundAdvantage    — default for items <15 lb
  #   USPSMediaMail          — books, catalogs, magazines, sheet music
  #   UPSGround              — heavy / oversized
  primary_service: "USPSGroundAdvantage"
  handling_time_days: 1        # business days from sale to shipment
  item_location_zip: ""        # filled from seller profile

# --- PHOTOS --------------------------------------------------------------
# Ordered list of relative paths from this file's directory. First item is
# the hero/listing thumbnail. LIST/EDIT's EPS uploader processes them in
# order and attaches the returned EPS URLs to the InventoryItem in the
# same order. eBay accepts up to 24 photos.
photos: []

# --- RETURNS -------------------------------------------------------------
# Returns are usually set via account-level eBay policy (linked by ID).
# The policy ID is resolved by LIST/EDIT from the seller's saved policies;
# leave blank to use the seller's default.
returns_policy_id: ""

# --- PROMOTED LISTING ----------------------------------------------------
# eBay form fields [63][64]. Default OFF — promoted listings cost extra
# and are opt-in per item.
promoted:
  general:  false              # CPC-style sponsored
  priority: false              # premium placement

# ===========================================================================
# MACHINE-READABLE CONSTRAINTS — DO NOT EDIT in per-item drafts
# ===========================================================================
# The validator in lib/list_edit.py:validate_draft_for_sync() reads this
# block. Keys are dot-paths into the YAML frontmatter (same as above).
# Value flags:
#   required:    true  — non-empty string / non-null number required
#   max_len:     N     — string length must be <= N
#   numeric:     true  — value must parse as a positive number (Decimal-safe)
#   lookup_only: true  — eBay restricts to its controlled vocabulary;
#                        validator does not enforce this locally but
#                        LIST/EDIT will check against the live Taxonomy API
# Sourced from ebay-fields-all.txt (2026-05-26 capture). If eBay's form
# changes, recapture that file and bump template_version, not this map.
_field_constraints:
  title:                                    { required: true, max_len: 80 }
  quantity:                                 { required: true, max_len: 5,  numeric: true }
  price:                                    { required: true, max_len: 13, numeric: true }
  cost_of_goods:                            { max_len: 13, numeric: true }
  best_offer.auto_decline_amount:           { max_len: 13, numeric: true }
  best_offer.auto_accept_amount:            { max_len: 13, numeric: true }
  condition_description:                    { max_len: 1000 }
  item_specifics.type:                      { required: true, max_len: 65 }
  item_specifics.brand:                     { max_len: 65 }
  item_specifics.upc:                       { max_len: 20 }
  item_specifics.collection:                { max_len: 65 }
  item_specifics.subject:                   { max_len: 65 }
  item_specifics.material:                  { max_len: 65 }
  item_specifics.character_family:          { max_len: 65 }
  item_specifics.occasion:                  { max_len: 65 }
  item_specifics.color:                     { max_len: 65 }
  item_specifics.country_of_origin:         { max_len: 65, lookup_only: true }
  item_specifics.pattern:                   { max_len: 65 }
  item_specifics.theme:                     { max_len: 65 }
  item_specifics.style:                     { max_len: 65 }
  item_specifics.size:                      { max_len: 65 }
  item_specifics.department:                { max_len: 65, lookup_only: true }
  item_specifics.time_period_manufactured:  { max_len: 65 }
  item_specifics.finish:                    { max_len: 65 }
  shipping.weight.major_lb:                 { max_len: 3, numeric: true }
  shipping.weight.minor_oz:                 { max_len: 2, numeric: true }
  shipping.package_in.length:               { max_len: 5, numeric: true }
  shipping.package_in.width:                { max_len: 5, numeric: true }
  shipping.package_in.depth:                { max_len: 5, numeric: true }
---

# Description

Replace this entire markdown body with the listing description. Everything
below the closing `---` becomes eBay's `product.description` field
verbatim (the V1 missing-description failure mode is fixed by the API
treating this as one HTTP payload — no rich-text editor round-trip).

eBay does not enforce a hard maxLen on description at the HTML level;
the server-side limit is well above what a typical listing uses. Keep
paragraphs short for mobile readability.

## What's Included

- (bullet each included component, with counts where relevant)
- (distinguish original vs replacement parts)
- (list any paperwork / extras by name + date when visible)

## Condition

(Prose describing visible condition. Every defect surfaced in
INVESTIGATE's defensible-claims section goes here, framed honestly
without minimizing. Lead with a single warm sentence — e.g. "This is a
vintage piece with vintage character. No item is perfect — but this one
is still beautiful and fully functional." — then bullet specifics.)

- (exterior wear / finish / hardware)
- (interior / functional condition)
- (specific defects, described not minimized)
- (paperwork or extras condition, if any)

## About this item

(1–2 sentence closing collector hook — era, distinguishing feature,
why a collector or end-user would want this specific piece.)
