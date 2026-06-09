# lib — shared libraries

## Setup (one time)

### 1. Install dependencies

```bash
pip install pyyaml
```

`apify_ebay.py` needs **no third-party package** — it calls the Apify REST
API with the Python standard library only. This is deliberate: it runs in
sandboxed environments (e.g. a Cowork tab) where `pip install` is blocked.
It only needs (a) Python, (b) network egress to `api.apify.com`, and (c)
the Apify token (config file or `APIFY_API_TOKEN` env var). `pyyaml` is for
`config.py` / the eBay Sell-API code, not for Apify.

### 2. Create the config file

Copy [config.example.yaml](config.example.yaml) to your user config
directory:

```bash
# macOS / Linux
mkdir -p ~/.ebaybiz
cp config.example.yaml ~/.ebaybiz/config.yaml
# then edit ~/.ebaybiz/config.yaml — set the Apify token

# Windows (PowerShell)
mkdir $env:APPDATA\ebaybiz -Force
Copy-Item config.example.yaml $env:APPDATA\ebaybiz\config.yaml
# then edit %APPDATA%\ebaybiz\config.yaml — set the Apify token
```

Get an Apify API token at:
<https://console.apify.com/account/integrations>

### 3. Verify the config

```bash
python config.py --where    # show config file path
python config.py --show     # show resolved config (secrets redacted)
python config.py --check    # verify required secrets are loadable
```

A successful `--check` looks like:

```
✓ APIFY_API_TOKEN: ok
✓ ANTHROPIC_API_KEY: ok
```

---

## config.py — config loader

Loads YAML config and provides typed accessors for API keys and
CURATE strategy profiles.

### Precedence

For any setting (highest wins):

1. **Explicit function argument** in code
2. **Environment variable** (where applicable — `APIFY_API_TOKEN`, etc.)
3. **Config file value** (from `~/.ebaybiz/config.yaml`)
4. **Built-in default** (where one exists)

This means:
- A user can keep their token in the config file long-term.
- CI environments can set env vars without a config file.
- Per-run overrides via explicit args still work.

### Public accessors

| Function | Returns | Raises |
|---|---|---|
| `get_apify_token()` | str | `ConfigError` if missing everywhere |
| `get_apify_actor()` | str | never raises — has a built-in default |
| `get_anthropic_key()` | str | `ConfigError` if missing everywhere |
| `get_profile(name=None)` | dict | `ConfigError` if named profile is missing |

### Strategy profiles

The `get_profile()` accessor reads CURATE strategy profiles from the
config. Profile fields:

| Field | Default | Notes |
|---|---|---|
| `margin_target` | 0.50 | Profit / sale-price target |
| `buy_point_multiplier` | 0.5 | Against worst-case sale floor |
| `fee_pct` | 0.13 | eBay + payment fees |
| `profit_floor` | 100 | Minimum net profit per item |
| `drive_cost` | 0 | Per-trip drive cost factored against profit |

See [config.example.yaml](config.example.yaml) for example profile
definitions.

---

## apify_ebay.py — Apify eBay sold-listings client

PRICE's **default Stage B** comp source (the un-gated direct-eBay sold
path). The Claude-in-Chrome browse is now the optional Stage C fallback,
used only when confidence is low or Apify is unavailable. Returns clean
structured `CompRecord` objects ready for PRICE's classification logic.

