# Proposal: split PREP's decisions from its rendering

*Drafted 2026-08-20, off the back of the photo audit. Not scheduled — this is
the idea, written down while it is fresh.*

## The problem, stated as symptoms

Everything that went wrong tonight was one shape wearing different clothes:

| symptom | what was actually true |
|---|---|
| `--pick asshot` printed success; `listing/` still held studio | a background `--apply` auto-picked *after* the pick, and nothing compared the manifest's choice to the files |
| `paul-fredrick` sat at `approved: true` with six frames at a rotation the shipping files had never been rendered with | a changed **decision** did not invalidate anything; only a changed **file** did |
| the same pasted `--rotate` line moved a frame three times | the command was relative, the page that generated it assumed absolute |
| a fairy doll's wings shipped grey | the mask was wrong, and nothing downstream could tell a wrong premise from a right one |
| 274 of 1,093 frames could not be audited at all | geometry could not be reconciled between source and output |

Each got a targeted fix. The fixes are right, and they are all patches on the
same missing idea: **PREP has no explicit, verified boundary between what was
decided and what was produced.** The manifest carries both, mixed, and the only
thing tying them together is that the code usually runs in the right order.

## The shape I would refactor toward

**One: a decision record that is pure data.**
Per frame: `{orientation, unskew, crop, look}` plus who decided it and when
(`auto` / `operator`) — and *nothing about files*. It is small, diffable,
reviewable, and can be replayed. Approvals attach to a hash of THIS, so any
change to any decision invalidates every approval downstream, automatically,
without anyone remembering to call an invalidator.

**Two: rendering as a pure function of that record.**
`render(source, decisions) -> bytes`. Same input, same output, always. The
shipping file records the decision hash it was produced from. Then:

- `listing/` is verifiable in one pass — recompute the hash, compare;
- the review card's "changed since PREP approved it" becomes structural rather
  than a heuristic;
- the auto-pick race is impossible: a look is a decision, and rendering never
  writes decisions.

**Three: the review page reads the record, and emits decisions.**
Not commands. Today the page composes a shell line in a flag syntax whose
semantics it has to know (relative vs absolute — it got that wrong). Emitting a
decision patch removes the whole class: idempotent because a patch is a value,
not an operation.

## What this would have prevented

Every row in the table above, structurally rather than by a guard. That is the
argument. The audit tooling (`prep_saturation_audit.py`,
`prep_saturation_verify.py`) would also become far cheaper — it exists partly to
answer "does the shipping file match what we intended", which a decision hash
answers exactly.

## What I would NOT change

- The four-stage order and the human gates. They work, and they caught real
  errors tonight that no automation would have — including one the detector was
  confidently wrong about.
- The Frame Check page's format (see `prompts/prep.md`). Only what it emits.
- The looks, and `k`-scaling. `asshot` at `k=0` proves the model is sound.

## Cost, honestly

A migration for ~150 existing manifests, and the render path is the hottest code
in the repo (~64s per frame per preset), so a rewrite there risks performance
regressions that only show up on a 17-hour batch. Worth staging: decisions
first, hashing second, page-emits-decisions third, and the render refactor last
or never.

## Open question worth answering first

**Why was OSD confidently wrong on five paul-fredrick frames** (270 at
confidence up to 12.2, recognised script, all corroborating, all wrong)? If the
detector's angle convention is composed with EXIF incorrectly for some frames,
that is a bug this refactor would not touch — and it is the one that quietly
ships sideways photos in a headless run.
