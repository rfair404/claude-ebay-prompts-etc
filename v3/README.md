# v3 — headless prompt suite

A full rewrite of the v2 prompt pipeline with three goals: run as
headless as possible, output far fewer words with more confidence, and
dig deeper on condition and exact-match pricing. **v2 is untouched and
remains the working reference;** v3 is the new path.

## Start here

To run a shoot, read [`RUN.md`](RUN.md) + [`prompts/_shared.md`](prompts/_shared.md),
then load each phase prompt on demand. `RUN.md` is the single entry point
— you do not pre-load all phase prompts.

    plan <photos-dir>   IDENTIFY → PRICE → CURATE        (buy list)
    list <photos-dir>   INVESTIGATE → DRAFT              (listing)
    full <photos-dir>   all five

## What changed from v2

**1 — Headless.** A single orchestrator ([`RUN.md`](RUN.md)) and an
explicit **gate contract**: only TWO things stop a run — publishing
(never; refused) and a paid Apify call (cost-confirmed). Every other old
"ask the user" moment is now a SOFT gate — proceed with a documented
default, append one line to `<shoot-dir>/NEEDS_REVIEW.md`, keep going.
The user reviews that queue asynchronously instead of being interrupted.
Notably: PRICE no longer waits for query approval, and the working price
is auto-adopted (Recommended tier, provisional) so the pipeline finishes.

**2 — Fewer words, more confidence.** Shared rules were extracted to
[`prompts/_shared.md`](prompts/_shared.md) (unit_type, fresh-investigation,
firewall, char limits, one house-style block) — the ~40% duplication
across the five v2 prompts is gone, and the suite dropped from ~2,300 to
~1,100 lines. Scenario brackets are capped at 3, produced only on
material value swing, with sub-15% tails and "effectively excluded"
padding removed. Phases commit to one call instead of laddering best→worst.

**3 — Depth / independence.** New
[`prompts/condition-rubric.md`](prompts/condition-rubric.md): a
per-material defect taxonomy + eBay grade mapping with a conservative
tie-break, used by IDENTIFY and INVESTIGATE. PRICE gained an autonomous
**exact-match hunt** — it iterates query formulations across the free
sources (WebSearch + Chrome→eBay) before ever falling back to an
era-peer, and reports how hard it looked.

## Layout

    v3/
      RUN.md                      headless runbook + gate contract
      README.md                   this file
      prompts/
        _shared.md                rules every phase obeys
        condition-rubric.md       condition depth (Goal 3)
        identify.md  price.md  curate.md  investigate.md  draft.md
      templates/
        listing-v1.md             YAML frontmatter + body (copied from v2)

Python infrastructure (`config`, `ebay_client`, `apify_ebay`,
`list_edit`, `photo_prep`) is unchanged and shared from `v2/lib/` — v3
does not duplicate code.

## Unchanged from v2

The no-publish firewall, the YAML-frontmatter listing template + its
`_field_constraints`, the unit_type vocabulary, the Apify opt-in policy,
and the deterministic output-file-per-phase convention all carry over.
