# IDENTIFY — v4, Function 1

Obeys [`_shared.md`](_shared.md) (style, confidence, fresh-investigation,
unit_type, char limits, persistence, gate contract). Read it first.

**Output:** `<shoot-dir>/identify.txt` (overwrite).

Enumerate the distinct items across a shoot's photos and write one
structured record each. Speculative-upward: surface best-case identity so
the user can see upside, with the verification path attached. Rationale +
history: [reference/identify-notes.md](reference/identify-notes.md).

## Photo intake (read the decisive set, not every frame)

Full-res photos are the biggest token cost in a shoot. A single-item shoot
is usually decided by 4–6 frames: the **hero/full-form** shot, the
**underside/backstamp/mark** shot(s), **1–2 detail** shots, any
**ruler/scale** frame. Skip near-duplicate angles. Coverage still wins where
it matters: on wide/group shoots read enough to enumerate every item, and
never skip a frame that shows a mark you must read (see "Hunt every mark").
Glance at the filename list to pick; fully read only what you need.

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
live tiers instead of collapsing to one verdict:

- **GOOD** — an *optimistic* floor; never "filler" by default.
- **BETTER** — the named-maker / upgraded read, with the cue supporting it.
- **BEST** — the trophy read if the tells align; stays `[BEST-CASE]` +
  verify, but is STATED, not buried.

Grade to the TOP of the supported band — a clean surface in adequate photos
is the top grade, not a hedged "pending". Damage must be *visible* to cost
grade.

When you genuinely cannot tell which tier AND it swings value, do **not**
silently pick the low one. **Poll the user with the SPECIFIC discriminating
question(s)** the specialization names — interactive: a stop-and-ask (same
gate as the maker-mark stop below); headless: degrade to the SOFT path
(`needs_followup_photo` + `NEEDS_REVIEW.md`); single-pass: same stop, written
to `.single_pass/ask.json` ([single_pass.md](single_pass.md)). Ask only what the photos can't
answer — settle everything you can read yourself; never offload an expert
read (pattern, seam, ribbons-vs-patch) onto the user.

## Category specializations (load expert knowledge on a match)

After forming an item's first-pass Category, check the registry table in
[`../specializations/README.md`](../specializations/README.md): match the
item against each module's **Triggers**. On a hit, **read that module and
apply it** before settling the item's fields. A loaded module supplies the
maker/type taxonomy and tells, the named high-value types, the category's
condition vocabulary, the authentication tests, and a **value tier**:

- settle Brand / Type / Era / Condition with specialist precision;
- record the module's **value tier** in Distinguishing marks (so
  PRICE/CURATE know which items deserve the exact-comp hunt);
- set `needs_followup_photo` to the SPECIFIC inspection shot the module
  names when the confident call needs a shot the photos don't show.

A specialization **refines, never overrides** the honesty rules and the
maker-attribution discipline below. No trigger match → general pass as
normal.

> ⛔ **Crop gate (marbles, bulk/group).** Before identifying a multi-marble
> shoot, generate per-marble crops + the numbered contact sheet
> (`tools/marble_triage.py <dir> --crops-only --expect N`), **show the user
> the contact sheet, and STOP** — begin IDENTIFY only on their go-ahead.
> HARD interactive stop; headless degrades to self-verify-count + log to
> `NEEDS_REVIEW.md`; single-pass keeps it HARD, via `.single_pass/ask.json`
> ([single_pass.md](single_pass.md)). See the ⛔ CROP GATE block in
> [`../specializations/marbles.md`](../specializations/marbles.md).

> 🚫 **CLIP/forum-index DISABLED for IDENTIFY (marbles).** The forum CLIP
> index and every tool that queries it are OFF for IDENTIFY —
> `lib/marble_index.py`, `verify_batch` / `marble_triage` /
> `marble_matches` / `marble_colormatch` / `marble_refset` — and the forum
> expert-answer text is not used to set the maker. Not for the maker/type,
> not as an escalation, not as a colour-match "lead," not for
> corroboration. The maker/type comes from **photos + the module's tells
> ALONE**, staying firmly-named or `Unknown`/`[BEST-CASE]`. Human
> reference-panel triangulation and WebFetch of reference photos remain
> allowed. Run the CLIP/forum tools ONLY when the user EXPLICITLY asks, as
> a separate step outside the record. Full rule: the 🚫 block in
> [`../specializations/marbles.md`](../specializations/marbles.md).
> In-specialization marbles only; other categories unchanged.

## Maker / brand attribution (work it HARD — high leverage)

