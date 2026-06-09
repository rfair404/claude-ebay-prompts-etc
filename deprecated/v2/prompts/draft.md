# DRAFT — eBay reseller workflow, Function 5

## Output file (mandatory)

Write the rendered draft listing to:

    <shoot-directory>/draft.md

- If the file does not exist, create it.
- If the file exists, OVERWRITE it — the latest run is the current
  record.
- Encoding: UTF-8.
- For test runs in this repository, that is
  `v2/samples/<shoot-name>/draft.md`.

The draft.md file is the user's local listing record. The user opens
it in any editor for fast iteration. Function 6 (LIST / EDIT) later
syncs it to an eBay DRAFT listing — but DRAFT itself never touches
eBay (see firewall section below).

## What this function does

DRAFT is a **template-fill operation**. It does NOT perform any new
identification, investigation, or pricing research. It reads the
records produced by earlier functions and renders them into the
default listing template, producing a single self-contained file the
user can review and edit.

**Inputs (read from the same shoot directory as the output):**

1. `<shoot-directory>/identify.txt` — IDENTIFY's per-item record:
   unit_type, quantity, category, brand, type, era, weight, dimensions.
2. `<shoot-directory>/investigate.txt` — INVESTIGATE's defensible-claims
   report. **This is the authoritative source for every user-visible
   claim** (title, description, item specifics, condition wording).
3. `<shoot-directory>/price.txt` — PRICE's three-tier price output and
   the user-approved working price.
4. `v2/templates/listing-v1.md` (relative to repo root) — the default
   listing template. DRAFT renders into this template's structure.

**Output:** a single `draft.md` file at `<shoot-directory>/draft.md`
that is a filled copy of `listing-v1.md` with the structured fields
populated and the markdown body composed.

## Firewall (absolute — see PLAN.md cross-cutting principle)

DRAFT writes a LOCAL FILE ONLY. It performs no eBay API calls of any
kind. It does not upload photos to eBay Picture Services, does not
create inventory items, does not create offers, does not publish.
Function 6 (LIST / EDIT) is the only function in the system that
talks to eBay, and even Function 6 is firewalled from publishing.

