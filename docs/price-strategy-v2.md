# PRICE strategy v2 — distribution-based (PROPOSAL, not yet implemented)

A proposal to re-examine PRICE now that Stage B (Apify) can cheaply return
two complementary views of the sold market. **Nothing here is wired in yet
— this is for review.**

## Why change

Today PRICE hand-picks a few comps from a single capped Apify pull and
reasons to three tiers. Two problems:

1. The pull defaulted to `price_high`, so the comp set leaned toward the
   ceiling (or, for niche queries, came back jumbled) — not a representative
   sample. The Recommended/working price can skew high.
2. "Pick the strongest comp" is subjective and cherry-picks.

We verified empirically (see the Apify investigation) that:
- **`best_match`** returns the representative body of the distribution.
- **`price_high`** genuinely returns the price+shipping high end, *sorted
  descending* (so the cap can't hide the expensive outliers) — modulo a
  row-0 anomaly and "results matching fewer words" padding on niche queries.

So we can now price on the **actual distribution** instead of a few comps.

## The core shift

From *"pick the strongest comps"* → *"characterize the sold distribution,
then place tiers on it."*

## Proposed pipeline (PRICE Stage B)

1. **Dual query** per selling unit (single/pair/set/lot per `unit_type`):
   - `best_match` — representative set (≈30 results, 2 pages).
   - `price_high` — ceiling/outlier set (≈20 results).
   - (Stage A WebSearch and Stage C Chrome keep their current roles: A broad
     context; C low-confidence fallback.)

2. **Normalize before any stats:**
   - Currency validation (existing charm-price guard).
   - **Strip noise:** drop the row-0 anomaly and "fewer-words" loose matches
     (title missing the key query tokens).
   - **Condition cohort:** bucket by condition; price the item against its
     like-condition cohort (New vs Used grades).
   - **Unit match:** compare only same `unit_type`; `duplicate` → per-piece.
   - **Exclusions (Tier C reasons, unchanged in spirit):** single-bid
     auctions, <50-feedback sellers, active/asking prices.

3. **Distribution stats** from the cleaned representative (`best_match`) set:
   - `n`, median, 25th/75th percentile (IQR), min/max.
   - Dispersion = IQR/median (or max/min) → confidence + rarity signal.

4. **Vet the `price_high` extremes** against the representative median:
   - A high item that is genuinely the same item/condition → a real **ceiling
     comp**.
   - A high item that's a bundle/mislisting/different model (e.g. >2–3×
     median and not comparable) → **excluded outlier** (logged, not silently
     dropped).

5. **Place the three tiers:**
   - **Exact-match short-circuit (unchanged):** if ≥1 true exact-match comp
     exists, anchor **Recommended** on it (median of exact matches); keep the
     distribution for context.
   - **Else distribution-based:**
     - **Conservative** = ~25th percentile of like-condition sold (no-objection floor).
     - **Recommended** (working price) = **median** of like-condition comparable sold.
     - **Push-high** = the vetted ceiling from `price_high` (a real comparable
       top sale); if none vets out, fall back to the representative ~90th pct.
   - Best Offer gate unchanged: enable if list > Recommended; auto-decline = Recommended.

6. **Confidence / data quality:**
   - `good`: n ≥ ~8 like-condition comps, tight IQR.
   - `partial`: n 3–7, or wide dispersion.
   - `thin`: n < 3 → **fall back to today's method** (anchor on closest comp /
     era-peer, widen the bracket, flag rarity). Don't compute percentiles off
     2 points.

7. **Report (price.txt):**
   - Distribution line: `n=… median=$… IQR=$…–$… ceiling=$…`.
   - Both run ids (best_match + price_high) as proof.
   - Comp URLs: the representative anchors + the vetted ceiling comps.
   - Three tiers, each labeled with its basis (percentile / median / vetted ceiling).
   - Outliers excluded + reason.
   - Confidence.

## What this fixes
- **Bias:** Recommended is the typical sold price (median), not the ceiling.
- **Outliers:** seen and judged explicitly, not hidden by a cap or dropped by a blunt rule.
- **Defensibility:** tiers trace to a distribution + sample size, not a vibe.
- **Confidence/rarity:** measured (n + dispersion + best_match/price_high overlap).

## Costs / caveats
- **2 Apify calls/item (~$0.20)** instead of 1 (already accepted).
- Must normalize condition + unit before stats, or the median lies.
- Must strip the row-0 anomaly + "fewer-words" padding (verified real).
- Thin markets are common in this inventory — the fallback matters.

## Open decisions (need your call)
1. **Percentiles:** Conservative=25th / Recommended=median / Push-high=vetted-ceiling-or-90th — OK, or different (e.g. Recommended = 40th to lean conservative)?
2. **Outlier cutoff:** exclude `price_high` items >2× median that aren't clearly comparable — too strict / too loose?
3. **Thin-market threshold:** n<3 → fallback. Right number?
4. **Condition:** price strictly within the same condition cohort, or pool Used grades together when samples are thin?
5. **Recommended basis:** median of *comparable* (same condition) vs median of *all* sold for the query?
6. **Keep the exact-match short-circuit** (anchor on an exact comp when one exists), or always blend with the distribution?
