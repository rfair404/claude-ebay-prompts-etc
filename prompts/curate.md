# CURATE — v4, Function 3

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** `<shoot-dir>/review.md` (overwrite).

Produce a prioritized buy list the user reads in the field, on a phone,
at the moment of purchase. Concise, scannable, decision-only. Reads
IDENTIFY + PRICE + the active strategy profile.

## Mode (pick one; state which in the output header)

CURATE runs in one of two modes — auto-detect from the shoot + intent:

- **A · BUY LIST** (default; acquisition) — distinct items you're deciding
  whether to *buy*. Per-item buy/skip/defer cards. Everything from "Each buy
  card…" down is Mode A.
- **B · LOT PLAN** (you *own* it; how to list) — a large owned lot of one
  category (IDENTIFY `group`/`wide`, high N; sell intent), e.g. hundreds of
  marbles. Output a **batching plan**: what to bulk, group, or single out. See
  "## Mode B" below.

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

## Mode B — Lot plan (owned bulk lot → listing batches)

Turn N owned items into the **fewest listings that capture the value**. The
governing fact (from the marbles specialization, true of most bulk lots):
**90%+ is filler.** Flag the worthy few; bulk the rest. Volume-biased by default.

Works off a photo hierarchy: a **hero shot** (whole lot — count + quality gauge
+ spot standouts), **grid/group shots** (the enumeration + triage workhorse;
ask for a contrasting matte background + a ruler in frame for size), and
**individual macros** only for the flagged few (prefer **backlit + raking-light**
shots, never a pole/pontil macro — these are vintage machine-made by default).

### Triage (lift each item/visual-group's value tier from IDENTIFY; don't re-infer)

- **FILLER ($)** — common, pennies (common cat's-eyes, Vacor, plain machine-made).
- **NAMED-COMMON ($$)** — identifiable named type, modest (e.g. Vitro Conqueror).
- **PULL ($$$)** — worth its own listing + research.
- **TROPHY ($$$$+)** — exact-comp hunt, push-high + Best Offer.

### Batch (the volume dial sets how aggressively)

- **FILLER → bulk lots.** Combine into "Lot of ~N mixed" listings; split by broad
  type only when it clearly lifts the total (cat's-eyes vs swirls). Cap at 1–3
  bulk lots for the whole filler mass. Price **as a lot**, never per piece.
- **NAMED-COMMON → themed group lots.** Group by type / colour / maker, ~6–20
  per lot ("10 vintage Vitro Conquerors, blues"). Themed lots beat random mixes.
- **PULL → single listings.** Full IDENTIFY → PRICE → DRAFT each.
- **TROPHY → single**, exact comp, push-high + Best Offer.
- **Volume dial** (`lot_strategy`): **`fast`** (default for Mode B) raises the
  single-out bar — bulk more, fewest listings, quickest turnaround; **`value`**
  lowers it — single out anything with upside. Set from the strategy profile.

### Order

Trophies → PULLs → themed lots → bulk lots (descending $ per listing). Surface
the **Pareto cut**: the few listings that capture ~80% of the value — do first.

### Format (Mode B)

    LOT PLAN — YYYY-MM-DD
    Source: <shoot> | Items: ~<N> | Strategy: fast/volume | Listings: <M>

    ## Do first — the Pareto cut (~<X>% of value in <k> listings)
    ### [#] SINGLE — <short name> — <TROPHY|PULL>
    Sells: $<low>–$<high>  ·  Best Offer: <y/n>
    Verify: <≤3, by value impact>  ·  Comp: <source / result>

    ## Themed group lots (<n>)
    - LOT — <theme> ×<count> — sells $<low>–$<high> (as a lot). Has: <which groups>.

    ## Bulk lots (<n>)
    - LOT — mixed <category> ×~<N> — sells $<low>–$<high>. <broad makeup>.

    ---
    ~<N> items → <M> listings (<s> single, <g> group, <b> bulk).
    Est. total $<low>–$<high>. Pareto: <k> listings ≈ <X>% of value.

### Honesty (Mode B)

- Group/bulk lots priced **as lots** — never imply per-piece value inside a lot.
- A piece earns a SINGLE only when a real comp clears the effort — else it goes
  in a lot. Don't single out filler hoping for upside.
- "~N" counts are estimates from the hero/grid shots — say so; refine as
  closer shots arrive.

## Strategy profile defaults (if none supplied)

    margin_target 0.50 · buy_point_multiplier 0.5 · fee_pct 0.13
    profit_floor 100 (× weight tier) · drive_cost 0
    lot_strategy fast (Mode B: bulk-bias; `value` to single out more)
    Shipping est (USD domestic): <1lb $4 · 1–5 $10 · 5–15 $18 ·
      15–25 $35 · 25–50 $75 · 50+ $125+

## Closing

Per _shared: path + one-line count — Mode A: "7 above floor, 3 skipped";
Mode B: "~N items → M listings (s single, g group, b bulk)". Don't restate cards.
