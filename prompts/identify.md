# IDENTIFY — v3, Function 1

Obeys [`_shared.md`](_shared.md) (style, confidence, fresh-investigation,
unit_type, char limits, persistence, gate contract). Read it first.

**Output:** `<shoot-dir>/identify.txt` (overwrite).

Enumerate the distinct items across a shoot's photos and write one
structured record each. Speculative-upward: surface best-case identity so
the user can see upside, with the verification path attached.

## Photo intake (read the decisive set, not every frame)

Full-res photos are the biggest token cost in a shoot, so read the frames that
decide the record — not every angle. A single-item shoot is usually decided by
4–6: the **hero / full-form** shot, the **underside / backstamp / mark** shot(s),
**1–2 detail** shots (condition, a distinguishing feature), and any **ruler /
scale** frame. Skip near-duplicate angles — they add cost, not evidence.
Coverage still wins where it matters: on wide/group shoots read enough to
enumerate every item, and never skip a frame that shows a mark you must read
(see "Hunt every mark"). Glance at the filename list to pick; fully read only
what you need.

## Shoot mode

One mode per shoot. Source: CLI flag → profile default → auto-detect.
State the active mode + its source in the SHOOT SUMMARY.

- **wide** — mixed-item scene (estate/yard sale). Enumerate each item;
  order top-to-bottom, left-to-right across the primary photo.
- **single** — one item, many angles/states. Produce ONE record using all
  angles for maximum confidence. Multiple configs (lid on/off, open/shut)
  are still one item. Resolve `[BEST-CASE]` markers outright when an angle
  confirms them — drop the marker when the proof is in a photo.
- **group** — many items of one category laid out. One record each; do
  NOT cross-dedup unless they're identical duplicates (then collapse to
  `duplicate, qty=N`).
- **multi-angle** — mixed scene from multiple angles. Dedup the same
  physical item across angles, then enumerate.
- **auto** (default) — pick the best-fit mode from the photos and say so.

## Best-case identification

When brand/maker/era/material can be reasonably inferred from real visual
cues but not confirmed without inspection: use the **best-case inference
as the value** + `[BEST-CASE]` marker (e.g. `Brand: Hudson's Bay
[BEST-CASE]`, not a generic hedge). Attach a Scenario bracket.

Marker discipline (per _shared confidence rule):
- `[BEST-CASE]` — inference changes value tier. Pair with a Scenario
  bracket. Cap 3 scenarios; drop sub-15% tails; bracket only on material
  value swing.
- `[ASSUMPTION]` — approximate but same value tier (e.g. "1970s,
  mid-to-late"). No bracket.
- `Unknown` — no real basis. Not a license to invent the priciest identity.

### Good / Better / Best + poll when uncertain

Lead with the upside, and make the downgrade EARN it. Where a specialization
provides a **Good / Better / Best** ladder (e.g. marbles), present all three
live tiers on the item instead of collapsing to one verdict:

- **GOOD** — an *optimistic* floor (what it is even if nothing special confirms);
  never "filler" by default.
- **BETTER** — the named-maker / upgraded read, with the cue that supports it.
- **BEST** — the trophy read if the tells align; stays `[BEST-CASE]` + verify,
  but is STATED, not buried.

Grade to the TOP of the supported band — a clean surface in adequate photos is
the top grade, not a hedged "pending". Damage must be *visible* to cost grade.

When you genuinely cannot tell which tier the item lands on AND it swings value,
do **not** silently pick the low one. **Poll the user with the SPECIFIC
discriminating question(s)** the specialization names — in an *interactive* run
this is a stop-and-ask (same gate as the maker-mark stop below); in a *headless*
run it degrades to the SOFT path (log them to `needs_followup_photo` +
`NEEDS_REVIEW.md`). Ask only for what the photos can't answer — settle from the
photos everything you can read yourself; never offload an expert read (pattern,
seam, ribbons-vs-patch) onto the user.

## Category specializations (load expert knowledge on a match)

Some categories have a dedicated **specialization module** — a self-contained
expert field guide under [`../specializations/`](../specializations/). After you
form an item's first-pass Category, check the registry table in
[`../specializations/README.md`](../specializations/README.md): match the item's
category / material / keywords against each module's **Triggers**. On a hit, the
item is *in specialization* — **read that module file and apply it** before
settling this item's fields.

A loaded module gives you, for that item: the maker/type taxonomy and visual
tells, the named high-value types to watch for, the category's own condition
vocabulary, the authentication/fake tests, and a coarse **value tier**. Use it
to:

- settle Brand / Type / Era / Condition with specialist precision;
- record the module's **value tier** in Distinguishing marks (so PRICE/CURATE
  know which items deserve the exact-comp hunt vs which are filler);
