# Refund without return — can we do it? (Issue #85)

Investigation for [#85](https://github.com/rfair404/claude-ebay-prompts-etc/issues/85):
does our eBay return/refund policy setup (return policy id `296995924014`,
"30-Day Free Returns - Seller Pays" — see
[`lib/SETUP_EBAY_API.md`](../lib/SETUP_EBAY_API.md) and
[`docs/top-rated-plus.md`](top-rated-plus.md)) let us issue a full refund to
a buyer without requiring the item back?

## Answer

**Yes.** Refunding without requiring a return is always available to us as a
manual, per-order seller action — it is **not** controlled by the return
policy object at all. Nothing in the Account API's return-policy schema (the
fields our `return_policy_id: 296995924014` sets — `returnsAccepted`,
`returnPeriod`, `returnShippingCostPayer`, `refundMethod`, `returnMethod`,
and `internationalOverride`; see `create_free_return_policy()` in
`lib/ebay_client.py`) has an on/off switch for "refund without return."
That policy only governs the terms of a
*stated, buyer-initiated* return (how long the window is, who pays return
shipping, and — US-only — whether REPLACEMENT is offered as an alternative to
money back). Whether to require the item back on any *given* refund is a
separate, case-by-case decision made at order- or case-level, independent of
what the listing's return policy says.

Live confirmation via the Account API (`GET /sell/account/v1/return_policy/296995924014`)
was not attempted — this worktree has no `config.yaml` / eBay credentials
configured (`config.yaml` is gitignored and absent; see
[`lib/SETUP_EBAY_API.md`](../lib/SETUP_EBAY_API.md)). The answer above is
based on eBay's public seller-help and developer documentation, which is
sufficient to resolve this without a live call.

## The three paths to refunding a buyer

### 1. Seller-initiated "Send Refund" (order details) — always available, no case needed

From an order's detail page, a seller can click **Send Refund** and issue a
full or partial refund (up to 100% of the transaction) for up to 90 days
after the order — with no requirement that the item come back. This is a
plain seller action, not tied to the listing's return policy in any way.

The one restriction: **Send Refund disappears once there is an open
cancellation request, return request, Money Back Guarantee case, "item not
received" report, or an external payment dispute on that order** — at that
point the refund has to be issued *through* that open request/case instead
of as a standalone action.

### 2. Responding to a buyer-opened return request — seller can waive the return

If a buyer opens a formal return request (governed by our 30-day/seller-pays
policy), the seller isn't limited to "accept and demand it back." One of
eBay's standard seller responses is to **refund the buyer and let them keep
the item** — i.e. resolve the request without collecting the item. That's a
seller/buyer mutual-agreement path, distinct from the stated return-shipping
terms; it doesn't require or depend on any particular `returnMethod` /
`returnShippingCostPayer` setting.

### 3. eBay Money Back Guarantee (MBG) / INAD case

For **item-not-as-described (INAD)** claims, eBay's Money Back Guarantee
overrides listing-level return settings — "no returns" never means "no
refunds" for INAD, and this is true regardless of what our return policy
object says. Once a buyer opens an INAD case, seller options are essentially:

- accept the return, pay return shipping, refund in full once it's received, or
- **refund and let the buyer keep the item** — no return required.

For a low-value item where return shipping would cost more than the item,
option 2 is usually the practical choice. If the seller doesn't resolve the
case one of these ways and eBay steps in, eBay typically refunds the buyer
and lets them keep the item anyway — so a voluntary no-return refund up front
produces the same buyer outcome while keeping the resolution seller-initiated
rather than eBay-imposed.

**Buyer's remorse** (changed their mind, no defect) is treated differently:
it's optional whether we offer it at all, we get to decide whether to accept
returns for remorse outside our stated window, and if we don't offer *free*
returns we're allowed to deduct 10–20% of the refund for a remorse return.
None of that restricts our ability to *voluntarily* refund without a return
if we decide it's not worth the hassle — it's discretionary, not a policy
setting.

## Does this need any particular return-policy setting?

