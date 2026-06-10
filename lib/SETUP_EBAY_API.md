# eBay Sell API setup — one-time, then headless forever

This is the durable, zero-interaction path for Function 6 (LIST/EDIT):
`list_edit.py` reads a `draft.md`, uploads its photos to eBay Picture
Services (EPS), and creates an **unpublished** eBay listing (a draft). No
browser, no file dialog, no tier/sandbox limits. The no-publish firewall
still holds — nothing here publishes; you publish manually in Seller Hub.

All settings live in `config.yaml` (project root, gitignored) under
`ebay:`. **Sandbox and production keysets are stored side by side** under
`ebay.sandbox:` and `ebay.production:`; the top-level `ebay.environment:`
line selects the active one — flip it to switch between testing and live.
After setup, run `python list_edit.py --sync <shoot-dir>`.

---

## What you need (and where it comes from)

| config key | what it is | where |
|---|---|---|
| `environment` | `sandbox` (test) or `production` (real) | your choice |
| `app_id` | App ID / Client ID | developer.ebay.com/my/keys |
| `cert_id` | Cert ID / Client Secret | same keyset |
| `dev_id` | Dev ID (**required for EPS photo upload**) | same keyset |
| `redirect_uri` | your RuName / redirect registered with the app | developer.ebay.com/my/auth-n-auth |
| `user_refresh_token` | user-consent token (Sell write scope) | captured below |
| `merchant_location_key` | an inventory location key | created once (below) |
| `fulfillment_policy_id` | shipping business policy ID | your eBay account |
| `payment_policy_id` | payment business policy ID | your eBay account |
| `return_policy_id` | return business policy ID | your eBay account |

`sandbox` and `production` are separate eBay worlds with separate
keysets. Test on `sandbox` first.

---

## Steps

**1. Keyset.** Create/find your keyset at https://developer.ebay.com/my/keys.
Put `app_id`, `cert_id`, `dev_id` and `environment` into `config.yaml`:

```yaml
ebay:
  environment: "sandbox"          # active: sandbox | production
  sandbox:
    app_id: "Your-Sandbox-AppID"
    cert_id: "Your-Sandbox-CertID"
    dev_id: "Your-Sandbox-DevID"
    redirect_uri: "Your-Sandbox-RuName"
  production:                      # fill these when you go live
    app_id: null
    cert_id: null
    dev_id: null
    redirect_uri: null
```
(All per-environment keys — `app_id`, `cert_id`, `dev_id`, `redirect_uri`,
`user_refresh_token`, `merchant_location_key`, `*_policy_id` — live under
the matching `sandbox:` / `production:` block. The steps below write into
whichever block `environment` points at.)

Verify the app token works (no user consent needed yet):

```
cd lib
python ebay_client.py --check
```
Expect `[OK] App token obtained`.

**2. User consent → refresh token** (needed for any write):

```
python ebay_client.py --user-consent-url      # prints a URL
# open it, sign in, authorize the Sell scopes
# eBay redirects to your redirect_uri with ?code=...  (copy the code)
python ebay_client.py --exchange-code "<code>"  # prints refresh_token
```
Paste the printed `refresh_token` into `config.yaml` as
`ebay.user_refresh_token`.

**3. Business policies + inventory location.** `createOffer` needs three
policy IDs and a location key. List what your account already has:

```
python list_edit.py --setup-check
```
It prints your fulfillment / payment / return policy IDs and any inventory
locations. Paste the chosen IDs into `config.yaml`:

```yaml
  # under the active environment block (e.g. ebay.production:)
    user_refresh_token: "v^1.1#..."
    merchant_location_key: "primary"
    fulfillment_policy_id: "..."
    payment_policy_id: "..."
    return_policy_id: "..."
```

If you have **no** business policies or location yet, create them once in
Seller Hub → Account → Business policies, and add an inventory location
(eBay Seller Hub, or the Inventory Location API). Re-run `--setup-check`
to get the IDs.

