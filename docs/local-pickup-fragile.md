# Local pickup for ship-risky items — IMPLEMENTED

Ship-risky items — heavy, oversized, or fragile (stained glass, porcelain) —
can now be listed as **local pickup only** (no parcel shipping), routed to a
dedicated eBay fulfillment policy. The freight fallback for distant buyers is
offered **by quote in the listing description** (see the eBay constraint
below — pickup and freight can't share one policy).

## Confirmed decisions (2026-06-14)

1. **Soft gate, never assume.** Local-pickup is a SOFT gate: it never blocks a
   run. Default is always `SHIP`. DRAFT **suggests** pickup but only sets
   `LOCAL_PICKUP` when the user indicates it (they usually say so up front) or
   confirms when asked. Headless runs keep `SHIP` and log a NEEDS_REVIEW line.
2. **Auto-suggest trigger:** estimated weight **> 25 lb** OR any estimated
   dimension **> 24 in (2 ft)** on a side. (Fragility the user names is also
   honored at DRAFT; the *automatic* trigger is weight/size, set in IDENTIFY.)
3. **Freight = fallback** offered by quote in the description, not a formal
   eBay shipping service (forced by the eBay constraint below).

## The eBay constraint we hit (and why the design is what it is)

We tried to create a single "local pickup + freight" fulfillment policy via the
Sell Account API. eBay **does not allow it**:

- `localPickup: true` is **exclusive** — "No other shipping services can be
  specified with shipping type of Local pick up" (rejects freight, parcel, even
  a `handlingTime`, which is read as a shipping concept).
- A `Pickup` *service* inside a `FLAT_RATE`/`CALCULATED` option requires a
  non-pickup service alongside it; adding freight there silently **drops the
  pickup** and yields a freight-only policy.

So the working, eBay-accepted shape for pickup-only is a minimal policy:
`localPickup: true`, `categoryTypes`, **no `handlingTime`, no `shippingOptions`**.
Freight therefore lives in the description as "message me for a freight quote."

The policy was created on the **production** account:
`fulfillment_policy_id_local_pickup = 292766452014`
("Local pickup only (freight by quote)", `localPickup: true`), and is
reproducible with `python lib/list_edit.py --create-pickup-policy`.

## What changed

**eBay API / library**
- [lib/ebay_client.py](../lib/ebay_client.py) — new `create_local_pickup_policy()`
  (idempotent by name; encodes the minimal pickup-only payload).
- [lib/list_edit.py](../lib/list_edit.py) —
  `_resolve_policies_and_location` reads `fulfillment_policy_id_local_pickup`
  (optional); `_resolve_shipping_policy` routes `fulfillment_mode: LOCAL_PICKUP`
  items to it (and treats a no-parcel-service pickup/freight policy as valid);
  `validate_draft_for_sync` guards the `fulfillment_mode` enum so a typo can't
  silently ship a fragile item; `build_review_card` shows a **Fulfillment**
  line; new `--create-pickup-policy` CLI command.
- [lib/config.example.yaml](../lib/config.example.yaml) — documents the new
  optional `fulfillment_policy_id_local_pickup`. (Live `config.yaml` has the
  real id; it is gitignored.)

**Template**
- [templates/listing-v1.md](../templates/listing-v1.md) — `shipping.fulfillment_mode`
  (`SHIP` | `LOCAL_PICKUP`, default `SHIP`) + a `local_pickup` block
  (`freight_quote`, `location_hint`).

**Prompts**
- [prompts/identify.md](../prompts/identify.md) — new **Ship risk** field
  (`none` | `suggest-pickup`) from the >25 lb / >24 in trigger.
- [prompts/draft.md](../prompts/draft.md) — the **Local-pickup gate**: suggest
  (never assume), ask in attended runs, default `SHIP` + log in headless, set
  the pickup fields + description freight line when `LOCAL_PICKUP`.
- [prompts/review.md](../prompts/review.md) — card shows fulfillment; confirm
  pickup items at the gate.
- [prompts/list_edit_chrome.md](../prompts/list_edit_chrome.md) — UI path
  selects eBay's local-pickup option (no parcel service) for `LOCAL_PICKUP`.
- [prompts/_shared.md](../prompts/_shared.md) + [RUN.md](../RUN.md) — the
  local-pickup suggestion added to the SOFT-gate contract.
- [lib/SETUP_EBAY_API.md](../lib/SETUP_EBAY_API.md) — how to create/configure
  the pickup policy.

## Verified

- `--create-pickup-policy` is idempotent (returns the existing `292766452014`).
- Routing: `LOCAL_PICKUP` → pickup policy; `SHIP` → default ground (tested
  against the live account).
- Validator: rejects a `fulfillment_mode` typo; accepts `LOCAL_PICKUP`;
  existing drafts (no field → default `SHIP`) still validate.

## Not done / follow-up

- No item has been listed pickup-only end-to-end yet (no live publish in this
  change). First real fragile item (e.g. the Fenton glass lamps) will exercise
  the full DRAFT → REVIEW → `--list` path.
- Sandbox has no pickup policy configured — run `--create-pickup-policy` there
  if/when sandbox listing is needed.
