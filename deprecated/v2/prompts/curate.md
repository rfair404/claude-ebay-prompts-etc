# CURATE — eBay reseller workflow, Function 3

## Output file (mandatory)

Write the buy list to a markdown file at:

    <shoot-directory>/review.md

- If the file does not exist, create it.
- If the file exists, OVERWRITE it — the latest run is the current
  buy-decision record.
- Encoding: UTF-8.
- For test runs in this repository, that is
  `v2/samples/<shoot-name>/review.md`.

The review.md file is the durable buy-decision record — the user
reads it in the field at the moment of purchase. Always write it,
every time CURATE runs.



You are producing a prioritized buy list from IDENTIFY + PRICE outputs.
The user reads this **in the field**, at the moment of purchase, often
on a phone. Output must be concise, scannable, and decision-focused.

## Core principle

Every item's buy card must answer four questions in a glance:
1. **Is it worth pursuing?** → headline buy threshold
2. **What's the upside?** → one sale range line
3. **What's the risk?** → single-line confidence warning if applicable
4. **What do I check?** → ordered verification checklist

Everything else is noise. Don't include profit-floor math, multi-line
price tables, full PRICE research, or detailed shipping breakdowns.
That data lives in the price.txt file and can be referenced if needed —
it doesn't belong in the buy card.

## Inputs

- IDENTIFY item records (with `unit_type`, `quantity`, and Scenario
  brackets where applicable)
- PRICE outputs (scenario-bracketed price ranges + research notes,
  always expressed per-listing-unit)
- Active strategy profile from `~/.ebaybiz/config.yaml`:
    margin_target, buy_point_multiplier, fee_pct, shipping_estimator,
    drive_cost, profit_floor

## Unit-type handling

Every IDENTIFY record carries `unit_type` (one of `single`, `pair`,
`set`, `lot`, `duplicate`) and `quantity`. CURATE's buy card scales
to the selling unit:

- `single` (qty=1) — buy card describes one listing. Math is
  per-item. Display: `BUY IF ≤ $X`.
- `pair` (qty=2) — buy card describes one listing for the pair.
  Math is for the pair. Display: `BUY IF ≤ $X for the pair`.
  Sells line: `Sells: $A–$B per pair`.
- `set` (qty=N) — buy card describes one listing for the set.
  Math is for the set. Display: `BUY IF ≤ $X for the set`.
  Sells line: `Sells: $A–$B per set of N`.
- `lot` (qty=N) — buy card describes one listing for the lot.
  Math is for the lot. Display: `BUY IF ≤ $X for the lot of N`.
  Sells line: `Sells: $A–$B per lot`.
- `duplicate` (qty=N) — PRICE gave a per-piece price. CURATE shows
  BOTH:
    - Per-piece buy point: `BUY IF ≤ $X per piece`
    - Total-buy-at-bulk note: `Total ≤ $(X × N) if buying all N`
  Sells line: `Sells: $A–$B per piece × N units = $TOTAL aggregate`
  Profit-floor math treats `duplicate` records as N separate
  listings — each unit must clear the floor independently (because
  each consumes its own listing/pack/ship labor).

The profit-floor weight tier applies to the SELLING UNIT, not to a
single piece. A pair of heavy bookends ships as one box (medium
weight if combined under 15 lb, heavy if 15–25 lb total) — use the
total-pack weight for tier lookup.

Skipping rules unchanged: not-for-sale and below-effective-floor
records do not appear in QUALIFYING regardless of unit_type.

## Output

A single markdown buy list — one file per shoot, named
`review-YYYY-MM-DD.md`. Each qualifying item should fit on roughly one
phone screen.

## Filtering (in order, BEFORE sorting)

1. **Not-for-sale filter.** `collectability: none (not for sale)` →
   excluded from the buy list entirely (not even in the SKIPPED list).

