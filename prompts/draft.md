# DRAFT — v4, Function 5

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/draft.md` (overwrite).

A pure template-fill transform — no new identification, investigation, or
pricing. Read the prior phases' files, render the template, validate
against its constraints, write one self-contained local file. **Local
file only — no eBay calls, no publishing** (firewall, per _shared).
Rationale, history and the measurements behind the rules:
[reference/draft-notes.md](reference/draft-notes.md).

## Photos come from PREP — DRAFT does not touch them up

Photo preparation is its own phase: **[PREP](prep.md)**, run after IDENTIFY.
It writes `<shoot-dir>/listing/` and stops at a HARD approval gate. DRAFT's
job is only to point `photos:` at the prepped files:

    python -m lib.photo_prep.prep <shoot-dir> --repoint-draft --apply-repoint

That maps each existing entry to its prepped counterpart **in the same
order** — entry one is the eBay gallery image and the list is often not
lexicographic.

If PREP has not been run, stop and run it; do not fall back to the old tools
(one-off exceptions in the notes). `upload_photos_to_eps` enforces this in
code, so an unprepped or unapproved shoot cannot publish regardless of what
this prompt says.

**Check the photos before drafting:** eyeball
`<shoot-dir>/.prep/prep_review.jpg`. PREP self-skips the known misfires and
prints the reason on each row, but that is a backstop — a crop can be merely
ugly rather than unsafe, and a look can be wrong for the item without any
rule catching it. Never draft on a bad pass: re-run PREP, `--pick` the other
look, or record a different rotation. The approval PREP's gate wants is the
user's, on that sheet.

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

A **style guide** under [`../styleguides/`](../styleguides/README.md) is a
study of how one good seller lists. **Off by default.** Load one only when
the run names it or a batch config sets `style_guide: <slug>`; then apply its
**DRAFT — titles** and **DRAFT — description voice** sections while
composing.

It changes *how we say it*, never *what we may claim*: INVESTIGATE remains
the sole source of claims, no `[BEST-CASE]` language reaches copy, defects
survive any trim, expected age gets one neutral clause, PII stays redacted
(and disclosed), and the field constraints hold. **On any conflict, the house
rule wins** — drop the guide's pattern, note it in `meta.notes`, move on.
Never reproduce a studied seller's title strings or sentences; a guide
carries technique, not text. If a guide is loaded, say so in `meta.notes`
(`style_guide: <slug>`).

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
**Category-REQUIRED aspects must still be emitted** even if INVESTIGATE
didn't itemize them and even when unbranded — eBay rejects the publish
otherwise (errorId 25002 "item specific <X> is missing"). A loaded
specialization names its category's required aspects in its Output hooks
(e.g. jewelry rings require `Metal` + `Metal Purity` + `Ring Size`); emit
those into `item_specifics.extra` from INVESTIGATE/IDENTIFY. When unsure
which a category requires, prefer including the obvious ones over a failed
publish.

**pricing:** `format` FIXED_PRICE (AUCTION only if price.txt says) ·
`price` = working price as string, ≤13 · `cost_of_goods` null ·
`quantity` per the table below. Best Offer per the gate below.

**Best Offer gate (default):** Best Offer is only worth enabling when the
list price sits ABOVE the supported price, so there's headroom to negotiate
down to it.

- Compare the list `price` to PRICE's **Recommended** tier (from price.txt).
- **If `price` > Recommended**: `best_offer.enabled` **true** ·
  `best_offer.auto_decline_amount` = **the Recommended tier price**, rounded
  to the nearest whole dollar, as a string. (Fallback if price.txt has no
  Recommended tier: 85% of list, nearest dollar.)
- **If `price` ≤ Recommended**: `best_offer.enabled` **false**, both amounts
  null — don't invite offers below the supported price.
- `best_offer.auto_accept_amount` is **always null** — never auto-accept;
  the user reviews and accepts offers manually.
- Log the gate decision + computed auto-decline in `meta.notes`.

**Net-floor check (MANDATORY — the floor is a price we AGREE to, not a
formality).** An auto-decline figure is a standing offer to sell at that
number. Before writing it, compute what we'd actually keep if a buyer hit it
exactly:

    net_at_floor = floor − fee(floor) − our_postage

using PRICE's **measured fee band** (16–18% depending on size — see
[price.md](price.md); it is NOT 13%) and the REAL postage for this item
(everything ships Ground Advantage on the single fulfillment policy — never
price a book at the media rate; ad-carrying paper was never Media Mail
eligible, per DMM 173.4.2). If `net_at_floor` is implausibly thin for the
handling — packing, a trip to the mailbox, and the return risk — **raise the
floor until it isn't**, and say so in `meta.notes`. The check bites hardest
on cheap heavy things: sub-$50 lots of paper.

**shipping:** weight/dims from IDENTIFY (round up); `free_shipping` true →
`domestic_shipping_type` FREE_FLAT_RATE; `primary_service` per Service
map; `handling_time_days` 1; `item_location_zip` blank. **Set
`fulfillment_mode` per the Local-pickup gate below** — default `SHIP`.

**Local-pickup gate (SOFT — suggest, never assume).** A `LOCAL_PICKUP` draft
is a *disclosure*, not a shipping configuration: the account's
local-pickup-only policy was deleted 2026-08-25, so the offer still lists on
the single fulfillment policy (with a warning from `list_edit.py`) and shows
parcel shipping to buyers. Say so at REVIEW. Decide `fulfillment_mode`:

- **Default `SHIP`.**
- **Set `LOCAL_PICKUP` only when the user has indicated it** — they said so
  for this item ("local only", "too fragile to ship"), or you asked and they
  confirmed.
- **When to ASK (and you must ask before assuming):** IDENTIFY's **Ship
  risk** is `suggest-pickup` (weight > 25 lb or any side > 24 in), OR the
  item is clearly fragile (stained/blown glass, thin porcelain): *"This
  looks <reason> — risky to ship. List as local pickup (freight by quote),
  or ship normally?"* **Attended:** ask and honor the answer. **Headless:**
  keep `SHIP` (the safe non-blocking default) and append a NEEDS_REVIEW line
  so it's raised again at REVIEW. Never silently flip an item to
  pickup-only.
- **When `LOCAL_PICKUP`:** `fulfillment_mode: LOCAL_PICKUP`,
  `free_shipping: false`, `domestic_shipping_type` blank, `primary_service`
  blank; still record weight/dims (useful for a freight quote).
  `local_pickup.location_hint` from the seller's city/region if known. Add a
  short closing line to the body: *"Local pickup only — <location_hint>. Not
  local? Message me for a freight quote."* Log the decision (+ trigger) in
  `meta.notes`.

**Carrier: there is no carrier choice.** Every item ships on the one
fulfillment policy, `296458692014` ("Free USPS Ground + eBay International
Shipping", free calculated USPS Ground Advantage, 1 day handling). Do not
propose a carrier switch, do not estimate a rival carrier's cost, and do not
override the policy at LIST time. Weight and dimensions are still worth
recording — they drive the calculated rate and a freight quote. If an item
genuinely cannot go USPS (oversize / >70 lb), say so in `meta.notes` and
append a NEEDS_REVIEW line rather than selecting a different policy.

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
**Media Mail is narrower than it looks.** Books (bound, 8+ pages), sheet
music, printed music/test materials, sound/video recordings, and computer
media → `USPSMediaMail`. **Periodicals — magazines, newspapers, catalogs,
mailers, any paper carrying advertising — are EXCLUDED from Media Mail**
(DMM 173.4.2) and route to `USPSGroundAdvantage`, at 2–3× the postage
(25–35% of a typical paper-lot sale price — feed that into the net-floor
check above). A "book" that is really an ad-carrying catalog is a
periodical, not a book. Other ≤15 lb non-media → `USPSGroundAdvantage`.
>15 lb / oversized → `UPSGround`. freight/movers → blank + flag (needs a
quote) — also a strong local-pickup candidate (gate above).

## The house title pattern

Adopted as house style 2026-08-24, measured across a studied seller's 245
active listings (numbers in the notes).

- **Slot order** — era → object/maker → material → distinguishing detail →
  measurement → unit phrase. Era leads; material sits mid-title.
- **Fill the field.** Target 75–80 characters. Stop only when the next true
  keyword will not fit — never because the title "reads finished" at 55.
- **Budget ~2 descriptors** (max 4). Past that it reads as keyword soup, and
  every adjective spent is a searchable noun lost.
- **Measurement earns a slot** where size is a buying decision (antiques,
  glass — not clothing). Include it when we have a measured number, not an
  estimate.
- **Casing:** sentence/title case; ALL-CAPS reserved for one or two tokens
  that genuinely carry weight (a maker, a model). **Separators:** prefer `/`
  for alternatives, `-` sparingly; no pipes, no bullets, no decorative
  characters.
- **The lead slot goes to a condition or scarcity word when one is earned**,
  ahead of the era word — it is the first word a scrolling buyer reads.
  Earn it like this:
  - `MINT` / `NOS` / `EXCELLENT` — from the condition grade INVESTIGATE
    assigned via [condition-rubric.md](condition-rubric.md). The rubric
    decides the word; the title just carries it. Grade to the top of the
    supported band — a clean piece is Mint.
  - `RARE` / `SCARCE` / `HTF` — only where INVESTIGATE found actual scarcity
    evidence (a low production figure, a short production window, a thin
    comp field after a real hunt). "I haven't seen another" is not evidence,
    and neither is a high asking price.
  - `FINE` — quality of make, when the piece supports it.
- No word is earned → **the era word leads instead**, and that is a normal
  outcome, not a failure. Era, maker and material enter the title only where
  INVESTIGATE supports them; an empty slot is correct when the evidence is
  not there. We lead as hard as the evidence allows and not one word harder.

## Markdown body (description)

Compose from INVESTIGATE's claim set only, in this **fixed section order**
(house skeleton as of 2026-08-24 — a body missing a section reads as a
thinner listing):

- **Opener** — ONE sentence, ~20 words, naming the item in full: era,
  maker, material, object. The title expanded into a sentence — same claim
  set, no new evidence. First person (see Voice).
- **The look-at-this line** — one sentence on why it is worth a look: the
  feature that makes it, how it displays, what it does. Enthusiasm is
  allowed here; **claims are not smuggled in with it**. "This displays
  beautifully" is taste and always fine; "this is a rare piece" is a
  scarcity claim and needs the same evidence the title's `RARE` needs.
- **The cross-sell line** — "This is one of several … I'm listing this
  week, so please take a look at my other listings." Include it **only when
  it is true**. One-off item → drop the line rather than invent a batch.
- **What's Included** — bullets from observable components; unit-type
  vocabulary.
- **Size** — measured dimensions, plus weight where it matters to the
  buyer. **Required whenever we have a measurement.** If nothing was
  measured, say so plainly rather than dropping the section.
- **Condition** — factual, minimal. Bullets enumerating EVERY defect
  INVESTIGATE flagged (no minimizing), each as `<location>: <defect>`. At
  most one short context line for expected vintage wear; no warm framing,
  no marketing. Defects always survive any trim. Close the section with the
  photos line — "Please see the photos and read the description for full
  details" — it sets the expectation that the photos are part of the
  disclosure; it never substitutes for naming a defect in words.
- **Markings** — maker's mark, signature, hallmark, stamp, or label: what
  it says and where it is, keyed to the photo that shows it. **Required.**
  When there is none, `No maker's mark found.` is the correct content —
  absence is information, and the honest alternative to implying
  attribution we cannot defend.
