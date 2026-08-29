# eBay listing pipeline — v4

A prompt-driven pipeline that carries a shoot from photos to a
review-ready (then, on approval, published) eBay listing with as little
human babysitting as possible. It runs headless end to end, stopping only
at hard gates (photo approval, then the pre-publish review card), digs
deep on condition and exact-match pricing before falling back to an
era-peer comp, and reports account-wide performance after the fact. See
[`docs/V4_PLAN.md`](docs/V4_PLAN.md) for the current refactor plan
(skinny prompts, one CLI, fewer round-trips, a session observer) and
[`docs/archive/v2-to-v3-migration.md`](docs/archive/v2-to-v3-migration.md)
for the history of how this pipeline got here — v1 and v2 are frozen for
context only in [`deprecated/`](deprecated/README.md); no active guidance
lives there.

## Start here

To run a shoot, read [`RUN.md`](RUN.md) + [`prompts/_shared.md`](prompts/_shared.md),
then load each phase prompt on demand. `RUN.md` is the single entry point
— you do not pre-load all phase prompts.

    plan <photos-dir>   IDENTIFY → PRICE → CURATE                  (buy list)
    list <photos-dir>   INVESTIGATE → DRAFT → REVIEW(gate)→publish  (listing)
    full <photos-dir>   all in order, ending at the REVIEW gate

    report              account-wide: what we actually made       (no shoot dir)
    promote             account-wide: what to pay to place        (no shoot dir)

A single phase can be run alone by name (`identify <name>`, `price <name>`, …);
see [`RUN.md`](RUN.md). **REPORT** ([`prompts/report.md`](prompts/report.md)) is
the odd one out — it takes no shoot directory, reads eBay's *outcomes* rather
than producing a listing, and never publishes or edits anything:

    python lib/sync_actuals.py --apply     # actuals from the Fulfillment API
    python lib/report.py --performance     # fees, ask-vs-actual, speed, categories
    python tools/sales_report.py           # the same, as a dashboard, synced first

**PROMOTE** ([`prompts/promote.md`](prompts/promote.md)) is the other one, and
runs after REPORT: it plans paid placement — which campaign, what budget, which
listings — and **proposes only**. Every eBay write stays the operator's
keystroke, because an ad added by mistake spends before anyone notices.

    python tools/promote.py --budget 20    # the plan + the exact calls to enact it

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

Python infrastructure (`config`, `ebay_client`, `ebay_sold_browse`,
`list_edit`, `draft_io`, `photo_prep`) lives in `lib/`.

## Core invariants

The no-*automatic*-publish firewall (publishing requires `--confirm` and
is never triggered by the pipeline or `--sync`), the YAML-frontmatter
listing template + its `_field_constraints`, the unit_type vocabulary, and
the deterministic output-file-per-phase convention all hold. The REVIEW
gate is what turns "publish" from an absolute refusal into an
approval-gated action: nothing goes LIVE without one explicit human
approval at the decision card. Stage B of the comp hunt runs, un-gated by
default, through the logged-in browser
([`lib/ebay_sold_browse.py`](lib/ebay_sold_browse.py)); Chrome is an
optional low-confidence cross-check. Apify was retired 2026-08-15 and
must not be re-enabled — see
[`docs/pricing-backend-issues.md`](docs/pricing-backend-issues.md).
See
[`docs/archive/v2-to-v3-migration.md`](docs/archive/v2-to-v3-migration.md)
if you want the history of how these settled.