2. **Weight-adjusted profit-floor filter.** Compute max reachable
   profit AND apply a weight-tier multiplier to the floor itself.
   Lift the weight tier directly from the `Estimated weight` field
   in each IDENTIFY record — do not re-infer from descriptive text.

       max_profit = (best_case_sale_upper × (1 − fee_pct))
                  − shipping_estimate
                  − conservative_buy_point

       effective_floor = profit_floor × weight_multiplier

   Weight tier multipliers (default — configurable in profile):

       Weight class             Multiplier   Effective floor (default base $100)
       ───────────────────────  ──────────   ──────────────────────────────────
       Light    (<5 lb)         1.0×         $100
       Medium   (5–15 lb)       1.0×         $100
       Heavy    (15–25 lb)      1.5×         $150
       Oversized (25–50 lb)     3.0×         $300
       Freight  (50+ lb)        5.0×         $500
       Requires movers          SKIP entirely unless an ultra-high-value
                                override applies (item authenticated above
                                $2000+, owner-confirmed)

   If max_profit < effective_floor → SKIPPED section. The skipped entry
   MUST show the weight tier and the elevated floor in the math line.

   **Rationale (do not omit from skipped output when relevant):** heavier
   items consume more photographing time, more pack time and materials,
   more shipping cost, have a smaller buyer pool (local-pickup-only is
   geographically constrained), and carry higher damage-claim risk. The
   user explicitly prefers small/light/easy-to-ship inventory. Heavy
   items must be substantially more profitable to be worth the labor
   and logistics burden.

3. **Insufficient-data filter.** PRICE marked `Data quality: INSUFFICIENT`
   or item flagged with provisional grouping needing re-shoot →
   DEFERRED section (one-line entry).

## Sort (QUALIFYING section)

1. Collectability tier: `collectable > antique > vintage > modern`
2. Best-case sale upper bound, descending, within each tier.

## Buy-point math

Compute two thresholds per qualifying item:

- **Conservative** = `worst_case_sale_floor × buy_point_multiplier`
  (the headline number — protects against worst case)
- **Stretch** = `(best_case_sale_upper × (1 − fee_pct))
                − shipping_estimate − effective_floor`
  (max buy price where best-case still nets the weight-adjusted floor.
  Uses effective_floor — heavier items therefore have a tighter stretch
  ceiling. Only applicable when user verifies best-case in person.)

## Output format

### Header (3 lines)

    BUY LIST — YYYY-MM-DD
    Source: <shoot>  |  Profile: <profile_name>  |  Floor: $<profit_floor>
    Sort: tier → best-case price desc.  Filtered: not-for-sale + below-floor.

### QUALIFYING section

Heading:
    ## Qualifying — pursue these (<N>)

One block per item, compact format:

    ### [N] <SHORT ITEM NAME> — <TIER>

    Sells: $<low>–$<high>  (best case: <one-line scenario description>)

    **BUY IF ≤ $<conservative>**   ·   stretch ≤ $<stretch> if best-case verified

    ⚠ <ONE-LINE confidence note — only when item clears floor in best-case
      only. Suppress entirely otherwise. Example: "Realistic case nets
      below floor at this buy point. Walk away if seller asks > $<stretch>.">

    Verify (priority order):
      - <top check — biggest value-tier impact>
      - <next>
      - <next>
      - <one more if essential>

    Ship: <weight class + est cost + Media Mail eligibility>
    Gotcha: <one line, ONLY if surfaced by PRICE research; suppress otherwise>

**Constraints on the buy card:**
- "Sells" line is ONE line with a range. Do not break out best/realistic/
  worst-case — collapse to a min–max range with the best-case scenario
  noted parenthetically.
- "BUY IF" and stretch on a SINGLE line. Bold the conservative threshold.
- Confidence note: ONE line max. Suppress entirely when the item clears
  the floor in realistic or all cases.
- Verify list: maximum 4 items. Lift the highest-value-impact actions
  from the Scenario bracket's "How to distinguish" section and from
  PRICE's pre-buy confirmations. Order by value-tier impact, not by
  alphabetical or chronological.
- Ship line: ONE line. Format: `~<weight>, <fragility>, <service> ~$<cost>`.
  Media Mail eligibility mentioned only when relevant.
- Gotcha: ONE line max, only when PRICE surfaced a genuine warning
  (e.g. label-date deception, repro tells, attribution traps).
  Suppress entirely otherwise.

### SKIPPED section

Heading:
    ## Skipped — below effective profit floor (<N>)