- **About this item** — 1–2 sentence collector hook from INVESTIGATE's
  listing-approach.
- **The close** — the standing block, same on every listing, from
  `store.closing_block` in [`../config.yaml`](../config.yaml). Boilerplate
  on purpose. Render it verbatim; **do not compose it per listing**, do not
  add claims to it. If a line stops being true, the fix is config, not a
  listing. When `store.display_name` is set, the sign-off names the store;
  when empty, the unnamed thank-you ships — never invent a brand name.

**Length:** aim ~130–180 words. Short paragraphs, ~20 words per sentence. A
body under ~100 words is usually a section left empty — check which one
before shipping it.

**Voice: first person, ours.** Write as the seller — "I picked this up from
a local estate", "I haven't tested it". Two things do not follow it into our
copy:

- **Sentence case, never an all-caps body.** Caps is shouting, harder to
  read on a phone, and flattens the emphasis we do want.
- **First person is not a licence to speculate.** "I think this might be
  Georgian" is exactly the guess the claim bar exists to stop. What I say in
  my own voice still has to be something INVESTIGATE established. Where I
  genuinely don't know, say that plainly and say what I did to check.

**In-hand voice — never from behind the camera** (house rule, adopted
2026-08-26). The listing speaks as a seller holding the item. Language
phrased from the camera's point of view is banned in every buyer-visible
field (title, body, `condition_description`, item specifics):

