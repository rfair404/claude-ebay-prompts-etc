# v3 — headless prompt suite

A full rewrite of the v2 prompt pipeline with three goals: run as
headless as possible, output far fewer words with more confidence, and
dig deeper on condition and exact-match pricing. **v2 is untouched and
remains the working reference;** v3 is the new path.

## Start here

To run a shoot, read [`RUN.md`](RUN.md) + [`prompts/_shared.md`](prompts/_shared.md),
then load each phase prompt on demand. `RUN.md` is the single entry point
— you do not pre-load all phase prompts.

    plan <photos-dir>   IDENTIFY → PRICE → CURATE                  (buy list)
    list <photos-dir>   INVESTIGATE → DRAFT → REVIEW(gate)→publish  (listing)
    full <photos-dir>   all in order, ending at the REVIEW gate

## What changed from v2

**1 — Headless.** A single orchestrator ([`RUN.md`](RUN.md)) and an
explicit **gate contract**: only ONE thing stops a run — the REVIEW gate
(after DRAFT, present a decision card and publish LIVE only on explicit
approval). Every other old "ask the user" moment is now a SOFT gate —
proceed with a documented default, append one line to
`<shoot-dir>/NEEDS_REVIEW.md`, keep going. The user reviews that queue
asynchronously instead of being interrupted. Notably: PRICE no longer
waits for query approval and no longer gates on Apify (it runs
automatically as Stage B of the comp hunt), and the working price is
auto-adopted (Recommended tier, provisional) so the pipeline finishes
straight through to the review card.

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
**exact-match hunt** — Stage A WebSearch → Stage B Apify eBay-sold →
optional Stage C Chrome (only when confidence is low) — iterating query
formulations before ever falling back to an era-peer, and reporting how
hard it looked.

## Layout

    <project root>/
      RUN.md                      headless runbook + gate contract
      README.md                   this file
      prompts/
        _shared.md                rules every phase obeys
        condition-rubric.md       condition depth (Goal 3)
        identify.md  price.md  curate.md  investigate.md  draft.md
        review.md                 Function 5.5 — the publish gate (decision card)
        list_edit_chrome.md       Function 6 fallback — Chrome stand-in
      templates/
        listing-v1.md             YAML frontmatter + body
      lib/                        eBay Sell API code (sync/publish/end) + SETUP_EBAY_API.md
      deprecated/                 frozen v1 prompts + v2 reference (context only)

**Function 5.5 — REVIEW.** The publish gate. One command —
`python lib/list_edit.py --review <shoot-dir>` — records the item, runs
preflight (condition/shipping/insurance), and renders a succinct decision
card (title, price + supporting comp links, condition, anything needing
manual review) to `review_card.md` + chat, then STOPS. Only an explicit
human approval at the card publishes the listing LIVE. This replaced the old
absolute no-publish firewall: publishing is now *gated*, not forbidden —
but never automatic, never inferred.

**Function 6 — LIST/EDIT.** Pushes an approved `draft.md` to eBay. The
agent reaches it only through a human-approved REVIEW card; the pipeline
never publishes on its own. Two paths:

- **Primary — eBay Sell API** (`lib/list_edit.py`). `--sync <dir>` creates
  an UNPUBLISHED offer; `--list <dir> --confirm` syncs then publishes LIVE
  (the post-approval command); `--publish`/`--list` without `--confirm` are
  dry runs. Headless and environment-proof: photos upload to EPS
  server-side, the description is one HTTP field (so the Chrome
  missing-fields bug can't occur), and re-sync is idempotent. **Verified
  end-to-end on sandbox** (11 photos, full description, specifics, Best
  Offer). One-time setup: [`lib/SETUP_EBAY_API.md`](lib/SETUP_EBAY_API.md).
- **Fallback — Chrome stand-in**
  ([`prompts/list_edit_chrome.md`](prompts/list_edit_chrome.md)). Only when
  the API isn't set up. Encodes the hard-won UI lessons: trusted-keystroke
  typing for the rich-text description (synthetic events don't persist),
  settle-and-verify before save, JS DOM state over lagging screenshots,
  never open the variations editor, drop inaccurate AI-suggested specifics.

**Managing listings (Function 6, on request).** `lib/list_edit.py` also
manages any offer/SKU on the account: `--offers` (query all, read-only),
`--withdraw-offer <id>` (end a live listing, keep the offer), `--delete-offer
<id>` and `--delete-item <sku>` (permanent removal). Mutations are dry-run
unless `--confirm` and are user-initiated — never part of the pipeline.

Python infrastructure (`config`, `ebay_client`, `apify_ebay`,
`list_edit`, `draft_io`, `photo_prep`) lives in `lib/`.

## Unchanged from v2

The no-*automatic*-publish firewall (publishing requires `--confirm` and
is never triggered by the pipeline or `--sync`), the YAML-frontmatter
listing template + its `_field_constraints`, the unit_type vocabulary, the
Apify opt-in policy, and the deterministic output-file-per-phase
convention all carry over. New in v3: the REVIEW gate turns the old
absolute publish refusal into an approval-gated publish; and Apify moved
from a gated, opt-in fallback to the un-gated default Stage B of the comp
hunt (Chrome demoted to an optional low-confidence cross-check).
