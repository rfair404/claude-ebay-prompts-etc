---
template_version: v1

meta:
  item_id:              "parts-washer-3p5gal"
  shoot_dir:            "samples/parts-washer-3p5gal"
  drafted_at:           "2026-06-10T00:00:00Z"
  ebay_offer_id:        null
  ebay_inventory_sku: "016fec0d"
  last_synced:          null
  notes: >
    Title = INVESTIGATE claim #1 [74/80]. Condition FOR_PARTS_OR_NOT_WORKING
    (condition-rubric tie-break: electric pump never shown powered -> untested ->
    lower grade; passed up USED_ACCEPTABLE). Working price $35 = PRICE Recommended
    tier (provisional; no exact sold comp, Apify+Chrome unavailable). Best Offer
    gate: price ($35) == Recommended ($35), NOT above it -> Best Offer DISABLED,
    both amounts null. Weight: item ~15.5-20 lb (matches 35740-family spec 15.5 lb);
    packed 22 lb (rounded up). Dims packed ~20x17x12 in (item 17-3/8x14-1/4x8-3/4 +
    padding). Service UPSGround (>15 lb). Shipping set CALCULATED (buyer pays) not
    free: ~15-20 lb steel unit ships ~$18-28, would erase a $35 sale (see PRICE
    shipping flag + NEEDS_REVIEW). Photo order: hero set to 02 (clear interior+pump)
    because 01 is motion-blurred; 01 placed last. country_of_origin "China" canonical.
    No brand, working, or completeness claim — none defensible from photos.

title: "3.5 Gallon Benchtop Parts Washer w/ Electric Solvent Pump - Untested AS-IS"

category_id:            ""
category_path:          "Business & Industrial > Automotive Tools & Supplies > Shop Equipment & Supplies > Parts Washers"

condition: "FOR_PARTS_OR_NOT_WORKING"

condition_description: "Used. Sold as-is for parts or repair. The electric recirculating pump was NOT tested - it is not shown running and is not guaranteed to work. The steel tank is dirty inside and out and will need cleaning; the basin has sediment and debris. Red paint has scuffs and scratches throughout. No through-rust seen. The pump unit and its supply hose are present (sitting in the basin in photos). The lid, fusible link, flexible nozzle/spigot, and power cord/switch are NOT clearly shown and are not guaranteed present or intact. Ships empty - no solvent or cleaning fluid included. Please review all photos; condition is as pictured."

item_specifics:
  type:                       "Parts Washer"
  brand:                      "Unbranded"
  upc:                        ""
  collection:                 ""
  subject:                    ""
  material:                   "Steel"
  character_family:           ""
  occasion:                   ""
  color:                      "Red"
  country_of_origin:          "China"
  pattern:                    ""
  theme:                      ""
  style:                      ""
  size:                       ""
  department:                 ""
  time_period_manufactured:   ""
  finish:                     ""
  extra:
    Capacity: "3.5 Gallon"
    Power Source: "Electric (Corded)"
    Type of Tool: "Benchtop Parts Washer"

format: "FIXED_PRICE"
price: "35.00"
cost_of_goods: null
quantity: 1
best_offer:
  enabled: false
  auto_decline_amount: null
  auto_accept_amount: null

shipping:
  weight:
    major_lb: 22
    minor_oz: 0
  package_in:
    length: 20
    width:  17
    depth:  12
  free_shipping: false
  domestic_shipping_type: "CALCULATED"
  primary_service: "UPSGround"
  handling_time_days: 1
  item_location_zip: ""

photos:
  - "02-IMG_0854.jpeg"
  - "04-IMG_0856.jpeg"
  - "03-IMG_0855.jpeg"
  - "01-IMG_0853.jpeg"

returns_policy_id: ""

promoted:
  general:  false
  priority: false

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

Used 3.5-gallon benchtop parts washer with an electric recirculating solvent pump — a compact shop unit for cleaning small automotive and mechanical parts. Sold as-is, untested, for parts or repair.

## What's Included

- One red steel 3.5-gallon benchtop parts washer tank
- Electric recirculating solvent pump unit with supply hose (present, untested)
- Spec plate reads: "3.5 Gallon Parts Washer, Made in China, No. 002798"
- Ships EMPTY — no solvent or cleaning fluid is included
- Lid, fusible link, flexible nozzle/spigot, and power cord are NOT confirmed in photos — do not assume included

## Condition

This is a used, working-shop tool sold honestly and as-is — it will need cleaning and the pump has not been tested.

- Electric pump is UNTESTED — not shown running, no working guarantee; sold for parts or repair.
- Tank is dirty inside and out, with sediment and debris in the basin — needs cleaning.
- Red enamel paint is scuffed and scratched throughout; no through-rust seen.
- Pump unit and its supply hose are present (shown sitting in the basin).
- Lid, fusible link, flexible nozzle, and power cord/switch are not clearly pictured and are not guaranteed present or intact.

## About this item

A common, no-frills import-style benchtop parts washer (the red-steel, fusible-link, recirculating-pump design). A low-cost candidate for a hobbyist or shop willing to clean it up and test/replace the pump. Please review every photo — what you see is what ships.