- **camera framing** — "visible in the photos", "shown/pictured",
  "as-shown", "photographed surfaces", "undersides not photographed";
- **photo-limit confessions** — "not identifiable/verifiable from the
  photos", "can't be assessed from the pictures";
- **inspection-process narration** — enumerating tests not run ("not
  shake-tested", "ring test not performed", "odor not verified").

This is our internal evidence process leaking into the copy: it tells the
buyer the seller never handled the item, and it manufactures doubt instead
of preventing returns. **Rephrase rule: state the finding, never the
method.** "No chips or cracks visible" → "No chips or cracks noted." "Knife
handle seated tight in photos; not shake-tested" → "Knife handle sits
tight." A photo-only observation ("UPC not shown in the photographed
surfaces") is an internal note for `meta.notes` / NEEDS_REVIEW, never buyer
copy.

Untested status survives ONLY where it sets the grade (electronics /
mechanical function, per condition-rubric): phrased plainly — "Untested;
sold as-is." — with no photo excuse attached. Two things the rule does NOT
touch: the internal record (IDENTIFY/INVESTIGATE still log every
can't-assess — this rule governs only what reaches the buyer), and the
standing close line "Please see the photos and read the description for full
details" (it points the buyer at the photos as disclosure; it doesn't
confess the inspection was photo-only). Defects themselves all still
survive — the rule strips the camera frame off a disclosure, never the
disclosure.

Hard rules: never include a claim absent from INVESTIGATE's listing-safe /
observable lists; never anything INVESTIGATE marked NOT defensible; never
IDENTIFY `[BEST-CASE]` language; honor unit-type phrasing.

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
substitute + log). Also scan every buyer-visible field for camera-frame
language (the in-hand-voice rule above) and rephrase any hit to the
finding. Only then write draft.md. Re-read after write and confirm every
constrained field fits and `_field_constraints` was copied verbatim.

