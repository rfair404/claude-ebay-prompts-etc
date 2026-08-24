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

## The gate: it proposes, you confirm

    python tools/promote.py --budget 20 --top 40           # plan only

The tool reads campaigns, ads, live listings and eBay's own suggested items,
ranks them for the goal, and prints the plan plus the exact API calls that would
enact it. **By default it makes none of them.**

Every write is opt-in per invocation and requires `--confirm`; without it the
call is printed and not sent:

    --create NAME       create a campaign
    --auto-add-min USD  ...with eBay's auto-add rule at that price floor
    --add-ads           add the proposed listings to it
    --bid / --ad-rate   what a click costs / what a sale costs
    --bidding DYNAMIC   hand bid management to eBay
    --delete ID         remove a campaign

This is not timidity about the API. It is that an ad added by mistake is not
free to undo: it spends before anyone notices.

## What the funding model decides — and what decides the funding model

The two models are not a preference. **Asking for the auto-add rule IS asking
for cost-per-sale**, because `campaignCriterion` is refused on CPC:

    36151  'campaignCriterion' is not supported for CPC funding model.

There is no daily-capped click campaign that fills itself.

| Model | Charged | Budget | Auto-add | Bid automation |
|---|---|---|---|---|
| `COST_PER_SALE` | an ad rate when a promoted listing sells | none — there is no daily budget | **yes** | ad rate strategy |
| `COST_PER_CLICK` | per click | daily cap | no | `FIXED` / `DYNAMIC` |

Three more refusals worth knowing before writing anything:

- **`36210 No ad group found for ad group id null`** — a CPC campaign cannot
  hold ads directly. It needs an ad group, and that group's default bid, not the
  daily budget, is what a click costs. The budget only caps the day.
- **`35039 categoryScope is required`** on any criterion-based campaign, even
  when the rule names no category. `MARKETPLACE` is eBay's tree, `STORE` the
  seller's.
- **`campaignCriterion` is create-time only.** The campaign resource has no
  criterion-update method, so a campaign built without the rule can never learn
  it and must be deleted and rebuilt. Decide the rule before creating.

## Auto-add and a curated list are mutually exclusive

`autoSelectFutureInventory` hands membership to eBay: it re-checks the inventory
daily and adds anything matching the rule. The revenue ranking then stops
deciding what is promoted — the price floor does. That is a real trade, not a
detail: state it plainly when proposing one, and do not present a ranked list as
the campaign's contents once a rule is in force.

## There is no ROAS automation

Budget changes are an explicit `updateCampaignBudget` call. The only automations
eBay offers are `autoSelectFutureInventory` (which listings) and
`updateBiddingStrategy` (`FIXED`/`DYNAMIC`, CPC only). Nothing is triggered by
performance.

Nor can ROAS be computed locally: `ad_report_metadata` answers **403** on this
account and orders carry one lump `totalMarketplaceFee` with no ad-fee line. A
ROAS rule would therefore be acting on a number nobody has. Say so rather than
approximating one.

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
