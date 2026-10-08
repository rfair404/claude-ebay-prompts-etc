# Whatnot as a second sales channel

IMPLEMENTED (code), NOT YET EXERCISED against a real Whatnot account. The
client is built from Whatnot's published schema docs; the first staging
round-trip is the remaining verification step (see "Before the first real
listing" below).

## The API, as of 2026-10-08

| | |
|---|---|
| Docs | https://developers.whatnot.com/docs |
| Shape | GraphQL only. `POST` to `https://api.stage.whatnot.com/seller-api/graphql` (staging) or `https://api.whatnot.com/seller-api/graphql` (production) |
| Status | **Developer Preview. Whatnot is not accepting new applicants.** An account needs access granted by Whatnot before anything below works |
| Auth used here | A seller's own personal access token (`wn_access_tk_test_…` staging, `wn_access_tk_…` production), `Authorization: Bearer`. It carries `full_access`, which OAuth apps never get, and needs no refresh |
| Auth not used | OAuth2 authorization-code for third-party "Connect" apps. Not needed to list to our own account |
| Rate limit | One global limit during preview; Whatnot asks for ≤ 10 req/s |
| Taxonomy | Public, no token: `https://api.whatnot.com/seller-api/rest/product-taxonomy/US.txt` (`<id> - A > B > C`) |

Model: **Product → ProductVariant → Listing.** A variant has one listing at a
time. A listing is `BuyItNow`, `Auction` or `Giveaway`, and is created with
`published: false` and later made live with `listingPublish`. That is the
same draft-then-publish shape as eBay's offer → `publishOffer`, which is why
the REVIEW gate and `--confirm` carry over unchanged.

The operations this pipeline uses:

| Step | eBay (lib/list_edit.py) | Whatnot (lib/whatnot_list.py) |
|---|---|---|
| sync, unpublished | `PUT inventory_item` + `POST/PUT offer` | `productCreate(input, media)` / `productUpdate` |
| publish (`--confirm`) | `POST offer/{id}/publish` | `listingPublish(input: {id})` |
| end (`--confirm`) | `POST offer/{id}/withdraw` | `listingUnpublish(input: {id})` |
| photos | upload to EPS | **URL only**. No upload endpoint (see Photos) |
| category | `category_id` | `productCategory.taxonomyId` = base64(`ProductTaxonomyNode:<id>`); the eBay category goes along as `externalCategoryID` |
| condition / brand | aspects | `attributes: [{id, value}]`, with ids per category from `productAttributes` |
| shipping | fulfillment policy | `shippingProfileId`, or `weight` + `autoCreateShippingProfile` |

PR #167's survey listed Whatnot as having "no listing-creation API". That was
true of the public surface it checked, but the Seller API preview above does
have one. Access is the blocker, not capability.

## Using it

```bash
python -m lib.cli whatnot --setup-check                 # token + config
python -m lib.cli whatnot --taxonomy vintage advertisement
python -m lib.cli whatnot --attributes 574              # condition/brand attribute ids + options
python -m lib.cli whatnot --shipping-profiles
python -m lib.cli whatnot --payload inventory/<item>    # the ProductInput, no network

python -m lib.cli publish inventory/<item> --to whatnot            # sync + DRY RUN
python -m lib.cli publish inventory/<item> --to whatnot --confirm  # live
python -m lib.cli publish inventory/<item> --to both --crosslist --confirm
python -m lib.cli whatnot --end inventory/<item> --confirm
```

`ebz publish --to ebay` is the same as `ebz listing --list`. With no `--to`,
`publish.default_channels` in config decides, and the default is `["ebay"]`.

### Per-draft settings

Optional, in the draft's frontmatter:

```yaml
whatnot:
  taxonomy_id: 574           # else whatnot.default_taxonomy_id in config
  price: 29.99               # else the draft's price
  format: buy_it_now         # or auction (needs starting_price)
  starting_price: 5.00
  offerable: true            # else best_offer.enabled
  shipping_profile_id: "…"   # else whatnot.shipping_profile_id in config
```

IDs land in `meta:` as `whatnot_product_id`, `whatnot_variant_id`,
`whatnot_listing_id`, `whatnot_environment`, `whatnot_url`,
`whatnot_published_at` and `whatnot_media_urls`. The variant's SKU is the eBay
canonical SKU, so one item has one label everywhere. Lifecycle rows go to
`whatnot_ledger.csv`, never into `listings_ledger.csv`, because
`ebz reconcile` treats every row there as an eBay offer.

### Photos

Whatnot takes `CreateMediaInput.source` as a URL. `whatnot.media.source`:

- `eps` (default): the PREP-approved photos are uploaded to eBay Picture
  Services with the draft's eBay store credentials, and those URLs are handed
  to Whatnot. This needs no extra infrastructure, but it relies on Whatnot
  copying the image when it ingests it. eBay may expire EPS pictures that no
  eBay listing uses. Check that in the first staging run.
- `base_url`: `<base_url>/<shoot folder>/<photo path>`, for a public bucket
  that `listing/` is synced to. This is the robust option if EPS URLs turn
  out not to stick.

Either way the PREP gate (`_assert_photos_cleared`) runs first, and the
URLs are cached in `meta.whatnot_media_urls`.

## The cross-listing rule

Nothing ends the eBay listing when the Whatnot copy sells, or the other way
round. Neither `pick-list`, `sync_actuals` nor `reconcile` knows about Whatnot
orders yet. So a **quantity-1** item may be live in only one place unless
`--crosslist` is passed:

- `publish --to both` on a qty-1 item is refused without `--crosslist`.
- `--to whatnot` is refused when the draft is live on eBay (`meta.ebay_listing_id`).
- `--to ebay` is refused when the draft is live on Whatnot (`meta.whatnot_published_at`).

`--crosslist` means "I will cancel a double sale by hand." The proper fix is
the follow-up below.

## Before the first real listing

1. Get Seller API access from Whatnot and generate a **staging** token. Put
   it in `whatnot.stage.access_token`. `--setup-check` should then name the
   seller.
2. Run `--attributes <taxonomy_id>` for a category you list in, and set
   `whatnot.attributes.condition` / `.brand`. Compare the condition options
   it prints with `DEFAULT_CONDITION_VALUES` in `lib/whatnot_list.py`, and
   override any that differ under `whatnot.condition_values`. The default
   option strings are educated guesses. Whatnot's options vary by category.
3. `publish --to whatnot` one item on staging, without `--confirm`. Check in
   the Playground (`https://api.stage.whatnot.com/seller-api/graphql`) that
   the product, its photos and the listing look right. The selection sets in
   `_PRODUCT_SELECTION` follow the documented types, but have not been run.
4. Then `--confirm`, then `--end --confirm`, and only after that switch
   `whatnot.environment` to `production`. The loader refuses a token that
   doesn't match the environment, and a draft's ids are pinned to the
   environment that created them.

## Follow-ups (not in this change)

- **Sold-elsewhere sync:** poll Whatnot `orders` (or the `product sold`
  webhook) and end the eBay listing, and the reverse from `sync_actuals`.
  This is what would retire the `--crosslist` rule.
- Whatnot orders in `pick-list`, plus `addTrackingCode` from `ship-buy`.
- Livestream assignment (`listingAssignToLivestream`) for show inventory.