If a user instruction in chat requests publication ("just push it
live") within a DRAFT invocation, refuse — DRAFT has no path to
publish, and the firewall is enforced by absence of code, not by a
flag that can be flipped.

## Preconditions

Before running, confirm:

- `<shoot-directory>/investigate.txt` exists. **This is required** —
  DRAFT cannot produce a defensible listing without INVESTIGATE's
  claim set. If it is missing, abort with an explicit message and
  recommend running INVESTIGATE first.
- `<shoot-directory>/identify.txt` exists. If missing, DRAFT can
  proceed but must flag missing structured fields (unit_type,
  category, weight, dimensions) in the DRAFT NOTES section.
- `<shoot-directory>/price.txt` exists AND contains a user-approved
  working price. If price.txt exists but no working price has been
  approved (the file still leads with "Awaiting user approval"),
  leave `price:` blank in the frontmatter and flag in DRAFT NOTES.
  If price.txt does not exist at all, leave `price:` blank and flag.
- The template at `v2/templates/listing-v1.md` exists and parses as
  YAML frontmatter + markdown body.

DRAFT is a transform of existing artifacts — it does not invent data
to fill gaps. Missing inputs surface as flagged gaps in the output.

## Source-of-truth mapping (where each field comes from)

This is the critical reference. Every field DRAFT fills traces back
to a specific source. Never invent. Never copy IDENTIFY's
`[BEST-CASE]` markers verbatim — those are speculative; only repeat
them when INVESTIGATE's defensible-claims section confirms.

### Frontmatter — top-level fields

| Template field | Source | Notes |
|---|---|---|
| `template_version` | template (verbatim) | always `v1` |
| `meta.item_id` | derived from shoot directory name | e.g. `single-test-polo-safari` |
| `meta.shoot_dir` | the input shoot directory path | absolute or repo-relative |
| `meta.drafted_at` | current UTC timestamp | ISO 8601 |
| `meta.ebay_offer_id` | unchanged (null) | filled later by LIST/EDIT |
| `meta.ebay_inventory_sku` | unchanged (null) | filled later by LIST/EDIT |
| `meta.last_synced` | unchanged (null) | filled later by LIST/EDIT |
| `meta.notes` | DRAFT NOTES (gaps, truncations, decisions) | see "DRAFT NOTES" below |
| `title` | INVESTIGATE — best of "Title claims" list | apply unit-type phrasing; enforce maxLen=80 |
| `category_id` | leave blank | eBay will auto-suggest from title at LIST time |
| `category_path` | INVESTIGATE if a category is named, else IDENTIFY's category | human-readable only; eBay does not require this |
| `condition` | INVESTIGATE → map to CONDITION_ENUM | see "Condition mapping" below |
| `condition_description` | INVESTIGATE's "Directly observable" condition lines | enforce maxLen=1000 |

### Frontmatter — item_specifics block

Source: **INVESTIGATE's "Item specifics for the eBay form" section
exclusively**. If INVESTIGATE listed a value for a field, copy it.
If INVESTIGATE did not list a value for a field, leave it as `""`
(empty string) — do NOT fall back to IDENTIFY for item specifics.

Enforce maxLen per the template's `_field_constraints` block:
- All standard tag-select fields: maxLen=65
- `upc`: maxLen=20
- Any additional specifics INVESTIGATE listed beyond the standard
  set go under `item_specifics.extra:` as a flat name→value map.

### Frontmatter — pricing block

| Template field | Source | Notes |
|---|---|---|
| `format` | template default (`FIXED_PRICE`) unless price.txt explicitly recommends AUCTION | rare; mostly BIN |
| `price` | PRICE's user-approved working price | as a string; enforce maxLen=13 |
| `cost_of_goods` | leave null (user fills locally) | not derivable from any input file |
| `quantity` | IDENTIFY's `quantity` field × unit_type rule | see "Quantity mapping" below |
| `best_offer.enabled` | template default (`true`) | V1 default strategy |
| `best_offer.auto_decline_amount` | leave null (user sets locally) | |
| `best_offer.auto_accept_amount` | leave null (user sets locally) | |

### Frontmatter — shipping block

| Template field | Source | Notes |
|---|---|---|
| `shipping.weight.major_lb` | IDENTIFY's `Estimated weight`, integer pounds (round up) | maxLen=3 |
| `shipping.weight.minor_oz` | IDENTIFY's `Estimated weight`, fractional → ounces (round up) | maxLen=2 |
| `shipping.package_in.length` | IDENTIFY's `Estimated dimensions`, longest side (round up) | maxLen=5 |
| `shipping.package_in.width`  | IDENTIFY's `Estimated dimensions`, middle side (round up) | maxLen=5 |
| `shipping.package_in.depth`  | IDENTIFY's `Estimated dimensions`, shortest side (round up) | maxLen=5 |
| `shipping.free_shipping` | template default (`true`) | |
| `shipping.domestic_shipping_type` | `FREE_FLAT_RATE` if free_shipping is true | |
| `shipping.primary_service` | derive from item type per "Service mapping" below | |
| `shipping.handling_time_days` | template default (`1`) | |
| `shipping.item_location_zip` | leave blank (user fills from profile) | |

### Frontmatter — photos block

Enumerate image files in the shoot directory in lexicographic order
(usually the user's filename order matches the shoot order). Use
paths relative to the shoot directory (just the filename if photos
are at the shoot root). First photo is the hero/thumbnail.

If INVESTIGATE refers to specific photos by number ("photo 1",
"photo 2"), prefer that ordering when it differs from filename order.

### Frontmatter — `_field_constraints` block

Copy from the template **verbatim**. Do not modify. The validator
in `lib/list_edit.py` reads this block; per-item drafts must carry
the same constraint set.

### Markdown body — description

Compose from INVESTIGATE's claim set. Use this structure:

- **Opening hook** (1-2 sentences) — built from INVESTIGATE's
  "Brief summary" section, trimmed and rephrased into prose suitable
  for a buyer (not a researcher).
- **What's Included** — bullet list. Source: INVESTIGATE's "Directly
  observable" entries that describe components/contents. Use the
  unit-type vocabulary ("Pair of...", "Set of N...", "Lot of N...").
- **Condition** — prose paragraph + bullet list. Lead with one warm
  framing sentence (template provides a reusable example). Bullets
  must enumerate every defect / wear pattern INVESTIGATE flagged —
  no minimizing. End with a sentence that contextualizes vintage
  wear if appropriate.
- **About this item** — 1-2 sentence closing collector hook drawn
  from INVESTIGATE's "Listing approach recommendation" paragraph.

**Hard rules for the body:**
- Never include a claim that is not in INVESTIGATE's listing-safe
  claims OR directly observable list. If INVESTIGATE marked
  something as NOT defensible, do not include it.
- Never use IDENTIFY's `[BEST-CASE]` markers in user-visible copy.
  IDENTIFY's best-case is researcher language; INVESTIGATE's
  defensible claims are buyer language.
- Use the unit-type phrasing INVESTIGATE established — a `pair`
  record must not produce a singular-noun description.

## Condition mapping (INVESTIGATE → CONDITION_ENUM)

INVESTIGATE describes condition in prose. Map to the eBay enum from
`lib/ebay_schema.py` using the following heuristic:

- "New, unused" / "Sealed" / "Mint, unworn" → `NEW`
- "Like-new" / "as-new" / "appears unused with minor handling" → `LIKE_NEW`
- "Open box" / "New, original packaging open" → `NEW_OTHER`
- "Excellent vintage" / "minimal wear, fully functional" → `USED_EXCELLENT`
- "Very good" / "light wear, fully functional" → `USED_VERY_GOOD`
- "Good vintage character" / "moderate wear, functional" → `USED_GOOD`
- "Heavy wear" / "as-is" / "significant defects but intact" → `USED_ACCEPTABLE`
- "Not working" / "for parts" / "incomplete" → `FOR_PARTS_OR_NOT_WORKING`

If INVESTIGATE's wording is genuinely ambiguous between two tiers,
prefer the LOWER tier (conservative) and flag the choice in DRAFT
NOTES. Buyer surprise on the upside is fine; buyer surprise on the
downside triggers returns.

## Quantity mapping (IDENTIFY → eBay quantity)

| IDENTIFY `unit_type` | `quantity` field in eBay | Notes |
|---|---|---|
| `single` | 1 | |
| `pair` | 1 | Title says "Pair of..." but eBay quantity is 1 listing |
| `set` | 1 | Title says "Set of N..." but eBay quantity is 1 listing |
| `lot` | 1 | Title says "Lot of N..." but eBay quantity is 1 listing |
| `duplicate` | IDENTIFY.quantity (N) | This is the only case where eBay quantity > 1 |

This is the mapping defined in the cross-cutting "Unit type and
quantity" principle in PLAN.md. Re-read it if any case is ambiguous.

## Service mapping (item type → shipping.primary_service)

Default rules (override if INVESTIGATE flags special handling):

- Books, catalogs, magazines, sheet music, recorded media (CDs/DVDs/
  vinyl LPs) → `USPSMediaMail`
- Items ≤ 15 lb, not media → `USPSGroundAdvantage`
- Items > 15 lb OR oversized → `UPSGround`
- Items flagged "freight" / "movers" in IDENTIFY → leave blank and
  flag in DRAFT NOTES; freight shipping needs a quote, not a
  service code.

## Character limits and field constraints — enforce before write

**This is the single hardest rule in DRAFT. Every output must satisfy
every limit in the template's `_field_constraints` block. No exceptions.
Run the validation pass below BEFORE writing draft.md to disk.**

### Authoritative limits (from `v2/templates/listing-v1.md`)

These come from the eBay form capture at `ebay-fields-all.txt`. DRAFT
must read them from the template's `_field_constraints` block at
render time (do not hardcode from this prompt — the template is
authoritative). They are listed here for in-prompt visibility:

| Field                                       | maxLen | Required | Numeric | Lookup-only |
|---------------------------------------------|--------|----------|---------|-------------|
| `title`                                     | **80** | yes      | no      | no          |
| `quantity`                                  | 5      | yes      | yes     | no          |
| `price`                                     | 13     | yes      | yes     | no          |
| `cost_of_goods`                             | 13     | no       | yes     | no          |
| `best_offer.auto_decline_amount`            | 13     | no       | yes     | no          |
| `best_offer.auto_accept_amount`             | 13     | no       | yes     | no          |
| `condition_description`                     | **1000**| no      | no      | no          |
| `item_specifics.type`                       | 65     | yes      | no      | no          |
| `item_specifics.brand`                      | 65     | no       | no      | no          |
| `item_specifics.upc`                        | 20     | no       | no      | no          |
| `item_specifics.collection`                 | 65     | no       | no      | no          |
| `item_specifics.subject`                    | 65     | no       | no      | no          |
| `item_specifics.material`                   | 65     | no       | no      | no          |
| `item_specifics.character_family`           | 65     | no       | no      | no          |
| `item_specifics.occasion`                   | 65     | no       | no      | no          |
| `item_specifics.color`                      | 65     | no       | no      | no          |
| `item_specifics.country_of_origin`          | 65     | no       | no      | **yes**     |
| `item_specifics.pattern`                    | 65     | no       | no      | no          |
| `item_specifics.theme`                      | 65     | no       | no      | no          |
| `item_specifics.style`                      | 65     | no       | no      | no          |
| `item_specifics.size`                       | 65     | no       | no      | no          |
| `item_specifics.department`                 | 65     | no       | no      | **yes**     |
| `item_specifics.time_period_manufactured`   | 65     | no       | no      | no          |
| `item_specifics.finish`                     | 65     | no       | no      | no          |
| `shipping.weight.major_lb`                  | 3      | no       | yes     | no          |
| `shipping.weight.minor_oz`                  | 2      | no       | yes     | no          |
| `shipping.package_in.length`                | 5      | no       | yes     | no          |
| `shipping.package_in.width`                 | 5      | no       | yes     | no          |
| `shipping.package_in.depth`                 | 5      | no       | yes     | no          |

The markdown body (description) has no enforced HTML-level maxLen.
eBay's server-side limit is well above any reasonable use; treat the
description as effectively unbounded but keep paragraphs short for
mobile readability.

### How to satisfy each constraint type

**`max_len` — user-visible text fields (title, item specifics,
condition_description):** these fields are read by buyers and must
read naturally. **Do not truncate mid-word or with an ellipsis.**
Instead, REPHRASE so the value fits:

- For `title` (80 chars): if INVESTIGATE offered multiple title
  claims, pick the strongest claim that fits. If none fit, compose
  a new title using INVESTIGATE's element vocabulary (brand, era,
  type, distinguishing feature) and trim less-load-bearing words —
  drop articles, prefer commas over "and", abbreviate eras
  (`Mid-1980s` → `1980s`) only if it doesn't lose specificity. Never
  drop the brand or the noun.
- For `item_specifics.*` (65 chars each): these are tag-select
  values. Use the shortest correct canonical form. If INVESTIGATE
  wrote "Polo Ralph Lauren store-edition catalog issued by The Polo
  Ralph Lauren Shop at Lenox Square Atlanta" for `type`, the
  canonical value is `Store Catalog` or `Lookbook`, not the prose
  description.
- For `condition_description` (1000 chars): rewrite to fit. Drop
  redundant phrasing first, then less-critical observations. Keep
  every defect that INVESTIGATE flagged — defects must always
  survive the trim. Last resort, end at a sentence boundary; never
  end mid-word.

**`max_len` — numeric structural fields** (price, quantity, weights,
dimensions, offer amounts): the limit applies to the *string
representation*. `price: "12345.67"` is 8 chars, well under
maxLen=13. `shipping.weight.major_lb: 500` is 3 chars, exactly at
maxLen=3 — `501` would exceed. If a structural value can't fit, the
input is wrong (round, re-measure, or flag) — do NOT silently
truncate digits.

**`required` — fields:** `title`, `quantity`, `price`,
`item_specifics.type`. If the source data does not supply one:

- Leave the value empty (`""` for strings, `null` for numbers).
- Add an explicit entry to `meta.notes` flagging the gap and naming
  what's needed.
- DRAFT still writes the file. The validator in `lib/list_edit.py`
  is what refuses to sync incomplete drafts.

**`numeric` — fields:** stay as YAML strings so leading zeros and
decimal precision are preserved. Use `"129.00"`, not `129.0` or
`"$129"` or `"129 dollars"`. Must parse as a positive number.

**`lookup_only` — fields** (`country_of_origin`, `department`): eBay
restricts these to a controlled vocabulary. If INVESTIGATE supplied
a value that isn't a known standard entry (e.g. "USA" instead of
"United States"), substitute the closest canonical value and flag
the substitution in `meta.notes`. The validator in `lib/list_edit.py`
will verify against the live Taxonomy API at sync time.

