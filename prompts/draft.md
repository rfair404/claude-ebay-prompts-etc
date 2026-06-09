# DRAFT — v3, Function 5

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/draft.md` (overwrite).

A pure template-fill transform — no new identification, investigation, or
pricing. Read the prior phases' files, render the template, validate
against its constraints, write one self-contained local file. **Local
file only — no eBay calls, no publishing** (firewall, per _shared).

## Inputs

1. `identify.txt` — structured fields (unit_type, qty, category, weight,
   dims).
2. `investigate.txt` — **authoritative for every user-visible claim**
   (title, description, item specifics, condition wording).
3. `price.txt` — three tiers + the working price. Headless adopts the
   Recommended/provisional working price (logged in NEEDS_REVIEW).
4. [`templates/listing-v1.md`](../templates/listing-v1.md) — the
   template; its `_field_constraints` block is the authoritative limits map.

## Preconditions

- `investigate.txt` **required** — abort with a clear message if missing
  (DRAFT can't make defensible copy without it).
- `identify.txt` missing → proceed but flag missing structured fields in
  `meta.notes`.
- `price.txt` missing or no working price → leave `price:` blank, flag.
  (Headless normally has a provisional price from PRICE.)

DRAFT never invents to fill gaps — missing inputs become flagged gaps.

## Source-of-truth mapping

Never invent. Never copy IDENTIFY `[BEST-CASE]` markers into copy — only
repeat a claim INVESTIGATE confirmed.

**Top-level:**
- `template_version` `v1` (verbatim) · `meta.item_id` from shoot-dir name
  · `meta.shoot_dir` path · `meta.drafted_at` UTC ISO-8601 ·
  `meta.ebay_*`/`last_synced` unchanged null · `meta.notes` = DRAFT NOTES.
- `title` — best fitting INVESTIGATE title claim; apply unit-type
  phrasing; ≤80.
- `category_id` blank (eBay suggests at LIST) · `category_path` from
  INVESTIGATE/IDENTIFY category (human-readable only).
- `condition` — map INVESTIGATE's grade (already from condition-rubric)
  to `CONDITION_ENUM`. · `condition_description` from INVESTIGATE's
  observable condition lines; ≤1000; **every flagged defect survives**.

**item_specifics:** from INVESTIGATE's "Item specifics" section ONLY. If
INVESTIGATE listed it, copy; else `""` — do NOT fall back to IDENTIFY.
Standard fields ≤65, `upc` ≤20. Extra specifics → `item_specifics.extra`.

**pricing:** `format` FIXED_PRICE (AUCTION only if price.txt says) ·
`price` = working price as string, ≤13 · `cost_of_goods` null ·
`quantity` per the table below. Best Offer per the gate below.

**Best Offer gate (default):** Best Offer is only worth enabling when the
list price sits ABOVE the supported price, so there's headroom to negotiate
down to it.

- Compare the list `price` to PRICE's **Recommended** tier (from price.txt).
- **If `price` > Recommended** (listing in-between Recommended and Push-high,
  or at/above Push-high): `best_offer.enabled` **true** ·
  `best_offer.auto_decline_amount` = **the Recommended tier price**, rounded
  to the nearest whole dollar, as a string — auto-decline anything below the
  supported price. (Fallback if price.txt has no Recommended tier: 85% of
  list, nearest dollar.)
- **If `price` ≤ Recommended**: `best_offer.enabled` **false**, both amounts
  null — you're already at or below the supported price, so don't invite
  offers.
- `best_offer.auto_accept_amount` is **always null** — never auto-accept;
  the user reviews and accepts offers manually.
- Log the gate decision + computed auto-decline in `meta.notes`.

**shipping:** weight/dims from IDENTIFY (round up); `free_shipping` true →
`domestic_shipping_type` FREE_FLAT_RATE; `primary_service` per Service
map; `handling_time_days` 1; `item_location_zip` blank.

**photos:** image files in shoot dir, lexicographic (first = hero). If
INVESTIGATE references photos by number, honor that order.

**`_field_constraints`:** copy from template verbatim — the validator
reads it.

### Quantity map
`single`/`pair`/`set`/`lot` → eBay `quantity` **1** (title carries the
"Pair of…/Set of N…/Lot of N…"). `duplicate` → IDENTIFY.quantity (N) —
the only case eBay quantity > 1.

### Service map
Books/catalogs/magazines/media → `USPSMediaMail`. ≤15 lb non-media →
`USPSGroundAdvantage`. >15 lb / oversized → `UPSGround`. freight/movers →
blank + flag (needs a quote).

## Markdown body (description)

Compose from INVESTIGATE's claim set only:
- **Hook** (1–2 sentences) from INVESTIGATE Summary, buyer-facing.
- **What's Included** — bullets from observable components; unit-type
  vocabulary.
- **Condition** — one warm framing sentence + bullets enumerating EVERY
  defect INVESTIGATE flagged (no minimizing); context sentence for
  vintage wear.
- **About this item** — 1–2 sentence collector hook from INVESTIGATE's
  listing-approach.

Hard rules: never include a claim absent from INVESTIGATE's listing-safe
/ observable lists; never anything INVESTIGATE marked NOT defensible;
never IDENTIFY `[BEST-CASE]` language; honor unit-type phrasing.

## Constraints — enforce before write (the hardest rule)

Limits come from the template's `_field_constraints` (authoritative; read
at render time). Key caps: title 80 · condition_description 1000 ·
item_specifics 65 (upc 20) · price/cost/offer 13 · quantity 5 · weight
lb 3 / oz 2 · dims 5. All counts are Unicode chars.

Satisfy by **rephrasing, never mid-word truncation:**
- title: try INVESTIGATE claims in order; first that fits wins. Else
  compose `<Era> <Brand> <Type> <feature>`, dropping lowest-priority
  words. Always keep brand (or "Vintage"/"Unbranded") + the noun.
- item_specifics: shortest correct canonical form (`Store Catalog`, not a
  prose sentence).
- condition_description: trim redundancy first, then least-critical
  observations; defects always stay; end at a sentence boundary.
- numeric fields: the cap is on the string form. If a real value won't
  fit, the input is wrong (round/re-measure/flag) — never drop digits.
- `lookup_only` (`country_of_origin`, `department`): substitute closest
  canonical ("USA"→"United States"), log it (SOFT gate).
- `required` (`title`, `quantity`, `price`, `item_specifics.type`)
  missing: leave empty, flag in `meta.notes` + NEEDS_REVIEW; still write
  the file (the lib validator is what blocks sync).

### Pre-write validation pass (required)
Walk every `_field_constraints` entry against the populated value:
length ≤ max_len (rephrase if not) · required present (else flag) ·
numeric parses positive (else flag, empty) · lookup canonical (else
substitute + log). Only then write draft.md. Re-read after write and
confirm every constrained field fits and `_field_constraints` was copied
verbatim.

## meta.notes (DRAFT NOTES)

One terse line per non-straight-copy decision: title source + char count;
condition grade + tie-break; weight/dims rounding; price tier + source;
photo order; any rephrase (field, orig→final length); any lookup
substitution. These stay local — never pushed to eBay.

## Closing

Per _shared: path + chosen title with `[N/80]` + working price. List
flagged gaps / substitutions as one-line bullets. Don't restate
frontmatter.
