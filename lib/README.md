# lib — shared libraries

## Setup (one time)

### 1. Install dependencies

```bash
pip install pyyaml
```

`pyyaml` is for `config.py` and the eBay Sell-API code. The rest of the
suite's dependencies (pillow, opencv, numpy) are in the repo-root
[`requirements.txt`](../requirements.txt); the eBay client and `config.py`
themselves are stdlib-only on purpose, so they run in sandboxed environments
where `pip install` is blocked.

### 2. Create the config file

Copy [config.example.yaml](config.example.yaml) to your user config
directory:

```bash
# macOS / Linux
mkdir -p ~/.ebaybiz
cp config.example.yaml ~/.ebaybiz/config.yaml
# then edit ~/.ebaybiz/config.yaml — set the eBay credentials

# Windows (PowerShell)
mkdir $env:APPDATA\ebaybiz -Force
Copy-Item config.example.yaml $env:APPDATA\ebaybiz\config.yaml
# then edit %APPDATA%\ebaybiz\config.yaml — set the eBay credentials
```

The eBay credentials are the only required ones — see
[SETUP_EBAY_API.md](SETUP_EBAY_API.md). The Apify token is optional and
serves `lens_id.py` alone; get one at
<https://console.apify.com/account/integrations> if you want the Google Lens
lookup.

### 3. Verify the config

```bash
python config.py --where    # show config file path
python config.py --show     # show resolved config (secrets redacted)
python config.py --check    # verify required secrets are loadable
```

`--check` enforces the eBay credentials and merely reports the optional
secrets; a missing optional secret is not a failure:

```
[OK] ebay credentials: ok
[--] APIFY_API_TOKEN: not set (optional -- lens_id.py only)
[--] ANTHROPIC_API_KEY: not set (optional -- unused today)
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
| `get_lens_actor()` | str | never raises — has a built-in default |
| `get_anthropic_key()` | str | `ConfigError` if missing everywhere |

`get_apify_token()` / `get_lens_actor()` serve `lens_id.py` only — the Google
Lens reverse-image lookup. Apify is not a comp source; see the Stage B section
below.
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

## ebay_sold_browse.py — PRICE Stage B (sold comps)

PRICE's default Stage B comp source: eBay's sold-listings search, read
through the operator's own logged-in browser. Returns the same `CompRecord`
shape the rest of PRICE consumes, and its ingest path is cached by
[`read_cache.py`](read_cache.py) keyed query+condition+UTC date, so a
same-day re-check of a query already ingested costs no round trip
(`--fresh` forces one anyway).

Run both sorts (`best_match` and `price_high`) for a query — that is what
gives PRICE a ceiling and a middle rather than one arbitrary slice.

> **Apify was retired as a comp backend on 2026-08-15 and must not be
> re-enabled.** `lib/apify_ebay.py` is gone. The measurements behind that
> call — the `li.s-item` → `li.s-card` markup migration, the actor
> silent-zero failures, and the foreign-currency leak — are in
> [`../docs/archive/pricing-backend-issues.md`](../docs/archive/pricing-backend-issues.md).
> The only surviving Apify use in the repo is `lens_id.py`'s Google Lens
> lookup, which is a *visual* search, not a price source.

Supporting modules on the same path:

| Module | Purpose |
|---|---|
| `comps_core.py` | The `CompRecord` shape and the shared parsing/normalizing helpers, extracted from `apify_ebay.py` when Stage B moved. |
| `price_stats.py` | The deterministic half of PRICE — filtering, distribution statistics, tier placement. See [`../docs/price-strategy-v2.md`](../docs/price-strategy-v2.md). |
| `read_cache.py` | Generic on-disk read cache, keyed query+date. |
| `comps_csv.py` | The reviewable `comps.csv` every stage writes to (below). |

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

The low-level client: OAuth, Taxonomy reads, and schema introspection —
which fields the InventoryItem and Offer objects support, plus per-category
item-aspects. The *writes* (create inventory item, create offer, publish,
withdraw, delete) live one level up in [`list_edit.py`](list_edit.py), which
is what DRAFT and REVIEW actually call.

### What works today (no eBay credentials needed)

```bash
python ebay_client.py --schema inventory_item
python ebay_client.py --schema offer
python ebay_client.py --schema all
```

Prints the static field reference from `ebay_schema.py` — type,
required-or-not, allowed values for enums, max lengths, and a one-line
purpose note per field. This is the spec DRAFT renders into.

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

User-context token writes (`create inventory item`, `create offer`,
`publish offer`) are live, and go through [`list_edit.py`](list_edit.py) —
not this module. Publishing requires `--confirm`; nothing in the pipeline
publishes on its own.

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

## easypost_client.py — buy shipping labels via EasyPost (GH #80)

eBay's own Logistics API is a confirmed dead end for a small seller
(Limited Release, invitation-only, USPS-only — #32). This client quotes
and buys postage from EasyPost instead: pay-as-you-go, free up to 3,000
labels/month, no contract, rates across USPS/UPS/FedEx/DHL.

Get an API key at <https://www.easypost.com/account/api-keys> — EasyPost
issues separate **test** and **production** keys from the same dashboard;
a test key's "purchases" never charge a real carrier or ship anything,
which is the safe way to exercise the CLI below before trusting it with
real postage money. Provisioning the account + funding its balance is a
one-time human step, same spirit as the eBay OAuth setup above — nothing
here does it for you.

Set the key the same way as every other credential in this repo:

```yaml
# ~/.ebaybiz/config.yaml
easypost:
  api_key: "EZAK..."