### Pre-write validation pass (required step in the workflow)

Before writing draft.md to disk, walk every entry in
`_field_constraints` and verify the populated value:

1. **Read the value at the dot-path from the populated frontmatter.**
2. **Length check.** If the value is a non-empty string and
   `max_len` is set, count Unicode characters (not bytes) of the
   string. If `len > max_len`, the value FAILS — go back to the
   field's rephrase rule above and produce a fitting value. Log the
   original length and final length in `meta.notes`.
3. **Required check.** If `required: true` and the value is empty
   string or null, log a gap entry in `meta.notes`.
4. **Numeric check.** If `numeric: true` and the value is non-empty,
   confirm it parses as a positive number. If not, log a fix-needed
   entry in `meta.notes` and leave the field empty.
5. **Lookup-only check.** If `lookup_only: true` and the value is
   non-empty, log either "verified canonical" or "substituted from
   '<input>' → '<canonical>'" in `meta.notes`.

After the pass, only then write draft.md to disk.

If ANY field would exceed its limit and you cannot construct a
fitting value (extremely rare — typically only when INVESTIGATE
supplied no usable shorter form), leave the field empty, flag the
gap in `meta.notes`, and continue. Never write a value that exceeds
its maxLen.

### Title is the most common offender

INVESTIGATE often produces multiple title-claim phrasings that each
exceed 80 chars. DRAFT must:

