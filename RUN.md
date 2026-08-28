# RUN — v4 headless runbook

The single entry point. To run the pipeline on a shoot, read THIS file
plus [`prompts/_shared.md`](prompts/_shared.md), then load each phase
prompt on demand as you reach it. You do not need to read all phase
prompts up front.

**Goal:** carry a shoot from photos to a review-ready artifact with no
human babysitting — stopping only at the HARD gates below. In `list`/`full`
mode the run carries through to a **REVIEW gate**: it presents a decision
card and stops for one explicit human approval, which is what publishes the
listing LIVE.

---

## Invocation

The user points at a photo directory and (optionally) names a workflow
mode. That directory is the shoot directory; all outputs land there.

**Resolving a bare name — always look in `inventory/` first.** When the user
says just "run `<name>`" (e.g. "run sand-dollars"), the shoot directory is
**`inventory/<name>/`** — start there before anywhere else. `inventory/` is the
default content store ("our" data: photos + per-item phase outputs; gitignored,
never version-controlled). Only treat `<name>` as a different location if it
isn't found under `inventory/`, or the user gives an explicit path.

    plan  <photos-dir>   → IDENTIFY → PRICE → CURATE                 (pre-buy: buy list)
    list  <photos-dir>   → PREP(gate) → INVESTIGATE → DRAFT → REVIEW(gate)→publish
    full  <photos-dir>   → all in order, ending at the REVIEW gate

