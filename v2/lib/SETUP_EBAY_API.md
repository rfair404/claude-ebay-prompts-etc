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
cd v2/lib
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
python list_edit.py --validate <shoot-dir>     # e.g. ../../to-id/hens/la-poule-black-white-small
python list_edit.py --sync     <shoot-dir>
```

`--sync` uploads photos to EPS, creates/updates the InventoryItem +
**unpublished** Offer, and writes `ebay_offer_id` / `ebay_inventory_sku` /
`last_synced` back into the draft. Open Seller Hub → Drafts to review and
publish manually.

Re-running `--sync` on the same draft updates the existing offer (the
draft remembers its `ebay_offer_id`).

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

## Firewall

`--sync` NEVER publishes — it stops at an unpublished offer. Publishing is
a separate, deliberate, human-run command that does nothing without
`--confirm`, is never invoked by `--sync`, and is never automatic. That
preserves the firewall's intent (no accidental or automated publication)
while giving you a one-command way to take a reviewed offer live. See
PLAN.md "No-publish firewall".

## Notes / limits

- **Photos via EPS** use the Trading API `UploadSiteHostedPictures` (needs
  `dev_id`). This is why `dev_id` is required even though the rest is REST.
- If you'd rather not use the Trading API, you can instead host the photos
  at public URLs and set them as `product.imageUrls` — but EPS is simplest
  and keeps images on eBay.
- `--sync` re-uploads all photos each run (no hash-diff yet); fine for
  typical listing sizes.
