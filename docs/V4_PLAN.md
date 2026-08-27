# v4 — the refactor plan

v3 made the pipeline headless (prompts/ + lib/ at root). v4 makes it **cheap
and quiet**: skinny prompts, terse tools, one CLI, fewer questions, and a
session observer that keeps it improving. The scope is the open Idea issues —
#30 (token/output diet) is the structural core, #36 (self-optimizing sessions)
is the feedback loop, #23 (PREP categories), #31 (web front-end) and #32
(pack-and-ship) build on the result.

Each phase is a PR-sized unit. Update the checkboxes as work lands.

## Phase 1 — prompt diet (#30, highest leverage)

Every stage prompt becomes a rules-only checklist; rationale, history and
evidence move to `prompts/reference/<stage>-notes.md`, read only when a rule
is disputed. Precedent: `price.md` 636→315 with `reference/price-notes.md`
(landed on main, #34). **No rule is dropped, no rationale is deleted — it
relocates.** The test for each paragraph: does an agent need this to ACT, or
only to AGREE?

- [x] `prep.md` — 629→340 + `reference/prep-notes.md` (190); the two
      near-duplicate review-page sections merged into one
- [x] `draft.md` — 543→402 + `reference/draft-notes.md` (111); the
      superseded old-chain procedure moved wholesale to the notes
- [ ] `identify.md` (364)
- [ ] `list_edit_chrome.md` (276)
- [ ] `_shared.md` (208)
- [ ] `curate.md` (186)
- [ ] `promote.md` (159)
- [ ] `review.md` / `investigate.md` / `condition-rubric.md` (already short —
      audit only, diet if a page of rationale hides in them)

## Phase 2 — terse tool output (#30)

Convention: a tool prints `OK n/m, k flagged → <file>.json` and writes detail
to the file; detail is read only when flagged. Apply to the noisiest first:
`lib/photo_prep/prep.py`, `lib/list_edit.py`, `tools/sales_report.py`,
`tools/ledger_reconcile.py`, `tools/live_audit.py`. Schema-check the JSON in
tests so "read only when flagged" can be trusted.

## Phase 3 — one CLI, shared plumbing (#30)

- [ ] Single entry point (`python -m lib.cli <subcommand>`) with shared
      config + eBay client bootstrap.
- [ ] Fold in the six untracked keepers: `ledger_reconcile`, `live_audit`,
      `mpn_apply`, `pick_list`, `policy_sweep`, `price_audit` (they stay
      uncommitted until they land here — see PR #37 notes).
- [ ] Merge the duplicated offer/publish logic between `list_edit.py` (2,199
      lines) and `list_edit_group.py`.
- [ ] Root hygiene: ledger/sales backups → `backups/` (gitignored), one-off
      review-card HTML + scratch images → `docs/archive/`, session logs out
      of `docs/`.

## Phase 4 — fewer round-trips

- [ ] On-disk cache for comp runs and eBay reads, keyed query+date, `--fresh`
      bypass.
- [ ] Single-pass mode for routine items: PREP→IDENTIFY→PRICE→DRAFT in one
      run, ONE review card at the end, conversation reserved for flagged
      exceptions. Builds on PREP's confidence gate (#36, landed in PR #37).

## Phase 5 — the observer (#36)

- [ ] `tools/session_observer.py`: parse session transcripts (timestamps +
      token usage are already in the JSONL), compute per-stage wall time,
      token attribution, interaction hot spots, repeats.
- [ ] Friction report → auto-filed `Idea:` issue, deduped against open ones.

## Ground rules

Honesty rules, condition disclosure, approval digests and the review gate on
publish do not move in any phase. A diet that changes behavior is a bug: for
Phase 1, the before/after prompt must produce the same decisions on the same
inputs — when in doubt, the line is a rule and stays.