Brand is the single highest-leverage field: a confirmed maker changes the
PRICE tier, enables the EXACT maker+pattern comp hunt, sharpens title SEO,
and informs authenticity. `Unknown` is a LAST RESORT after a real
attribution attempt. Run this pass on every item before settling Brand:

**Stop-and-ask gate (mark-likely categories, interactive runs).** The gate
categories — an editable list; grow it as you learn:

- **Jewelry** — incl. gemstone & costume (maker / karat / assay / stone
  marks; clasp, gallery, inner band).
- **Precious metals** — silver/sterling, gold, platinum (hallmarks,
  assay/karat marks, EPNS/EP, maker roundels + pattern numbers).
- **Glass** — art, pressed, cut, studio (pontil, acid-etched, or sticker
  marks).
- **Pottery / ceramics / porcelain** — backstamps, impressed/painted marks,
  pattern names/numbers, country-of-origin wording.

When an item is in a gate category AND a mark is **plausibly present but
not decisively readable from the photos**, **STOP and ask the user to read
the inside/underside marking before you spend on searches/Lens or settle
Brand.** The user is holding the piece; their close-read beats any web or
Lens guess. Ask specifically — name the surface and what to look for ("Can
you read the mark on the base of the silver pot? Any lion/letters,
'STERLING'/'EPNS', or a number?"). Resume with whatever they report; only
if they decline or can't read it fall through to research → Lens → (last
resort) Unknown.

**Exception (skip the stop).** A gate category is a *default* to stop, not
an absolute. If THIS piece is plainly mass-produced / unmarked / low-value
such that a mark is genuinely unlikely AND a maker wouldn't move value or
SEO — a plain modern drinking glass, a generic unmarked terracotta pot —
you MAY proceed without asking. Log the carve-out in one line
(`exception: <why a mark is unlikely here>`). In doubt → stop and ask: a
missed mark is the most expensive thing to leave on the table.

HARD stop in an **interactive** run. **Headless** degrades to the SOFT
path: `needs_followup_photo: yes` naming the exact macro shot, log the
question to `NEEDS_REVIEW.md`, proceed with the pass below. **Single-pass
mode** ([single_pass.md](single_pass.md)) keeps this a HARD stop — same
trigger — but writes the question to `.single_pass/ask.json` instead of
pausing the chat.

1. **Hunt every mark.** Scan ALL surfaces — base/underside, foot rim, back,
   lid underside, inside rim, handle/spout joins, seams, stickers/labels.
   Any mark, stamp, signature, hallmark, trademark, pattern/model number,
   size code, or registry mark MUST be addressed (read it, decode it, or
   state present-but-illegible). Never silently skip a visible mark.
2. **Decode it by type.**
   - **Silver/metal:** distinguish GENUINE assay hallmarks (lion passant =
     English sterling, town + date letters) from a MAKER's mark, from
     EPNS / EP / "quadruple plate" / "silver on copper" plate marks, from
     pattern + size numbers, and from DECORATIVE pseudo-hallmarks. Assay →
     solid silver + origin/date; trademark roundel + pattern number → a
     specific plate maker (often catalog-matchable).
   - **Ceramics/porcelain/pottery:** backstamp, impressed mark, painted
     mark, pattern name/number; country-of-origin wording dates it
     ("England" vs "Made in England", post-1891 rule, "Occupied Japan").
   - **Other:** logos, model numbers, union/care labels, date codes.
3. **Research the mark.** Reference knowledge + web search to MATCH a
   partial or stylized mark to a maker (silver-mark encyclopedias,
   pottery-mark references, pattern-number catalogs). A pattern/size number
   plus a trademark shape often names the maker even when the legend is
   worn.
4. **Commit at the right confidence.** Research yields a likely maker →
   `Brand: <maker> [BEST-CASE]` + Scenario bracket + the exact resolution
   step. Only a legible, matched mark earns a maker WITHOUT `[BEST-CASE]`.
   A genuinely markless piece, or a fantasy pseudo-hallmark with no maker
   name, is honestly `Unknown` — say which it is. Reach
   `Unknown`/`Unbranded` only AFTER genuinely working at least two SPECIFIC
   maker candidates — name each, rule it out against an observable — never
   as a first-pass shrug. Note the candidates and why each failed.

**Honesty guard (non-negotiable):** trying harder is NOT license to invent.
Never promote a guess to a stated maker; never read a maker into
fantasy/pseudo marks. The bar for a named maker is a real, decodable
signal; for `[BEST-CASE]`, a mark whose style/elements genuinely point to
one. A present-but-unresolvable mark → `needs_followup_photo: yes` with the
SPECIFIC shot (raking-light macro) and the downstream value impact.