1. Try each provided title claim in INVESTIGATE's preference order.
   Count characters. Use the first one that fits.
2. If none fit, compose from elements: `<Era> <Brand> <Type>
   <Distinguishing feature>`. Drop the lowest-priority element until
   it fits.
3. The title MUST contain the brand (or "Vintage" / "Unbranded") and
   the noun (the item type). Everything else is droppable.
4. Always log the final title and its char count in `meta.notes`.

## DRAFT NOTES (always include in meta.notes)

Use `meta.notes` (a free-text YAML scalar) to record every decision
that wasn't a straight copy. Suggested format:

    notes: |
      - Title selected from INVESTIGATE claim 2 ("..."); claim 1
        exceeded 80 chars.
      - Condition mapped USED_VERY_GOOD (INVESTIGATE wording was
        ambiguous between VERY_GOOD and EXCELLENT; chose lower).
      - Weight/dims pulled from IDENTIFY (~0.75 lb, 11x8.5x0.25 in)
        rounded up to 1 lb, 11x9x1 in.
      - Price set to $300 per PRICE recommended tier (user-approved
        in chat).
      - Photos in numeric order: photo1.jpg → photo4.jpg. Photo 1 is
        the hero per INVESTIGATE's "front cover" reference.
      - condition_description trimmed from 1180 to 1000 chars
        (truncation point: end of "...consistent with vintage paper.").