**4. Validate + sync.** Always validate first (no creds needed):

```
python list_edit.py --validate <shoot-dir>     # e.g. ../to-id/hens/la-poule-black-white-small
python list_edit.py --sync     <shoot-dir>
```

`--sync` uploads photos to EPS, creates/updates the InventoryItem +
**unpublished** Offer, and writes `ebay_offer_id` / `ebay_inventory_sku` /
`last_synced` back into the draft. Open Seller Hub → Drafts to review and
publish manually.

Re-running `--sync` on the same draft updates the existing offer (the
draft remembers its `ebay_offer_id`).

---

## Authorize / Reauthorize (connect a store, or switch to a different one)

The `user_refresh_token` ties the app to **one eBay seller account**. Use
this to grant access the first time, when the token expires (~18-month
lifetime), or to **switch the active environment to a different store**.

**What changes vs. what stays:**
- **Stays:** the app keyset (`app_id` / `cert_id` / `dev_id` / `redirect_uri`)
  for the active environment — you're re-authorizing the *same app*.
- **Changes (all in the active env block, e.g. `ebay.production:`):** the
  `user_refresh_token`, and — because they're account-specific — the
  `merchant_location_key` and the three `*_policy_id` values. A different
  store has different policy IDs and location; the old ones will NOT work.

**⚠ The make-or-break step:** during consent you must be signed in to eBay
as the **store you want to connect**. Log out of any other eBay account
first, or run the browser step in an **incognito/private window** —
otherwise you silently re-authorize whatever account is already logged in.

Run from the repo root (`environment:` already points at the target env):

```
# 0. Back up the working config so you can revert
cp config.yaml config.bak-currentstore.yaml

# 1. Print the consent URL
python lib/ebay_client.py --user-consent-url
```

```
# 2. Open that URL in a browser SIGNED IN AS THE TARGET STORE (incognito = safest).
#    Approve the Sell scopes. eBay redirects to your redirect_uri with ?code=...
#    Copy the `code` value from the address bar.
#    (Codes are single-use and expire in minutes — do step 3 promptly.
#     If step 3 fails with invalid_grant, URL-decode the code first:
#     %23 -> #, %5E -> ^, etc.)
```

```
# 3. Exchange the code for the new refresh token (prints it)
python lib/ebay_client.py --exchange-code "<code>"
```

Paste the printed `refresh_token` into `config.yaml` under the **active
environment block** (nested — e.g. `ebay.production.user_refresh_token`,
not a flat `ebay.user_refresh_token`):

```yaml
ebay:
  production:
    user_refresh_token: "v^1.1#..."
```

```
# 4. Pull the new account's policy IDs + inventory location
python lib/list_edit.py --setup-check
```

Paste the four account-specific values into the same env block:

```yaml
    merchant_location_key: "<new>"
    fulfillment_policy_id: "<new>"
    payment_policy_id:     "<new>"
    return_policy_id:      "<new>"
```

```
# 5. Verify both the auth and the listing path see the new creds
python lib/list_edit.py --setup-check
python lib/list_edit.py  --setup-check
```

The target store must have **Business Policies opted in** and an
**inventory location** (see Step 3 above) — otherwise `--setup-check` lists
no policies and `--sync` fails. To switch back later, restore
`config.bak-currentstore.yaml`.

---

## Going live (publish) — explicit and confirmation-gated

API-created offers are UNPUBLISHED and do **not** appear in the Seller Hub
"Drafts" tab (that tab is for UI-created drafts). They go live only via the
publish command — which is a **dry run** unless you add `--confirm`:

```
python list_edit.py --publish <shoot-dir>            # shows what WOULD go live; publishes nothing
python list_edit.py --publish <shoot-dir> --confirm  # actually publishes -> real, live listing
```

On success it prints the listing URL (`ebay.com/itm/<id>`) and writes
`ebay_listing_id` + `published_at` into the draft.