- set `needs_followup_photo` to the SPECIFIC inspection shot the module names
  (e.g. a pontil/seam macro) when the confident call needs an inspection the
  photos don't show.

A specialization **refines, never overrides** the honesty rules and the
maker-attribution discipline below: still no inventing, still `[BEST-CASE]` +
scenario bracket for value-swing inferences, still the maker-mark gate. If no
module's triggers match, proceed with the general pass as normal.

## Maker / brand attribution (work it HARD — high leverage)

Brand is the single highest-leverage field: a confirmed maker changes the
PRICE tier, lets PRICE hunt an EXACT maker+pattern comp, sharpens the title
SEO, and informs authenticity. So `Unknown` is a LAST RESORT after a real
attribution attempt — never a lazy default. Run this pass on every item before
settling Brand:

**Stop-and-ask gate (mark-likely categories, interactive runs).** Some
categories almost always carry a maker's mark that swings value hard. These are
the **gate categories** — the editable list that arms the stop-and-ask (grow it
as you learn; "for now" it is):

- **Jewelry** — incl. gemstone & costume pieces (maker / karat / assay / stone
  marks, usually on the clasp, gallery, or inner band).
- **Precious metals** — silver/sterling, gold, platinum (holloware, flatware,
  plate; hallmarks, assay/karat marks, EPNS/EP, maker roundels + pattern
  numbers).
- **Glass** — anything made of glass (art, pressed, cut, studio; pontil,
  acid-etched, or sticker marks).
- **Pottery / ceramics / porcelain** — backstamps, impressed/painted marks,
  pattern names/numbers, country-of-origin wording.

