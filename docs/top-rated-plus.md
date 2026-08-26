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
- `config.yaml` → `ebay.production.fulfillment_policy_id: 296458692014`
  ("Free USPS Ground + eBay International Shipping (1 day)"). As of 2026-08-25
  this is the **only** fulfillment policy any item uses — there is no per-item
  shipping choice. `fulfillment_policy_id_international` points at the same id,
  because that policy is already the international-enabled one and the code path
  in `list_edit.py` requires the key to be set.
- Helper for handling-time repair: `ebay_client.set_fulfillment_handling_time()`.

## Deleted policies still cited by live offers (2026-08-25)

Five fulfillment policies and one payment policy were deleted from the eBay
account at some point without anything in this repo noticing. The Account API
returns **404** for each, yet `config.yaml` was still writing the first one into
every new draft:

| Id | Was | Still on live offers |
|---|---|---|
| `292380047014` | old ground default | 40 published |
| `295948332014` | (silverplate group) | 26 published |
| `292427280014` | Free Media Mail 1-day | 6 published |
| `292766452014` | local pickup only | — |
| `292460878014` | FedEx SmartPost / Ground Economy | — |
| `292380048014` | auction payment (no immediate pay) | — |

An offer keeps the terms it was **published** with, so those listings keep
serving buyers correctly — but the terms cannot be read back through the
Inventory API (it only echoes the dead id) or the Account API (404), and the
Browse API returns 403 on our seller keyset.

They **are** readable via the Trading API's `GetItem`, which returns the
published snapshot. `tools/live_shipping_survey.py` does exactly that, reusing
the same Trading transport as the EPS photo upload. Run it before overwriting
any of these offers — republishing one rewrites its terms from the *current*
policy, and you want to know what you are replacing.

Two consequences worth remembering:

- `prompts/draft.md` still routes heavy/oversize items to the FedEx policy
  `292460878014`, which no longer exists. That guidance is dead and will produce
  an unusable offer.
- The Media Mail policy is gone, which happens to match the standing rule that
  magazines and other periodicals with advertising are **not** Media Mail
  eligible (DMM 173.4.2).

## Known exception: 292488008014

`Calculated: USPSParcel free, 2 business days (292488008014)` carried 2-day
handling and failed TRS+. It is also now 404 — deleted along with the rest.
