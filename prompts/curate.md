# CURATE — v3, Function 3

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/review.md` (overwrite).

Produce a prioritized buy list the user reads in the field, on a phone,
at the moment of purchase. Concise, scannable, decision-only. Reads
IDENTIFY + PRICE + the active strategy profile.

## Each buy card answers four things at a glance

1. Worth pursuing? → headline buy threshold.
2. Upside? → one sale-range line.
3. Risk? → one-line warning, only if real.
4. What to check? → ordered verify list (≤4).

Everything else is noise. No profit-floor math in the card, no price
tables, no full research — that's in price.txt.

## Selling-unit scaling

Card scales to the listing unit. `single` → `BUY IF ≤ $X`; `pair` →
`…for the pair`; `set` → `…for the set of N`; `lot` → `…for the lot of
N`; `duplicate` → show per-piece buy point + `Total ≤ $(X×N)`, and treat
each unit as a separate listing for the floor (each must clear alone).
Weight tier uses the packed selling-unit weight.

## Filter (in order, before sorting)

1. **Not-for-sale** (`none (not for sale)`) → dropped entirely.
2. **Weight-adjusted profit floor:**

       max_profit = best_case_sale_upper × (1 − fee_pct)
                    − shipping_estimate − conservative_buy_point
       effective_floor = profit_floor × weight_multiplier

   Multipliers (default base $100): light 1.0× $100 · medium 1.0× $100 ·
   heavy 1.5× $150 · oversized 3.0× $300 · freight 5.0× $500 ·
   requires-movers SKIP (unless authenticated >$2000, owner-confirmed).
   Lift the weight tier from IDENTIFY's `Estimated weight`, don't
   re-infer. `max_profit < effective_floor` → SKIPPED (show the math
   incl. weight tier + elevated floor).
3. **Insufficient data** (PRICE `Data quality: thin`, or provisional
   group needing re-shoot) → DEFERRED.

## Sort (qualifying)

Collectability tier (`collectable > antique > vintage > modern`), then
best-case upper bound descending within tier.

## Buy-point math

- **Conservative** (headline) = `worst_case_sale_floor ×
  buy_point_multiplier`.
- **Stretch** = `best_case_sale_upper × (1 − fee_pct) − shipping −
  effective_floor` — max buy where best-case still nets the floor; only
  "if best-case verified".

## Format

    BUY LIST — YYYY-MM-DD
    Source: <shoot> | Profile: <name> | Floor: $<profit_floor>
    Sort: tier → best-case desc. Filtered: not-for-sale + below-floor.

    ## Qualifying — pursue these (<N>)

    ### [N] <SHORT NAME> — <TIER>
    Sells: $<low>–$<high>  (best case: <one-line scenario>)
    **BUY IF ≤ $<conservative>**  ·  stretch ≤ $<stretch> if best-case verified
    ⚠ <one line — ONLY when item clears floor in best-case only; else suppress>
    Verify: <top check> · <next> · <next>   (≤4, by value-tier impact)
    Ship: ~<weight>, <fragility>, <service> ~$<cost>
    Gotcha: <one line, ONLY if PRICE surfaced a real warning; else suppress>

    ## Skipped — below effective floor (<N>)
    - <NAME> (<tier>, <weight class>) — best $<Y>, max profit $<net> vs $<floor> floor. <verdict>

    ## Deferred — need more data (<N>)
    - <NAME> — <what's missing, one line>

    ---
    <N> enumerated → pursue <N>, skipped <N>, deferred <N>.

The SKIPPED math line MUST show weight class + effective_floor whenever
the multiplier > 1.0× — that's where the user sees size/weight, not just
margin, was the reason.

## Honesty

- No real PRICE range → DEFERRED, never QUALIFYING.
- The ⚠ note is MANDATORY when an item clears the floor in best-case
  only — never suppress it there.
- Conservative is the headline; stretch is always conditioned on
  "if best-case verified".
- Skipped items always show the math.

## Strategy profile defaults (if none supplied)

    margin_target 0.50 · buy_point_multiplier 0.5 · fee_pct 0.13
    profit_floor 100 (× weight tier) · drive_cost 0
    Shipping est (USD domestic): <1lb $4 · 1–5 $10 · 5–15 $18 ·
      15–25 $35 · 25–50 $75 · 50+ $125+

## Closing

Per _shared: path + one-line count ("7 above floor, 3 skipped"). Don't
restate cards.
