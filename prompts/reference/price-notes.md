# PRICE — rationale, history, and evidence

Companion to [prompts/price.md](../price.md). The prompt holds the rules; this
file holds *why* they exist, so the prompt stays a checklist. Read this only
when a rule is disputed or being changed — never as part of a routine PRICE run.

## Why distribution-based (v2)

v1 picked "the strongest comp" by eye. v2 replaced that with: characterize the
cleaned sold distribution, then place tiers on it. Two complementary sold-market
views (`best_match` = representative body, `price_high` = ceiling) feed
`lib/price_stats.py`, which does deterministic filtering + statistics, so the
tiers trace to a sample size + percentiles rather than a hand-picked comp. The
exact-match hunt survives because a true exact comp beats any distribution.
Full design: [docs/price-strategy-v2.md](../../docs/price-strategy-v2.md).

## Fee band — measured, not assumed

The prompt used to say "13% + $0.40". Across 92 real sales the account actually
paid **16.0% blended**, and the rate is regressive (small sales pay a larger
share), hence the banded table in the prompt. Re-measure with
`python lib/report.py --performance` (REPORT, Function 7) and update the
prompt's table when the bands move. Never quote net-to-us from a remembered
rate — that's how the 13% figure went stale.

## Why the delivered basis

eBay comps almost always charge buyer-paid shipping, so a comp's `sold_price`
understates the buyer's real outlay by the shipping cost — often $10–25 on
breakables and heavier items. Our free-shipping default means OUR list price is
a delivered price; comparing it to comps' item-only prices made fair prices
look like pushes above market. `lib/ebay_sold_browse.py` computes and saves
`total_price` automatically — the in-page extractor reads "+$24.25 delivery" →
24.25 and "Free delivery" → 0 straight off the card, so `total_price` is
always present.

## Why silver pushes high

Our silver was systematically UNDER-priced and sold immediately — money left
on the table, confirmed across multiple sales. "Sold immediately" is the tell
that the list price was below market. The trigger is deliberately broad (any
"silver", precious or not) because the under-pricing pattern showed up on
plate and base-metal "silver" too, not just sterling.

## Apify history (why Stage B is the browser)

Stage B used to run through Apify actors. Every actor we depended on was
eventually blocked or deleted, and a blocked actor returns 0 items with a
SUCCEEDED status — indistinguishable from a genuinely thin market, which is
how a dead backend once nearly priced a batch. Disabled 2026-08-15
(`apify.enabled: false`; calls raise `ApifyDisabledError`). The logged-in
browser replaced it: the anonymous in-app browser is served a "Security
Measure / verify yourself" CAPTCHA (never solve it), but the user's real
logged-in Chrome (claude-in-chrome) is not challenged.

What the browser gives that no actor did: `Best offer accepted` (a sale at
that flag means the ask was NOT the clearing price — soft ceiling), delivered
shipping, seller feedback score + %, bid counts, item URLs and thumbnails.
Measured on a live 60-comp page: every field populated, 0 missing.

The Research-log template once demanded Apify run ids as proof Stage B ran;
after the browser migration the equivalent proof is the two saved per-sort
JSONs + their `n` counts. The BLOCKED/THIN distinction survives in browser
form: THIN = the page itself says "0 results"; BLOCKED = the extractor returns
`n:0` on a page with visible results (selector drift — fix the extractor, and
Stage B contributes no tier until it's fixed).

## Extractor anti-scrape guards (do not "simplify" away)

Three traps the in-page extractor already handles, each learned the hard way:

1. **Placeholder cards** — eBay salts results with fake cards pointing at item
   id `/itm/123456`; the extractor requires a ≥9-digit id.
2. **Reversed "Sponsored"** — rendered as `derosnopS` so it can't be
   string-matched; it also appears on nearly every card, so it is useless as a
   filter either way.
3. **Welded spans** — adjacent spans have no whitespace, so raw `textContent`
   joins them ("Sell one like this" + seller name →
   `thiscelticitaliansilver`); the extractor joins leaf nodes with newlines.

## Why the thumbnail board is a hard rule

The user verifies every comp by eye and by click — a text list forces them to
open each URL blind. Remote `<img src="https://i.ebayimg.com/…">` renders as a
broken image in the sandboxed chat surface (CSP blocks the host), which is why
the thumbnails must be downloaded, resized, and embedded as base64 `data:`
URIs. See `_shared.md` "Showing comps to the user".

## Ladder empirics

`price_high` often returns data when `best_match` is empty (observed:
mask-red query — `best_match`=0, `price_high`=25). That's why an empty
`best_match` doesn't block: `price_stats` promotes `price_high` to the
representative set and flags it in its output.
