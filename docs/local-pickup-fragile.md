# Local pickup for ship-risky items — PLAN (not yet implemented)

Add a **local-pickup-primary** fulfillment path for items that are risky to
ship — fragile/breakable (stained glass, blown glass, porcelain, plaster),
heavy (oversized/freight/movers tiers), or just too bulky. Such items should
default to **local pickup**, **not offer normal shipping**, and keep a
**freight/contact-for-quote** fallback for the rare distant buyer.

This document is the plan only. No prompt, template, or library code is
changed in this PR — review and approve the approach here first.

## Goal (what the user asked for)

> Modify the list prompt to use eBay local pickup, and not offer shipping on
> an item that is easily breakable / fragile, or too big to ship.

Confirmed product decisions (2026-06-14):

1. **Fulfillment mode = pickup-primary + freight fallback.** Local pickup is
   the offered method; standard parcel shipping is *not* offered. A
   freight / "message me for a shipping quote" path stays available so a
   committed distant buyer isn't fully locked out.
2. **Decision gate = ask before drafting (attended runs).** When an item
   looks ship-risky, the pipeline asks the user to confirm pickup-primary vs
   normal shipping *before* DRAFT writes the shipping block. (Headless
   reconciliation below — this is the one open point against the existing
   gate contract.)
3. **Ship-risk trigger is broad:** weight **and** dimensions **and**
   fragility — anything genuinely risky to ship. Not just the heavy tiers.

## How shipping works today (baseline)

- **IDENTIFY** ([prompts/identify.md](../prompts/identify.md)) already
  estimates per-item **weight** (tiered: light <5 / medium 5–15 / heavy
  15–25 / oversized 25–50 / freight 50+ / requires-movers) and **dimensions**,
  and surfaces **material** in Type / Distinguishing marks. So the raw signals
  for a ship-risk call already exist — they just aren't synthesized into one.
- **DRAFT** ([prompts/draft.md](../prompts/draft.md), "shipping:") sets
  `free_shipping: true`, `domestic_shipping_type: FREE_FLAT_RATE`, and a
  `primary_service` via its **Service map** (Media Mail / Ground Advantage /
  UPS Ground; freight/movers → blank + flag). Shipping is always assumed.
- **Template** ([templates/listing-v1.md](../templates/listing-v1.md)) has a
  `shipping:` block (weight, dims, `free_shipping`, `domestic_shipping_type`,
  `primary_service`, `handling_time_days`, `item_location_zip`) with a
  `_field_constraints` map the validator enforces. **There is no
  local-pickup / fulfillment-mode concept anywhere.**
- **API path** ([lib/list_edit.py](../lib/list_edit.py)
  `_resolve_shipping_policy`) routes each item to an account-level
  **fulfillment policy ID** (default ground, or an optional Media Mail
  policy). Local pickup would be **a third fulfillment policy**.
- **Chrome stand-in** ([prompts/list_edit_chrome.md](../prompts/list_edit_chrome.md)
  step 6) confirms/sets free shipping + service in the eBay UI.

## Design

### 1. Define the ship-risk classification (one rule, computed in IDENTIFY)

Add a single explicit field to each IDENTIFY record, derived from signals
IDENTIFY already gathers — so the judgment is made once, with the photos in
hand, and threads downstream:

```
ship_risk: none | fragile | oversized | both
ship_risk_reason: <one line — what makes it risky>
```

An item is **ship-risky** when ANY of:

- **Fragile material / form:** stained or leaded glass, blown/pressed glass,
  porcelain / ceramic / pottery, plaster/chalkware, thin enamel, items with
  protruding brittle parts (lamp arms, finials, handles, spouts). Material is
  already read for brand attribution — reuse it.
- **Heavy:** weight tier is **oversized / freight / requires-movers**
  (≥25 lb), which DRAFT already half-handles by blanking the service.
- **Bulky (dimensional/girth):** large longest-side or length+girth even when
  light — large lampshades, framed art, mirrors, big ceramics. Add a simple
  dimensional threshold (e.g. longest side > ~30 in, or length+girth > ~100
  in) so big-but-light items qualify too.

`none` is the default; the field is only non-`none` when a real signal fires.
This keeps every existing (small, durable) item shipping exactly as today.

### 2. The fulfillment gate (attended runs ask; headless defaults)

Per the user's choice, in an **attended** run the pipeline **asks before
DRAFT writes the shipping block** whenever `ship_risk != none`:

> *"<item> looks <reason> — risky to ship. List as **local pickup
> (+ freight/quote fallback)**, or ship normally?"*

The answer sets the fulfillment mode (below). **Headless reconciliation
(open point — see end):** RUN.md's gate contract says REVIEW is the *only*
hard stop. In an unattended run there is no one to ask, so a ship-risk item
**defaults to pickup-primary + freight**, logs one line to `NEEDS_REVIEW.md`,
and the choice is surfaced again on the REVIEW card for the user to confirm
or flip. This keeps headless runs non-blocking while honoring "ask" in
attended runs.

### 3. Data model — new template fields

Extend the `shipping:` block in
[templates/listing-v1.md](../templates/listing-v1.md):

