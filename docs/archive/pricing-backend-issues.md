# PRICE Stage B backend — reliability issues & the path to a less-flaky solution

> **RESOLVED 2026-08-15 — Apify removed.** Stage B now runs through the user's
> logged-in Chrome (`lib/ebay_sold_browse.py`). `lib/apify_ebay.py` moved to
> `deprecated/`. Everything below is kept as the history that led here.
>
> **The root cause, finally measured.** eBay migrated search results from the
> legacy `li.s-item` markup to a new `li.s-card` layout. On a live sold search:
>
> ```
> li.s-item  ->  0 elements
> li.s-card  -> 62 elements
> ```
>
> Every community actor was written against `.s-item`, so they parse **zero
> rows off a page that renders perfectly**. That is the real explanation for
> "0 items with a SUCCEEDED status" — the proxy blocks were a second,
> independent failure (cirkit 403s on warm-up because it burns OUR Apify
> residential pool, which eBay blocks; automation-lab is 404/deleted).
>
> No amount of actor-swapping or health-gating fixes a market-wide selector
> break. The browser has no proxy to block, no third party to delete the
> scraper, and costs nothing per query.
>
> **eBay's anti-scrape traps** (all handled in `ebay_sold_browse.EXTRACTOR_JS`):
> decoy cards linking to a fake `/itm/123456`; "Sponsored" rendered reversed as
> `derosnopS` so it can't be string-matched (and present on nearly every card,
> so useless as a filter); and adjacent spans with no whitespace, so raw
> `textContent` welds them together (`"Sell one like this"` + seller name →
> `thiscelticitaliansilver`).
>
> Measured result on a live 60-comp page: **60/60 extracted, 0 missing** across
> title, sold date, delivered shipping, seller, feedback, URL and thumbnail —
> plus `Best offer accepted`, which no actor ever exposed.

_Status: 2026-07-29. Written after a multi-day Stage B outage during the more-mags-444 / cats-mags estate batches._

Stage B (Apify eBay-sold scraping) is the pricing tool's comp source. It has proven **flaky**, and this note documents *why*, the *mitigation shipped*, and *options for a durable fix*. See also [price-strategy-v2.md](price-strategy-v2.md) and memory `apify_actor_down_2026_07_27`.

## What went wrong

**The configured actor fails SILENTLY when eBay blocks it.** Our primary actor `automation-lab/ebay-sold-scraper` returned **0 items with a SUCCEEDED status** for *every* query (2026-07-27 → 07-29), including controls with thousands of live sold comps ("Nike Air Force 1", "1847 Rogers Bros First Love"). Its run log:

```
INFO  Using residential proxy (US)
INFO  Searching eBay completed listings for "Nike Air Force 1"
INFO  No listings on page 1, stopping search
INFO  Search complete: 0 sold listings scraped      (exit 0 — "SUCCEEDED")
```

