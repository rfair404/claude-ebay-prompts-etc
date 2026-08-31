# tools — operator scripts

One job each, run by hand. Nothing here is part of an automatic run: the
pipeline itself is `RUN.md` + `prompts/` + `lib/`, and the preferred front
door is the CLI (`python -m lib.cli`). These are the things you reach for
between runs — audits, batch photo work, packing, reporting.

Every script carries its own docstring; `python tools/<name>.py --help` is
the authority on flags. This table exists so you can find the right one.

## The account, as eBay sees it

The standing rule is that the eBay API is truth and the local ledger is
assumed stale. These are how you check.

| Script | What it answers |
|---|---|
| `count_listings.py` | How many offers and live listings exist. A before/after invariant for any batch write — the totals must not increase. |
| `ebay_sheet.py` | An inventory sheet built from the eBay APIs alone. |
| `inventory_sync.py` | Live inventory pulled from eBay and reconciled against the local shoots. |
| `ledger_reconcile.py` | `listings_ledger.csv` vs the Sell API. eBay wins, always. |
| `live_audit.py` | The local files vs what eBay actually shows. |
| `live_shipping_survey.py` | The shipping terms eBay is *actually* serving on our live listings. |
| `policy_sweep.py` | Surveys — and optionally repairs — the return/fulfillment policy on every offer. |
| `offer_floor_audit.py` | Does each live listing's Best Offer floor match its price file? |
| `price_audit.py` | Which listings are still asking above their own comp evidence. |
| `price_vs_actual.py` | Did the PRICE stage's justified band survive contact with buyers? |
| `prep_assess.py` | Read-only: is each listing sellable, and what state are its photos in? |

## PREP — batch photo work

| Script | What it does |
|---|---|
| `prep_run.py` | Runs PREP over a queue of shoots, in-process and in parallel. |
| `prep_card.py` | A shoot's current state as one wide preview card, for chat. This is the review loop. |
| `prep_sheet_html.py` | The same review as an interactive page instead of a tall JPEG. |
| `prep_index.py` | One review artifact for a whole batch of PREP'd shoots. |
| `prep_proposal.py` | The auto first pass as one page — what PREP decided before anyone was asked, with the proposed crop drawn on the frame. |
| `prep_orient_review.py` | Every frame at all four turns, for a model to read and decide. |
| `prep_asksheet.py` / `prep_answer.py` | Combine unresolved frames across many shoots into a few big sheets, then record the answers by sheet index. Default output path is `docs/ask/`. |
| `prep_plain.py` | Crop + orient only — no colour correction of any kind. |
| `prep_push.py` | Pushes a batch of PREP'd shoots to their live eBay listings. |
| `prep_gc.py` | Sweeps the regenerable byproducts PREP leaves behind, across every shoot. |
| `compare_live.py` | ORIGINAL vs LIVE-ON-EBAY contact sheet for one shoot. |
| `prep_saturation_audit.py` / `prep_saturation_verify.py` | Finds photos that shipped with the colour drained out, then separates colour lost from the *item* from colour lost from the backdrop. |
| `osd_audit.py` | Scores tesseract's orientation detection against the human answer recorded beside it. The measurement behind the OSD confidence floor. |
| `retouch_run.py` / `retouch_tracker.py` | The retroactive re-touch of shoots whose backdrop was crushed, plus its resumable tracker. |
| `plainify.py` | Replaces a shoot's listing images with plain renders and pushes them. |
| `media_asshot_run.py`, `media_crop_fix.py`, `media_review_card.py` | Printed media (books, magazines, catalogs, mailers): render as-shot with the crop forced off, and the before/after page for it. rembg cuts out the picture *printed on* paper, which is why this path exists. |

## Listing and review

| Script | What it does |
|---|---|
| `review_card_html.py` | Renders the REVIEW gate as one page — the listing as a buyer will meet it, plus the hero-frame picker. Every REVIEW is presented on this card. |
| `comps_board.py` | A self-contained comps board: thumbnail, clickable listing, delivered price. |

## Pack and ship

| Script | What it does |
|---|---|
| `pick_list.py` | The orders that still have to be packed. |
| `pick_list_html.py` | The print-friendly pick sheet for one shipment — open it, hit print. One page is one box; it doubles as the packing slip, because eBay has no packing-slip endpoint. Writes buyer PII, so it never leaves the local machine. |

## Money and meta

| Script | What it does |
|---|---|
| `sales_report.py` | One command: syncs eBay's actuals, then draws the dashboard. |
| `promote.py` | Plans a Promoted Listings campaign. Proposes; never writes. |
| `session_observer.py` | Reads our own transcripts and finds the friction (V4_PLAN Phase 5). |

## Marbles

The marble specialization's tooling. See
[`../specializations/marbles.md`](../specializations/marbles.md) for the
rules these serve.

| Script | What it does |
|---|---|
| `marble_triage.py` | One-shot triage — the fast batch pipeline for sorting shoots. Always verify crops before sorting on them. |
| `marble_decide.py` | A never-dead-end decision packet: a direction, the in-hand questions that resolve it, and a labeled reference panel. |
| `marble_matches.py` | The top-N CLIP forum matches for one marble — the "let me help decide" panel. Forum matching is a gross sort, not a maker assertion. |
| `marble_colormatch.py` | Tight-crops one marble and lists colour/visual look-alikes from the forum index. |
| `marble_refset.py` | A maker-*labelled* reference set built from forum posts showing marbles in known packaging, then matches a subject against it. |
| `marble_typechart.py` | A labeled type-reference chart from the MCSA studio references. |
| `verify_batch.py` | Visual maker discrimination for a whole shoot — head-to-head or matrix. |
| `gold_eval.py` | Gold-set regression for the classifier: "am I right, or going soft?" |
| `reindex_forum.py` / `reindex_full.py` | Re-crop and re-embed the marbleconnection forum index. `reindex_full.py` is the resumable production run. |
| `marble_index_next.cmd` | What the scheduled Windows task runs to index one more forum page. |
