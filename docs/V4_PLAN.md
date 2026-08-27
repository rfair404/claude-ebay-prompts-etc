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
- [x] `identify.md` — 364→326 + `reference/identify-notes.md` (46); mostly
      rules already, so the cut is prose compression
- [x] `list_edit_chrome.md` — 276→222 + `reference/list-edit-chrome-notes.md`
      (52); incident histories (hen run, 2026-06-07 photo findings) moved;
      status corrected from "deprecated when API lands" to "fallback for
      categories the API can't publish"
- [x] `_shared.md` — 208→201; near-pure contract, light trim only
- [x] `curate.md` — audited, already rules-only; header bump
- [x] `promote.md` — 159→158, two small trims; header bump
- [x] `review.md` / `investigate.md` / `condition-rubric.md` — audited,
      already rules-only; header bumps

Phase 1 result: the per-stage prompt set totals ~2,500 lines of rules (was
~3,300 mixed), with ~490 lines of rationale parked in `prompts/reference/`
loaded only on dispute.

## Phase 2 — terse tool output (#30)

Convention: a tool prints `OK n/m, k flagged → <file>.json` and writes detail
to the file; detail is read only when flagged. Apply to the noisiest first:
`lib/photo_prep/prep.py`, `lib/list_edit.py`, `tools/sales_report.py`,
`tools/ledger_reconcile.py`, `tools/live_audit.py`. Schema-check the JSON in
tests so "read only when flagged" can be trusted.

## Phase 3 — one CLI, shared plumbing (#30)

- [x] Single entry point: `python -m lib.cli <command>` (`lib/cli.py`) — a
      runpy dispatcher with argv pass-through, so every tool's documented
      flags work unchanged; the registry is one line per command. Documented
      in RUN.md "Ops commands".
- [x] Keepers folded in and committed: `ledger_reconcile`, `live_audit`,
      `pick_list`, `policy_sweep`, `price_audit` (+ registry entries for
      `sales-report`, `promote`, `voice`, `listing`, `prep`). `mpn_apply`
      was a completed one-off (top-level script over a session artifact) —
      archived to `docs/archive/one-offs/`, not folded.
- [x] list_edit / list_edit_group dedup — SURVEYED, ALREADY CONVERGED:
      group (431 lines) delegates every shared concern to `list_edit`
      (`_to_decimal_str`, `_body_to_html`, `_resolve_policies_and_location`,
      `upload_photos_to_eps`, `_find_offer_id_for_sku`, `_package_type`);
      what remains is variation-specific by design (group body, per-SKU
      offers with the documented no-Best-Offer divergence 25737, the
      `publish_by_inventory_item_group` endpoint). Extracting a shared
      offer-builder would add indirection to save ~10 lines on the money
      path — declined.
- [x] Root hygiene: ledger/sales/config backups → `backups/` (gitignored),
      root one-off cards + scratch images → `docs/archive/root-cards/`,
      pushed-batch session logs → `docs/archive/`. `docs/ask*` and
      `docs/prep_batch.*` stay — they are live default output paths of the
      batch ask tooling, not stale logs.

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
