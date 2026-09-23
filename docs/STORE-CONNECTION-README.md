# Connecting a new store — the runbook

How to attach an **additional eBay seller account** to this app so both stay
connected at once, and you can say "publish this one to the junk store."

This is the operator's record of the steps, in the order they actually have to
happen, with the things that bit us written down next to the step that bit.
The reference material lives in
[`lib/SETUP_EBAY_API.md`](../lib/SETUP_EBAY_API.md) (what each field is) and
[`lib/config.example.yaml`](../lib/config.example.yaml) (the skeleton to copy).
This file is the *procedure*.

Feature: GH #147, implemented in PR #150. (PR #148 was an earlier attempt,
closed unmerged — it lacked `--store` on `ebay_client.py`'s own CLI, which is
the one flag this whole runbook depends on. If you are looking at #148, you
cannot complete step 2.)

---

## Before you start

A named store is **its own eBay seller account**, not a second environment of
your existing one. `ebay.sandbox:` / `ebay.production:` are two worlds of the
*same* account; `ebay.stores.<name>:` is a *different* account. That
distinction drives everything below: the account-specific values (refresh
token, policies, location) cannot be shared, and the main store's IDs will not
work.

Requirements on the new account, in order of how long they take to fix:

- [ ] The eBay account exists and you can sign in to it.
- [ ] It is **opted in to Business Policies** (Seller Hub → Account →
      Business policies). There is no API for the opt-in — this is a manual
      step in Seller Hub, and until it is done step 5 returns no policies.
- [ ] It has at least one **inventory location**. Also not creatable by this
      app. Seller Hub, or the Inventory Location API by hand.
- [ ] It has the three policies an offer needs: fulfillment, payment, return.

A brand-new selling account has none of the last three. Doing step 1–4 first
is still worth it — the consent token is the slow part to get right, and step
5 is what tells you exactly which of the above is missing.