5. **Visual second opinion (Google Lens) — markless or can't-place
   pieces.** When the maker is unresolved AND the piece is plausibly
   collectible/branded, get an independent read before settling Brand.
   Choose the photo(s) by goal — never default to the wide hero:
   - **Design match** → the clearest **full-form** shot (in focus, minimal
     background).
   - **Mark match** → if step 1 found a mark (usually underside/back), send
     that **close-up too**: Lens OCR can *read* a maker name off it.
     **Caveat (verified):** OCR reads **printed/painted** marks; it
     routinely **can't read low-contrast EMBOSSED METAL stamps** (returns
     "No results"). For a pressed-metal stamp, **your own step-1 close-read
     is the authority** — don't read Lens's empty result as "no mark".

   **Cap at 2–3 images, each earning its place:** full-form (design), mark
   close-up (OCR — only if a readable mark exists), at most one more for a
   genuinely distinctive feature. Never dump all photos — near-duplicates
   add cost and noise, and a bad crop can mislead Lens.

       # markless / metal-marked — design + AI mode, NO OCR (cheaper):
       python lib/lens_id.py <shoot-dir>/<full-view>.jpg --no-ocr
       # readable printed/painted mark — add the close-up + OCR:
       python lib/lens_id.py <shoot-dir>/<full-view>.jpg <shoot-dir>/<mark-shot>.jpg

   The CORE is always the **"what is this?"** read off the full-form shot —
   it needs no mark. OCR is an add-on for readable marks only; OCR failing
   never blocks the design result. The tool prints a **verdict + maker
   tally + any mark/OCR read**. Weigh it — never obey it:
   - **`MARK READ (OCR): <Maker>`** → strongest signal. Confirm the reading
     against the photo, then `Brand: <Maker>` (a verified read mark is
     evidence — no `[BEST-CASE]`).
   - **`leans <Maker>`** (recurs across matches AND fits what you see) →
     `Brand: <Maker> [BEST-CASE]` + bracket, note "Lens-corroborated".
   - **`split across makers` / `widely-copied`** → stay
     `Unbranded`/`Unknown`; the names are later keywords, NOT a claim. The
     common, honest result — never promote a stray mention. Lens is
     evidence about the *design*, not proof of *this* piece's maker.
   - Record `Lens cross-check: <verdict>` on the item and reconcile with
     your own call in one line (agree / conflict / refined).

   **Gate (cost + privacy):** RUN only when attribution is genuinely
   uncertain AND would move value/SEO. SKIP for legibly-marked items,
   obvious commodities, lots of generics — it sends the photo to Google via
   a temp public host (auto-expiring) and spends ~$0.02–0.05 of Apify
   credit. Needs direct egress (tmpfiles + api.apify.com); in a blocked
   sandbox, host via the lib and drive the actor through the Apify MCP.

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
- **Brand** — run the maker-attribution pass FIRST. Legible/matched mark →
  the maker; style points to a likely maker → `value [BEST-CASE]` +
  bracket; genuinely markless or fantasy-marked → `Unknown` (last resort).
  ≤65.
- **Type** — specific descriptor. Same marker convention. ≤65.
- **Era** — date/range. `[ASSUMPTION]` if inferred from styling. ≤65.
  Directory context (`_shared.md`) may narrow the range — a household's
  accumulation window is a prior, never a date on the item itself.
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
- **Ship risk** — `none` or `suggest-pickup`. Set `suggest-pickup` when
  estimated weight **> 25 lb** OR any dimension **> 24 in** on a side, with
  a short reason (`too heavy ~40 lb`). Only a *signal* for DRAFT's
  local-pickup suggestion — IDENTIFY never decides fulfillment. Can't
  estimate → leave `none` and rely on `needs_followup_photo`. (Fragility
  the user names is honored at DRAFT; the auto-trigger here is purely
  weight/size.)
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

Default every record to `single`. When photos suggest a grouping, surface a
question in "Needs user confirmation" AND append it to NEEDS_REVIEW.md —
then keep going. Patterns worth surfacing: functional unit (chess
board+pieces), mounted/cased/framed (lean toward `set`), matched pair (2
identical), matched set (3+ identical), provisional group (alike but not
cleanly enumerable → `needs_followup_photo: yes`), identical duplicates (N
copies). Different brands never merge.

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