No. `returnsAccepted`, `returnPeriod`, `returnMethod`,
`returnShippingCostPayer`, `refundMethod`, and `internationalOverride`
(our own return-policy creation code, `lib/ebay_client.py`, sets all six —
see `create_free_return_policy()`) on the Account API return-policy object exist
to define the terms of a *stated* return under that policy (window length,
who pays return shipping, the US-only REPLACEMENT option, and a
domestic/international split on both the refund and return method). None
of them toggle refund-without-return — that field doesn't exist in the
schema. Our current policy (30-day, seller pays return shipping, required
for Top Rated Plus — see [`docs/top-rated-plus.md`](top-rated-plus.md)) is
irrelevant to whether we *can* skip the return; it only shapes what
happens if a buyer insists on a formal return under that policy.

## Seller protections / downsides to weigh case by case

- **We don't get the item back.** No resale value, no chance to inspect for
  fraud/damage claims. This is the core trade-off — worth it when the item's
  value is low relative to return-shipping cost/hassle, or when a quick
  resolution avoids escalation.
- **Performance impact:** a refund issued via Send Refund, or as our own
  chosen resolution to a return request/INAD case, does **not** by itself
  create a seller defect. Defects come from eBay/MBG *ruling against* the
  seller on an escalated case — so proactively refunding (with or without
  requiring the item back) before a case escalates is protective of account
  health, not a risk to it.
- **High-return-rate fee exposure:** eBay can add a 5% "high return rate"
  final-value-fee surcharge once a seller accumulates enough return-related
  defects/returns in a rolling window. A one-off no-return refund used to
  head off a bad case is not itself a defect, but returns/refunds overall
  still feed this metric — so this is a tool to use selectively, not a way to
  avoid all downside from a return-heavy item.
- **Timing/availability:** Send Refund is only available *before* a case is
  opened (or once no case is open); once escalated to MBG/a formal return
  request, the refund has to be issued through that request/case's own
  "refund and let them keep it" option instead.

## Practical guidance for us

Given our stated policy is already free 30-day seller-pays returns, refund
without-return is a live option any time:

- **Send Refund** at order level, any time within 90 days, as long as no
  case/return/dispute is already open on that order — use this proactively
  for a buyer complaint before it escalates, or for a low-value item where
  demanding a return back costs more in shipping/hassle than the item is
  worth.
- If a buyer has already opened a **return request or MBG/INAD case**,
  choose the "refund and let buyer keep it" resolution instead of demanding
  the item back, for the same low-value/high-shipping-cost cases the issue
  describes.
- This is a **per-case seller judgment call**, not something we need to
  change in our return-policy configuration (id `296995924014`) to enable.

## Sources

- [How to handle a return request as a seller | eBay](https://www.ebay.com/help/selling/managing-returns-refunds/handle-return-request-seller?id=4115)
- [Manage returns, missing items, and refunds for sellers | eBay](https://www.ebay.com/help/selling/managing-returns-refunds/manage-returns-missing-items-refunds-sellers?id=4079)
- [Refunding buyers | eBay](https://www.ebay.com/help/selling/managing-returns-refunds/refunding-buyers?id=5182)
- [eBay Money Back Guarantee policy | eBay](https://www.ebay.com/help/policies/ebay-money-back-guarantee-policy/ebay-money-back-guarantee-policy?id=4210)
- [Returns and refunds | eBay](https://www.ebay.com/help/returns-refunds)
- [How returns work | eBay Seller Performance](https://export.ebay.com/en/seller-performance/transactions/how-returns-work/)
- [eBay Account API — Business Policies `addSellerProfile` field reference (returnShippingCostPayer, returnMethod)](https://developer.ebay.com/Devzone/business-policies/CallRef/addSellerProfile.html)
- [ReturnPolicyRequest model reference (returnsAccepted, returnShippingCostPayer, returnPeriod, returnMethod, refundMethod)](https://github.com/michabbb/sdk-ebay-rest-account/blob/master/docs/Model/ReturnPolicyRequest.md)
- [eBay Account API release notes archive (return-policy field history)](https://developer.ebay.com/api-docs/sell/account/release-notes-archive.html)
- eBay Community threads (seller-reported behavior, corroborating the above): [Buyer's remorse](https://community.ebay.com/t5/Ask-a-Mentor/Buyer-s-remorse/td-p/33980243), [How to handle buyer's remorse with a no-return policy](https://community.ebay.com/t5/Selling/How-to-handle-buyer-s-remorse-with-a-no-return-policy/td-p/31129979), [Do we still get a defect for refunding a buyer?](https://community.ebay.com/t5/Selling/Do-we-still-get-a-defect-for-refunding-a-buyer/td-p/28880003)
