# SINGLE-PASS — v4, routine-item fast path (V4_PLAN Phase 4, #30)

Obeys [`_shared.md`](_shared.md). Read it first. Builds on PREP's confidence
gate (#36, PR #37) — the same "ask only about what you can't resolve" pattern,
extended across the whole chain.

**Not a sixth stage.** Every rule in [`identify.md`](identify.md),
[`prep.md`](prep.md), [`price.md`](price.md), [`investigate.md`](investigate.md)
and [`draft.md`](draft.md) applies exactly as written — same evidence, same
honesty bar, same defaults. Single-pass mode changes ONE thing: whether a
routine item's four (five, counting INVESTIGATE — see below) stages run as
one sitting with a single card at the end, or as separate conversational
turns each waiting on a reply that a routine item never needed.

**Checked by:** `python -m lib.cli single-pass <shoot-dir>` — a read-only
gate check ([`lib/single_pass.py`](../lib/single_pass.py)) across whatever
each stage has already written. It does not identify, price, or draft
anything; it reports OK or names the one thing blocking.

## The order

IDENTIFY → PREP(gate) → PRICE → INVESTIGATE → DRAFT, then ONE review card.
(V4_PLAN's issue text shorthands this "PREP→IDENTIFY→PRICE→DRAFT"; the real
dependency order is RUN.md's `list`-mode sequence — DRAFT hard-requires
`investigate.txt`, so INVESTIGATE is in the chain even though the issue's
one-liner didn't name it. Nothing about the order changes — this is a
correction to the shorthand, not a new rule.)

## Run every stage straight through — don't narrate the routine

Do the stage's normal work and write its normal output file. Don't stop to
report a SOFT-gate default (`_shared.md`'s gate contract: grouping questions,
unit_type ambiguity, `needs_followup_photo`, the provisional working price,
lookup substitutions, the local-pickup suggestion) — log it to
`NEEDS_REVIEW.md` exactly as headless already does, and move to the next
stage without a chat turn in between.

## The one thing that stops it — a stage's OWN HARD stop, unchanged

Three conditions already exist as **interactive-only HARD stops** — nothing
here invents a fourth:

1. **IDENTIFY's maker-mark gate** — a gate-category item (jewelry, precious
   metals, glass, pottery/ceramics) with a mark plausibly present but not
   decisively readable (identify.md, "Stop-and-ask gate").
2. **IDENTIFY's GOOD/BETTER/BEST poll** — genuinely can't tell which tier and
   it swings value (identify.md, same gate as #1).
3. **The marble CROP gate** — a bulk/group marble shoot's contact sheet needs
   a look before IDENTIFY starts ([`../specializations/marbles.md`](../specializations/marbles.md)).

Interactively, each of these pauses the chat. **Under single-pass, write the
ask instead of pausing**, then stop that item's work — the trigger is
identical, only the delivery changes:

    <shoot-dir>/.single_pass/ask.json
    {"stage": "identify", "detail": "<the exact question you'd have asked>"}

(A list of objects if more than one item in the shoot needs one.) `python -m
lib.cli single-pass <shoot-dir>` reads this file back and reports it verbatim
— it never guesses an answer to keep the chain moving, and it never advances
past the stage that wrote it. Once the user answers, finish that stage
normally (the file is a record, not a lock — overwrite or clear it on the
next attempt) and re-run `single-pass` to continue.

**PREP needs no sentinel file** — its confidence gate already writes
everything single-pass needs into `.prep/prep.json` (`orientation.guessed`,
`orientation.needs_ask`, an un-overridden crop refusal). Nothing to add there;
`lib/single_pass.py` reads the manifest PREP was already writing.

**PRICE and DRAFT never write this file.** PRICE is explicitly autonomous
("no step gates or stops" — price.md) and DRAFT's only hard stop is a missing
`investigate.txt`, which the stage order above already prevents reaching
prematurely. Nothing in either stage's judgment changes.

## When every stage is clean

`single-pass` builds the ONE review card — the same card and the same page
REVIEW always builds (`list_edit.py --review` + `review_card_html.py`;
[`review.md`](review.md)). Present it exactly as REVIEW does and **STOP**.
Nothing below this line is different from any other run:

- **The REVIEW gate is untouched.** No approval, no publish, from anything
  single-pass did. Silence, "ok", or "looks good" against the card still
  means nothing; the explicit-approval rule ([`_shared.md`](_shared.md)
  "Publish firewall") is exactly as strict as it always was.
- **Everything the Ground rules protect stays exactly where it is:** honesty
  rules, condition disclosure, approval digests, the REVIEW gate. Single-pass
  mode's whole surface area is *when a question gets asked*, not *whether
  one gets answered honestly* — if extending this pattern to a stage ever
  seemed to require touching one of those, that is a stop, not a workaround:
  say so and leave the stage running its normal interactive path.

## Fit for a routine item, not every item

Reach for single-pass on the item this pipeline sees most: a single-item
shoot in a non-gate category, ordinary condition, an easy comp hunt. A
gate-category item with a plausible mark, a marble bulk shoot, or a
low-confidence photo pass is exactly what the HARD stops above exist to
catch — single-pass surfaces the one question and waits, same as an
interactive run would, just without four separate check-ins to get there.
