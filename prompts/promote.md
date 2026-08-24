# PROMOTE — v3, Function 8 (paid placement)

Obeys [`_shared.md`](_shared.md). Read it first.

**Output:** printed to chat, plus `reports/promote_plan.json` when a plan is
worth keeping. **No writes to eBay, ever, from this phase.**

The only phase that spends money on an item AFTER it is listed. Everything
before it decides what to sell and at what price; PROMOTE decides what to pay to
put a listing in front of people, and — just as important — what not to pay for.

Runs after REPORT, never before it. Promotion is a bet on a listing, and the
evidence for the bet lives in REPORT's numbers.

---

## The gate: it proposes, you enact

    python tools/promote.py --budget 20 --top 40

The tool reads campaigns, ads, live listings and eBay's own suggested items,
ranks them for the goal, and prints the plan plus the exact API calls that would
enact it. It does not make those calls. Creating a campaign, adding an ad and
setting a bid are writes against a live account with real money attached, and
they stay the operator's keystroke — the same shape as the PREP gate and
`list_edit`'s `--confirm`.

This is not timidity about the API. It is that an ad added by mistake is not
free to undo: it spends before anyone notices.

## Goal: MAXIMISE REVENUE — and what that means mechanically

Rank by ask value, **gated on demand**. Score is `ask x estimated search
impressions`, where the impression estimate is eBay's own, taken from
`suggest_items`.

Both halves are load-bearing:

- **Ask alone promotes expensive things nobody searches for.** A $470 stoneware
  jar and a $470 item with no query volume are the same ask and not the same
  bet.
- **Impressions alone promotes cheap things with traffic.** A $12 catalog with
  3,000 impressions wins on eyeballs and cannot move the revenue number.

A listing eBay does not suggest is **not eligible at all**. That is the demand
gate: absent a signal, promotion is paying to shout into a category nobody is
browsing.

The score is deliberately crude, and the honest version is not available here —
attributed ad performance lives behind `POST /sell/marketing/v1/ad_report_task`,
which this phase does not create. Say so when reporting; do not present the
score as measured return.

## Reuse a campaign before creating one

The account accumulates campaigns — five already, four of them paused, 81 ads
inside them doing nothing. Resuming a paused ON_SITE campaign keeps the ads it
already holds and costs one call; a new campaign starts from zero and adds
another thing to reconcile. Create one only when there is nothing to reuse.

**Items already carrying an ACTIVE ad in a RUNNING campaign are excluded** —
they are already promoted, and adding them again is not a second bet.
Items sitting in a PAUSED campaign ARE shown, with which campaign they are in,
because that is an argument for resuming rather than rebuilding.

## Budget is a ceiling, not a plan

State both numbers, always: the daily cap and what it means per month against
the ask value actually being promoted.

    25 listings · $2,950.82 of ask value
    at $20/day the ceiling is $600/month — 20.3% of that ask value

A cost-per-click campaign only spends on clicks, so the ceiling is rarely
reached; that is exactly why it is worth printing. A cap that would consume a
fifth of the promoted value if it ever ran hot is a cap set for a much larger
pool of listings than this account holds. Size the budget to the pool, and
re-read the ratio every time the pool changes.

Two funding models, and they are not interchangeable:

| Model | Charged | Use when |
|---|---|---|
| `COST_PER_SALE` | an ad rate, only when a promoted listing sells | you want no spend without revenue — the lowest-risk default |
| `COST_PER_CLICK` | per click, against a daily budget | you want volume and can watch the spend |

## What PROMOTE must never do

- **Never promote an item that is not sellable.** Sold, ended and
  out-of-stock listings are excluded by reading the live sheet, not by trusting
  a local ledger.
- **Never treat "has an ad" as "the ad sold it."** REPORT's dashboard makes this
  mistake impossible to make silently: it prints the promoted-vs-unpromoted
  realisation side by side and labels it a correlation.
- **Never raise a bid to fix a pricing problem.** If a category clears at 66% of
  ask, the ask is wrong; paying for more impressions on a mispriced listing buys
  more offers at the same wrong number.

## Reading the result honestly

Ad spend cannot be separated out of the fee column. The Fulfillment API returns
one lump `totalMarketplaceFee` per order, so once a campaign is running, the
blended fee rate REPORT prints (16.0%) absorbs the ad fees and no local
computation can split them. If a campaign's real cost matters, it has to come
from eBay's ad report, and `GET /ad_report_metadata` currently answers 403 on
this account — an app-authorisation gap to resolve before relying on it.
