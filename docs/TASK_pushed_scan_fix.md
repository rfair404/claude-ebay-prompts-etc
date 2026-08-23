# Task: make "which shoots are not pushed to eBay?" answerable from eBay, not from prep.json

## What happened

Asked which shoots had approved photos in `listing/` that had never been pushed to
eBay, an agent scanned `inventory/**/.prep/prep.json` and reported on two fields:

* `pushed_at` / `pushed_listing_id` is null  -> "NEVER PUSHED"  (14 shoots)
* newest mtime in `listing/*.jpg` > `pushed_at` -> "pushed, then re-rendered" (39 shoots)

**Both signals are wrong.** Every one of the 14 "never pushed" shoots was in fact
live on eBay. The user caught it on `inventory/goodwill/keys`, and a title-based
cross-check against `inventory_sheet.csv` confirmed all 14 (FR art pieces,
christmas-train, floating-opal, both ej-08-19 pieces, four j-crew catalogs, the
silverplate lot). There is no unpushed backlog.

## Root cause

`pushed_at` in a shoot's `.prep/prep.json` is only stamped when a push runs
against *that shoot directory*. It is null, permanently, whenever the shoot
publishes some other way. The known case:

* **A shoot split into per-item listings.** `inventory/goodwill/keys` holds 22
  prepped frames and five subdirectories `item-1` … `item-5`, each with its own
  `draft.md` whose `photos:` entries point back at `../listing/`. Five listings
  were published (206502130192, …386, …524, …702, …790) on 2026-08-19. The parent
  `.prep/prep.json` never learned about any of them.

The mtime comparison is unsound for a second reason: a re-render is routine and
does not imply the live listing is stale — the render may be what was pushed, and
the push may not have re-stamped `pushed_at`. Comparing a local mtime against a
local timestamp cannot answer a question about eBay's state.

## What to build

Replace the prep.json-based check with one whose authority is eBay. The repo
already has the pieces:

| Tool | What it gives you |
|---|---|
| `tools/ebay_sheet.py` | rebuilds `inventory_sheet.csv` / `.json` from the Sell Inventory + Taxonomy APIs alone — sku, listing id, listing status, **image_count**. Reads nothing from disk. |
| `tools/inventory_sync.py` | starts from eBay's side, then maps each SKU back to the local shoot that owns it (matched from `draft.md`) plus the preset that shoot last rendered. This is the correct direction of the join. |
| `tools/compare_live.py <shoot>` | downloads what is live on eBay's CDN for a SKU and builds an ORIGINAL vs LIVE contact sheet. The only pixel-level evidence. |

Requirements:

1. **Walk `draft.md`, not `.prep/prep.json`, to enumerate sellable units.** One
   shoot may own several listings; a shoot with no `draft.md` was never drafted.
   Handle drafts in subdirectories whose `photos:` point at `../listing/`.
2. **Resolve each draft to eBay by SKU first**, falling back to a normalised
   title match only when the SKU is missing or stale (drafts are known to carry
   null offer ids for listings that are demonstrably live — see the
   `tools/ebay_sheet.py` docstring, and the frankie-roys-things -> FR rename that
   put six shoots out of reach of a path-based join).
3. **Report three states, and say which evidence produced each**: not listed;
   listed and the live image count matches the draft's `photos:` count; listed
   but the counts disagree.
4. **Do not claim "the live pixels are stale" from any local timestamp.** If that
   question needs answering, it needs `compare_live.py` on the shoot, and the
   answer is per-frame.
5. Leave `pushed_at` alone as a field — just stop treating its absence as
   evidence. If you want it trustworthy, the separate fix is to stamp it on
   *every* publish path, including the per-item one; that is a change to the push
   code, not to the scanner.

## Known-good fixtures to test against

* `inventory/goodwill/keys` -> 5 live listings from `item-1` … `item-5`, parent
  `pushed_at` null. A correct scanner reports 5 listed, 0 missing.
* `inventory/more-mags-444/j-crew/{1,2,4,5}` -> live, statuses include
  `OUT_OF_STOCK` and `ENDED`, parent `pushed_at` null. Note that "listed" and
  "currently active" are different questions; report the status rather than
  collapsing it to a boolean.
* `inventory/silverplate-SC-lot/IS-rememberance-nos-silverplate-lot` -> **a real
  discrepancy that must survive the rewrite**: `listing/` holds 11 frames,
  eBay reports `image_count` 10 on listing 206502961414. One frame did not make
  it up. A scanner that reports this shoot as clean is wrong.

## Definition of done

`python tools/<your-scanner>.py` prints, for every shoot in `inventory/`, its
listings with live status and an image-count reconciliation, sourced from eBay;
the three fixtures above come out as described; and nothing in the output depends
on a local file timestamp.
