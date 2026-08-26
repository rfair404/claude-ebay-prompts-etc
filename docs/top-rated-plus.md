# Top Rated Plus — what our listing policies must carry

Source: eBay [Seller standards policy](https://www.ebay.com/help/policies/selling-policies/seller-standards-policy?id=4347).

Most of that policy is **behavioral**, not something a listing can encode:
defect rate ≤0.5%, ≤2 cases closed without seller resolution, late shipment
rate ≤5 (or 3%), ≥95% of transactions with carrier-validated tracking uploaded
within handling time, account active ≥90 days, ≥100 transactions and $1,000 in
US sales over 12 months. Nothing in this repo moves those numbers.

Exactly **two** requirements are listing-policy fields, and they are the
Top Rated *Plus* criteria — the seal in search results plus a 10% final-value-fee
discount:

| Requirement | Field | Policy |
|---|---|---|
| Same- or 1-business-day handling | `handlingTime` ≤ 1 DAY | fulfillment |
| 30-day or longer **free** returns | `returnPeriod` ≥ 30 DAY **and** `returnShippingCostPayer: SELLER` | return |

"Free" is the half that is easy to miss. A 30-day BUYER-pays policy satisfies
the window and qualifies for nothing. Free returns are judged on **item
location**: our items are US-located and listed on `EBAY_US`, so free
*domestic* returns is the bar. The `internationalOverride` on our policy stays
buyer-pays deliberately — it has no bearing on TRS+ here.

## Current wiring

- `config.yaml` → `ebay.production.return_policy_id: 296995924014`
  ("30-Day Free Returns - Seller Pays", created via
  `ebay_client.create_free_return_policy()`).
- Every fulfillment policy on the account is 1-day handling **except** one, below.
- Helper for handling-time repair: `ebay_client.set_fulfillment_handling_time()`.

## Known exception: 292488008014

`Calculated: USPSParcel free, 2 business days (292488008014)` still carries
2-day handling and **fails TRS+**. It cannot be repaired: cutting it to 1 day
makes it byte-identical to the configured default `292380047014`, and the
Account API rejects that with `20400 / "Duplicate Policy"`.

It is redundant, not used by our offer builder, and left in place rather than
deleted (deletion is destructive and the local ledger is assumed stale). Do not
select it for a listing. If it ever needs to go, verify no live offer references
it first.