**Actor:** `automation-lab/ebay-sold-scraper`
([store](https://apify.com/automation-lab/ebay-sold-scraper)), configurable
via `apify.ebay_actor`. It **pins a US residential proxy**, so eBay returns
USD natively — this is the fix for the foreign-currency leak (see below).
Pricing: pay-per-event, ~$0.10 for a 30-listing call.

**Why not `caffein.dev/ebay-sold-listings`?** It has no proxy/country
control, so when Apify's proxy exits a non-US country eBay localizes prices
(e.g. BRL/CZK) and the actor mislabels them `USD`, inflating values ~5×.
Its `soldCurrency` field lies, so it can't be filtered on. We migrated to a
US-proxy actor and added a charm-price validator (below) as a backstop.

### Currency-leak validator

Every run is checked by `_check_currency_leak()`, which does **not** trust
the actor's currency label. Genuine USD eBay sold prices are overwhelmingly
charm-priced (.99/.95/.00/.50); an FX leak multiplies all prices by one
rate and destroys that structure. If the charm-price share collapses below
30% (with ≥5 priced comps), the run is flagged:

- `on_currency_leak="raise"` (default) → raises `CurrencyLeakError` so the
  caller can fall back to the Chrome path instead of anchoring on bad data.
- `"repair"` → finds the FX divisor that restores charm structure (refined
  search over major currencies), divides all prices by it, tags
  `sold_currency="USD (repaired from <CUR>)"`.
- `"ignore"` → returns as-is.

### CLI usage (manual testing)

```bash
python apify_ebay.py "Polo Ralph Lauren On Safari catalog"
```

Options:
- `--max N` — max results per keyword (default: 30)
- `--pages N` — max search pages per keyword, 60/page (default: 3)
- `--sort ORDER` — `best_match` / `newly_listed` / `price_low` /
  `price_high` (default)
- `--listing-type T` — `all` (default), `auction`, `buy_it_now`
- `--condition C [C ...]` — condition filter(s), e.g. `used new`
- `--min-price N` / `--max-price N` — USD price range filter
- `--actor NAME` — override the Actor ID
- `--timeout SEC` — max wait for the Apify run (default: 300)
- `--on-leak MODE` — `raise` (default) / `repair` / `ignore`
- `--json` — output raw JSON instead of human-readable list

Multiple keywords can be passed as separate positional args — each runs as
a separate search:

```bash
python apify_ebay.py "Polo Ralph Lauren On Safari" "Polo Ralph Lauren Fall 1981"
```

### Programmatic usage

```python
from apify_ebay import search_ebay_sold, CurrencyLeakError

try:
    comps = search_ebay_sold(
        "Polo Ralph Lauren On Safari catalog",
        max_listings=30,
        sort="price_high",
    )
except CurrencyLeakError:
    comps = []  # fall back to the Chrome (Stage C) path

for c in comps:
    print(f"${c.sold_price:.2f} — {c.title}")
    print(f"  Sold: {c.sold_date}  Condition: {c.condition}  {c.listing_type}")
    print(f"  Seller: {c.seller_username} ({c.seller_feedback_pct}%)")
    print(f"  URL: {c.url}")
```

### CompRecord shape

| Field | Type | Notes |
|---|---|---|
| `title` | str | Listing title (mandatory) |
| `sold_price` | float | USD, from numeric `soldPrice` (mandatory) |
| `url` | str | Direct link to the eBay listing (mandatory) |
| `sold_date` | Optional[str] | ISO 8601, parsed from `soldDate` ("May 12, 2026") |
| `condition` | Optional[str] | Localized eBay condition label |
| `condition_id` | Optional[int] | Not exposed by this actor (None) |
| `seller_username` | Optional[str] | From `sellerName` |
| `seller_feedback_score` | Optional[int] | From `sellerFeedbackCount` ("2K" → 2000) |
| `seller_feedback_pct` | Optional[float] | From `sellerFeedbackPercent` ("100%" → 100.0) |
| `shipping_cost` | Optional[float] | Parsed from `shippingCost` ("+$108.12 delivery") |
| `shipping_type` | Optional[str] | `free` when shipping is free, else None |
| `sold_currency` | Optional[str] | `"USD"` (or `"USD (repaired from <CUR>)"` after repair) |
| `total_price` | Optional[float] | `sold_price + shipping_cost` |
| `item_id` | Optional[str] | eBay item ID |
| `listing_type` | Optional[str] | `Buy It Now` / `Auction` |
| `bids_count` | Optional[int] | Bid count (for Tier-C single-bid exclusion) |
| `keyword_tag` | Optional[str] | Input keyword (set on single-keyword runs) |
| `bo_accepted` | bool | **Always False** — actor doesn't expose this |
| `raw` | dict | Original Apify item dict (debugging) |

### Known testing landmark

The Polo Ralph Lauren "On Safari" catalog returned a single
exact-match sold listing in our prior Claude-in-Chrome test:

- Title: "POLO RALPH LAUREN On Safari Illustrated Catalog Fashion Wildlife Zebra Cover"
- Sold: May 20, 2026, $300, Best Offer accepted
- URL: <https://www.ebay.com/itm/206286396483>
- Seller: matwas_7428

Running:

```bash
python apify_ebay.py "Polo Ralph Lauren On Safari catalog"
```

…should surface this listing as the first or near-first result.
That's a clean regression check that Apify is returning what the
Chrome path was finding.

---

## Security notes

The config file at `~/.ebaybiz/config.yaml` (or the Windows equivalent)
contains plaintext API tokens. This is standard practice for CLI
tools (AWS CLI, GitHub CLI, gcloud all do the same), but worth knowing:

- The directory is user-level — not world-readable on Unix.
- On macOS/Linux, you can tighten permissions: `chmod 600 ~/.ebaybiz/config.yaml`
- Don't commit `~/.ebaybiz/config.yaml` to git or share it.
- The `config.example.yaml` in this repo contains only placeholder
  values; safe to commit.

If you need stronger secret storage (OS keychain integration, KMS, etc.)
that's a future enhancement — for a single-user CLI on a personal
machine, the config-file approach is appropriate.

---

---

## ebay_client.py + ebay_schema.py — eBay Sell API client (basic)

Used by DRAFT to (eventually) publish listings, and used today for
schema introspection — discovering which fields the InventoryItem and
Offer objects support, plus per-category item-aspects.

### What works today (no eBay credentials needed)

```bash
python ebay_client.py --schema inventory_item
python ebay_client.py --schema offer
python ebay_client.py --schema all
```

Prints the static field reference from `ebay_schema.py` — type,
required-or-not, allowed values for enums, max lengths, and a one-line
purpose note per field. This is the spec DRAFT will render into.

### What works once eBay credentials are configured

Set the `ebay:` section in `~/.ebaybiz/config.yaml` (or the Windows
equivalent) per `config.example.yaml`. The required-to-start fields are
`app_id` and `cert_id`. Then:

```bash
# Verify credentials load and an app-context token can be obtained
python ebay_client.py --check

# Discover the category tree for a marketplace
python ebay_client.py --category-tree-id --marketplace EBAY_US

# Suggest leaf categories for a free-text description
python ebay_client.py --category-suggestions "vintage fashion catalog"

# Fetch the item-aspects (item-specifics fields) for a leaf category
python ebay_client.py --category-aspects 1184
```

Add `--json` to any live call to get the raw API response.

### Sandbox vs Production

`ebay.environment` in config picks the environment. Default: `sandbox`.
Switch to `production` only when you're ready to call against real
eBay data. The credentials are environment-specific — a Sandbox keyset
won't authenticate against Production.

### User-context OAuth (needed for write operations like publishing listings)

The Sell Inventory API requires a user-context token (not just app
credentials). Setup runs once and the resulting refresh_token lasts
~18 months:

1. Register a `redirect_uri` (called "RuName" by eBay) at
   <https://developer.ebay.com/my/auth-n-auth> and add it to config.
2. `python ebay_client.py --user-consent-url` and open the printed URL.
3. Sign in to eBay and authorize the app.
4. eBay redirects to your `redirect_uri` with `?code=<authorization-code>`.
5. `python ebay_client.py --exchange-code <code>` — prints the
   refresh_token to paste into config (`ebay.user_refresh_token`).

User-context token writes (`publish offer`, `create inventory item`, etc.)
are NOT yet wired into this client — they will be added when DRAFT is
implemented.

### Files in this client

| File | Purpose |
|---|---|
| `ebay_schema.py` | Static field reference for InventoryItem + Offer |
| `ebay_client.py` | OAuth, Taxonomy API helpers, CLI |
| `config.example.yaml` (`ebay:` section) | Credential template |

### Dependencies

Uses only Python stdlib (`urllib.request`, `urllib.parse`, `base64`,
`json`). No new `pip install` needed.

---

## comps_csv.py — reviewable comps CSV (all PRICE stages)

Stage B (Apify) saves JSON; stages A (WebSearch) and C (Chrome) are
agent-driven, so PRICE logs their comps here. One `<shoot-dir>/comps.csv`
with a `stage` column lets the user open a single spreadsheet and review
every comp the hunt looked at. Stdlib only (`csv`).

Columns: `captured_at, item, stage, query, price, title, sold_date,
condition, listing_type, url, note`.

```bash
# fresh file for a run
python comps_csv.py --shoot-dir <dir> --reset
# append a Stage A / C comp
python comps_csv.py --shoot-dir <dir> --item 1 --stage C --query "..." \
  --price 99.99 --title "..." --url "https://www.ebay.com/itm/..." \
  --sold-date 2026-05-14 --condition "Pre-Owned" --note "near-exact"
# fold a saved Apify run JSON in as stage B rows (unified review file)
python comps_csv.py --shoot-dir <dir> --from-apify-json apify_runs/apify_run_*.json
```

Programmatic: `from comps_csv import append_comp, from_apify_json, reset`.
In environments without a shell, write `<dir>/comps.csv` directly using the
header above.

---

## What's not in this MVP

- **PRICE calls this as Stage B** — the prompt invokes `apify_ebay.py` /
  `search_ebay_sold()` directly as the default comp source; there is no
  separate orchestration layer (the prompt is the orchestrator).
- **No write operations on the eBay client yet** — only schema discovery
  and Taxonomy reads. Inventory-item creation, offer creation, and
  publishing are added when DRAFT lands.
- **No caching** — every `search_ebay_sold()` call burns a fresh
  Apify run. For repeated identical queries within a session, you'd
  want a short-lived cache. Easy to add later.
- **No retry logic on transient failures** — Apify's `.call()` has
  internal retries, but if you hit network flakiness you may see
  occasional `ApifyError`s. Add tenacity-style retry if needed.
- **No tests** — defensive parsing makes the wrapper schema-tolerant,
  but a fixture-based test suite is worth adding once we've seen the
  default Actor's actual output shape.
