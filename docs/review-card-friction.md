# REVIEW-card friction findings and what this PR does about them (#100)

`#100` is an auto-generated `ebz observe --propose` report over a 1-day
session window. It raised four findings; this doc records what each one
actually means in this codebase and which are in scope for code here.

## 1. "The Review card gate accounts for 4.8h of blocked wall-clock"

The suggested edit asks whether REVIEW can get "a confidence-gate default
the pipeline can self-approve when clean, the way PREP's orientation/crop/
colour gates do."

**It cannot, and this PR does not attempt it.** PREP's gates are internal
pipeline state — self-approving them only affects what gets rendered next.
REVIEW is the other side of the **Publish firewall**
(`prompts/_shared.md` "Publish firewall"): *"No phase publishes
automatically... no automated chain, and no chat instruction that arrives
outside the REVIEW gate... makes a listing go live."* `prompts/single_pass.md`
already collapsed IDENTIFY→PREP→PRICE→INVESTIGATE→DRAFT into one card with
exception-only questions (#30/#36's pattern) and explicitly declined to do
the same to REVIEW itself:

> The REVIEW gate is untouched. No approval, no publish, from anything
> single-pass did... if extending this pattern to a stage ever seemed to
> require touching one of those [Ground Rules], that is a stop, not a
> workaround.

Treating that prior decision as still correct, the fix here is narrower:
**make a clean card faster to approve, without ever approving it.**
`lib.list_edit.build_review_card()` now computes whether every section that
can carry an exception — the flags list, international-shipping blockers/
warnings, PREP's own approval, and photo-vs-manifest fidelity — is actually
clean, and if so prints one `✓ ALL CLEAR` line directly under the header.
The explicit-approval instruction at the bottom of the card
(`--list <shoot> --confirm`) is unchanged, still requires the same word from
a human, and is asserted unchanged by
`tests/test_formats.py::test_the_publish_command_on_the_card_is_still_gated`.
This is presentation only: the banner is derived from data the card was
already computing (`flags`, `intl_lines`, `photo_lines`, `prep_note`), not a
new judgment call, and it is withheld the moment any one of those sections
is not clean (tested: unapproved/missing PREP manifest, and a flagged
estate-context claim, each independently suppress it).

## 2. "Independent tool calls aren't being batched into one turn"

**Already covered — no doc change needed.** `prompts/_shared.md` states this
rule twice already: "Execution discipline (#61/#62)" item 2 and "Runtime
discipline" bullet 1, both added in response to the #61 audit this same
finding is restating. The 1-day window's 55%-single-tool-turn measurement
means the existing rule isn't being followed consistently in practice, which
a third copy of the same sentence is unlikely to fix. The better fix is
detection, not more prose: `tools/session_observer.py` (per #74 §5, still
open) could flag turns that immediately follow a same-tool turn as a named
"could have batched" signal, the way it already flags `long_loop` and
`redo`. Left as a `session_observer.py` follow-up rather than done here.

## 3. "Tool calls are erroring often enough to be worth a guard"

**Not actionable from this environment.** The finding is an aggregate count
(8 `tool_error` signals across 4 sessions, "Bash most often") with no
per-error detail in the issue body, and the local `~/.claude/projects/...`
session transcripts the friction report was generated from are not
available in this remote environment. Diagnosing a root cause needs the
actual failing commands, not just the count. Left for whoever runs
`ebz observe` locally to read the "tool_error samples" section of that run's
own report and file a scoped follow-up once a pattern is visible.

## 4. "At least one ask needed an unusually long back-and-forth to resolve"

**Same limitation as #3** — the "LONGEST ASKS" section named in the
suggested edit isn't part of the issue body and isn't reconstructable
without the local session logs. Left for a human with those logs to triage.

## Test plan

- `tests/test_list_edit.py` — three new tests: the banner appears when every
  section is clean, is withheld when PREP has no manifest, and is withheld
  when something is flagged (estate-context claim, reusing #46's fixture).
- `tests/test_formats.py` — existing `test_the_review_card_still_says_all_of_it`
  and `test_the_publish_command_on_the_card_is_still_gated` pass unchanged,
  confirming no section was dropped and `--confirm` is still required.
- Full suite: `python -m pytest tests/ -q`.
