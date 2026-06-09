# Apify Actor bug check — 2026-05-26

**Target:** Polo by Ralph Lauren Volume II, Fall 1977 catalog (lot2b).
**Query:** `polo ralph lauren 1977 catalog volume II`
**Actor:** `caffein.dev/ebay-sold-listings`
**Spend:** $0.24 total ($0.12 × 2 runs)

## Raw artifacts (saved alongside this file)

- `apify_run1.json` — 17 items
- `apify_run2.json` — 30 items
- `apify_run1.stderr.txt`, `apify_run2.stderr.txt`

## Historical bug (from V2 PLAN.md, prior session)

Same query, same itemId `267583308210`, multiple runs reportedly produced
prices off by exactly **5.017×** (the BRL/USD rate):

- Run XPEs:  $280   (correct USD)
- Run Jy1aq: $1404.82   (5.017× — Brazilian proxy returned BRL labeled as USD)
- Run IDy56: $280   (correct USD)

Cause attributed to: scraping proxy IP rotation between USD and BRL eBay
locales.

## This session's findings

### 1. The 5.017× BRL inflation bug DID NOT reproduce

Item `267583308210` returned **$280 in both run #1 and run #2** — the
correct USD price. Of 11 itemIds appearing in BOTH runs, every single
price matched at ratio 1.000:

| itemId          | run1     | run2     | ratio | verdict |
|---|---|---|---|---|
| 397654852122    | $295.00  | $295.00  | 1.000 | MATCH   |
| **267583308210**| **$280** | **$280** | 1.000 | MATCH (historically-buggy item, behaved correctly) |
| 206292211743    | $225.00  | $225.00  | 1.000 | MATCH   |
| 396760219230    | $199.00  | $199.00  | 1.000 | MATCH   |
| 227237772600    | $195.00  | $195.00  | 1.000 | MATCH   |
| 206292220228    | $150.00  | $150.00  | 1.000 | MATCH   |
| 397503117996    | $105.00  | $105.00  | 1.000 | MATCH   |
| 188229548894    | $99.00   | $99.00   | 1.000 | MATCH   |
| 366298640109    | $60.00   | $60.00   | 1.000 | MATCH   |
| 366218211878    | $45.00   | $45.00   | 1.000 | MATCH   |
| 267500628367    | $44.99   | $44.99   | 1.000 | MATCH   |

**Verdict:** in 2 of 2 runs on this query, the BRL-inflation bug was
NOT triggered. The bug is either rare (proxy-IP-dependent — needs a
Brazilian exit to fire) or has been quietly fixed upstream by the
Actor maintainer.

### 2. SEVERE coverage variability between runs (new finding — bigger issue than the BRL bug)

Run #1 returned 17 items. Run #2 returned 30 items. **Only 11 itemIds
overlap.** Of the 19 items unique to run #2, the most important is:

- **`206207424548` — "RALPH LAUREN POLO VINTAGE CATALOGUE Fall 1977 volume 2 Very Good vintage cond." @ $214.00**

This is the **EXACT MATCH** comp for our subject item (Polo Vol II /
Fall 1977 catalog). Run #1 missed it entirely. Run #2 surfaced it.
Same query, same parameters, same Actor, same minute.

If we had relied on run #1 alone, we would have had ZERO direct Vol II
/ 1977 comps from Apify — we would have only seen era-peers ($150,
$200, $225, $295) and been forced to triangulate. Chrome surfaced this
$214 comp consistently.

**Verdict:** the bigger practical risk for PRICE is not 5× price
inflation; it's **silent comp omission**. Critical exact-match comps
can be invisible to any single Apify call. Cross-referencing against
Source B (Chrome) catches this.

### 3. GBP→USD silent currency conversion (new finding)

Two items in run #1 came from UK seller `alciren`:

