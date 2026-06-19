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
  to `CONDITION_ENUM`. · `condition_description` — telegraphic factual
  disclosure per condition-rubric's "write it telegraphic" spec: defect
  fragments → one grade-relevant `No … noted.` → can't-assess. No
  narrative, marketing, decorative description, or what's-included.
  ≤1000 but aim far shorter; **every flagged defect survives**.

**item_specifics:** from INVESTIGATE's "Item specifics" section ONLY. If
INVESTIGATE listed it, copy; else `""` — do NOT fall back to IDENTIFY.
Standard fields ≤65, `upc` ≤20. Extra specifics → `item_specifics.extra`.
**Category-REQUIRED aspects must still be emitted** even if INVESTIGATE didn't
itemize them and even when unbranded — eBay rejects the publish otherwise (errorId
25002 "item specific <X> is missing"). A loaded specialization names its category's
required aspects in its Output hooks (e.g. jewelry rings require `Metal` +
`Metal Purity` + `Ring Size`); emit those into `item_specifics.extra` from
INVESTIGATE/IDENTIFY. When unsure which a category requires, prefer including the
obvious ones over a failed publish.

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
map; `handling_time_days` 1; `item_location_zip` blank. **Set
`fulfillment_mode` per the Local-pickup gate below** — default `SHIP`.

**Local-pickup gate (SOFT — suggest, never assume).** Some items are
awkward or unsafe to parcel-ship (heavy, oversized, or fragile like stained
glass). For these we list as **local pickup** (no parcel shipping; freight
offered by quote in the description), routed to the account's local-pickup
fulfillment policy. Decide `fulfillment_mode`:

- **Default `SHIP`.** Most items ship normally — leave `fulfillment_mode:
  SHIP` and fill the shipping fields as usual.
- **Set `LOCAL_PICKUP` only when the user has indicated it** — either they
  said so for this item ("local only", "pickup only", "too fragile to ship"),
  or you asked and they confirmed. The user usually says so up front.
- **When to ASK (and you must ask before assuming):** if IDENTIFY's **Ship
  risk** is `suggest-pickup` (weight > 25 lb or any side > 24 in), OR the item
  is clearly fragile (stained/blown glass, thin porcelain), surface the
  suggestion: *"This looks <reason> — risky to ship. List as local pickup
  (freight by quote), or ship normally?"* **Attended:** ask and honor the
  answer. **Headless (no one to ask):** keep `SHIP` (the safe non-blocking
  default), and append a NEEDS_REVIEW line suggesting local pickup so it's
  raised again at REVIEW. Never silently flip an item to pickup-only.
- **When `LOCAL_PICKUP`:** set `fulfillment_mode: LOCAL_PICKUP`,
  `free_shipping: false`, `domestic_shipping_type` blank, `primary_service`
  blank (no parcel service); still record weight/dims (useful for a freight
  quote). Set `local_pickup.location_hint` from the seller's city/region if
  known. Add a short closing line to the description body: *"Local pickup only
  — <location_hint>. Not local? Message me for a freight quote."* Log the
  decision (+ trigger) in `meta.notes`.

**photos:** image files in shoot dir, lexicographic (first = hero). If
INVESTIGATE references photos by number, honor that order.

**`_field_constraints`:** copy from template verbatim — the validator
reads it.

### Quantity map
`single`/`pair`/`set`/`lot` → eBay `quantity` **1** (title carries the
"Pair of…/Set of N…/Lot of N…"). `duplicate` → IDENTIFY.quantity (N) —
the only case eBay quantity > 1.

### Service map
Applies only when `fulfillment_mode: SHIP`. (LOCAL_PICKUP offers no parcel
service — leave `primary_service` blank.)
Books/catalogs/magazines/media → `USPSMediaMail`. ≤15 lb non-media →
`USPSGroundAdvantage`. >15 lb / oversized → `UPSGround`. freight/movers →
blank + flag (needs a quote) — also a strong local-pickup candidate (gate
above).

## Markdown body (description)

Compose from INVESTIGATE's claim set only:
- **Hook** (1–2 sentences) from INVESTIGATE Summary, buyer-facing.
- **What's Included** — bullets from observable components; unit-type
  vocabulary.
- **Condition** — factual, minimal. Bullets enumerating EVERY defect
  INVESTIGATE flagged (no minimizing), each as `<location>: <defect>`.
  At most one short context line for expected vintage wear; no warm
  framing sentence, no marketing. Defects always survive any trim.
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
- condition_description: already telegraphic, so rarely near the cap. If
  over, drop grade-relevant clears first, then can't-assess; defects
  always stay; end at a fragment boundary.
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

## Register the item (SKU + ledger record)

**Whenever you finish writing `draft.md` — and again after any later edit to
it — register the item:**

    python lib/list_edit.py --record <shoot-dir>

This computes the item's **SKU** (a deterministic 8-hex hash of title +
folder — no eBay call, no credentials), stamps it into the draft's
`meta.ebay_inventory_sku`, and creates/refreshes the item's row in the
listings ledger with status **DRAFTED**. It's idempotent — once the SKU is
stamped it's reused, so an edit just refreshes the row's title/price and
never duplicates it. The same row is later updated in place to
SYNCED → PUBLISHED → ENDED/DELETED.

This guarantees the record exists from draft time, **before REVIEW**.
Belt-and-suspenders: `--review` (and `--sync`) also call `--record`
internally, so a missed run is recovered; if you can't run a shell here, the
record is created at the next step that can.

## Closing

Per _shared: path + chosen title with `[N/80]` + working price. List
flagged gaps / substitutions as one-line bullets. Don't restate
frontmatter. In `list`/`full` mode, REVIEW ([review.md](review.md)) runs
next — it turns this draft into the decision card and stops for approval
before anything publishes.
