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
| [local-pickup-fragile.md](local-pickup-fragile.md) | IMPLEMENTED — how ship-risky items route to the local-pickup fulfillment policy, and the freight fallback for distant buyers. |
| [price-strategy-v2.md](price-strategy-v2.md) | IMPLEMENTED — the distribution-based PRICE strategy (`lib/price_stats.py`) and the policy defaults it ships with. |
| [top-rated-plus.md](top-rated-plus.md) | What eBay's seller-standards policy requires a *listing* to carry, separated from the behavioral requirements a listing cannot encode. |
| [osd-audit-2026-08-21.md](osd-audit-2026-08-21.md) | The measurement behind PREP's OSD confidence floor — why orientation detection was confidently wrong, and the number that fixed it. |
| [prep-white-backgrounds.md](prep-white-backgrounds.md) | PROPOSAL, not built. Why the punch preset does not transfer from dark cloth to white-background shoots. |
| [prep-resume-plan.md](prep-resume-plan.md) | PLAN ONLY, no code. `--resume` / `--jobs N` for PREP (#74 item 3). |
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