| itemId | Apify reported | Actual eBay listing | Conversion rate |
|---|---|---|---|
| 168196425053 | $94.51 USD  | GBP 69.99 | ~1.350 |
| 168278201194 | $121.52 USD | GBP 89.99 | ~1.350 |

Apify silently converted GBP → USD at ~1.350 and labeled
`sold_currency: USD`. The math is internally consistent (£69.99 ×
1.350 ≈ $94.49, very close to $94.51), but the `sold_currency` field
is **misleading** — the true currency of sale was GBP, not USD.

Verified via Chrome navigation to the actual eBay item pages —
both display "GBP 69.99" and "GBP 89.99" prominently, not USD.

**This is a different bug class from the BRL one:**
- BRL bug: untransformed BRL amount returned as USD (5× nominal inflation)
- GBP bug: GBP amount correctly converted but `sold_currency` field lies

Implication for PRICE: if a UK comp is the strongest available, the
USD-converted price may look unusually precise (cents like $94.51, not
typical $99 / $89 round eBay numbers), and the `sold_currency` field
won't flag it. Round-number heuristic check is a good sanity guard.

Run #2 did not include these `alciren` items at all — another instance
of the coverage-variability problem.

### 4. The non-Polo noise in run #2

Run #2 includes 11 results for vintage car parts (windshield wiper
remover, wire loom retainers, door panel clips, etc.) all priced
$38–$59. None are Polo catalogs. Most are tagged `For: 19...`
suggesting "(For: 1977 ...)" appears in the listing title and triggered
the "1977" keyword match.

This is normal eBay tokenizer behavior, not a bug, but it does
illustrate that broader keyword matches dilute results. Run #1 with
its smaller result set returned 0 of these noise items — could be
proxy-side or sort-side filtering luck.

## Recommendation

1. **The BRL 5× bug appears unreproducible on the current query.**
   Either fixed upstream, or only triggered when the Apify proxy
   randomly exits via a Brazilian IP. Treat it as a known-but-rare
   failure mode; the GBP-conversion and coverage-variability issues
   are more pressing in practice.

2. **Coverage variability is a real ongoing problem.** No single
   Apify call gives you the full sold-comp set. For high-stakes PRICE
   work, either:
   - Run Apify 2-3 times and merge by itemId (cost: $0.24-$0.36)
   - Always cross-reference against Source B (Chrome) — Chrome
     consistently surfaced the $214 comp; Apify missed it 1 of 2 times

3. **GBP→USD silent conversion needs a wrapper fix.** Either:
   - Wrapper detects round-number heuristic (`.49`, `.51`, `.52`
     suffixes on prices in non-typical-eBay-price ranges) and flags
   - Fetch the source listing currency explicitly and override
     `sold_currency` when it conflicts with the visible eBay price

4. **Switch-Actor evaluation remains worthwhile.** The
   `caffein.dev/ebay-sold-listings` Actor has now produced two distinct
   data-integrity issues (BRL inflation per V2 PLAN.md, GBP conversion
   per this session). Worth testing one of the alternatives:
   - `astronomical_reception/ebay-sold-lite`
   - `marielise.dev/ebay-sold-listings-intelligence`
   - `midwest_united/ebay-sold-comps`

## Cross-source summary (this session)

| Source | Cost | Result count | Found $214 exact-match Vol II? | Notes |
|---|---|---|---|---|
| A — WebSearch | $0 | ~10 link hits | NO (WorthPoint only) | Indexing too shallow for eBay sold listings |
| B — Chrome → eBay | $0 | 24 visible on page 1 | **YES** (consistently) | Bot challenge on first attempt, resolved on retry |
| C — Apify run #1 | $0.12 | 17 items | NO | Missed it; GBP-conversion artifacts present |
| C — Apify run #2 | $0.12 | 30 items | **YES** (`206207424548`) | Found it; coverage shifted from run #1 |

The $214 exact-match anchor in the existing price.txt remains valid.
No price revisions needed.
