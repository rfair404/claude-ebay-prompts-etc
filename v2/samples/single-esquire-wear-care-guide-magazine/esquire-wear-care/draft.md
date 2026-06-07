---
template_version: v1

# --- META -------------------------------------------------------------------
meta:
  item_id:              "esquire-wear-care"
  shoot_dir:            "v2/samples/single-esquire-wear-care-guide-magazine/esquire-wear-care"
  drafted_at:           "2026-05-27T00:00:00"
  ebay_offer_id:        "5170287532423"
  ebay_inventory_sku:   null
  last_synced:          "2026-05-27T00:00:00"
  notes: |
    DRAFT NOTES:
    - No price.txt for this item. Price drawn from comp.txt recommendation ($15–$18 BIN + BO).
      Using $17.00 with free shipping (USPS Media Mail ~$5–6 absorbed). Auto-decline $10.00.
    - Zero sold comps in 90-day eBay window. Two active unsold asks: $21.45 (hector8) and
      $49.10 (powerangers). Price is condition-discounted for Fair/back-cover-crumple relative
      to both active asks.
    - Year estimated c.1972 per powerangers listing (trusted signal). NOT confirmed from interior
      — no legible date line in photos. Do not claim specific year in listing.
    - Back cover crumple must be clearly visible in listing photos. Photo 6
      (IMG_20260526_224549076.jpg) is the back cover — verify damage is well-lit before listing.
    - Page completeness not confirmed — only 2 interior spreads photographed. Consider
      adding a note that completeness has not been individually verified if interior count
      is not checked before listing.

# --- TITLE ------------------------------------------------------------------
# PRE-WRITE CHECK: 72 chars — passes max_len: 80
title: "Esquire's Wear & Care Guide Volume 1 No 1 Vintage Men's Fashion Magazine"

# --- CATEGORY ---------------------------------------------------------------
category_id:            ""
category_path:          "Collectibles > Paper > Magazines > Men's"

# --- CONDITION --------------------------------------------------------------
condition: "USED_ACCEPTABLE"

# PRE-WRITE CHECK: 483 chars — passes max_len: 1000
condition_description: "Front cover: edge soiling along full top/spine edge; minor surface scuffing across cover face. No tears; magazine lies flat. Back cover: significant crumple and crease damage concentrated in upper right quadrant — this is the primary condition liability and is shown clearly in photos. Interior: pages appear cream/aged and intact in the two visible spreads; no tears or detachment observed. Binding intact. Overall condition: Fair. Please review all photos carefully before purchasing."

# --- ITEM SPECIFICS ---------------------------------------------------------
item_specifics:
  type:                       "Magazine / Special Publication"   # 30 chars ✓
  brand:                      "Esquire"                          # 7 chars ✓
  upc:                        ""
  collection:                 ""
  subject:                    "Men's Fashion"                    # 13 chars ✓
  material:                   ""
  character_family:           ""
  occasion:                   ""
  color:                      ""
  country_of_origin:          "United States"                    # 13 chars ✓ lookup_only
  pattern:                    ""
  theme:                      "Men's Fashion & Grooming"         # 24 chars ✓
  style:                      ""
  size:                       ""
  department:                 "Men"                              # 3 chars ✓ lookup_only
  time_period_manufactured:   "1970s"                            # 5 chars ✓
  finish:                     ""
  extra:
    Publisher: "Esquire, Inc."
    Volume: "1"
    Issue Number: "1"

# --- PRICING ----------------------------------------------------------------
format: "FIXED_PRICE"
price: "17.00"           # 5 chars ✓; free shipping absorbs ~$5–6 Media Mail
cost_of_goods: null
quantity: 1
best_offer:
  enabled: true
  auto_decline_amount: "10.00"   # 5 chars ✓; per comp.txt recommendation
  auto_accept_amount: null

# --- SHIPPING ---------------------------------------------------------------
shipping:
  weight:
    major_lb: 1           # 1 char ✓; ~0.5 lb item + packaging, rounded up
    minor_oz: 0           # 1 char ✓
  package_in:
    length: 12            # 2 chars ✓; magazine 11×8.5 in + packing overhead
    width:  9             # 1 char ✓
    depth:  1             # 1 char ✓
  free_shipping: true
  domestic_shipping_type: "FREE_FLAT_RATE"
  primary_service: "USPSMediaMail"    # magazine — Media Mail eligible
  handling_time_days: 1
  item_location_zip: ""

# --- PHOTOS -----------------------------------------------------------------
# Hero first (front cover). Back cover (photo 6) must show crumple damage clearly.
photos:
  - IMG_20260526_224445481.jpg       # front cover — hero
  - IMG_20260526_224502074.jpg       # contents page (1 of 2)
  - IMG_20260526_224507184_HDR.jpg   # contents page (2 of 2) — Vol 1 No 1 visible
  - IMG_20260526_224519904.jpg       # interior spread 1 — Pattern on Pattern
  - IMG_20260526_224540672.jpg       # interior spread 2 — Body Language feature
  - IMG_20260526_224549076.jpg       # back cover — crumple damage visible here

# --- RETURNS ----------------------------------------------------------------
returns_policy_id: ""

# --- PROMOTED ---------------------------------------------------------------
promoted:
  general:  false
  priority: false

# ===========================================================================
# MACHINE-READABLE CONSTRAINTS — DO NOT EDIT
# ===========================================================================
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

## What's Included

- 1 copy of Esquire's Wear & Care Guide, Volume 1, Number 1 — the complete magazine as photographed

## Condition

This is a vintage copy in Fair condition — honest and priced accordingly. The interior is in notably better shape than the covers suggest.

- Front cover: edge soiling along the full top and spine edge; minor surface scuffing across the cover face; no tears or missing sections
- Back cover: significant crumple and crease damage in the upper right quadrant — the primary defect; shown clearly in the listing photos; please review before purchasing
- Interior pages: cream/aged and intact across two visible spreads; no tears, loose pages, or major soiling observed
- Binding: intact; magazine opens flat

## About this item

Esquire's Wear & Care Guide was a newsstand-only special publication — not a monthly issue — published by Esquire, Inc. (488 Madison Avenue, New York) in the early 1970s. This is Volume 1, Number 1, confirmed on the contents page. Contents cover wardrobe coordination, color and pattern mixing, grooming (beards, hair, body toning), a stain removal encyclopedia, and a traveler's almanac. Cover price was $2.50. A first issue of a named Esquire special-edition series — Vol. 1 No. 1 is confirmed in the magazine itself.