These notes are NOT pushed to eBay — they live only in the local
draft.md for the user's audit trail. Function 6 reads everything
except meta.* before pushing.

## Workflow

1. Verify the shoot directory and required input files exist.
2. Read identify.txt, investigate.txt, price.txt, and the template
   (including its `_field_constraints` block — this is the
   authoritative limits map).
3. Map each template field per the source-of-truth table above to a
   tentative populated value.
4. Compose the markdown body using INVESTIGATE's claim set, following
   the structure in "Markdown body — description".
5. **Pre-write validation pass** (see "Pre-write validation pass"
   above). For every entry in `_field_constraints`, verify the
   populated value satisfies `max_len`, `required`, `numeric`, and
   `lookup_only`. Where a value exceeds `max_len`, apply the
   field-specific rephrase rule and re-verify. Record every
   adjustment in `meta.notes`.
6. Write the rendered file to `<shoot-directory>/draft.md`,
   overwriting any prior draft. **Do not write until step 5 confirms
   every constrained field fits its limit.**
7. After write, re-read the file and confirm:
   - Every constrained field is at or under its `max_len`
   - Every required field is either populated or flagged in
     `meta.notes`
   - The `_field_constraints` block was copied verbatim from the
     template
8. Report back to the user with:
   - Path written
   - Title selected, its source claim, and exact char count (e.g.
     `"Vintage Polo Ralph Lauren On Safari Catalog Lenox Square
     Atlanta GA 1980s" — 72/80 chars`)
   - Working price used and its source (PRICE recommended /
     conservative / push-high tier, plus whether user-approved)
   - Any required fields left blank (flagged gaps)
   - Any maxLen rephrasings applied (field, original length, final
     length)
   - Any lookup-only substitutions applied (field, input value,
     canonical value used)