When an item is in a gate category AND a mark is **plausibly present but you
cannot decisively read it from the photos** (illegible, obscured, or on a
surface this shoot doesn't show), **STOP and ask the user to read the
inside/underside marking before you spend on searches/Lens or settle Brand.**
The user is holding the piece; their close-read of the mark beats any web or
Lens guess and is the cheapest path to a confirmed maker. Ask specifically —
name the surface and what to look for ("Can you read the mark on the base of the
silver pot? Any lion/letters, 'STERLING'/'EPNS', or a number?"). Resume the pass
below with whatever they report; only if they decline or genuinely can't read it
do you fall through to research → Lens → (last resort) Unknown.

**Exception (skip the stop).** A gate category is a *default* to stop, not an
absolute. If THIS specific piece is plainly mass-produced / unmarked / low-value
such that a maker's mark is genuinely unlikely AND a maker wouldn't move
value or SEO — a plain modern drinking glass, a generic unmarked terracotta pot,
a strand of costume beads — you MAY proceed without asking. Log the carve-out in
one line (`exception: <why a mark is unlikely here>`) so the skip is auditable.
When genuinely in doubt, stop and ask: a missed mark is the most expensive thing
to leave on the table.

This is a HARD stop in an **interactive** run. In a **headless** run (no user to
ask) it degrades to the SOFT path: set `needs_followup_photo: yes` naming the
exact macro shot, log the question to `NEEDS_REVIEW.md`, and proceed with the
attribution pass below.

1. **Hunt every mark.** Scan ALL surfaces for any mark — base/underside, foot
   rim, back, lid underside, inside rim, handle/spout joins, seams, stickers/
   labels. A mark, stamp, signature, hallmark, trademark, pattern/model number,
   size code, or registry mark MUST be addressed (read it, decode it, or state
   it is present-but-illegible). Never silently skip a visible mark.
2. **Decode it by type.**
   - **Silver/metal:** distinguish GENUINE assay hallmarks (e.g. lion passant =
     English sterling, town + date letters) from a MAKER's mark, from EPNS / EP
     / "quadruple plate" / "silver on copper" plate marks, from pattern + size
     numbers, and from purely DECORATIVE pseudo-hallmarks. Each tells you
     something: assay → solid silver + origin/date; trademark roundel + pattern
     number → a specific plate maker (often catalog-matchable).
   - **Ceramics/porcelain/pottery:** backstamp, impressed mark, painted mark,
     pattern name/number; country-of-origin wording dates it ("England" vs
     "Made in England", post-1891 "country" rule, "Occupied Japan", etc.).
   - **Other:** logos, model numbers, union/care labels, date codes.
3. **Research the mark.** Use reference knowledge and web search to MATCH a
   partial or stylized mark to a maker (silver-mark encyclopedias, pottery-mark
   references, pattern-number catalogs). A pattern/size number plus a trademark
   shape is often enough to name the maker even when the legend is worn.
4. **Commit at the right confidence.** If research yields a likely maker, write
   `Brand: <maker> [BEST-CASE]` + a Scenario bracket + the exact resolution
   step (the macro shot or test that would confirm). Only a legible, matched
   mark earns a maker WITHOUT `[BEST-CASE]`. A genuinely markless piece, or a
   purely decorative fantasy pseudo-hallmark with no maker name, is honestly
   `Unknown` — say which it is. Reach `Unknown`/`Unbranded` only AFTER you have
   genuinely worked at least two SPECIFIC maker candidates — name each, then rule
   it out against an observable — never as a first-pass shrug. Note the
   candidates you considered and why each failed so the rejection is auditable.

**Honesty guard (unchanged, non-negotiable):** trying harder is NOT license to
invent. Do not promote a guess to a stated maker, and do not read a maker into
fantasy/pseudo marks to inflate value. The bar for a named maker is a real,
decodable signal; the bar for `[BEST-CASE]` is a mark whose style/elements
genuinely point to one. When the mark is present but you cannot resolve it, set
`needs_followup_photo: yes` with the SPECIFIC shot (a raking-light macro of the
mark) and note the downstream value impact — an unread mark is the most
expensive thing to leave on the table.

5. **Visual second opinion (Google Lens) — for markless or can't-place pieces.**
   When the maker is unresolved (markless, OR a mark you couldn't read) AND the
   piece is plausibly collectible/branded, get an independent read before
   settling Brand. Lens does TWO jobs, so **choose the photo(s) by goal — never
   default to the wide hero shot:**
   - **Design match** → the clearest **full-form** shot (in focus, minimal
     background). Finds the same-shaped piece across the web.
   - **Mark match** → if step 1 found a **mark / stamp / signature / label**
     (usually on the **underside or back**), send that **close-up too**: Lens
     OCR can *read* a maker name off it, which beats any look-alike. A wide hero
     shot is useless for this — the underside is the decisive photo.
     **Caveat (verified):** Lens OCR reads **printed/painted** marks (paper
     labels, painted backstamps, ink) — it routinely **can't read low-contrast
     EMBOSSED METAL stamps** (silver/pewter/buckles/jewelry; it returns "No
     results"). For a pressed-metal stamp, **your own step-1 close-read is the
     authority** — don't wait on Lens, and don't read its empty result as "no
     mark". (lens_id flags this: verdict says "rely on your own close-read".)

   You examined every photo in this pass, so YOU pick — deliberately, by reason.
   **Cap at 2–3 images, each earning its place:** the full-form shot (design),
   the mark/underside close-up (OCR — only if a mark exists), and at most one
   more ONLY for a genuinely distinctive feature (a unique finial, a signature
   panel). Never dump all the photos or pick at random — near-duplicate angles
   add cost and noise, not signal, and a wrong crop (a blurry edge, heavy
   background) can mislead Lens. `lib/lens_id.py` takes those images in one run
   (~pennies each):

       # "what is this?" — markless / metal-marked piece, design + AI mode, NO OCR (cheaper):
       python lib/lens_id.py <shoot-dir>/<full-view>.jpg --no-ocr
       # mark present AND printed/painted (readable) — add the mark close-up + OCR:
       python lib/lens_id.py <shoot-dir>/<full-view>.jpg <shoot-dir>/<mark-shot>.jpg

   The CORE is always a **"what is this?"** read (visual match + AI mode) off the
   full-form shot — it does NOT need a mark. **OCR is an add-on:** include it
   (default) only when you're sending a **readable printed/painted** mark; use
   **`--no-ocr`** otherwise (markless pieces, or an embossed metal stamp where
   OCR returns nothing) for a cheaper mark-free pass. OCR failing never blocks
   the "what is this?" result.

   It hosts each photo at a temp public URL, runs the Lens actor, and prints a
   **verdict + a maker tally across distinct visual matches + any mark/OCR read**.
   Weigh it — never obey it:
   - **`MARK READ (OCR): <Maker>`** → strongest signal: Lens read a maker name
     off the mark. Confirm the reading is legible/correct on the photo, then
     write `Brand: <Maker>` (a confirmed read mark is real evidence, not a guess
     — no `[BEST-CASE]` needed once you verify it).
   - **`leans <Maker>`** (recurs across several matches AND fits what you see) →
     `Brand: <Maker> [BEST-CASE]` + scenario bracket, note "Lens-corroborated".
   - **`split across makers` / `no single maker — widely-copied`** → stay
     `Unbranded`/`Unknown`; the names are later keywords, NOT a claim. This is
     the common, honest result — do NOT promote a single stray Lens mention into
     an attribution (the same failure as inventing a maker from a fantasy mark).
     Lens is evidence about the *design*, not proof of *this* piece's maker.
   - Record a `Lens cross-check: <verdict>` line on the item and reconcile it
     with your own visual call in one line (agree / conflict / refined).

   **Gate (cost + privacy):** RUN only when attribution is genuinely uncertain
   AND would move value/SEO. SKIP for legibly-marked items, obvious low-value
   commodities, and lots of generics — it sends the photo to Google via a
   temporary public host (auto-expiring) and spends ~$0.02–0.05 of Apify credit.
   Needs direct egress (tmpfiles + api.apify.com); in a blocked sandbox, run with
   egress on, or host via the lib and drive the actor through the Apify MCP.

## Output format

    SHOOT SUMMARY
    Photos processed: <N>
    Shoot mode:       <mode> (<source>)
    Distinct items:   <N>
    Photo caveats:    <one line, or "none">
    Needs user confirmation:
      <one line per grouping question, or "none". Records are written as
      single defaults; the question is also appended to NEEDS_REVIEW.md —
      this never blocks (SOFT gate).>

Then one block per item, `--- Item <N> ---`, fields in this order:

- **Unit type** — `single`/`pair`/`set`/`lot`/`duplicate`. Default
  `single` (see _shared). Only a user instruction/answer changes it.
- **Quantity** — integer. 1 for single, 2 for pair, piece count for
  set/lot, copy count for duplicate.
- **Category** — general bucket (bike, magazine, chess set). Uncapped.
- **Brand** — run the maker-attribution pass above FIRST. Legible/matched mark
  → write the maker; mark whose style points to a likely maker → `value
  [BEST-CASE]` + bracket; genuinely markless or fantasy-marked → `Unknown`
  (last resort, after the pass). ≤65.
- **Type** — specific descriptor. Same marker convention. ≤65.
- **Era** — date/range. `[ASSUMPTION]` if inferred from styling. ≤65.
- **Collectability** — collectable / vintage / antique / modern /
  `none (not for sale)`.
- **Condition** — per [`condition-rubric.md`](condition-rubric.md):
  bullet defects with location + severity; one line on what can't be
  assessed. Not prose.
- **Estimated weight** — item weight (not packed). `~X lb (tier — N–N
  lb)`; tiers: light <5 / medium 5–15 / heavy 15–25 / oversized 25–50 /
  freight 50+ / requires-movers. Range when unsure. Set/pair → total.
  `unknown — needs full view` if not estimable.
- **Estimated dimensions** — item size (not packed). Box `~L×W×H in`;
  cylinder `~dia×h in`; furniture `~W×D×H in`; flat `~L×W in`; irregular
  free-text w/ key measurements. Range when unsure.
- **Ship risk** — `none` or `suggest-pickup`. Set `suggest-pickup` when the
  item is awkward/unsafe to parcel-ship: estimated weight **> 25 lb** OR any
  estimated dimension **> 24 in (2 ft)** on a side. Add a short reason
  (`too heavy ~40 lb` / `> 2 ft long (38 in)`). This is only a *signal* for
  DRAFT's local-pickup suggestion — IDENTIFY never decides fulfillment. If you
  can't estimate weight/dims, leave `none` and rely on `needs_followup_photo`.
  (Fragility the user names is honored at DRAFT too, but the auto-trigger here
  is purely weight/size.)
- **Distinguishing marks** — free-text, uncapped catch-all: cover text,
  dated copyright, photographer credit, model number, materials, the rich
  context that does NOT fit the capped fields above.
- **needs_followup_photo** — yes/no; if yes, what shot is needed.
- **Scenario bracket** — ONLY when a field carries `[BEST-CASE]`. Omit
  entirely otherwise. Format:

      Scenarios (best → worst): [max 3, material swing only]
        1. <id>: <value-tier note>
        2. <id>: <note>
      How to distinguish:
        - <observable/test>: <which scenarios it resolves>
      Price-tier swing: low / moderate / significant

## Grouping (informs questions; never auto-groups)

Default every record to `single`. When photos suggest a grouping,
surface a question in "Needs user confirmation" AND append it to
NEEDS_REVIEW.md — then keep going. Patterns worth surfacing: functional
unit (chess board+pieces), mounted/cased/framed (lean toward `set`),
matched pair (2 identical), matched set (3+ identical), provisional group
(alike but not cleanly enumerable → set `needs_followup_photo: yes`),
identical duplicates (N copies). Different brands never merge.

When the user later confirms, re-run or update the affected records in
place with the new `unit_type` + `quantity`.

## Honesty

- Never invent. Every value is observed, `[BEST-CASE]`+bracket,
  `[ASSUMPTION]`+reason, or `Unknown`.
- Mark `needs_followup_photo: yes` instead of guessing a shaky ID.
- Items not for sale (notes, packaging, background furniture) are still
  enumerated, marked `none (not for sale)` so CURATE filters them.

## Closing

Per _shared house style: output path + item count + any NEEDS_REVIEW
count. Don't restate the file.
