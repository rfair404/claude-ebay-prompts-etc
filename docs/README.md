# docs — what is current, what is history

`prompts/` is what the pipeline obeys and `RUN.md` is how you run it. This
directory is everything that had to be written down but does not belong in a
phase prompt: the plan being worked, the measurement behind a rule, and the
record of decisions already made.

**The rule that matters:** nothing under `archive/` is current guidance. If a
finding there still applies, it has been written into a prompt — cite the
prompt, not the archive.

## Current

| Doc | What it is |
|---|---|
| [V4_PLAN.md](V4_PLAN.md) | The refactor being worked: skinny prompts, terse tools, one CLI, fewer round-trips, a session observer. Phase checkboxes are the live status. |
| [STORE-CONNECTION-README.md](STORE-CONNECTION-README.md) | IMPLEMENTED (#147/#150) — the runbook for connecting an ADDITIONAL eBay seller account (e.g. a junk store) so both stay connected at once. The procedure, in order, with the silent-failure steps called out. |
| [live-streaming-setup.md](live-streaming-setup.md) | The new-machine checklist for eBay Live streaming: OBS (Flatpak) + the eBay WHIP profile, v4l2loopback, the Nikon D5100 feed, and what the OM System TG-7 can and can't do (`tools/live/`). |
| [local-pickup-fragile.md](local-pickup-fragile.md) | IMPLEMENTED — how ship-risky items route to the local-pickup fulfillment policy, and the freight fallback for distant buyers. |
| [price-strategy-v2.md](price-strategy-v2.md) | IMPLEMENTED — the distribution-based PRICE strategy (`lib/price_stats.py`) and the policy defaults it ships with. |
| [ebay-live.md](ebay-live.md) | MVP — eBay Live show lineup (`tools/live_lineup.py`), the host page + OBS overlay (`/live/<show>`), and the show's promo image (`tools/live_marquee.py`). What eBay documents for Live, what's built, and the open questions in the eBay UI that gate the next step. |
| [top-rated-plus.md](top-rated-plus.md) | What eBay's seller-standards policy requires a *listing* to carry, separated from the behavioral requirements a listing cannot encode. |
| [osd-audit-2026-08-21.md](osd-audit-2026-08-21.md) | The measurement behind PREP's OSD confidence floor — why orientation detection was confidently wrong, and the number that fixed it. |
| [prep-white-backgrounds.md](prep-white-backgrounds.md) | PROPOSAL, not built. Why the punch preset does not transfer from dark cloth to white-background shoots. |
| [prep-resume-plan.md](prep-resume-plan.md) | PLAN ONLY, no code. `--resume` / `--jobs N` for PREP (#74 item 3). |
| [modes-9-1.md](modes-9-1.md) | PLAN, not built. Triage / List / Manage modes for v9.1.0: what each does, the bulk-edit and store-reassign rules, and the PR slices. |
| [v9-alpha-walkthrough.md](v9-alpha-walkthrough.md) | The ten-stop demo script for Russell for v9.0.0-alpha.1: one command per stop, what it shows, what needs his hands, and what the demo never touches. |
| [client-onboarding/checklist.md](client-onboarding/checklist.md) | DRAFT — attorney review pending. Start here for a new consignment or purchase client: what to collect by deal and client type, red flags, and where each record goes. |
| [client-onboarding/consignment-agreement.md](client-onboarding/consignment-agreement.md) | DRAFT — attorney review pending. The client-facing consignment agreement template. |
| [client-onboarding/purchase-agreement.md](client-onboarding/purchase-agreement.md) | DRAFT — attorney review pending. The purchase agreement and bill of sale template for buying a client's items outright. |
| [client-onboarding/exhibits.md](client-onboarding/exhibits.md) | DRAFT — attorney review pending. Exhibit A (item schedule) and Exhibit B (certification of authority), shared by both agreements. |
| `prep_batch.md` | Generated, untracked (#106). The batch-review table written by the PREP batch tooling; re-run the scan to refresh it. |
| `ask*/` | Generated, untracked (#106). Default output path of `tools/prep_asksheet.py` — the frame index `tools/prep_answer.py` reads back. The tool creates the directory itself, so a fresh clone needs nothing here. |

## archive/

Superseded, completed, or one-time. Kept for the reasoning, not the
instructions.

| Doc | Why it is here |
|---|---|
| [v2-to-v3-migration.md](archive/v2-to-v3-migration.md) | How the pipeline became headless. The v1 and v2 trees themselves were deleted in #99. |
| [pricing-backend-issues.md](archive/pricing-backend-issues.md) | RESOLVED — the Apify flakiness that ended with Stage B moving to the logged-in browser. Apify must not be re-enabled. |
| [prep-refactor-proposal.md](archive/prep-refactor-proposal.md) | The idea behind splitting PREP's decisions from its rendering, written before #21 scheduled it. |
| [TASK_pushed_scan_fix.md](archive/TASK_pushed_scan_fix.md) | The task that established API-is-truth: "which shoots are pushed?" must be answered from eBay, never from `prep.json`. |
| [inventory_cleanup_prompt.md](archive/inventory_cleanup_prompt.md) | The one-time disk-reclaim agent prompt for `inventory/`. |
| `inventory_photo_status.md`, `prep_pushed*.md`, `root-cards/` | Dated run logs — which listings were re-pushed, when, with how many frames. |
| [ledger-titles.txt](archive/ledger-titles.txt) | The listings ledger reduced to titles only (203 entries) — the titles-only view kept so the ledger itself, which carries our asking prices, does not have to be. |
| [one-offs/](archive/one-offs/README.md) | Scripts that ran once against the live account. Do not re-run them; see that README for the tool to use instead. |