**Take a listing down** (withdraw) — same dry-run/`--confirm` guard:

```
python list_edit.py --end <shoot-dir>            # dry run; ends nothing
python list_edit.py --end <shoot-dir> --confirm  # withdraws the live listing
```

Withdraw ends the public listing and returns the offer to UNPUBLISHED, so
you can re-sync/re-publish it later.

## Firewall

`--sync` NEVER publishes — it stops at an unpublished offer. Publishing
requires `--confirm`, is never invoked by `--sync`, and is never
automatic. The agent reaches it only after a human approves the REVIEW
card ([../prompts/review.md](../prompts/review.md)); the post-approval
command is `list_edit.py --list <dir> --confirm` (sync + publish in one
step). That preserves the firewall's intent — no accidental or automated
publication — while giving you a one-command way to take a reviewed offer
live.

## Shipping policies (ground default + optional Media Mail)

`--sync` runs a **preflight** that picks the fulfillment policy per item:

- **`fulfillment_policy_id`** — the default (ground) policy, used for most items.
- **`fulfillment_policy_id_media`** *(optional)* — a USPS **Media Mail** policy.
  When a draft's `primary_service` is Media Mail (DRAFT sets this for books /
  magazines / comics / music / movies) and this policy is set, the offer uses
  it; otherwise it falls back to the ground policy. To create one, add a
  fulfillment policy with shipping service `USPSMedia` (free flat-rate) and
  paste its ID here.

The same preflight also (a) **auto-remaps the condition** to one the item's
category accepts (e.g. `USED_GOOD` → `USED_EXCELLENT`/"Used" for non-media
categories that only allow New/Used), and (b) **flags items priced > $100**
to add insurance at label time (only $100 is auto-included; the eBay API
can't set insurance — see ShipCover in Seller Hub).

## Managing listings (query / withdraw / delete)

Account-level operations that work on ANY offer or SKU — not just items with
a local draft. All mutations are **dry-run unless `--confirm`** (same guard
as publish).

```
python list_edit.py --offers                            # list every offer: status, offerId, listingId, price, sku/title
python list_edit.py --withdraw-offer <offerId> --confirm  # end a LIVE offer; offer stays (UNPUBLISHED), re-publishable
python list_edit.py --delete-offer  <offerId> --confirm   # DELETE an offer (permanent); ends the listing if live; SKU kept
python list_edit.py --delete-item   <sku>     --confirm   # DELETE the inventory item AND all its offers (permanent)
```

- **Withdraw vs delete:** withdraw takes a live listing down but keeps the
  offer so you can re-publish; delete removes it for good.
- `--delete-item` is the cleanest way to fully retire an item (removes the
  SKU and every offer under it).
- Use `--offers` first to find the IDs.

## Listings ledger

Every listing the tooling creates is appended to a plain-text ledger,
`listings_log.txt` (repo root; override with `EBAYBIZ_LISTINGS_LOG`). One
line per event — when an offer is first created (`--sync`/`--list`) and when
it goes live (`--publish`/`--list --confirm`):

```
<utc> | OFFER_CREATED | offer_id=… sku=… price=$… | <title>
<utc> | PUBLISHED | listing_id=… offer_id=… sku=… price=$… | <title> | <url>
```

It's append-only (a running record of everything listed) and gitignored
(account/inventory activity). Re-syncing an existing item does not add a
duplicate line — only the first creation and each publish are recorded.

## Notes / limits

- **Photos via EPS** use the Trading API `UploadSiteHostedPictures` (needs
  `dev_id`). This is why `dev_id` is required even though the rest is REST.
- If you'd rather not use the Trading API, you can instead host the photos
  at public URLs and set them as `product.imageUrls` — but EPS is simplest
  and keeps images on eBay.
- `--sync` re-uploads all photos each run (no hash-diff yet); fine for
  typical listing sizes.