eBay served its residential proxy a blocked/empty/challenge page; the actor parsed zero listing elements and **exited success with 0 items**. This is the worst failure mode: **0 comps looks identical to a genuinely thin market**, so pricing silently degrades to guesses instead of erroring. (Apify's "95.9% success rate" counts non-crashing runs, not runs that return data — so it stayed green while broken.)

Corroborating: our own in-app browser hit eBay's "Security Measure / verify yourself" CAPTCHA on sold searches the same days. **This is an eBay-side anti-bot escalation**, hitting multiple scrapers at once — not our config or code.

## Actors tested (2026-07-29 control = "Nike Air Force 1")

| Actor | Result | Notes |
|---|---|---|
| `automation-lab/ebay-sold-scraper` (primary) | **0 items**, SUCCEEDED | Blocked by eBay; fails silently. Full-featured (bids, seller feedback, dual sort). |
| `scrapesmith/ebay-sold-listing-scraper` | **0 items** | Same silent block. |
| `omao/ebay-sold-scraper` | **works** via API, but bare | Has anti-bot "warmup" → gets through. BUT: no `sort`, no seller feedback, no bids; shipping is free-text. Also **crashes when called via the Apify MCP tool** (`meta.origin='MCP'` fails its SDK enum — call it via REST/CLI only). |
| `khadinakbar/ebay-sold-comps-analytics-scraper` | **works** via API, feature-rich | Gets through. Has `sortBy` incl. `best_match`+`price_desc` (dual query), **numeric `shippingCost`** (delivered basis), condition + listing-type. **Missing:** bids-count, seller-feedback-score. |

## Mitigation shipped (2026-07-29)

`lib/apify_ebay.py` now has a **per-actor adapter + automatic fallback**:

- Primary stays `automation-lab` (full-featured) so it's used the moment eBay unblocks it.
- When the primary returns **0 comps** and the caller didn't pin an actor, it **auto-retries once with the fallback** `khadinakbar/ebay-sold-comps-analytics-scraper` (different proxy that currently gets through), maps its schema to our `CompRecord`, and proceeds. Surfaced in `LAST_RUN["fallback_actor"]` and the CLI ("FALLBACK USED …").
- Toggle: `APIFY_EBAY_FALLBACK_ACTOR` env (or `--no-fallback`); set to `""`/`none` to disable.
- **Preserved on fallback:** dual-query distribution (best_match + price_desc), delivered/total-price basis (numeric shipping), condition + unit + outlier `price_stats` filters. Verified end-to-end: "J.Crew catalog 1995" → 12 comps → `price_stats` n=10, median $62.50, filters fired.
- **Degraded on fallback:** the **single-bid-auction** and **low-seller-feedback** drop filters become no-ops (khadinakbar exposes neither; no currently-unblocked actor does).

## Health gate shipped (2026-08-15)

The control-query check is **no longer a standing human rule — it's enforced in code.**

`search_ebay_sold(..., health_gate=True)` (default): when a search returns **0 comps**, it re-probes with a control query (`"Nike Air Force 1"`, override `APIFY_EBAY_CONTROL_QUERY`) against the primary and fallback, then resolves the ambiguity:

| Control result | Verdict | Meaning |
|---|---|---|
| ≥ 5 comps | `THIN` | Backend healthy — the market really is empty. Priceable signal. |
| < 5 comps | `BLOCKED` | Raises **`BackendBlockedError`**. Not data. Not priceable. |

- The probe runs **only on an empty result**, so the happy path costs nothing extra.
- `LAST_RUN["verdict"]` is always one of `OK` / `THIN` / `BLOCKED` / `UNVERIFIED_EMPTY`.
- CLI: `python lib/apify_ebay.py --control` for a standalone health check (**exit 0** healthy, **exit 3** blocked); `--no-health-gate` opts out and yields `UNVERIFIED_EMPTY`.
- `prompts/price.md` now branches on all three states; `BLOCKED` forbids descending the query ladder and forbids contributing a tier.

**First live run (2026-08-15) immediately caught a real outage:** the *primary* `cirkit/ebay-product-scraper` returned **0 comps on the control** (run `DpKaHxZnaES90hwq5`) while the fallback `khadinakbar` returned 10 (run `3xYu9DN8X1n1TPNji`). Stage B is currently up **only via the fallback** — exactly the condition that used to be invisible.

## Remaining issues / why it's still flaky

1. **We depend on third-party community actors.** They're low-usage (1–94 monthly users), can break, change schema, or be deprecated at any time, and each has different anti-bot resilience. Today's working fallback may be tomorrow's silent-0.
2. ~~Silent-failure risk persists at the fallback layer~~ — **closed by the health gate above** for the total-block case. **Still open: PARTIAL blocks.** The gate only fires on *zero* comps; a degraded actor returning 2–3 junk comps passes straight through and gets priced. Needs a plausibility check on small result sets.
3. **Fallback data-quality caveat:** khadinakbar's sold-filter looked slightly leaky in testing — a couple of results were *our own freshly-listed active items* stamped with today's "sold" date. Needs verification that it returns genuinely-ended listings only; until then, sanity-check comp sold-dates and treat same-day "sold" of your own SKUs with suspicion.
4. **eBay blocks are intermittent** — the whole scraping approach is inherently subject to eBay's anti-bot cycles.

## Options for a less-flaky long-term solution

Roughly cheapest/least-work → most-robust:

1. **Multi-actor race + health gate (low effort, recommended next).** Keep 2–3 known-good actors; run the control query, pick whichever returns data, and only then run the real query. Turns silent-0 into an explicit "all backends blocked" signal. Extends the fallback we just shipped.
2. **Chrome-with-login as a reliable Stage C.** `claude-in-chrome` drives the user's *logged-in* eBay session; logged-in users are challenged far less than anonymous scrapers. Good manual/high-value fallback when all actors are blocked (our anonymous in-app browser got CAPTCHA'd; a logged-in one likely won't).
3. **eBay Terapeak (official sold data).** eBay's own product-research tool exposes 1–2 years of real sold comps through the seller's account (free with most eBay stores). Pulling comps via the authenticated account sidesteps anti-bot entirely. Best "official" sold-price source available to us — worth investigating an export/automation path.
4. **Paid dedicated scraping provider** (Zyte/ScraperAPI/Bright Data eBay templates). Managed residential proxies + CAPTCHA handling = higher reliability, at higher $/query. Removes the community-actor dependency.
5. **eBay official APIs — mostly unavailable for sold data.** Browse API = *active* listings only (asking prices, already used in `lib/ebay_browse.py`). **Marketplace Insights API** (true sold data) is gated/closed to most developers (404/denied for us — see memory `ebay_api_seller_access`). Finding API is deprecated. So there is no easy official sold-price API; Terapeak (via the account) is the practical official route.
6. **Accumulate our own comp cache.** Every run already saves comp JSON (`apify_runs/` + shoot dirs). Over time this builds a private sold-comp history that reduces dependence on live scraping for repeat categories.

**Recommendation:** ship #1 (multi-actor health-gated race — small extension of the fallback) as the immediate resilience win, and evaluate **#3 Terapeak** as the durable official-data path. Keep #2 (Chrome-logged-in) as the human-in-the-loop backstop for high-value items.
