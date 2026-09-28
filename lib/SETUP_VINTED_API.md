# Vinted Pro Integrations — account + API setup

`lib/vinted_client.py` is the client for Vinted's official seller API. This page covers what
a human has to do before it can talk to a real account, and what the pipeline
still needs before it can publish drafts there.

## 1. What you're signing up for

| | |
|---|---|
| API | **Vinted Pro Integrations**, docs at <https://pro-docs.svc.vinted.com/>, OpenAPI at `/downloads/api.yml` |
| Who gets it | **Allowlisted Vinted Pro business accounts only.** There's no self-serve key. Vinted enables the account, then the Integrations Portal (<https://pro-portal.svc.vinted.com/>) issues tokens. |
| Markets (as documented, Sept 2026) | AT, BE, DE, ES, FR, IT, LU, NL, PT, UK. Prices in **EUR/GBP only**. |
| Capacity | 500 active item slots per API user to start, reviewed after 30 days |
| Covers | Items (create/update/delete/validate, up to 100 per batch), ontologies, price suggestions, orders, shipment and label PDF, cancel/relist, webhooks, sandbox "Dev Mode" |

> **The US gap.** A US Vinted account (vinted.com) can sell by hand, and the
> seller pays no fees (the buyer pays roughly 5% + $0.70). But no US market
> appears in the Pro API docs, and the API only accepts EUR/GBP. If the account
> you open is US-only, expect Vinted to say API access isn't available (yet).
> Ask them directly (step 2); nothing in the code needs to change when they
> say yes. Scraping or automating the consumer app instead breaks Vinted's
> Terms and risks a ban, so this repo won't do it.

## 2. Account steps (you)

1. Create the Vinted account and upgrade it to **Vinted Pro** (a business
   account: company details, tax ID, payout bank).
2. Ask Vinted's Pro team for **Pro Integrations API access**. Say it's a
   direct integration from your own inventory system, not a third-party tool.
   Ask specifically:
   - Is API access available for a **US** Pro account, or only EU/UK?
   - Which market and currency will the account list in?
   - What's the starting item-slot allowance?
3. Once allowlisted, sign in to the Integrations Portal:
   - Generate a **sandbox** token first (Dev Mode). It comes as one string,
     `<access_key>,<signing_key>`. Copy all of it, comma included.
   - Generate a production token only after the sandbox checks pass.

## 3. Wire it in (config.yaml in the main checkout, never committed)

```yaml
vinted:
  environment: sandbox            # flip to production deliberately
  sandbox:
    token: "<access_key>,<signing_key>"
  # production:
  #   token: "<access_key>,<signing_key>"
```

A named store (e.g. `junk`) goes under `vinted.stores.<name>` in the same
shape. `VINTED_PRO_TOKEN` / `VINTED_ENVIRONMENT` override config for a
one-off run.

## 4. Verify

```bash
python -m lib.cli vinted check
python -m lib.cli vinted ontologies --out vinted_ontologies.json
```

`check` makes one signed, side-effect-free GET. If it fails with 401/403:
the token belongs to the other environment, or the clock is off (Vinted
rejects stale signature timestamps).

## 5. What the pipeline still needs before it can publish

The transport layer is done. The full phased plan is in
[docs/VINTED_PLAN.md](../docs/VINTED_PLAN.md); in short:

1. **Ontology mapping.** A draft's eBay `category_id` / `condition` /
   `item_specifics` → Vinted `catalog_id` / `status_id` / `item_attributes` /
   `package_size_id` / `color_ids`. This needs the real `ontologies` dump
   from step 4 to design against.
2. **Photo hosting.** Vinted takes `photo_urls`, not uploads. The PREP output
   has to be reachable at a public URL.
3. **Currency.** Items must be priced in EUR or GBP (unless Vinted opens a
   USD market). PRICE works in USD delivered, so a conversion step is needed.
4. **Cross-platform "sold is final".** A one-of-a-kind item sold on eBay must
   be deleted on Vinted and vice versa. That means an `ITEM_SOLD` webhook (or
   an orders poll) plus a `platform` column in the ledger.
5. **Category fit.** Vinted bans shell and ivory (cameos, ivory antiques) and
   leans to fashion. Collectibles, vintage media and homeware are the
   plausible first candidates.

Every account-changing call in the client (create/update/delete, cancel,
relist, webhooks) is a **dry run unless `confirm=True`**. Creates land as
Vinted **drafts** unless `publish=True` is also passed, so REVIEW still gates
what goes live.