```yaml
shipping:
  fulfillment_mode: SHIP        # SHIP | LOCAL_PICKUP   (new; default SHIP)
  local_pickup:                 # only meaningful when fulfillment_mode == LOCAL_PICKUP
    pickup_only: true           # no parcel shipping offered
    freight_quote: true         # allow "message for freight quote" fallback
    location_hint: ""           # city/region shown to buyers (from seller profile)
  # ...existing weight / package_in / free_shipping / domestic_shipping_type /
  #    primary_service / handling_time_days / item_location_zip unchanged...
```

When `fulfillment_mode == LOCAL_PICKUP`:
- `free_shipping` → **false**, `domestic_shipping_type` → a new
  `LOCAL_PICKUP` value (or left blank + handled by policy), `primary_service`
  → blank.
- Weight/dims are still recorded (useful for a freight quote), just not used
  for a parcel service.

Add a `_field_constraints` entry validating `fulfillment_mode` against the
enum `{SHIP, LOCAL_PICKUP}` (extend the validator to support an `enum` flag,
or treat it as a `lookup_only` string).

### 4. eBay mechanics — account-level local-pickup fulfillment policy

eBay models local pickup as a **fulfillment policy** with pickup enabled and
(optionally) a freight service, *not* as a per-listing free-text field. Plan:

- Create one **"Local pickup (+ freight)" fulfillment policy** on the eBay
  account: `pickupDropOff`/local-pickup enabled, parcel services removed, and
  a **Freight** shipping service added for the quote fallback (or pickup-only
  with a description note if Freight isn't desired).
- Add `fulfillment_policy_id_local_pickup` to `lib/config.example.yaml` (under
  each store's `ebay:` block, alongside `fulfillment_policy_id` and
  `fulfillment_policy_id_media`).
- `lib/list_edit.py:_resolve_shipping_policy` gains a branch: when
  `draft.shipping.fulfillment_mode == LOCAL_PICKUP`, route to the local-pickup
  policy (with the same "policy exists / offers a service" validation it does
  today, and a clear message if the policy isn't configured).
- Ensure the **merchant location** used has a real pickup address (already
  required by `_resolve_policies_and_location`).

`SETUP_EBAY_API.md` gains a short section on creating this policy and pasting
its ID, mirroring the Media Mail policy instructions.

### 5. Prompt + doc changes (the actual edits, in a follow-up PR)

| File | Change |
|---|---|
| [prompts/identify.md](../prompts/identify.md) | Add the `ship_risk` + `ship_risk_reason` fields to the output format; add a short "ship-risk assessment" rule (material + weight tier + dimensional threshold). |
| [prompts/draft.md](../prompts/draft.md) | New "Fulfillment mode" rule: read `ship_risk`; run the attended gate / headless default; when LOCAL_PICKUP, set the new shipping fields, blank the parcel service, and add a **local-pickup + freight-quote** note to the description body. Update the Service map note. |
| [templates/listing-v1.md](../templates/listing-v1.md) | Add `fulfillment_mode` + `local_pickup` fields and the `_field_constraints` enum entry. |
| [prompts/review.md](../prompts/review.md) | Surface fulfillment mode on the decision card (so the user confirms pickup-primary at the REVIEW gate). |
| [prompts/list_edit_chrome.md](../prompts/list_edit_chrome.md) | Step 6: when LOCAL_PICKUP, choose eBay's local-pickup option instead of free shipping; don't set a parcel service. |
| [lib/list_edit.py](../lib/list_edit.py) | `_resolve_shipping_policy`: route LOCAL_PICKUP items to `fulfillment_policy_id_local_pickup`; validate it's configured. |
| [lib/config.example.yaml](../lib/config.example.yaml) | Add `fulfillment_policy_id_local_pickup`. |
| [lib/SETUP_EBAY_API.md](../lib/SETUP_EBAY_API.md) | Document creating the local-pickup (+ freight) policy. |
| [prompts/_shared.md](../prompts/_shared.md) + [RUN.md](../RUN.md) | Document the fulfillment gate (attended ask, headless SOFT-gate default) in the gate contract. |

### 6. Validation / testing

- Walk an existing fragile item (e.g. `inventory/fenton-lamps/` — glass lamps)
  through IDENTIFY → DRAFT and confirm it classifies `fragile`, asks, and
  produces a LOCAL_PICKUP draft with no parcel service.
- Confirm a normal small durable item is untouched (`ship_risk: none`, ships
  as today).
- `lib/list_edit.py --sync` dry-run against a LOCAL_PICKUP draft routes to the
  pickup policy and validates cleanly; clear error when the policy ID is
  missing from config.

## Open point for the reviewer

**The gate contract.** RUN.md currently states the REVIEW gate is the *only*
hard stop, and every other "ask the user" moment is a SOFT gate (default +
log, never block). "Ask before drafting" introduces a second interactive
prompt in attended runs. The plan reconciles this by making the ask a
**SOFT gate with a default** (pickup-primary + freight, logged, re-surfaced at
REVIEW) so headless runs never block — the "ask" only materializes when a
human is present. **Confirm this reconciliation**, or say whether you'd rather
it be a true hard stop even in headless runs (which would change the
headless contract).