**Decide up front: reuse the keyset, or register a second app?** Reuse is the
default and what we did. One eBay developer app can hold consent for any
number of seller accounts — the `user_refresh_token` is the only thing that
ties credentials to an account. A second keyset buys isolation (revoking one
app doesn't touch the other) and costs a new app registration + RuName at
developer.ebay.com.

---

## Step 1 — add the store block to `config.yaml`

Back up first. This file holds every live credential and is gitignored, so a
bad edit is not recoverable from git.

```bash
cp config.yaml config.bak-prejunk-$(date +%F).yaml
```

Add a block under `ebay.stores.<name>:`. **Do not overwrite the top-level
`ebay:` block** — that is your default store, and the reauthorize procedure in
`SETUP_EBAY_API.md` (which *does* overwrite it) is the wrong procedure for
this job.

When reusing the keyset, **alias it with YAML anchors instead of pasting the
secrets twice.** Put anchors on the four production keyset fields:

```yaml
ebay:
  production:
    app_id: &prod_app_id RussellF-...
    cert_id: &prod_cert_id PRD-...
    dev_id: &prod_dev_id 3c4b85e1-...
    redirect_uri: &prod_redirect_uri Russell_Fair-...
```

and reference them from the new store:

```yaml
  stores:
    junk:
      environment: "production"
      app_id: *prod_app_id
      cert_id: *prod_cert_id
      dev_id: *prod_dev_id
      redirect_uri: *prod_redirect_uri
      user_refresh_token: null        # step 3
      merchant_location_key: null     # step 5
      fulfillment_policy_id: null     # step 5
      payment_policy_id: null
      return_policy_id: null
```

Why anchors and not copy-paste: the keyset is one fact. Duplicated, a future
key rotation updates `production:` and silently leaves `stores.junk:` on a
dead keyset — and the failure surfaces as an auth error on the junk store
weeks later, with nothing pointing at the rotation as the cause.

Confirm both stores resolve before going further. `--check` needs no consent:

```bash
cd lib
python ebay_client.py --check                # expect: store: default
python ebay_client.py --check --store junk   # expect: store: junk
```

Both should print `[OK] App token obtained`. The junk store should print
`user_refresh_token: (missing — needed for writes)` — that is step 3's job.
An unknown name fails loudly and lists what *is* configured:

```
[X] EbayAuthError: eBay store 'junk' is not configured.
    Add it under ebay.stores.junk in <path>
      Configured stores: (none configured)
```

---

## Step 2 — consent URL, signed in as the NEW store

```bash
python ebay_client.py --user-consent-url --store junk
```

**⚠ This is the step that goes wrong silently.** The printed URL is
*byte-identical* to the default store's when the keyset is shared — same
`client_id`, same `redirect_uri`, same scopes. Nothing in it names the junk
store. The **only** thing that decides which seller account you are
connecting is which eBay account is signed in to the browser you paste it
into.

So: open it in a **fresh incognito/private window**, and sign in as the junk
store. In your normal profile you are already signed in as the main store,
and you will authorize *that* account into `stores.junk` — which then fails
later as a confusing mismatch (policy IDs that don't exist, or worse,
listings quietly going to the wrong store) rather than as an auth error here.

Approve the Sell scopes. eBay redirects to your RuName with `?code=...` in
the address bar. Copy the `code` value.

Codes are **single-use and expire in minutes** — do step 3 straight away. If
step 3 answers `invalid_grant`, the usual cause is a URL-encoded code: decode
it first (`%23` → `#`, `%5E` → `^`).

---

## Step 3 — exchange the code for the store's refresh token

```bash
python ebay_client.py --exchange-code "<code>" --store junk
```

It prints the refresh token *and* the exact YAML to paste — correctly nested
under the store, which is the other place this goes wrong:

```yaml
  ebay:
    stores:
      junk:
        user_refresh_token: "v^1.1#..."
```

A flat `ebay.user_refresh_token` is the legacy default-store shape. Put it
there and you have re-pointed your **main** store at the junk account.

Refresh tokens last ~18 months. Note the date somewhere; the failure mode at
expiry is an auth error on that store only.

Re-check:

```bash
python ebay_client.py --check --store junk
# expect: [OK] User refresh_token present — write operations possible.
```

---

## Step 4 — confirm you connected the account you meant to

Do this before pasting any policy IDs. It is the cheap version of the
mistake-catch:

```bash
python list_edit.py --store junk --setup-check
```

The policy and location IDs it lists belong to whichever account you actually
consented as. If they match your **main** store's IDs, you authorized the
wrong account — redo steps 2–3 in incognito. Do not "fix" it by pasting the
IDs it found.

---

## Step 5 — the account-specific values

From the same `--setup-check` output, paste four values into the
`ebay.stores.junk:` block — same field names as the `production:` block, just
under the store's own name:

```yaml
      merchant_location_key: "<junk account's location>"
      fulfillment_policy_id: "<junk account's shipping policy>"
      payment_policy_id: "<junk account's payment policy>"
      return_policy_id: "<junk account's return policy>"
```

If `--setup-check` lists nothing under a heading, that thing does not exist on
the account yet — go back to **Before you start** and create it in Seller Hub,
then re-run. This is the expected state for an account that has never sold.

A junk store is the case for **not** inheriting the main store's terms. The
main store runs 30-day free returns (seller pays) to hold Top Rated Plus;
as-is lots should have their own return policy on the junk account, and
`return_policy_id` here is what selects it. Nothing is inherited — these four
fields are read per-store.

Verify both stores independently, and make sure the default one is unchanged:

```bash
python list_edit.py --setup-check              # default store
python list_edit.py --store junk --setup-check  # junk store
```

---

## Step 6 — target the store when listing

Three ways, in precedence order (first one that applies wins):

| How | Scope | Use when |
|---|---|---|
| `--store junk` on the command | that one command | one-off, or overriding a draft |
| `store: "junk"` in the draft's frontmatter | that item, every command | **the normal way** — set once at DRAFT |
| `EBAYBIZ_STORE=junk` env var / `ebay.active_store:` in config | everything | a whole session or run of junk items |

Anything not covered by those uses the implicit `"default"` store — i.e.
every existing draft and command keeps behaving exactly as before.

The draft field is the one to reach for. Put it in the frontmatter at DRAFT
time and `--review` / `--sync` / `--publish` / `--list` all pick it up with
nothing to remember per command:

```yaml
store: "junk"
```

The REVIEW card then shows a `Store:` line and bakes `--store junk` into the
approve command it prints, so copying the card's command verbatim — which is
the standing rule anyway — targets the right store by construction.

```bash
python list_edit.py --review <shoot-dir>                     # store from the draft
python list_edit.py --list   <shoot-dir> --store junk --confirm
python list_edit.py --offers --store junk                    # junk store's live offers
```

The publish firewall is unchanged and per-store: `--sync` never publishes,
`--publish`/`--list` are dry runs without `--confirm`.

---

## Part 2 — the storefront (the business, not the account)

Credentials answer *which account the API call goes to*. They say nothing
about the fact that a second store is usually a different **business**:
different terms, different voice, its own name on the sign-off. Connecting
the account without onboarding the business is how a junk listing goes out
reading "Thank you for looking — Pop's Games."

So a store has two halves, and both need doing:

| Half | Where | What it holds |
|---|---|---|
| Credentials | `ebay.stores.<name>` | keyset, refresh token, eBay policy IDs, location |
| Storefront | `storefronts.<name>` | identity, terms, routing, voice |

One `--store junk` selects both — `get_storefront()` uses the same
precedence as `load_credentials()` (explicit > `EBAYBIZ_STORE` >
`ebay.active_store` > `"default"`).

**The default storefront is the existing top-level `store:` block, read in
place.** It is not copied into `storefronts.default`: one fact, one home, and
single-store configs keep working with nothing to migrate.

### The two inheritance rules

Get these backwards and the mechanism is worse than useless.

- **Policy inherits.** Anything a named storefront doesn't state falls
  through to the default. A sparse override states only what differs, and an
  undecided policy stays visibly undecided instead of being invented.
- **Identity does NOT inherit.** `display_name` and `closing_block` resolve
  to `None` for a named storefront that omits them, and DRAFT then ships the
  unnamed thank-you. Inheriting identity is the exact bug this exists to
  prevent, so it is pinned by `tests/test_storefront.py` rather than left to
  reviewer memory.

An unknown storefront name is a hard error listing the configured ones. It
never silently falls back — falling back would ship the main store's name and
terms on a listing meant for somewhere else, which is the expensive failure
and a silent one.

### Onboarding checklist for a new storefront

- [ ] `display_name` — or leave `null` deliberately. Null is a safe state,
      not a blank to fill in: DRAFT ships the unnamed thank-you and never
      invents a brand.
- [ ] `closing_block` — its own, written for what this store sells. Every
      line must be true of every listing it ships on.
- [ ] `returns` — and make the eBay return policy under
      `ebay.stores.<name>.return_policy_id` agree with it. The config string
      is what DRAFT says; the policy ID is what eBay enforces. They are two
      records of one decision and nothing checks they match.
- [ ] `shipping` — same split: the string describes, the
      `fulfillment_policy_id` enforces.
- [ ] `routing` — what belongs in this store. Prefer condition over value: a
      cheap but clean, warrantable item still belongs in the main store, and
      a low price is not a defect.
- [ ] Leave genuinely undecided policy **unset** so it inherits, rather than
      guessing a value that then reads as a decision someone made.

### The junk storefront, as onboarded 2026-09-23

```yaml
storefronts:
  junk:
    display_name: null                 # unnamed thank-you until it has a name
    closing_block: |                   # its own; no "Pop's Games"
      Sold as-is. ...
    returns: "none_as_is"              # keeps as-is risk off the main store's
                                       # metrics; costs Top Rated Plus on THIS
                                       # account only
    shipping: "buyer_pays_calculated"  # free postage eats a cheap item's margin
    routing: ["as_is", "untested", "damaged"]
    price_posture: "below_new"         # NOT ceiling-first — see below
    new_price_ceiling_pct: 0.75
```

**Why junk overrides the house price rule.** Ceiling-first reads the sold-comp
ceiling as the market's top. That is sound for collectables, where nobody can
make another one, and wrong for anything still manufactured: the buyer's
alternative is the retail box, with a warranty and a return window. Most junk
stock is the second kind, so the ask is capped at a fraction of NEW delivered
(`price_stats.apply_new_price_ceiling`). Three behaviours worth knowing:

- Only tiers **over** the cap move; the original is kept as `uncapped_price`
  and every change is explained in the report. A cap is a ceiling, not a
  target — cheap comps stay cheap.
- **No new supply** (discontinued) → no cap. Ceiling-first is right again.
- **Comps at or above new** → a red flag, not an opportunity. Used does not
  outsell new, so either the comps are a different item, the comps are stale,
  or the new reference is the wrong spec. The helper caps anyway but says so
  loudly; resolve which before trusting either number.

---

## Known gaps (as of 2026-09-23)

1. **No API path to make an account sellable.** Business Policies opt-in and
   creating an inventory location are both manual Seller Hub steps, so
   "connect a store" cannot be done end to end from this repo for a new
   account. `--setup-check` diagnoses it but can't fix it.
   (`--create-pickup-policy` is the one policy the app can create itself.)

2. **Three different `store` keys.** Top-level `store:` is the default
   storefront's identity; `ebay.stores.<name>:` is credentials;
   `storefronts.<name>:` is a named storefront; and a draft's `store:` field
   selects the last two together. Consistent once learned, confusing on
   first read.

3. **The terms are stated twice.** `storefronts.<name>.returns` is what DRAFT
   writes in the copy; `ebay.stores.<name>.return_policy_id` is what eBay
   enforces at checkout. Nothing cross-checks them, so a storefront could
   promise "sold as-is, no returns" while its eBay policy accepts 30-day
   returns. Worth a `--setup-check` assertion.

4. **Identity and price posture are wired; condition bar and voice are not.**
   DRAFT reads the resolved storefront for the close and sign-off, and PRICE
   honours `price_posture: below_new`. The condition rubric and the style
   guides still read house-global rules, so an as-is storefront does not yet
   get its own condition bar or voice.

5. **The new-price reference is manual.** `apply_new_price_ceiling` takes a
   delivered new price; finding it is a PRICE-stage search, not an automated
   lookup. Nothing enforces that the "new" listing is the same spec, which is
   exactly the mistake the `above_new` flag exists to catch after the fact.

---

## Working from a git worktree

Tools resolve `config.yaml` from the repo root, and a worktree has no
`config.yaml`, no `inventory/`, and no real ledger. To run new code against
real credentials, point at the main checkout's config explicitly:

```bash
export EBAYBIZ_CONFIG="C:/Users/Reuseum/Documents/Claude/Projects/ebaybiz/config.yaml"
```

Steps 1–5 are pure credentialing and are safe this way. Anything that writes
to the ledger should be run from the main checkout instead — see GH #103.
