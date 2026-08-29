# deprecated — archived for context only

The original v1 prompts, kept for reference. **Nothing here is used by the
active pipeline.**

- `v1/` — the original root prompts (`ebay_flip_prompt.md`,
  `polo_rl_lot_prompt.md`, `backgammon_prompt.md`, `listing_template.md`,
  …) plus v1 working data (`inventory.txt`, `ebay_sold_comps.txt`,
  `pricing_analysis.txt`, `ebay-fields-all.txt`).

The v2 spec, prompt suite and samples were **deleted** — v4 is the only
live generation and the v2 material had no readers. The v2 **lib** was
never archived: it was promoted to the top-level `lib/`, which the active
pipeline depends on. `apify_ebay.py` went with the v2 material; Stage B
runs through [`../lib/ebay_sold_browse.py`](../lib/ebay_sold_browse.py).
For how the pipeline got here, see
[`../docs/archive/v2-to-v3-migration.md`](../docs/archive/v2-to-v3-migration.md).

The live project is at the repo root: `RUN.md`, `prompts/`, `templates/`,
`lib/`. Start at [`../RUN.md`](../RUN.md).

**Convention:** v1 is frozen for context only. No new work should extend
anything under `deprecated/`, and no doc outside this directory should
cite it as current guidance — cite the live pipeline (currently v4, see
[`../docs/V4_PLAN.md`](../docs/V4_PLAN.md)) instead.