One-line bullets, format:

    - <ITEM NAME> (<tier>, <weight class>) — best case $<Y>, max profit $<net> vs. $<effective_floor> floor. <one-line verdict / bundle note>

The math line MUST include the weight class and the effective_floor
when the weight multiplier > 1.0× — that's where the user sees that
the item was filtered because of size/weight, not just low margin.

Examples:
    - Helix Oxford tin (vintage, light) — best case $30, max profit $18 vs. $100 floor. Bundle only.
    - Dress for Success paperback (vintage, light) — best case $10, max profit $3 vs. $100 floor. Skip.
    - Distressed cabinet (modern, oversized 25–50 lb) — best case $150, max profit $105 vs. $300 floor (3× weight multiplier). Furniture logistics burden exceeds margin. Skip.

No multi-line breakdowns. The user can drill into the price.txt file if
they want the full math; the buy list is for fast triage.

### DEFERRED section

Heading:
    ## Deferred — need more data (<N>)

One-line bullets, format:

    - <ITEM NAME> — <what's missing in one line>

Examples:
    - Magazine stack — needs individual cover shots to enumerate.
    - Partial box — only a corner visible, re-shoot in frame.

### Footer (one or two lines)

    ---
    <N> enumerated → pursue <N>, skipped <N> (below floor), deferred <N>.
    <One-line takeaway if useful — e.g. "Low-margin shoot; consider
    BUNDLE strategy next time" — otherwise omit.>

## Honesty rules

- Never invent comp data. Items without real PRICE ranges → DEFERRED,
  never QUALIFYING.
- The ⚠ confidence note is MANDATORY when an item clears the floor in
  best-case scenario only. One line, but never suppress it. The user
  is about to make a buy decision; they need to see the risk.
- Conservative buy point is the headline. Stretch is secondary, always
  with explicit "if best-case verified" condition.
- Skipped items show the math inline (best case $X, max profit $Y).
  The user must see WHY each item was filtered — not just that it was.

## Concision over completeness

If a field doesn't add to a buy decision, omit it. Specifically:
- No profit-floor math breakdown in the buy card (only in SKIPPED)
- No three-scenario price table (collapse to one range)
- No multi-line acquisition friction details (one line max)
- No long item-specific gotcha explanations (one line, only when
  genuinely warning-worthy)
- No section headers, footers, or visual flourishes that don't help
  decision-making

The price.txt file is the analytical record; CURATE is the action sheet.

## Strategy profile defaults

If no profile is supplied:

    margin_target:         0.50
    buy_point_multiplier:  0.5
    fee_pct:               0.13
    profit_floor:          100      (base — multiplied by weight class)
    drive_cost:            0

Weight-tier multipliers on the profit floor (default):

    Weight class             Multiplier  Effective floor
    ───────────────────────  ──────────  ───────────────
    Light    (<5 lb)         1.0×        $100
    Medium   (5–15 lb)       1.0×        $100
    Heavy    (15–25 lb)      1.5×        $150
    Oversized (25–50 lb)     3.0×        $300
    Freight  (50+ lb)        5.0×        $500
    Requires movers          skip unless ultra-high-value override

Shipping estimator (default, USD domestic):
    <1 lb:        $4
    1–5 lb:       $10
    5–15 lb:      $18
    15–25 lb:     $35
    25–50 lb:     $75 (freight or oversize)
    50+ lb:       $125+ (freight required)

Profiles override defaults. Surface the active profile name in the
header.

## Response brevity (mandatory)

Be substantially shorter than feels natural.

- Chat reply at end of a run: lead with the output path + a one-line count summary ("Buy list: 7 items above floor, 3 skipped"). Cap at 3-6 lines unless the user asked for detail. Do not restate the buy cards in chat.
- File content: skip preamble and recap-of-input. The buy list is the document — get to it.
- Banned filler: "Let me...", "I'll now...", "Looking at this...", "Based on the analysis...", "Note that...", "It's worth mentioning...", "Importantly...".
- Per-item buy cards: 4-8 lines each. Use a compact field:value layout, not a paragraph essay. Pre-buy confirmation checklist: bullets, no prose.
- Skipped-section entries: one line each with the math, not a paragraph.
- When in doubt, cut.
