# INVESTIGATE — v4, Function 4

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/investigate.txt` (overwrite).

Produce the defensible claim set for ONE listing unit, from the photos
only. Conservative-upward: start at directly-observable, build up only
where visible evidence supports each claim. Every claim must survive a
buyer challenge. DRAFT consumes this verbatim.

## Direction (opposite of IDENTIFY)

IDENTIFY surfaces best-case + what to verify. INVESTIGATE claims only
what the photos defend. Treat IDENTIFY's `[BEST-CASE]` markers as "verify,
don't repeat" — repeat one only when a photo directly supports it.

## Critical assumption

The photos are ALL the evidence. If a mark/signature/date/label/feature
isn't visible, treat it as nonexistent. No "the back probably…", no
hidden-side speculation. Suspected-but-unphotographed evidence goes to
Open questions, not into a claim.

Re-read only the decisive frames (hero + mark + the defects you'll claim), per
IDENTIFY's "Photo intake" rule — don't re-open every angle you already saw at
IDENTIFY. IDENTIFY's record tells you which frames matter.

## Unit-type phrasing

Thread `unit_type` into every title/description: `single` singular noun;
`pair` "Pair of…"; `set` "Set of N…" / category-conventional + note
completeness; `lot` "Lot of N…" + enumerate contents; `duplicate` one
claim block + note N available. A `pair` record never yields a singular
title.

## Condition

Run [`condition-rubric.md`](condition-rubric.md): inspect per material,
record each defect with location + severity, state what can't be
assessed (function untested, undersides unseen), map to one
`CONDITION_ENUM` grade with the lower-grade tie-break. Defects feed
"Directly observable"; un-assessables feed "NOT defensible".

## Output

    === INVESTIGATION — <item> ===
    Photos: <N> (<angles/states>)

    ## Summary
    <2–4 sentences: what it is, the confident call, the headline. Elevator
    pitch — a busy user reads only this.>

    ## Directly observable
    - <plainly visible fact, photo ref where useful>
    Declarative language; visible facts only.

    ## Safe baseline (true regardless of scenario)
    <One paragraph: the conservative claims defensible even in the worst
    identification case — the listing's bedrock.>

    ## Scenarios (most likely → less likely, evidence-ranked)
    Rank by HOW MUCH VISIBLE EVIDENCE SUPPORTS each — not by value. Cap 3.
    Drop anything below "real" support; do NOT list a scenario only to
    exclude it.
    ### Scenario 1 — MOST LIKELY
    <id>  ·  Evidence: <visible features>  ·  Probability: high
    ### Scenario 2 — Possible (if warranted)
    <id>  ·  Evidence: <weaker but real>  ·  Probability: moderate

    ## Confident assessment
    <One paragraph, COMMIT to one call: "Most defensibly <scenario>,
    supported by <features>. The one observable that would shift this is
    <what to check>." This is what DRAFT consumes. (Headless: this call
    stands; the shift-observable is logged to NEEDS_REVIEW, not asked.)>

    ## NOT defensible from these photos
    - <thing that cannot be claimed>
    REQUIRED. Common: specific maker w/o stamp, year w/o date, country
    w/o mark, working status w/o test evidence, material beyond visible.
    Any directory-context block (`_shared.md`) goes here too — e.g. "smoke-
    free home" — even where the photos alone are silent on it and would
    otherwise support the claim.

    ## Listing-safe claims (DRAFT consumes)
    Title claims — 2–4, strongest first, EACH ≤80, with count:
      - <phrase> [<N>/80]
    Description claims — self-contained sentences (uncapped):
      - <sentence>
    Item specifics — canonical short value, EACH ≤65, with count:
      - Brand: <value or "Unbranded"> [<N>/65]
      - Material / Type / Era / Country / Color / …: <value> [<N>/65]
    Condition: <one-line summary + the CONDITION_ENUM grade>

    Pre-emit check: every title ≤80, every specific ≤65, counts shown. If
    a defensible claim genuinely can't fit, flag it (rare) rather than
    truncate.

    ## Open questions (would strengthen claims)
    - <specific, actionable: "Is there a maker stamp on the soleplate
      underside (not photographed)?" — maps to a scenario it resolves>
    Headless: do NOT wait on these — commit to the assessment above and
    append each question to NEEDS_REVIEW.md. If the user later answers
    ("yes it says X"), treat that as evidence (tag "user-confirmed:") and
    re-run.

    ## Listing approach
    <One paragraph: lead the title with the strongest defensible claim;
    use the body for period context; note what NOT to include.>

## Honesty

- Every claim traces to a visible detail. "appears to be / consistent
  with / likely" for inferences; declarative only for observed facts.
- Silence is dishonest — if a claim isn't defensible, say so in NOT
  defensible.
- Fresh-investigation rule (per _shared): no prior records, no "V1 said".

## Scope

One item per report. May read IDENTIFY for context (what to look for),
but re-derives claims independently. INVESTIGATE is not identification,
pricing, buy-decision, or drafting — only "what can I defend in writing?"

## Closing

Per _shared: path + the confident-assessment headline + NEEDS_REVIEW
count. Don't restate the report.
