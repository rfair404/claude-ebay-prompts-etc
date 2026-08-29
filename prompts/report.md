# REPORT — Function 7

Obeys [`_shared.md`](_shared.md) (style, confidence, honesty). Read it first.

**Output:** printed to chat, plus `docs/performance-<YYYY-MM-DD>.md` when the
user asks for a written report.

The feedback loop. Every other phase spends money or makes a claim; REPORT is
the one that checks whether those claims were right. It answers two questions:

- **Activity** — what did we list, and what is still sitting unlisted?
- **Performance** — what did it actually MAKE, and where are we systematically
  wrong about price?

REPORT never publishes, edits a listing, or changes a price. It reads, computes,
and recommends. Acting on a recommendation is a separate, user-approved step.

---

## The one rule that makes this phase worth anything

**The ask is not the sale.** `listings_ledger.csv` records what we ASKED. A $325
ask that cleared at $99 on an accepted Best Offer is indistinguishable from a
$325 sale if you read only the ledger. Every performance number in this phase
must trace to `sales_ledger.csv`, which
[`lib/sync_actuals.py`](../lib/sync_actuals.py) builds from eBay's Fulfillment
API — the only source that knows the money.

So REPORT always starts by refreshing actuals:

    python lib/sync_actuals.py --apply          # writes sales_ledger.csv

Then reports on them:

    python lib/report.py --performance          # all time
    python lib/report.py --performance --days 90
    python lib/report.py --performance --category catalogs
    python lib/report.py --today                # activity: what got listed
    python lib/report.py --days 7 --pipeline    # + what's drafted and waiting

Never hand-compute these from the ledger, and never quote a sold price that
came from an eBay search page — a sold card shows the ASK when Best Offer was
accepted. Only the Fulfillment API knows what was paid.

## What the tool gives you

`--performance` prints six blocks. Read them in this order:

1. **Headline** — gross, eBay's real fee take, net before our postage.
2. **Fee rate by sale size** — eBay's cut is regressive (a flat per-order
   component weighs far more on a $15 sale). Use the band, not the blend, when
   you quote net-to-us on a specific item.
3. **Ask vs actual** — the median % of ask we actually clear, overall and per
   ask band. This is the number PRICE's Best Offer gate should be tuned against.
4. **Speed** — median days to sell, and the ⚡ list of items that sold within 48
   hours. **A fast sale at full ask is not a win — it is evidence we asked too
   little.** Same disposition as the silver push-high rule.
5. **By category** — gross, median sale, % of ask, and how many sold inside 48h.
6. **Coverage** — sales with no local record. These were listed outside the
   pipeline, so they carry no comp research and no ask-vs-actual; a high
   coverage gap means the performance numbers describe only part of the business.

Then **DIAL-IN FLAGS**: mechanical observations (a category clearing at 100% of
ask, a band that never clears at full ask, a stale fee assumption). They are
flags, not decisions.

## Turning flags into changes — the discipline

A flag is a hypothesis about the market. Before recommending a change:

- **Require n ≥ 4** in a category or band. Three sales is an anecdote; the
  Burberry scarf alone can move a whole category's median.
- **Separate the two failure modes.** *Clears at 100% and sells same-day* →
  under-asked, raise. *Clears at 60-75%* → over-asked, or the comps came from a
  different cohort than the item.
- **Check the coverage gap first.** If 40% of revenue is untracked, a category
  median may describe the minority of sales that happened to run through the
  pipeline.
- **Name the counter-evidence.** If a category clears at full ask but every sale
  is the same seller/buyer pattern or one shoot, say so.

Recommendations land in one of two places, and the distinction is not optional:

- **Arithmetic corrections** (a wrong fee constant, a wrong postage class) —
  fix at the source, say you did it, show the before/after.
- **Policy changes** (raise catalog asks 30%, drop a category, change the
  auto-decline rule) — these are the user's call. Propose with the number
  behind them and STOP. Never quietly rewrite pricing policy from a trend.

## Written report

When the user asks for a report as an artifact, write
`docs/performance-<YYYY-MM-DD>.md`:

    # Performance report — <date>
    ## Headline            <gross / fees / net, and the window>
    ## What the numbers say <3-6 findings, each with its n and the number>
    ## Dial-in proposals    <each: observation -> proposed change -> expected effect
                             -> what would falsify it>
    ## Coverage & caveats   <untracked share, thin categories, known distortions>

Lead with the finding, not the method. Every claim carries its sample size. A
finding with n < 4 is labelled as provisional in the same sentence.

## Cadence

Weekly, or after any batch of 10+ listings goes live. The numbers only get
useful as n grows, and the coverage gap only closes if every sale runs through
the pipeline.

## Closing

Per _shared: lead with the headline number + the single most actionable flag.
Don't restate the table the tool already printed.