If no mode is given: a single-item or grouped shoot of items the user
already owns → `list`; a wide field/estate scene → `plan`. State the
inferred mode in your first line and proceed (it's a SOFT call).

**Single phase vs. the sequence.** "run" defaults to *run the steps in
sequence* — a bare name or a mode keyword (`plan`/`list`/`full`) runs the
pipeline. But a **phase keyword runs ONLY that one prompt and stops** (it does
NOT continue to the next phase):

    identify    <name>   → only IDENTIFY     ([prompts/identify.md](prompts/identify.md))    → identify.txt
    prep        <name>   → only PREP         ([prompts/prep.md](prompts/prep.md))            → listing/ (HARD gate)
    price       <name>   → only PRICE        ([prompts/price.md](prompts/price.md))          → price.txt (+ comps.csv)
    curate      <name>   → only CURATE       ([prompts/curate.md](prompts/curate.md))        → review.md
    investigate <name>   → only INVESTIGATE  ([prompts/investigate.md](prompts/investigate.md)) → investigate.txt
    draft       <name>   → only DRAFT        ([prompts/draft.md](prompts/draft.md))          → draft.md (+ --record)
    review      <name>   → only REVIEW       ([prompts/review.md](prompts/review.md))        → review_card.md + review_card.html (HARD gate)
    report               → only REPORT       ([prompts/report.md](prompts/report.md))        → performance numbers (+ docs/performance-<date>.md)

**REPORT takes no shoot name** — it is account-wide, not per-item. "report",
"how are we doing", "what did we actually make", "what sold this week" all land
here. It is the only phase that reads eBay's *outcomes* rather than producing a
listing, and it never publishes or edits anything.

So "run identify sand-dollars" (or just "identify sand-dollars") runs IDENTIFY
alone on `inventory/sand-dollars/` and stops — it does not roll on to PRICE. A
single phase reads its normal inputs (the prior phases' files already in the
shoot dir) and writes only its own output; use it to re-run or fix one step
without redoing the whole pipeline. The same `<name>` → `inventory/<name>/`
resolution applies.

Run phases in order. Each phase reads the prior phase's file from the
shoot directory, so phases compose without re-deriving.

---

## The gate contract (the whole point of headless)

Reclassify every "ask the user" moment as HARD or SOFT.

### HARD gate — the only thing that stops a run

1. **The REVIEW gate (publish).** In `list`/`full`, after DRAFT, run
   REVIEW: present the decision card ([prompts/review.md](prompts/review.md))
   and STOP. Publishing the listing LIVE happens ONLY on the user's
   explicit approval at this card. No automatic publish, no publish from a
   "just list it" said before the card, no publish inferred from
   "ok"/"looks good"/silence. (This replaced the old absolute no-publish
   firewall — publishing is now gated here, not forbidden.)

2. **The PREP photo gate — three stages, three approvals.** After IDENTIFY,
   PREP walks the operator through **orientation, then crop, then colour**, in
   that order, one sheet per stage, each sheet showing every option for that
   stage side by side as thumbnails. You do not move to the next stage until the
   user says the current one is right, and the code enforces it: a stage will not
   open until its predecessor is approved, approving a stage clears every later
   sign-off, and `--apply` refuses to write `listing/` until all three are in.
   Photos go live ONLY after the user approves. Unlike the maker-mark gate this does NOT degrade in a headless
   run — it halts, because a bad photo is the one error buyers see first and 66
   sideways ones shipped while the rules said otherwise. It is enforced in code
   too: `upload_photos_to_eps` refuses photos that are not prepped and approved,
   and an approval goes stale the moment a source or output file changes.
   Flip it to soft only when the user says so.

3. **The IDENTIFY maker-mark gate (interactive only).** In a gate category —
   jewelry, precious metals, glass, pottery/ceramics (editable list in
   [prompts/identify.md](prompts/identify.md)) — when a maker's mark is plausibly
   present but undecisive from the photos, IDENTIFY STOPS and asks the user to
   read the inside/underside marking before spending on searches/Lens or settling
   Brand. A clear no-mark-likely item (plain modern glass, generic terracotta)
   takes a logged exception and proceeds. This gate exists only when a human is
   at the keyboard; in a headless run (cron, `full`/`list` with no one to answer)
   it degrades to the SOFT path — `needs_followup_photo` + a `NEEDS_REVIEW.md`
   line — and the run proceeds.

**PREP and REVIEW both stop a headless run** — PREP because photos are the first
thing a buyer judges and the rules alone did not hold, REVIEW because it is what
authorises publishing. The maker-mark gate above is interactive-only and
degrades. PRICE's Apify call (Stage B) used to
be a second HARD gate; it no longer is — Apify runs automatically as part
of the comp hunt (`automation-lab/ebay-sold-scraper`, ~$0.10/run), no cost
confirmation. See PRICE for the Stage-A/B/C ordering and the currency-leak
validator on Apify.

### SOFT gates — proceed with the default, log it

Never block on these. Pick the documented default, append ONE line to
`<shoot-dir>/NEEDS_REVIEW.md`, continue.

| Situation | Default to take | Logged so the user can… |
|---|---|---|
| Photos suggest a pair/set/lot the user didn't name | keep `single` | confirm the grouping later |
| unit_type genuinely ambiguous (set vs lot) | choose the LOWER claim (`lot`; `single` over `pair`) | upgrade if warranted |
| INVESTIGATE open question (a feature not photographed) | commit to most-likely call without it | answer / re-shoot to firm up |
| No user-approved working price yet | adopt PRICE's **Recommended** tier as *provisional* working price | confirm/refine at publish |
| `lookup_only` value not canonical (e.g. "USA") | substitute closest canonical ("United States") | verify |
| Required field has no source data | leave empty, flag | fill it |
| Item below profit floor | route to CURATE SKIPPED with the math | override per category |
| Item ship-risky (>25 lb or >24 in/side, or fragile) | keep `SHIP`; suggest local pickup, log it | confirm local-pickup (attended: DRAFT asks before assuming) |

**Working price is NOT a HARD gate.** PRICE still records "final price
deferred to publish time", but headless flow auto-adopts the Recommended
tier as the working price so DRAFT can complete. The final published price
remains the user's call — it is confirmed (or changed) at the REVIEW gate
before anything goes live. Nothing publishes before that gate.

### NEEDS_REVIEW.md format

Append (don't overwrite) one line per deferred decision:

    [PHASE] <shoot-item> — <decision taken> · <what the user could change>

Example:

    [IDENTIFY] items 2-3 — kept as 2 singles (default); possible brass-candlestick pair
    [IDENTIFY] silver-pot — Brand left Unknown; mark present but illegible, needs base macro (maker-mark gate degraded, headless)
    [PRICE] iron — adopted Recommended $48 as working price; no exact comp, era-peer anchor
    [INVESTIGATE] iron — committed "Size 5 sad iron"; maker stamp not photographed

At the end of a run, if `NEEDS_REVIEW.md` has entries, surface the count
in your closing line ("3 items need review"). That's the async review
queue — the user reads it when convenient instead of being interrupted.

---

## Phase pointers

Load each prompt when you reach its phase.

## Concurrency and delegation

A multi-item batch is not one long conversation — it is one conductor plus
one worker per item. The trigger is a **multi-item batch**; a single item, or
an interactive back-and-forth about one piece, stays in the main thread.

**Conductor (main thread) holds only:** the batch manifest (which items,
which phase each is in), the gates, and ledger writes. It never holds
photos, comp JSON, forum matches or per-item research — those are what
blow the context window up.

**One `Agent` worker per item**, ≤4 concurrent, covering IDENTIFY → PRICE →
INVESTIGATE → DRAFT:

- fresh context per worker; it reads `prompts/_shared.md` plus the phase
  prompt it needs — not optional, see `_shared.md`'s standing rules;
- writes outputs to the shoot dir exactly as a foreground run would
  (`identify.txt`, `price.txt`, `comps.csv`, `investigate.txt`, `draft.md`);
- returns a **compact verdict only** — item name, proposed title, price +
  tier, the ⚠ list, and the path to `draft.md` — never its full reasoning.
  `python -m lib.cli status <shoot-dir>` is the one call to check any
  item's state (phase files, PREP gate, ledger row, next action) instead
  of re-deriving it from `ls`/`cat`.
- writes a short trace file into the item dir on failure, so a failed
  worker is inspectable after the fact — a verdict alone isn't a transcript.

**The gate queue, not a gate barrier.** REVIEW still stops and requires one
explicit approval per item — that discipline doesn't weaken. What changes is
that a finished item's card joins a queue instead of blocking every other
item: answer gates whenever convenient while other workers keep running.

**What may run in a worker vs. only in the conductor:**

| writes | parallel-safe? |
|---|---|
| `identify.txt`, `price.txt`+`comps.csv`, `investigate.txt`, `draft.md` (per-item dirs) | ✅ disjoint dirs |
| `.prep/`, `listing/` | ✅ disjoint dirs |
| `listings_ledger.csv` via `list_edit.py --record` | ❌ one shared unlocked CSV — **conductor only, serialized** |
| `--list --confirm` (publish) | ❌ conductor only, behind the REVIEW gate |

Two workers calling `--record` at once can lose a row — every ledger-touching
call belongs in the conductor.

**Background anything over ~30s.** `prep --auto` and any other runner that
takes tens of seconds (Apify comp hunts, Chrome browse) go to
`run_in_background`; keep working the item (or another item) while it
renders, collect the result when it lands. Don't block the whole run on one
runner that nothing downstream is waiting on yet.

## Ops commands — one entry point

Every account/ops tool runs through the `ebz` dispatcher (V4_PLAN Phase 3):

    python -m lib.cli                      # list the commands
    python -m lib.cli <command> [args...]

`reconcile` (ledger vs Sell API — eBay wins), `live-audit` (local files vs
live state, `--apply` to heal), `pick-list` (orders awaiting shipment),
`policy-sweep`, `price-audit` (asks above their own comp evidence),
`sales-report`, `promote`, `voice` (in-hand linter, `--audit` for a tree),
`listing` (the LIST/EDIT CLI), `prep`, `status` (one-call shoot state —
phase files, PREP gate, ledger row, next action; see "Concurrency and
delegation" above). Argv passes through untouched, so every flag documented
for a tool works identically under `ebz`. The direct `python tools/<x>.py` /
`python lib/<x>.py` invocations keep working.

## Locking a format

Every artefact passed between phases carries a version stamp — `template_version`
on the draft, `MANIFEST_VERSION` on `.prep/prep.json`, a fixed column order on
`listings_ledger.csv`, a fixed section list on the REVIEW card. A stamp on its
own does nothing; `tests/test_formats.py` is what makes it mean something. It
holds the exact field set of each format and fails the moment one changes.

**To change a format:**

1. make the change;
2. run `python tests/test_formats.py` — it will fail, and that is the point;
3. decide which kind of change it is:
   - **additive and safe** → add the field to the lock in the same commit, so
     the next reader can see when it appeared;
   - **breaking for readers** → bump the version stamp, teach the readers both
     shapes, then update the lock.

Never relax an assertion so it stops noticing. That converts a format change
from a decision into an accident.

The lock also carries two debts, as counts that may only go DOWN: 31 drafts
stamp `v1` with no `_field_constraints` block at all, and 21 carry a partial
one. Those are under-enforced — the validator checks fewer fields than it should
— but none of them disagrees with the template, and a rule that DISAGREES fails
outright with no tolerance.

| Phase | Prompt | Reads | Writes |
|---|---|---|---|
| IDENTIFY | [prompts/identify.md](prompts/identify.md) | photos | `identify.txt` |
| PREP | [prompts/prep.md](prompts/prep.md) | photos (+`identify.txt`) | `listing/` + `.prep/prep.json` (HARD gate: orientation → crop → colour, each approved) |
| PRICE | [prompts/price.md](prompts/price.md) | `identify.txt` | `price.txt` |
| CURATE | [prompts/curate.md](prompts/curate.md) | `identify.txt`+`price.txt`+profile | `review.md` |
| INVESTIGATE | [prompts/investigate.md](prompts/investigate.md) | photos (+`identify.txt`) | `investigate.txt` |
| DRAFT | [prompts/draft.md](prompts/draft.md) | `identify.txt`+`investigate.txt`+`price.txt`+template | `draft.md` + `--record` → SKU stamped + ledger row (DRAFTED) |
| REVIEW | [prompts/review.md](prompts/review.md) | `draft.md`+`price.txt`+`NEEDS_REVIEW.md` | `--review` → `review_card.md` (records+preflights) + `review_card.html` (the page the decision is made on) → (on approval) LIVE listing |
| REPORT | [prompts/report.md](prompts/report.md) | `sales_ledger.csv`+`listings_ledger.csv`+drafts | printed numbers (+ `docs/performance-<date>.md`) |

**REPORT closes the loop.** PRICE decides what to ask; REPORT measures what we
actually got, and feeds the gap back. Its two commands:

    python lib/sync_actuals.py --apply     # refresh actuals from the Fulfillment API
    python lib/report.py --performance     # fees, ask-vs-actual, speed, categories

`sync_actuals` exists because two local records are structurally incomplete:
the listings ledger only knows the ASK (an accepted Best Offer never writes
back), and the Inventory API is blind to anything listed by hand on eBay.com.
Orders are the only source that sees every sale and the price actually paid.

**The publish step (reached via the REVIEW gate, on explicit approval):**

| Path | How | Reads | Effect |
|---|---|---|---|
| LIST/EDIT (**primary**) | `python lib/list_edit.py --list <shoot-dir> --confirm` | `draft.md` | sync + **publish LIVE** via Sell API |
| Sync-only (no publish) | `python lib/list_edit.py --sync <shoot-dir>` | `draft.md` | eBay UNPUBLISHED offer only |
| LIST/EDIT (fallback) | [prompts/list_edit_chrome.md](prompts/list_edit_chrome.md) | `draft.md`+`price.txt` | eBay **DRAFT** via Chrome UI |

REVIEW (Function 5.5) is the gate that authorizes Function 6. The publish
command (`--list … --confirm`) runs ONLY after the user explicitly approves
the review card, one item at a time (batch only on "approve all"). The
firewall still holds against *automatic* publishing: the pipeline never
publishes, `--sync` only ever creates an UNPUBLISHED offer, and every
publish path keeps the `--confirm` code guard — the human's approval at the
gate is what authorizes passing it. A dry run is available any time
(`--list`/`--publish` without `--confirm`).

- **Primary — Sell API (`lib/list_edit.py`).** Headless, full-res photo
  upload (EPS), one-payload description (no missing-fields bug), idempotent
  re-sync. Verified end-to-end on sandbox. One-time setup:
  [lib/SETUP_EBAY_API.md](lib/SETUP_EBAY_API.md). Use this by
  default. `--validate <dir>` needs no credentials.
- **Fallback — Chrome stand-in.** Only when the API isn't set up. Subject to
  read-tier/sandbox limits and the debounce/trusted-input pitfalls
  documented in its prompt.

**Managing live listings (Function 6, on user request).** Beyond
publishing, `lib/list_edit.py` queries and manages any offer/SKU on the
account. Like publish, every mutation is **dry-run unless `--confirm`** and
runs ONLY when the user asks (never as part of the automated pipeline):

| Op | Command | Effect |
|---|---|---|
| Query | `--offers` | list every offer (status, offerId, listingId, price, sku) — read-only |
| Withdraw | `--withdraw-offer <id> --confirm` | end a LIVE listing; offer kept (UNPUBLISHED), re-publishable |
| Delete offer | `--delete-offer <id> --confirm` | delete an offer (permanent; ends listing if live; SKU kept) |
| Delete item | `--delete-item <sku> --confirm` | delete the inventory item + ALL its offers (permanent) |

Withdraw/delete are destructive and **HARD-gated like publish**: confirm the
exact target with the user (run the dry run first, show what it hits), and
only pass `--confirm` on an explicit yes. Use `--offers` to find IDs.

Cross-cutting depth rules:
- Condition analysis in IDENTIFY and INVESTIGATE uses
  [prompts/condition-rubric.md](prompts/condition-rubric.md).
- IDENTIFY loads a category **specialization** (expert field guide) when an
  item matches one — registry + modules in
  [specializations/README.md](specializations/README.md) (e.g. marbles).
- DRAFT and PREP can load an optional **style guide** — a studied seller's
  presentation technique (title slots, body skeleton, photo conventions) —
  registry + modules in [styleguides/README.md](styleguides/README.md). **Off
  by default**; a run turns one on by name. It is a stylistic overlay: house
  rules (honesty bar, wear phrasing, PII, stage contract) always win.
- PRICE runs the autonomous exact-match hunt (Stage A WebSearch → Stage B
  Apify eBay-sold → optional Stage C Chrome when confidence is low) before
  any era-peer fallback; see its prompt.

Shared rules (style, confidence, firewall, unit_type, char limits,
persistence) live in [prompts/_shared.md](prompts/_shared.md).

Python infrastructure (config, eBay client, Apify wrapper, photo prep)
is unchanged and shared from `lib/` — v3 does not duplicate code.

---

## Headless run sequence (full mode)

1. Resolve shoot dir + mode (state inferred mode in one line).
2. IDENTIFY → write `identify.txt`. Log any grouping questions to
   NEEDS_REVIEW; do not stop.
2b. PREP → `--auto` FIRST (one pass, no questions: every frame turned and every
   crop planned, conservatively — margin left around the item — and nothing
   approved). Show the widget of that best attempt, then one card
   (`python tools/prep_card.py <shoot>`) of the revised frames, and ask once:
   approve (`--approve-auto`, orientation + crop together) or override (open the
   staged flow below). Name every frame whose orientation was GUESSED.
   Override / anything the auto pass got wrong → `--check` (ORIENTATION ONLY — the crop and the colour reading are not
   measured until orientation is approved, so no crop box describes a rotation
   that could still change), then `python tools/prep_sheet_html.py <shoot>` and publish
   `.prep/review.html` as an artifact — that page IS the review surface for all
   three stages, not a JPEG and not a prose description. Card per frame, every
   option side by side, an override and a free-text box on every card, and an
   Accept button per stage. Then walk the three staged reviews in
   order, STOPPING for the user at each one (HARD gate). Never batch them into
   one question:
   `--stage orientation` (fix with `--set-rotate NAME=DEG`, the idempotent form) → `--approve-stage
   orientation` → `--stage crop` (`--crop NAME=off|on|padF`) → `--approve-stage
   crop` → `--apply` → `--stage color` (`--pick studio|punch`) →
   `--approve-stage color` → `--approve`. Photos land in `listing/`;
   INVESTIGATE still reads the originals.
3. PRICE each saleable item → run the exact-match hunt (Stage A WebSearch
   → Stage B Apify eBay-sold → Stage C Chrome only if confidence is low);
   adopt Recommended tier as provisional working price; write `price.txt`.
   Never stops.
4. CURATE (plan mode) → write `review.md`.
5. INVESTIGATE (list mode), per item → commit to the confident
   assessment; log open questions; write `investigate.txt`.
6. DRAFT (list mode) → point `photos:` at the prepped files
   (`prep --repoint-draft --apply-repoint`), render template, run the
   pre-write validation pass, write `draft.md`, then `python lib/list_edit.py --record <shoot-dir>`
   to stamp the item's SKU into the draft and create its lifecycle ledger
   record (status DRAFTED). Do this for EVERY draft, and again after any edit.
7. REVIEW (list mode) → `python lib/list_edit.py --review <shoot-dir>` — one
   command that records (if needed) + preflights + builds `review_card.md` —
   then `python tools/review_card_html.py <shoot-dir>` and deliver
   `review_card.html`. That page IS the review surface: the listing as a buyer
   meets it, the hero picker, and every ⚠ line — not the text card alone and
   never a prose summary. Present it with the card and STOP (HARD gate).
   Surface title + price + the ⚠ count.
8. On explicit approval at the card → `python lib/list_edit.py --list
   <shoot-dir> --confirm` (sync + publish LIVE); report the listing URL.
   On a change request → re-run the owning phase, re-render `draft.md`,
   re-run `--review`. Anything other than explicit approval = no publish.
