# DRAFT — v3, Function 5

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/draft.md` (overwrite).

A pure template-fill transform — no new identification, investigation, or
pricing. Read the prior phases' files, render the template, validate
against its constraints, write one self-contained local file. **Local
file only — no eBay calls, no publishing** (firewall, per _shared).

## Photos come from PREP — DRAFT does not touch them up

Photo preparation is its own phase now: **[PREP](prep.md)**, run after IDENTIFY.
It handles orientation, crop, backdrop and the subject pass in one pass, writes
`<shoot-dir>/listing/`, and stops at a HARD approval gate.

**DRAFT's job here is only to point `photos:` at the prepped files:**

    python -m lib.photo_prep.prep <shoot-dir> --repoint-draft --apply-repoint

That maps each existing entry to its prepped counterpart **in the same order** —
entry one is the eBay gallery image and the list is often not lexicographic.

If PREP has not been run, stop and run it; do not fall back to the old tools.
`upload_photos_to_eps` enforces this in code, so an unprepped or unapproved
shoot cannot publish regardless of what this prompt says.

<details>
<summary>The old hand-chained sequence (superseded — kept for one-off use)</summary>

The steps below ran as a chain: `strip_exif` → `even_background` →
`trim_whitespace` → `center_crop`, each writing a subdirectory. **Do not run
them as a chain any more.** Four subdirectories that all look plausible, plus a
lexicographic photo picker at the end, is where 66 sideways photos hid until
buyers complained. PREP replaces the whole sequence with one output directory
and a manifest recording what was done to each frame.

The individual tools still work for a quick one-off, and `orient.py`'s manifest
is read *and written* by PREP so a rotation recorded in either is the call in
both. What must not happen is running both on one shoot and then guessing which
directory the draft points at.

> ⛔ **The orientation test is CONTENT, not metadata.** "Would a buyer see this
> the right way up?" is the only question that matters, and it cannot be answered
> from EXIF. A clean metadata pass is necessary and nowhere near sufficient: a
> phone held at an angle, a book laid on its side, a box shot end-on all produce
> files that are correct by their EXIF and wrong on the page. Buyers complained
> about exactly this, and it was declared fixed twice on the strength of the
> metadata alone. **Never call photos good without looking at them, and never at
> thumbnail size** — a whole batch was passed off small tiles with every frame
> still rotated. See step 1b.

Run the sequence (skip a step when it doesn't apply):

1. **EXIF / orientation** — always. Bakes any real Orientation tag into the
   pixels (`exif_transpose`), then strips metadata, so a viewer needs no tag to
   get it right.
       python -m lib.photo_prep.strip_exif <shoot-dir>            # -> <shoot-dir>/no-exif/
       python -m lib.photo_prep.strip_exif <shoot-dir> --force    # after fixing a bug: stale
                                                                 # outputs are NEWER than their
                                                                 # sources and are skipped otherwise

1b. **Orientation review — ALWAYS, and a HUMAN rules on it.** Step 1 only fixes
   photos whose camera told the truth. For the rest:
   - Build a numbered contact sheet of the SHIPPED files (`no-exif/`, opened with
     NO transpose), frame numbers matching this draft's `photos:` order.
   - Any frame that isn't obviously right: render it at **all four rotations side
     by side** and pick by eye. Text baseline is the strongest cue (which way
     would you tilt your head to read it), then how the object naturally sits.
   - Record the call — never rotate a shipped file in place:
         python -m lib.photo_prep.orient <shoot-dir> --set "1=180,2=ccw,3=cw"
     It writes `<shoot-dir>/orientation.json` (degrees CW) and REBUILDS
     `no-exif/` from the source every time, so a wrong call cannot stack and a
     later strip_exif cannot silently revert a human's decision.
   - **Show the user the sheet and get their ruling BEFORE anything publishes.**
     They are looking at the real listings; you are not.
   - On a flat lay with objects at odds, no rotation makes everything upright.
     Pick the hero and SAY which secondary item stays inverted — that is a
     re-shoot issue, not a rotation one. Don't claim the frame is perfect.
2. **Backdrop cleanup — only for near-white/seamless backdrops** (NOT dark-felt
   shots; the corner-sampling logic is tuned for light backgrounds). Evens
   lighting/shadow, then trims the border to the subject.
       python -m lib.photo_prep.even_background <dir>            # -> uniform backdrop
       python -m lib.photo_prep.trim_whitespace <dir>           # -> <dir>/trimmed/
3. **Center-crop — always.** Centers the item on its focal point at an
   eBay-friendly aspect (square 1:1 default). Finds the subject by
   background-contrast (works on dark felt AND light box interiors) and unions
   ALL foreground pieces so a pair/set stays whole.
       python -m lib.photo_prep.center_crop <dir> --check        # per-photo verdict + reason
       python -m lib.photo_prep.center_crop <dir> --apply        # recenter in place (backs up to .orig/)
   `--apply` overwrites in place so DRAFT's lexicographic photo picker uses the
   centered files with no other change; default (no `--apply`) writes to
   `cropped/`. Tunables: `--aspect 4:5`/`orig`, `--pad 0.12`, `--threshold` (flag
   cutoff, default 6%). Writes a `crop_review.jpg` before|after contact sheet.
   The tool REFUSES a crop it can't make safely (subject fills the frame, nothing
   detected, detector locked onto a logo/fragment, or the crop would cut the
   item): it prints `SKIPPED <reason>`, passes the original through untouched,
   and labels that row on the review sheet. Expect a lot of skips on macro-heavy
   shoots — that's the tool working. `--force` crops anyway; don't, unattended.

Chaining: each tool reads a dir and writes a subdir — point the next step at the
previous output. See [[feedback_center_crop_before_draft]].

</details>

**Check the photos before drafting (per [[feedback_crop_check_first]] ethos):**
eyeball `<shoot-dir>/.prep/prep_review.jpg`. PREP self-skips the known misfires
and prints the reason on each row, but that is a backstop, not a substitute — a
crop can be merely ugly rather than unsafe, and a look can be wrong for the item
without any rule catching it. Never draft on a bad pass: re-run PREP, `--pick`
the other look, or record a different rotation. The approval that PREP's gate
wants is the user's, on that sheet.

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

## Style guide (optional overlay — OFF unless the run turns it on)

A **style guide** under [`../styleguides/`](../styleguides/README.md) is a study
of how one good seller lists — title slot order and budgets, body skeleton,
voice. It is **off by default**. Load one only when the run names it ("use the
patinaelements style guide") or a batch config sets `style_guide: <slug>`; then
read `../styleguides/<slug>.md` and apply its **DRAFT — titles** and
**DRAFT — description voice** sections while composing.

It changes *how we say it*, never *what we may claim*. Every rule above and
below still binds: INVESTIGATE remains the sole source of claims, no
`[BEST-CASE]` language reaches copy, defects survive any trim, expected age
gets one neutral clause, PII stays redacted (and disclosed), and the field
constraints hold. **On any conflict, the house rule wins** — drop the guide's
pattern, note it in `meta.notes`, and move on. Never reproduce a studied
seller's title strings or sentences; the guide carries technique, not text.

If a guide is loaded, say so in `meta.notes` (`style_guide: <slug>`) so the
review card shows which listings were drafted under an overlay.

**The patinaelements guide is no longer an overlay** — its title pattern and
body skeleton were adopted as house style on 2026-08-24 and are written into
the two sections below, claim bar intact. The overlay mechanism stays for the
*next* seller we study.

## Source-of-truth mapping

Never invent. Never copy IDENTIFY `[BEST-CASE]` markers into copy — only
repeat a claim INVESTIGATE confirmed.

**Top-level:**
- `template_version` `v1` (verbatim) · `meta.item_id` from shoot-dir name
  · `meta.shoot_dir` path · `meta.drafted_at` UTC ISO-8601 ·
  `meta.ebay_*`/`last_synced` unchanged null · `meta.notes` = DRAFT NOTES.
- `title` — best fitting INVESTIGATE title claim; apply unit-type
  phrasing; ≤80. **Build it to the house title pattern below** — a short
  title is a wasted title.
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

**Net-floor check (MANDATORY — the floor is a price we AGREE to, not a
formality).** An auto-decline figure is a standing offer to sell at that number,
so before writing it, compute what we'd actually keep if a buyer hit it exactly:

    net_at_floor = floor − fee(floor) − our_postage

using PRICE's **measured fee band** (16–18% depending on size — see
[price.md](price.md); it is NOT 13%) and the REAL postage for this item
(Media Mail only if it's a book with no advertising; magazines and anything with
ads ship Ground Advantage at 2–3× the cost). If `net_at_floor` is implausibly
thin for the handling — packing, a trip to the mailbox, and the return risk —
**raise the floor until it isn't**, and say so in `meta.notes`.

This exists because it was gotten wrong: two magazine lots went live with floors
set from the Recommended tier under the old 13% fee assumption, and at those
floors an accepted offer netted **$13.46 and $9.67** on heavy Ground Advantage
parcels. Both were raised at the REPORT phase (2026-08-15). The failure mode is
specific — a low floor looks generous and costs nothing until someone takes it.

Postage weighs most on cheap heavy things, so the check bites hardest exactly
where the old rule was loosest: sub-$50 lots of paper.

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

**Carrier-check gate — FedEx vs USPS (SOFT — suggest, never auto-switch).**
Default carrier is the USPS policy. But at DRAFT, evaluate whether this item
should go FedEx instead. **Trigger the FedEx check when ANY of these is true:**
- estimated **packed weight > 5 lb**, OR
- **any side > 24 in** (long/oversized), OR
- **very fragile** (glass, thin porcelain, etc.), OR
- **item value > $200** (price or working price).

When triggered, do this:
1. **Estimate the FedEx cost** for the packed weight/dims to the buyer (assume an
   average CONUS zone ~4–5 unless a real destination is known; FedEx Home
   Delivery / Ground Economy). Note it's an ESTIMATE (no live FedEx rate API
   here) and show the assumptions (weight, dims, zone). The account's FedEx
   policy is **`fulfillment_policy_id` 292460878014** ("FedEx SmartPost / Ground
   Economy") — reference it by id.
2. **Add the DRIVE COST to FedEx's effective cost.** The seller's nearest FedEx
   drop-off is **~30 min away (≈1 hr round-trip)**; USPS is picked up free from
   home, so USPS pays no drive. Factor the round-trip drive (gas + the seller's
   time) into FedEx's total so a small label saving that the drive eats up does
   NOT count as cheaper. State the drive adjustment you used.
3. **Compare FedEx (label + drive) vs the USPS estimate.** Recommend the **FedEx
   policy** only when FedEx is genuinely cheaper after the drive, OR when USPS
   can't handle the item (oversize / >70 lb). Otherwise keep USPS.
4. **Never auto-switch.** Suggest it and let the user choose. If they accept,
   set the offer's fulfillment policy to the FedEx id at LIST time (a `--review`/
   publish-time policy override); log the decision + the two estimates in
   `meta.notes`, and append a NEEDS_REVIEW line so it surfaces at REVIEW.

**Always, on the DRY RUN** (`--list`/`--publish` without `--confirm`, and in the
REVIEW card): if the FedEx estimate (incl. drive) is **less than USPS** for this
item, SURFACE the FedEx estimated price and recommend switching — even if the
item didn't hit a trigger above. The user wants to see any FedEx saving before
publishing.

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

## The house title pattern

Measured, not guessed: 245 active listings from a seller worth matching,
and the pattern held in every category sampled — jewelry, collectibles,
glass, antiques, clothing (see
[`../styleguides/_studies/patinaelements.md`](../styleguides/_studies/patinaelements.md)).
Adopted as house style 2026-08-24.

**Slot order** — era → object/maker → material → distinguishing detail →
measurement → unit phrase. Era leads (their mean era slot is 0.4, and
97% of titles carry an era word); material sits mid-title around slot 6.

**Fill the field.** Target 75–80 characters. Their median is 78 and 89%
run ≥75. Stop only when the next true keyword will not fit — never
because the title "reads finished" at 55.

**Budget ~2 descriptors** (their mean is 1.8, max 4). Past that it reads
as keyword soup, and every adjective spent is a searchable noun lost.

**Measurement earns a slot** where size is a buying decision — 56% of
their antiques titles carry one, 37% of glass, 0% of clothing. Include
it when we have a measured number, not an estimate.

**Casing:** sentence/title case, with ALL-CAPS reserved for one or two
tokens that genuinely carry weight (a maker, a model). Whole-title caps
is shouting, not emphasis. **Separators:** prefer `/` for alternatives,
`-` sparingly; no pipes, no bullets, no decorative characters.

Every slot is still governed by the claim bar. Era, maker, and material
enter the title only where INVESTIGATE supports them — an empty slot is
correct when the evidence is not there. **We never buy a slot with a
claim we cannot defend**, and condition/scarcity words (`MINT`, `RARE`)
do not get the lead slot on our listings even though the studied seller
gives them one.

## Markdown body (description)

Compose from INVESTIGATE's claim set only, in this **fixed section
order**. The skeleton is house style as of 2026-08-24 — it is the spine
the studied seller uses on 100% of their bodies, and a body missing a
section reads as a thinner listing:

- **Hook** (1–2 sentences) from INVESTIGATE Summary, buyer-facing.
- **What's Included** — bullets from observable components; unit-type
  vocabulary.
- **Size** — the measured dimensions, plus weight where it matters to
  the buyer. **Required whenever we have a measurement.** If nothing was
  measured, say so plainly rather than dropping the section.
- **Condition** — factual, minimal. Bullets enumerating EVERY defect
  INVESTIGATE flagged (no minimizing), each as `<location>: <defect>`.
  At most one short context line for expected vintage wear; no warm
  framing sentence, no marketing. Defects always survive any trim.
- **Markings** — maker's mark, signature, hallmark, stamp, or label:
  what it says and where it is, keyed to the photo that shows it.
  **Required.** When there is none, `No maker's mark found.` is the
  correct content — absence is information a buyer wants, and it is also
  the honest alternative to implying attribution we cannot defend.
- **About this item** — 1–2 sentence collector hook from INVESTIGATE's
  listing-approach.

**Length:** aim ~120–160 words (their median body is 133 across 245 listings). Short
paragraphs, ~20 words per sentence. A body under ~100 words is usually a
section left empty — check which one before shipping it.

**Voice stays ours:** sentence case (never all-caps bodies), neutral
catalog voice, no shop boilerplate, no cross-sell line, no payment-terms
block. We took their structure, not their delivery.

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