```

or `EASYPOST_API_KEY` (env var wins if both are set).

### CLI usage

```bash
# Quote — spends nothing, no --confirm needed
python -m lib.cli ship-quote \
    --to-name "Jane Buyer" --to-street1 "1 Main St" --to-city Springfield \
    --to-state IL --to-zip 62704 \
    --from-name "My Store" --from-street1 "9 Ship St" --from-city Elgin \
    --from-state IL --from-zip 60120 \
    --weight-oz 24 --length-in 10 --width-in 8 --height-in 4

# Buy — DRY RUN by default; add --confirm to actually spend money
python -m lib.cli ship-buy --shipment-id shp_... --rate-id rate_...
python -m lib.cli ship-buy --shipment-id shp_... --rate-id rate_... --confirm
```

`ship-buy`'s confirm gate mirrors `list_edit.py --publish/--end` exactly:
without `--confirm` it makes no call to EasyPost's purchase endpoint and
only reports what would be bought and its cost. On a confirmed purchase,
the printed carrier + tracking number are handed off as the exact
`tools/pick_list.py --record-tracking ORDER_ID --carrier CODE
--tracking-number NUM --confirm` invocation (#70) needed to advance the
local ledger to SHIPPED — this module does not write the ledger itself,
and (as of this writing) `--record-tracking` has not landed on `main` yet.

---

## comps_csv.py — reviewable comps CSV (all PRICE stages)

Stage B saves its run to JSON; stages A (WebSearch) and C (Chrome) are
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

## Everything else in lib/

The modules above are the ones with setup steps. The rest, one line each:

| Module | Purpose |
|---|---|
| `cli.py` | The `ebz` entry point — `python -m lib.cli <command>`. Start here rather than calling tools directly. |
| `list_edit.py` | Function 6. Syncs a `draft.md` to an unpublished offer, publishes on `--confirm`, renders the REVIEW card, and manages any offer/SKU on the account. |
| `list_edit_group.py` | Multi-variation (CHOICE) listings — one listing, MPN-keyed variations. Delegates every shared concern to `list_edit`. |
| `draft_io.py` | Reads and writes the YAML-frontmatter listing template. |
| `single_pass.py` | PREP→IDENTIFY→PRICE→DRAFT in one pass, for routine items. |
| `photo_prep/` | The PREP stages — orientation, unskew, crop, colour — plus `center_crop.py`. |
| `status.py` | Pipeline state for a shoot, reconciled against eBay rather than local files. |
| `sync_actuals.py` | Pulls what each item actually sold for from the Fulfillment API. **Always pass a wide `--days`** — `--apply` rewrites the ledger for its window only. |
| `report.py` | REPORT-phase output: fees, ask-vs-actual, speed, categories. |
| `source_report.py` | Per-source ROI. |
| `us_only.py` | Detects export-restricted items and routes them to the US-only fulfillment policy. |
| `verdict.py` | The decision record a phase leaves behind. |
| `voice_check.py` | The linter for house voice — catches camera-frame copy before it ships. |
| `seller_style.py` / `seller_intel.py` | Measuring another seller's live titles and description HTML. |
| `ebay_browse.py` | Browse API — other sellers' *active* listings (asking prices). Sold-by-seller is not available to us. |
| `ebay_visual.py`, `lens_id.py` | Visual lookups. `lens_id` fails silently on an empty result — always run a known-indexed control image first. |
| `vindex.py`, `marble_*.py`, `mcsa_index.py`, `reembed.py`, `forum_replies.py` | The marble specialization's CLIP index, classifier and forum tooling. |
| `dir_context.py` | Resolves a shoot directory to repo-relative paths. |

Setup for the eBay Sell API — the one thing with real prerequisites — is in
[SETUP_EBAY_API.md](SETUP_EBAY_API.md).
