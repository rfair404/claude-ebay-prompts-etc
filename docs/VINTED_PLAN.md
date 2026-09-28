# Vinted publishing plan

**Status: ON HOLD. Waiting for Vinted to approve API access.** (2026-09-28)

The account exists (US). The Pro Integrations API that `lib/vinted_client.py`
talks to needs a **Vinted Pro** account that Vinted has allowlisted. Vinted
Pro is not offered in the US yet (help article 918 lists FR IT NL LU BE PT ES
UK IE DE AT). We've asked Vinted about US access; everything below Phase 1
waits for their answer. Transport + setup: [lib/SETUP_VINTED_API.md](../lib/SETUP_VINTED_API.md).

## The model: Vinted is a *channel*, not a store

A store (lib/stores.py) is a seller account plus the business behind it.
Vinted doesn't change the business. The same inventory, identity and terms are
offered in a second place. So a draft stays in its store and gains a
`channels.vinted` block. An item can be on eBay and Vinted at once, and
selling on either one ends it on both.

```yaml
channels:
  vinted:
    enabled: false          # opt-in per item; the eligibility check can refuse
    catalog_id: null        # from the category map (Phase 2)
    status_id: null         # from the condition map
    package_size_id: null   # from weight
    color_ids: []
    brand: ""               # item_specifics.brand, else blank
    price: ""               # from the pricing rule below
    currency: ""            # USD if the US opens, else EUR/GBP
    item_id: null           # Vinted UUID after create
    state: null             # VALIDATED | DRAFT | LIVE | SOLD | DELETED
    last_synced: null
```

The eBay fields and `listings_ledger.csv` stay as they are. Vinted state goes
into its own ledger, `vinted_ledger.csv` (named per store with
`stores.store_file`), so this work never rewrites the eBay ledger. That
ledger was corrupted once already by overlapping writers.

## Phases

