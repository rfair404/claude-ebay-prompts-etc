# What changed when v3 replaced v2

Archival context, moved out of `README.md` (#83) so the front door describes
the current pipeline on its own terms instead of as a diff against a
generation that's now two versions behind. v2 lives frozen in
[`../../deprecated/v2/`](../../deprecated/v2/); this is the record of why v3
looked the way it did when it replaced it. v3 itself was later folded into
v4 — see [`../V4_PLAN.md`](../V4_PLAN.md) for that step.

## What changed from v2

**1 — Headless.** A single orchestrator ([`RUN.md`](../../RUN.md)) and an
explicit **gate contract**: only ONE thing stops a run — the REVIEW gate
(after DRAFT, present a decision card and publish LIVE only on explicit
approval). Every other old "ask the user" moment is now a SOFT gate —
proceed with a documented default, append one line to
`<shoot-dir>/NEEDS_REVIEW.md`, keep going. The user reviews that queue
asynchronously instead of being interrupted. Notably: PRICE no longer
waits for query approval and no longer gates on its comp source — Stage B
runs automatically through the user's logged-in browser
([`lib/ebay_sold_browse.py`](../../lib/ebay_sold_browse.py); Apify was retired
2026-08-15 after repeated silent blocking, see
[`./pricing-backend-issues.md`](./pricing-backend-issues.md)) — and the
working price is auto-adopted (Recommended tier, provisional) so the pipeline
finishes straight through to the review card.

**2 — Fewer words, more confidence.** Shared rules were extracted to
[`prompts/_shared.md`](../../prompts/_shared.md) (unit_type, fresh-investigation,
firewall, char limits, one house-style block) — the ~40% duplication
across the five v2 prompts is gone, and the suite dropped from ~2,300 to
~1,100 lines. Scenario brackets are capped at 3, produced only on
material value swing, with sub-15% tails and "effectively excluded"
padding removed. Phases commit to one call instead of laddering best→worst.

**3 — Depth / independence.** New
[`prompts/condition-rubric.md`](../../prompts/condition-rubric.md): a
per-material defect taxonomy + eBay grade mapping with a conservative
tie-break, used by IDENTIFY and INVESTIGATE. PRICE gained an autonomous
**exact-match hunt** — Stage A WebSearch → Stage B Apify eBay-sold (as it
was at the time; see "Unchanged from v2" below for how Stage B's backend
has since moved) → optional Stage C Chrome (only when confidence is low)
— iterating query formulations before ever falling back to an era-peer,
and reporting how hard it looked.

## Unchanged from v2

The no-*automatic*-publish firewall (publishing requires `--confirm` and
is never triggered by the pipeline or `--sync`), the YAML-frontmatter
listing template + its `_field_constraints`, the unit_type vocabulary, the
Apify opt-in policy, and the deterministic output-file-per-phase
convention all carried over. v3 turned the old absolute publish refusal
into an approval-gated publish (the REVIEW gate); and Apify moved from a
gated, opt-in fallback to the un-gated default Stage B of the comp hunt
(Chrome demoted to an optional low-confidence cross-check). The firewall,
template, vocabulary, and file convention are still true in the current
(v4) pipeline — see README.md's "Core invariants" section for the
standalone statement. Stage B's *backend* has since moved again: Apify
was retired 2026-08-15 in favor of the logged-in browser
([`./pricing-backend-issues.md`](./pricing-backend-issues.md)); the
"un-gated default Stage B" shape described above is what's current, the
Apify part of it is not.