## meta.notes (DRAFT NOTES)

One terse line per non-straight-copy decision: title source + char count;
condition grade + tie-break; weight/dims rounding; price tier + source;
photo order; any rephrase (field, orig→final length); any lookup
substitution. These stay local — never pushed to eBay.

## Register the item (SKU + ledger record)

**Whenever you finish writing `draft.md` — and again after any later edit
to it — register the item:**

    python lib/list_edit.py --record <shoot-dir>

This computes the item's **SKU** (a deterministic 8-hex hash of title +
folder — no eBay call, no credentials), stamps it into the draft's
`meta.ebay_inventory_sku`, and creates/refreshes the item's ledger row with
status **DRAFTED**. Idempotent — once the SKU is stamped it's reused, so an
edit refreshes the row and never duplicates it. The same row is later
updated in place to SYNCED → PUBLISHED → ENDED/DELETED. This guarantees the
record exists from draft time, **before REVIEW**; `--review` and `--sync`
also call `--record` internally, so a missed run is recovered.

## Closing

Per _shared: path + chosen title with `[N/80]` + working price. List
flagged gaps / substitutions as one-line bullets. Don't restate
frontmatter. In `list`/`full` mode, REVIEW ([review.md](review.md)) runs
next — it turns this draft into the decision card and stops for approval
before anything publishes.