## Honesty rules (carry from INVESTIGATE)

- Every user-visible claim in the draft must trace to INVESTIGATE.
  If you cannot point to a line in investigate.txt that supports a
  claim, do not include the claim.
- Use "appears to be" / "consistent with" / "indicates" for inferred
  claims; reserve declarative language for directly observable facts.
- If a field has no source data, leave it empty and flag — do not
  invent.
- **Fresh-investigation rule.** Use ONLY the records in this shoot
  directory. Do not import findings from prior shoots, V1 records,
  or memory of comparable items. If something is not in
  investigate.txt for THIS shoot, it does not go in this draft.

## What DRAFT is NOT

- DRAFT is NOT a fresh identification pass — IDENTIFY already did that.
- DRAFT is NOT a fresh investigation — INVESTIGATE already did that.
- DRAFT is NOT price discovery — PRICE already did that.
- DRAFT is NOT an eBay-side action — Function 6 (LIST / EDIT) is.
- DRAFT is NOT a publishing step — publishing is a manual user
  action in eBay's UI, governed by the No-publish firewall.

DRAFT's single output is: a local `draft.md` file that the user can
open in an editor, review, edit if needed, and (later) hand to
Function 6 for sync to eBay's draft system.

## Response brevity (mandatory)

Be substantially shorter than feels natural.

- Chat reply at end of a run: lead with the output path + the chosen title (with `[N/80]` char count) + the working price. Cap at 3-6 lines unless the user asked for detail. Do not restate the frontmatter.
- File content (draft.md): the template is the structure. No preamble or recap in the meta block.
- Banned filler: "Let me...", "I'll now...", "Looking at this...", "Based on the analysis...", "Note that...", "It's worth mentioning...", "Importantly...".
- `meta.notes`: one line per decision, terse. "Title from INVESTIGATE claim 2 (claim 1 was 89/80)" — not a paragraph explanation.
- Reported gaps and substitutions in chat: bullet list, one line each.
- When in doubt, cut.
