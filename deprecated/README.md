# deprecated — archived for context only

These are earlier iterations, kept for reference. **Nothing here is used
by the active pipeline.**

- `v1/` — the original root prompts (`ebay_flip_prompt.md`,
  `polo_rl_lot_prompt.md`, `backgammon_prompt.md`, `listing_template.md`,
  …) plus v1 working data (`inventory.txt`, `ebay_sold_comps.txt`,
  `pricing_analysis.txt`, `ebay-fields-all.txt`).
- `v2/` — the v2 spec + prompt suite (`PLAN.md`, `RESUME.md`, `prompts/`,
  `samples/`, `templates/`). The v2 **lib** was NOT archived — it was
  promoted to the top-level `lib/` because the active pipeline depends on
  it.
- `PRL-batches/` — old v1 batch work artifacts.

The live project is at the repo root: `RUN.md`, `prompts/`, `templates/`,
`lib/`. Start at [`../RUN.md`](../RUN.md).

**Convention:** v1 and v2 are frozen for context only. No new work should
extend anything under `deprecated/`, and no doc outside this directory
should cite v1/v2 as current guidance — cite the live pipeline (currently
v4, see [`../docs/V4_PLAN.md`](../docs/V4_PLAN.md)) instead.
