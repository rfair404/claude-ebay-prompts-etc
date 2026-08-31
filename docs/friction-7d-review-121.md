# 7-day friction review findings and what this PR does about them (#121)

`#121` is an auto-generated `ebz observe --propose` report over a 7-day
session window, bundling seven findings. A smaller 1-day version of the same
report (`#100`) was already resolved by merged PR #102 — see
[`review-card-friction.md`](review-card-friction.md). This doc covers only
the findings that are new here or that needed re-checking against the
current state of the docs, not a re-litigation of #100's reasoning.

## 1. "Long shell calls are blocking the run in the foreground"

**Already covered — no doc change needed.** RUN.md already states the
general principle, not just a named-tool list:

> **Background anything over ~30s.** `prep --auto` renders images and
> nothing else in the run depends on it finishing — kick it off with
> `run_in_background`, run PRICE Stage A/B while it renders, collect it
> when it lands. **The same applies to any other foreground call in that
> range.**

That sentence (RUN.md, "Background dispatch" section) is not scoped to the
named-command list that follows it ("Always start these backgrounded — by
command prefix...", added for #74/#95) — it is the general rule, and the
named list is a specific, no-judgment-call subset of it. #121's own
measurement (54 Bash/PowerShell calls over 30s totalling 9.8h, only 2%
backgrounded) is evidence the rule isn't being *followed*, not evidence it
is *missing* — same shape as #100 finding 2's conclusion about the batching
rule. Restating an already-general sentence a third time is unlikely to fix
a compliance gap; left as-is.

## 2. "The 'Existing changes' gate accounts for 8.7h of blocked wall-clock"

**Investigated; not confidently identifiable, so not touched.** "Existing
changes" is not a name used anywhere in `prompts/`, `RUN.md`, or any gate
this repo's pipeline code defines (confirmed by grep across the tree before
starting this work). Tracing where the friction report gets its gate names
from (`tools/session_observer.py`) shows why: the "gate" a `gate_wait::*`
finding names is not a fixed, coded gate at all — it is the literal
`header` field of whatever `AskUserQuestion` tool call the agent happened to
issue that session, truncated to 24 characters for the aggregate
(`ask_count[(q.get("header") or "?")[:24]]`, `ask_wait[...]`, both in
`tools/session_observer.py`). In other words, "Existing changes" is
free-text an agent wrote into a one-off question header during some session
in the 7-day window, not a named, reusable gate with a fixed code path the
way REVIEW or PREP's orientation/crop/colour gates are.

Two things follow from that:

- I cannot tell what the question actually was — only its first 24
  characters survive into the aggregate, and the local session transcripts
  it came from are not available in this environment.
- There is no scoped place in the codebase to add a "confidence-gate
  default" for it even in principle, because it isn't a gate this repo
  defines — it is whatever an agent decided to ask, once, that session. A
  bypass would have to change the agent's judgment about *when to ask*,
  which needs the original transcript (what triggered the question, what
  the options were) to evaluate safely, not a guess from a 24-character
  fragment.

Per the task instructions, guessing an implementation for an unidentified
gate is out of scope here. Left for the issue owner, who has the underlying
`ebz observe` report locally, to point at the actual ask (or the samples
under its `FRICTION`/`LONGEST ASKS` sections) if a real, nameable gate turns
out to be behind it.

## 3. "The Review card gate accounts for 4.8h of blocked wall-clock"

**Already resolved by #102 — confirmed, not duplicated.** Same finding
shape as #100 finding 1. PR #102 declined a REVIEW confidence-gate bypass
(citing RUN.md's HARD-gate rule and `prompts/single_pass.md`'s prior refusal
to extend confidence-gating to REVIEW) and instead added a presentation-only
`✓ ALL CLEAR` banner to `lib.list_edit.build_review_card()` so a clean card
is faster to *approve* without changing what approval requires — see
[`review-card-friction.md`](review-card-friction.md) §1 for the full
reasoning, and `tests/test_list_edit.py` /
`tests/test_formats.py::test_the_publish_command_on_the_card_is_still_gated`
for what's still enforced. Nothing further attempted here.

## 4. "Independent tool calls aren't being batched into one turn"

**Already covered — no doc change needed.** `prompts/_shared.md` states this
rule in two places already, both added for #100/#61/#62: "Execution
discipline (#61/#62)" item 2 ("Batch independent tool calls into one turn...
Measured: 1.00 tool calls per assistant turn on average, and ~55% of tool
turns immediately followed a same-tool turn...") and "Runtime discipline"
bullet 1. #121's numbers (mean 1.00 tool calls/turn across 1777 turns, 54%
same-tool-adjacent) are close to #100's own measurement and read the same
way #100 finding 2 already concluded: a compliance gap, not a missing rule.
Left as `session_observer.py`-detection follow-up per #100's note, not
duplicated in prose.

## 5. "The same tool call is repeating 3+ times within a session"

**New finding (not in #100). Not diagnosable from here, but made one small,
scoped improvement.** Without the actual repeat-call samples (local
transcripts, not available in this environment) there's no way to tell
whether the 27 repeat signals are silent-failure retries or a cacheable
result — guessing a fix for either would be exactly the kind of blind retry
this finding is warning about.

What *is* available: `tools/session_observer.py` already computes, per
repeat, the exact signature that repeated (`_tool_signature()` — tool name
plus its command/file_path/pattern/etc, capped at 160 chars) and keeps it in
`aggregate_friction()`'s `samples["repeat"]`. That detail already reaches
the human-readable `report()` output, but `propose_fixes()`'s `repeat_calls`
proposal — the text that ends up in an auto-filed issue like this one — only
carried the aggregate count ("27 repeat-call signal(s) across 20
session(s)"), discarding the one piece of detail that would make a future
occurrence self-diagnosing without transcript archaeology. `tool_error_rate`
already does this (names the worst-offending tool); `repeat_calls` now
mirrors it, naming the worst-repeated signature and its count in the
evidence line. This is a small, additive change to evidence text only — it
does not change what counts as a repeat, when the proposal fires, or add
any new subsystem.

## 6. "Tool calls are erroring often enough to be worth a guard"

**Not actionable from this environment — same limitation as #100 finding
3.** 78 `tool_error` signals across 20 sessions is an aggregate count; the
actual failing commands live in the local session transcripts this
environment does not have access to. Left for whoever runs `ebz observe`
locally to read the `tool_error` samples in that run's own report.

## 7. "At least one ask needed an unusually long back-and-forth to resolve"

**Not actionable from this environment — same limitation as #100 finding
4.** 33 `long_loop` signals across 20 sessions, with the `LONGEST ASKS`
detail not part of the issue body and not reconstructable without the local
session logs. Left for a human with those logs to triage.

## What changed here

Only finding 5: `tools/session_observer.py`'s `propose_fixes()` now names
the worst-repeated tool+input signature in the `repeat_calls` proposal's
evidence text, using data (`samples["repeat"]`) it already collected.

## Test plan

- `tests/test_session_observer.py::test_propose_fixes_names_the_worst_repeat_signature`
  — new test asserting the worst repeat signature and its count appear in
  the `repeat_calls` evidence text.
- `tests/test_session_observer.py::test_propose_fixes_flags_repeat_tool_error_and_long_loop_signals`
  — existing test, unchanged, still passes (repeat_calls still fires at the
  same threshold).
- Full suite: `python -m pytest tests/ -q`.
