# PRICE strategy v2 — distribution-based (IMPLEMENTED)

Re-examines PRICE now that Stage B (Apify) can cheaply return two
complementary views of the sold market. **Implemented:** the deterministic
half (filtering + statistics + tier placement) lives in
[`lib/price_stats.py`](../lib/price_stats.py); the orchestration (dual query,
query ladder, ceiling vetting, exact-match short-circuit, reporting) is wired
into [`prompts/price.md`](../prompts/price.md). This document is the rationale
and the record of the policy choices below.

**Policy defaults chosen** (the "Open decisions" at the bottom — all
configurable as constants at the top of `price_stats.py`):
1. Conservative = 25th pct · Recommended = median · Push-high = vetted
   `price_high` ceiling, else 90th pct.
2. Outlier cutoff: a `price_high` survivor above **2.5× median** is surfaced
   as a *ceiling candidate to vet* (not auto-dropped) — the prompt confirms
   comparability, else falls back to the 90th pct.
3. Thin-market threshold: **n < 3** like-condition comps → closest-comp
   fallback. Broaden the query ladder while surviving comps < 3.
4. Condition: price within the like-condition cohort; **pool Used grades
   (with unknown) only when the strict cohort is thin**.
5. Recommended basis: median of **comparable (same-condition)** sold.
6. Exact-match short-circuit: **kept** — a true exact comp anchors
   Recommended on the median of exact matches.

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
  descending* (so the cap can't hide the expensive outliers). Two things to
  watch, NOT strip: a frequently **out-of-order first row** (cause
  unconfirmed — keep it and flag it each run for the human to judge), and
  "results matching fewer words" padding on niche queries.

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
   - **Flag, don't strip, the first row:** the first item is often out of
     order; cause unconfirmed, so KEEP it and surface a note each run for the
     human to judge. (Separately, "fewer-words" loose matches — title missing
     the key query tokens — can be down-weighted.)
   - **Unit match (biggest lever):** when pricing a single, EXCLUDE multi-item
     listings — title contains `lot`/`lots`/`set`/`bundle`/`complete`/
     `collection`, multiple distinct years, or `N issues/pcs/pieces`. (Demo:
     m-agni raw $10–$506 → after excluding lots $10–$144, IQR $27–$115 →
     $24–$45.) `duplicate` → per-piece; when pricing a lot, keep only lots.
   - **Same-item (core tokens):** require the brand/type tokens from IDENTIFY
     in the comp title — drops loose keyword matches. (Demo: eagle-art's $250
     non-needlepoint outlier removed by requiring "needlepoint".)
   - **Condition cohort:** bucket by condition; price the item against its
     like-condition cohort (New vs Used grades).
   - **Exclusions (Tier C reasons, unchanged in spirit):** single-bid
     auctions, <50-feedback sellers, active/asking prices.

3. **Distribution stats** from the cleaned representative (`best_match`) set:
   - `n`, median, 25th/75th percentile (IQR), min/max.
   - **Report the IQR + median as the core comp range — NOT min–max.** After
     the unit/same-item/condition filters, the middle 50% is the robust price
     basis; min–max is dominated by the outliers those filters target.
   - Dispersion = IQR/median (or max/min) → confidence + rarity signal.
   - Anything outside the IQR fence is an outlier to vet — and the `price_high`
     set is exactly where those high-side outliers live.

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

## Thin results → broaden the query (NOT a new draft)

The comp **search query** is internal to PRICE and is **not** the listing
**title**. Broadening a query never changes the `draft.md`, the SEO title,
the SKU, or the ledger record. (A *new draft* — and its re-record — happens
only if the actual listing title/identification changes, e.g. DRAFT
re-renders after a mis-ID. That's separate and deliberate.)

Build queries from IDENTIFY's structured short fields (Brand / Type / Era /
Category) — which IDENTIFY already separates from the rich prose — as a
specificity ladder, so we never have to "simplify the SEO title":

- **L1 (specific):** Brand + Type + Era [+ one distinguishing word]
- **L2 (broader):** Type + material; drop era + modifiers
- **L3 (broadest):** category noun (+ material)

Fallback rule: run `best_match` + `price_high` at L1. If the combined unique
comps are below `MIN`, step down (L2, then L3) and re-run, until enough
comps or the ladder is exhausted. Log each formulation in the Hunt line.
Note: `price_high` often returns data when `best_match` is empty (observed:
mask-red `best_match`=0, `price_high`=25), so an empty `best_match` doesn't
block — broaden for representativeness, but you already have a usable set.

Open decision: `MIN` comps before broadening (0 only, or < 3?) and how many
ladder steps.

## What this fixes
- **Bias:** Recommended is the typical sold price (median), not the ceiling.
- **Outliers:** seen and judged explicitly, not hidden by a cap or dropped by a blunt rule.
- **Defensibility:** tiers trace to a distribution + sample size, not a vibe.
- **Confidence/rarity:** measured (n + dispersion + best_match/price_high overlap).

## Costs / caveats
- **2 Apify calls/item (~$0.20)** instead of 1 (already accepted).
- Must normalize condition + unit before stats, or the median lies.
- Surface (don't strip) the out-of-order first row each run; cause
  unconfirmed. "Fewer-words" padding on niche queries is verified real.
- Thin markets are common in this inventory — the fallback matters.

## Lessons from live runs

**Size (and other un-tokenizable grade axes) is not a filterable token —
the broad median can under-price.** First live end-to-end run (2026-06-10,
`inventory/sand-dollars`, lot of 18 ~3in white keyhole sand dollars): the engine
cleanly filtered unit/condition and reported a used-cohort **median of $18**
— but that pools *every* sand-dollar lot regardless of piece SIZE, and size
is the dominant price driver here (tiny 1.5–2in lots sell $10–15; ~3in lots
sell $24.99–30). The token filter can't separate them because eBay titles
express size inconsistently ("2-3in", "3 inch", "2.5-3.5\"", or not at all),
so a size term in the query/`require_tokens` is unreliable. The
**exact-match short-circuit** is what corrected it: two genuine size+grade
matches (13 @ 2.5-3in $24.99; 20 @ 2-3in $30) anchored Recommended at **$28**,
well above the pooled median. (Filter note observed the same run:
`looks_multi_item` misses a *bare leading count* — "25 White Keyhole…" reads
as single-item — directionally harmless here but a known gap.)

**How to apply now:** for any size- or grade-driven category, don't take the
engine's broad median at face value — anchor on the size/grade sub-cohort via
the exact-match short-circuit, and say in `price.txt` that the broad median is
a mix artifact (as the sand-dollars run did).

**↻ Revisit in the future:** the engine could make this less manual — e.g. a
size-bucket extractor that parses inch/cm ranges from comp titles and prices
within the item's size band (treating size like a condition cohort), and/or
fixing `looks_multi_item` to catch a bare leading count. Worth doing once more
live runs show how often the size-mix artifact actually bites; until then the
short-circuit + an honest note is the documented workaround.

## Open decisions — RESOLVED (defaults shipped; tune in `price_stats.py`)

The proposal shipped with the defaults below. Each is a named constant at the
top of [`lib/price_stats.py`](../lib/price_stats.py) — change it there in one
place, no prompt edit needed. Revisit any of these if real runs argue for it.

1. **Percentiles** — `CONSERVATIVE_PCT=25` / `RECOMMENDED_PCT=50` (median) /
   Push-high = vetted ceiling, else `PUSH_HIGH_FALLBACK_PCT=90`. (Open knob
   if Recommended should lean conservative: set `RECOMMENDED_PCT=40`.)
2. **Outlier cutoff** — `OUTLIER_MULT=2.5`. A `price_high` survivor above
   2.5× median is surfaced as a ceiling candidate **to vet**, not auto-
   dropped (the module can't judge comparability; the prompt does).
3. **Thin-market threshold** — `THIN_N=3`. Below it: closest-comp fallback;
   broaden the query ladder while surviving comps < 3.
4. **Condition** — like-condition cohort; pool Used grades (+ unknown) only
   when the strict cohort is thin (`pool_used_when_thin`, on by default).
5. **Recommended basis** — median of *comparable* (same-condition) sold.
6. **Exact-match short-circuit** — kept; anchors Recommended on the median of
   exact matches when the hunt finds one.