### Phase 0: can be done now, no API needed
| # | Work | Output |
|---|---|---|
| 0.1 | **Eligibility filter.** Vinted bans shell, ivory and fur. Leave out jewelry, fine silver and firearms-adjacent/ITAR items, anything LOCAL_PICKUP, and lots over the parcel limit. The first wave is collectibles, vintage media and homeware. | `lib/vinted_map.py: eligible(draft) -> (bool, reason)` + tests |
| 0.2 | **Photo hosting.** Vinted takes `photo_urls`, not uploads. Host the PREP output ourselves: a Cloudflare R2 bucket with a public URL, keyed `<store>/<item_id>/<n>.jpg`. Don't reuse eBay's picture-service URLs; they expire when the eBay listing ends. | `lib/photo_host.py` (upload is idempotent, returns URLs) |
| 0.3 | **Description renderer.** eBay body → Vinted plain text, 5–2000 chars. Drop eBay-only lines (Best Offer, eBay shipping/returns wording, store cross-sell). `voice_check` still runs. Title limit is 5–100 chars (eBay's is 80, so titles carry over). | `vinted_map.render_description()` + tests |
| 0.4 | **Candidate list.** Run the filter over current drafts and live eBay items, from the main checkout. | a count and a list, reviewed with you |

### Phase 1: sandbox, the day a token arrives
1. `ebz vinted check`, then `ebz vinted ontologies --out inventory/vinted_ontologies.json`.
2. Look through the ontology dump for: which markets and currencies the account really gets, the catalog tree, status names/ids, package sizes, colours, and which attributes are required per catalog.
3. Go through the whole loop in Dev Mode: create (draft) → status → `dev_trigger_item_sold` → order → shipment → label PDF → cancel/relist.

### Phase 2: mapping tables (from the real ontology)
- **Category.** `config/vinted_category_map.yaml` maps eBay `category_id` → Vinted `catalog_id` (+ required attributes). Only seed our top eBay categories. **An eBay category that isn't in the map makes the item ineligible**; nothing is guessed.
- **Condition.** eBay enum → Vinted status. Proposed mapping (confirm against the ontology names/ids):

  | eBay | Vinted |
  |---|---|
  | NEW | New with tags / New without tags (has tags or packaging?) |
  | NEW_OTHER, LIKE_NEW | New without tags |
  | USED_EXCELLENT | Very good |
  | USED_VERY_GOOD, USED_GOOD | Good |
  | USED_ACCEPTABLE | Satisfactory |
  | FOR_PARTS_OR_NOT_WORKING | not eligible |

- **Package size.** Take `shipping.weight` + `package_in` → the smallest Vinted package size that fits. If none fits, the item is ineligible.
- **Colour.** `item_specifics.color` → `color_ids`, only on an exact name match, else empty.

### Phase 3: validate + REVIEW
- `ebz vinted validate DRAFT` builds the item and calls `POST /items/validate`. Validation is free and creates nothing.
- The review card gets a Vinted panel: mapped catalog/status/package, price and net, validation result. **REVIEW approves each channel separately**, so an item can go live on eBay and be held back on Vinted.

### Phase 4: publish (same gates as eBay)
- `ebz vinted publish DRAFT --confirm` creates the item as a **Vinted draft** (`is_draft` forced). A second, separate `--publish --confirm` makes it live, and only after REVIEW sign-off.
- The #180 sold-is-final guard runs first and checks **both** ledgers.
- Record `item_id` + state in `vinted_ledger.csv`. Creation is asynchronous, so confirm with `item_status` before marking LIVE.

### Phase 5: sales in both directions (the part that has to work)
Because every item is one of a kind, a sale on one channel **must** end the
other before someone buys it twice.
- **Detection.** Poll, not webhooks: the local app is 127.0.0.1-only and has no public endpoint. Poll `list_orders(after_id=…)` in the existing pick-list `--poll` loop. Webhooks come later, through a Cloudflare Worker, if polling turns out to be too slow.
- **Vinted sale** → end the eBay listing (`list_edit` end path) → SOLD in both ledgers → a pick sheet with the Vinted label PDF attached (`shipment_label_pdf`). Vinted's label is prepaid, so no EasyPost purchase.
- **eBay sale** (sync_actuals / pick-list) → `delete_items([uuid], confirm=True)` on Vinted → vinted_ledger SOLD.
- If the Vinted listing can't be deleted (IN_PROGRESS, API error), raise a **loud** alert on the dashboard. Never fail silently.

### Phase 6: production pilot
- 10–20 items from the Phase 0.4 list, 30 days. That also happens to be when Vinted reviews the 500-slot allowance.
- Measure sell-through vs the same items on eBay, days-to-sale, net per sale, and any double-sale near-miss.
- Go/no-go on widening the categories.

## Pricing rule (proposed, needs your OK)

eBay prices are delivered prices: free shipping, and the seller pays about
13% in fees plus ads. On Vinted US the seller pays **nothing**: the buyer pays
item price + about 5% + $0.70 + shipping on a prepaid label. So the same net
needs a *lower* Vinted sticker:

```
net_ebay     = price_ebay × (1 − fee_pct − ad_pct) − ship_cost
price_vinted = charm_round(net_ebay × 1.05)   # small cushion for offers
```

Example: a $40 eBay item at 13% fees, 0 ads and $6 shipping nets $28.80.
Listing it on Vinted at about $29.99 nets the same, and the buyer pays
roughly $29.99 + $2.20 + shipping. If the account ends up in EUR/GBP instead,
the same rule applies after currency conversion, and PRICE comps must come
from that market, not eBay US.

## Decisions for you

1. **Auto-delist on sale.** Delisting the other channel is the one write that
   should run **unattended**, because waiting for a human risks a double
   sale. Recommend: yes, auto.
2. **Pricing rule** above (net parity + 5% cushion), or one price everywhere?
3. **First-wave categories.** Collectibles, vintage media, homeware. Do you
   want silver/jewelry left out entirely, or reconsidered after the pilot?
4. **Photo hosting.** A Cloudflare R2 public bucket (recommended), or
   something else?

## Dependencies & sequencing
- **Blocked on Vinted:** Phases 1–6.
- **Depends on #180** (sold-is-final guard) for Phases 4–5.
- **Can start now:** Phase 0.1–0.3. They're pure functions with tests, and
  worth doing only if we expect approval. Otherwise hold everything.
- Files this will touch: new `lib/vinted_map.py`, `lib/photo_host.py`,
  `tools/vinted.py` subcommands, the Vinted panel in `tools/review_card_html.py`,
  and the pick-list poll hook in `tools/pick_list.py` (**hot file**, check
  open PRs first per CLAUDE.md).

## If Vinted says no to US API access
Leave the client in place (it costs nothing). Two fallbacks:
- **Hand-list on Vinted US.** Use the Phase 0 filter + renderer to produce a
  copy-paste pack per item: title, text, price, photos. Mark those drafts
  with a `[CHANNEL] vinted (hand-listed)` line, as with other channels we
  list by hand. Sales still have to be marked in the ledger by hand.
- **Etsy instead.** Open API v3 is self-serve and covers vintage items
  (20+ years). The channel model above carries over directly.
