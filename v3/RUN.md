# RUN — v3 headless runbook

The single entry point. To run the pipeline on a shoot, read THIS file
plus [`prompts/_shared.md`](prompts/_shared.md), then load each phase
prompt on demand as you reach it. You do not need to read all phase
prompts up front.

**Goal:** carry a shoot from photos to a review-ready artifact with no
human babysitting — stopping only at the two HARD gates below.

---

## Invocation

The user points at a photo directory and (optionally) names a workflow
mode. That directory is the shoot directory; all outputs land there.

    plan  <photos-dir>   → IDENTIFY → PRICE → CURATE        (pre-buy: buy list)
    list  <photos-dir>   → INVESTIGATE → DRAFT              (post-buy: listing)
    full  <photos-dir>   → all five in order

If no mode is given: a single-item or grouped shoot of items the user
already owns → `list`; a wide field/estate scene → `plan`. State the
inferred mode in your first line and proceed (it's a SOFT call).

Run phases in order. Each phase reads the prior phase's file from the
shoot directory, so phases compose without re-deriving.

---

## The gate contract (the whole point of headless)

Reclassify every "ask the user" moment as HARD or SOFT.

### HARD gates — stop the run

1. **Publish to eBay.** Never. Refuse and explain (firewall). Not even
   if the user says to — that requires a deliberate code+prompt refactor.
2. **Paid Apify call (PRICE Source C).** Confirm cost before each call:
   "About to spend ~$0.12 on an Apify query for `<query>`. Approve,
   change, or skip?" Apify is opt-in only — never run by default.

These are the ONLY two reasons to stop a headless run.

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

**Working price is NOT a HARD gate.** PRICE still records "final price
deferred to publish time", but headless flow auto-adopts the Recommended
tier as the working price so DRAFT can complete. The final published
price remains the user's call — nothing publishes regardless.

### NEEDS_REVIEW.md format

Append (don't overwrite) one line per deferred decision:

    [PHASE] <shoot-item> — <decision taken> · <what the user could change>

Example:

    [IDENTIFY] items 2-3 — kept as 2 singles (default); possible brass-candlestick pair
    [PRICE] iron — adopted Recommended $48 as working price; no exact comp, era-peer anchor
    [INVESTIGATE] iron — committed "Size 5 sad iron"; maker stamp not photographed

At the end of a run, if `NEEDS_REVIEW.md` has entries, surface the count
in your closing line ("3 items need review"). That's the async review
queue — the user reads it when convenient instead of being interrupted.

---

## Phase pointers

Load each prompt when you reach its phase.

| Phase | Prompt | Reads | Writes |
|---|---|---|---|
| IDENTIFY | [prompts/identify.md](prompts/identify.md) | photos | `identify.txt` |
| PRICE | [prompts/price.md](prompts/price.md) | `identify.txt` | `price.txt` |
| CURATE | [prompts/curate.md](prompts/curate.md) | `identify.txt`+`price.txt`+profile | `review.md` |
| INVESTIGATE | [prompts/investigate.md](prompts/investigate.md) | photos (+`identify.txt`) | `investigate.txt` |
| DRAFT | [prompts/draft.md](prompts/draft.md) | `identify.txt`+`investigate.txt`+`price.txt`+template | `draft.md` |

**Post-pipeline (manual trigger only, NOT in the automated run):**

| Step | Prompt | Reads | Effect |
|---|---|---|---|
| LIST/EDIT | [prompts/list_edit_chrome.md](prompts/list_edit_chrome.md) | `draft.md`+`price.txt` | eBay **DRAFT** listing (never published) |

Function 6 pushes an approved `draft.md` into an eBay draft via Chrome.
It runs ONLY when the user explicitly asks ("push to eBay draft"), one
item at a time — never as part of `full`. The no-publish firewall applies:
terminal action is "Save for later", never "List it".

Cross-cutting depth rules:
- Condition analysis in IDENTIFY and INVESTIGATE uses
  [prompts/condition-rubric.md](prompts/condition-rubric.md).
- PRICE runs the autonomous exact-match hunt (free Sources A+B) before
  any era-peer fallback; see its prompt.

Shared rules (style, confidence, firewall, unit_type, char limits,
persistence) live in [prompts/_shared.md](prompts/_shared.md).

Python infrastructure (config, eBay client, Apify wrapper, photo prep)
is unchanged and shared from `v2/lib/` — v3 does not duplicate code.

---

## Headless run sequence (full mode)

1. Resolve shoot dir + mode (state inferred mode in one line).
2. IDENTIFY → write `identify.txt`. Log any grouping questions to
   NEEDS_REVIEW; do not stop.
3. PRICE each saleable item → run the exact-match hunt on free sources;
   adopt Recommended tier as provisional working price; write
   `price.txt`. Stop ONLY if you reach a paid-Apify decision.
4. CURATE (plan mode) → write `review.md`.
5. INVESTIGATE (list mode), per item → commit to the confident
   assessment; log open questions; write `investigate.txt`.
6. DRAFT (list mode) → render template, run the pre-write validation
   pass, write `draft.md`.
7. Closing line: outputs written + NEEDS_REVIEW count + the one headline
   fact per artifact. Nothing is published; LIST/EDIT remains manual.
8. (Optional, on user request) LIST/EDIT → eBay draft via
   [prompts/list_edit_chrome.md](prompts/list_edit_chrome.md). Still a
   draft; the user publishes manually in Seller Hub.
