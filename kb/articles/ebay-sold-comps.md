# eBay sold/completed comps — KB article

```yaml
topic: finding real sold-price comps on eBay
applies_to: [cross-category, pricing, comps, valuation]
version: 1
last_reviewed: 2026-06-21
sources:
  - eBay advanced search — Sold/Completed items filter (the primary comp source)
  - Internal: prompts/price.md (PRICE phase), lib/price_stats.py, lib/ (Apify scraper)
  - feedback_apify_optin / feedback_price_delivered_basis (policy memory)
```

## When to consult this

Any time a run needs **what an item actually sold for** — PRICE phase, a CURATE
buy decision, or sanity-checking a draft price. This article is the *method* for
turning eBay sold data into a defensible price; the deep policy (tiers, stats,
ceiling vetting) lives in [`../../prompts/price.md`](../../prompts/price.md) and
[`../../lib/price_stats.py`](../../lib/price_stats.py). Use this for the
search-craft and the pitfalls.

## Knowledge (the digest)

### Sold ≠ asking. Always.
**Active listings show hope; sold listings show the market.** Never price off
active/asking listings — filter to **Sold items** (and read completed/unsold as
a ceiling signal). The whole comp hunt is about *realized* prices.

### The comp-hunt ladder (our stack)
1. **Stage A — WebSearch / manual eBay sold search** for the exact item to scope
   the market and the right query words.
2. **Stage B — Apify** (default, un-gated): the
   [`automation-lab/ebay-sold-scraper`](../../lib/) pulls structured sold
   results (US proxy, correct currency). This is the workhorse — runs
   automatically in PRICE.
3. **Stage C — Chrome** (optional, only when confidence is low): drive a live
   eBay sold search in-browser for the exact comp or to read details Apify
   misses. Also the fallback for sources that block scraping.

### How to search eBay sold well (query craft)
- **Start specific, then broaden** (the query ladder): brand + line/pattern +
  model/size + key attribute → drop terms until you have enough comps.
- **Match the value-moving attributes:** maker, exact pattern/model, size,
  material, colorway, era. A "close enough" comp on the wrong variant misprices.
- **Use eBay search operators:** quotes for phrases (`"national line rainbo"`),
  `-` to exclude (`-repro -reproduction -lot`), `(a,b)` for synonyms.
- **Filter:** Sold items; correct **Condition**; and exclude **lots/bulk** when
  pricing a single (and vice-versa — see `unit_type`).
- **Thin market?** Broaden the query while like-condition comps < 3, then fall
  back to the closest comparable (per price.md's thin-market rule).

### Compare on the DELIVERED basis
Per [delivered-basis policy](../../prompts/price.md): compare comps on
**sold price + shipping** (delivered), not item-only — especially against
free-shipping listings where shipping is baked into the price. Parse the
scraper's `shippingCost` into a `total_price` and run the stats on the delivered
field. Then show **net-to-us** after fees/shipping.

### Reading the distribution (not one number)
Don't average a handful by eye. Per price-strategy-v2:
- **Conservative** ≈ 25th percentile · **Recommended** ≈ median of the
  like-condition cohort · **Push-high** ≈ vetted ceiling (else 90th pct).
- **Exact-match short-circuit:** a true exact comp anchors Recommended on the
  median of exact matches.
- **Outliers:** a survivor above ~2.5× median is a **ceiling candidate to vet**
  (confirm comparability), not an auto-drop.

## Red flags / gotchas

- **Currency leak:** non-US proxies return foreign-currency or foreign-market
  prices that look wrong — the scraper was migrated to a US proxy specifically
  to fix this. If prices look off by a FX-shaped factor, suspect the proxy/market.
- **Lots vs singles:** a "$60" sold comp that's actually a **lot of 12** is a $5
  item. Read the title/photos for quantity; honor `unit_type`.
- **Condition mismatch:** a Mint comp doesn't price a chipped item — keep the
  like-condition cohort strict; only pool Used grades when the cohort is thin.
- **Charm-price artifacts / bad parses:** a charm-price validator exists for a
  reason — sanity-check suspicious round/low values against the raw listing.
- **Best Offer "sold" prices** on eBay show the **list** price, not the accepted
  offer — the real sale was often lower. Treat BO solds as a soft ceiling.
- **Reproductions in the comp set:** repro/fake examples sell cheap and poison
  the median — exclude them (`-repro`) and cross-check authenticity.
- **Stale/seasonal:** a single old comp isn't the market; prefer recent solds
  and enough of them.

## How it maps onto our fields

- **PRICE → Conservative / Recommended / Push-high tiers** from the
  distribution, on the delivered basis, with net-to-us shown. The working price
  auto-adopts Recommended (provisional) per the headless gate.
- **PRICE → "how hard we looked"** report: which stages ran, query ladder used,
  n comps, exact-match or peer fallback.
- **CURATE → buy/skip:** compare expected sold (net-to-us) against acquisition
  cost + effort.
- **needs_followup:** if comps hinge on an unread attribute (size, pattern,
  mark), flag the inspection that would pin the right comp.

## Sources

- **eBay advanced search, Sold/Completed filter** — the primary realized-price
  source. (Active listings = asking, never price off them.)
- **[`../../prompts/price.md`](../../prompts/price.md)** — the full PRICE policy:
  query ladder, tiers, ceiling vetting, exact-match short-circuit, thin-market.
- **[`../../lib/price_stats.py`](../../lib/price_stats.py)** — the deterministic
  filtering + percentile/tier statistics.
- **Apify `automation-lab/ebay-sold-scraper`** — Stage B structured sold pull
  (US proxy). Marketplace Insights API is closed; Apify is the default path.
- Policy memory: `feedback_apify_optin`, `feedback_price_delivered_basis`,
  `project_price_strategy_v2`.
