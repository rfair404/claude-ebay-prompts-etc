# Agent prompt — reclaim disk in `inventory/` without losing an original

Repo: `C:\Users\Reuseum\Documents\Claude\Projects\ebaybiz`

## Read this first — the two facts that govern every decision

1. **`inventory/` is NOT in git.** `git ls-files inventory` returns 0. There is
   no `git checkout` to undo you with. Every delete in this task is permanent.
2. **The reconciliation is already done and it came back clean.** eBay knows 219
   SKUs; 218 of them have a local `draft.md`/`draft_group.md` on disk. There is
   *no* pool of forgotten v1/v2 shoots to sweep out. The waste is **derived
   render output** — regenerable bytes produced by the v3 prep pipeline itself.
   Do not go hunting for "old-era shoots"; that hypothesis was tested and failed.

Current size: **27.3 GB** across `inventory/`, of which ~9.2 GB is
`.prep/presets/`.

## Ground rules

- **Never delete a camera original.** An original is a top-level image file in a
  shoot directory (the dir that owns `draft.md`).
- **Never delete these**, they are load-bearing in the live pipeline:
  - `no-exif/` — `lib/photo_prep/prep.py` lists it in `SOURCE_FALLBACKS`; for
    some shoots it is the only surviving copy of frames.
  - `.orig/` — `lib/photo_prep/center_crop.py --apply` overwrites originals in
    place and backs them up here. This *is* the original for those shoots.
  - `listing/` — the shipped photo set.
  - `.prep/prep.json`, `.prep/*.jpg` sheets, and every record file
    (`draft.md`, `identify.txt`, `investigate.txt`, `price.txt`, `comps.csv`,
    `review_card.md`, `needs_review.md`, `SOLD.md`).
- **Work one phase at a time.** After each phase, print bytes reclaimed and stop
  for the user to confirm before the next.
- **Dry-run first, always.** Every phase gets a `--dry-run` listing the exact
  paths and the byte total before anything is removed.
- Move to a quarantine dir rather than hard-deleting:
  `C:\Users\Reuseum\Documents\Claude\Projects\ebaybiz\_quarantine\<phase>\<original\relative\path>`
  Report the quarantine size at the end. The user empties it, not you.

## Phase 1 — preset renders (≈9.2 GB) — NOW TOOLED, use the tool

Do not hand-roll this. `prep.py` was purely additive — no `rmtree`, no `unlink`
anywhere in the file — which is the root cause of the whole 27 GB. That is fixed:

- `run_apply` now calls `_sweep_unreferenced_presets` at the end of every render,
  so the **leak** class cannot accumulate again.
- `prep.py <shoot> --gc` lists what an **approved** shoot is still holding;
  `--gc --gc-force` removes it. Dry by default, because `inventory/` is
  gitignored.
- `tools/prep_gc.py` runs the same logic tree-wide.

Run:

    python tools/prep_gc.py                      # dry, whole tree
    python tools/prep_gc.py --unreferenced-only  # just the leak, safe anywhere
    python tools/prep_gc.py --force              # do it

Current dry-run over 161 manifests:

| class | shoots | size |
|---|---|---|
| unreferenced (pure leak — nothing can read it) | 74 | 3,506 MB |
| superseded in approved shoots (unchosen looks + answered ask panels) | 148 | 5,681 MB |
| held back, shoot not approved | 13 | — |
| **total** | | **9,187 MB** |

The tool keeps the chosen look, keeps `listing/`, and refuses any shoot that is
not approved — before approval the unchosen looks *are* the comparison the
operator has not made yet. All of it regenerates with `--apply`.

## Phase 2 — abandoned intermediate directories (591 MB verified, NOT 1.2 GB)

**An earlier draft of this document was wrong here, and the way it was wrong is
the lesson.** It listed these patterns by name and told you to quarantine them
wholesale. Half the bytes on that list were not duplicates:
`decatur-pubs/_prepped` is the only copy of the 142 renamed frames behind four
live listings, `backgammon1/photos` is the only copy of a sold item's photos,
and `more-tube-lamps-lots/6cg7/cropped` is the only copy of a drafted item's.
A pattern name is not evidence. **Prove duplication per directory, then delete.**

### The guard you must run before removing any directory

For each candidate dir `D` inside shoot `S`:
1. List `D`'s images recursively, excluding contact sheets
   (`crop_review`, `prep_review`, `prep_presets`, `contact_sheet`, `review`).
2. List every other image in `S` (recursively, **excluding `D`'s own subtree** —
   a naive `rglob` from the parent re-finds `D` and reports 100% coverage for
   everything; that bug made a first pass clear all ten hold-outs).
3. Match on the stem, then again with a leading `NN_` prefix stripped and on
   substring. `listing-photos/01_P8140040.jpg` is `P8140040.JPG` renamed; a
   plain stem compare calls it unique when it is a copy.
4. Delete only at **≥95% coverage**. Anything below that is a hold.

### Verified droppable — 40 dirs, 591 MB

| pattern | MB |
|---|---|
| `cropped/` (22 of 24 dirs) | 398 |
| `listing.bak-crushed/` | 122 |
| `no-exif/trimmed/` | 30 |
| `listing-plain/` | 20 |
| `.picasaoriginals/` (2 of 3) | 14 |
| `listing-photos/` (all 6, rename-matched) | 6.5 |
| empty `_prepped`/`.prior-run-bak` shells | ~0 |

### HOLD — sole source, do NOT delete (601 MB)

| dir | MB | why |
|---|---|---|
| `decatur-pubs/_prepped/` | 511 | 142 semantically renamed frames (`01_cover.jpg`) behind 4 **live** listings; only ~41 images exist in the sibling item dirs. This is the published set. |
| `backgammon1/photos/` | 82 | the only photos of an item **sold 2026-08-10** (order 10-15011-26276, $49.99) |
| `more-tube-lamps-lots/6cg7/cropped/` | 7.3 | the only photos of a drafted Sylvania 6CG7 tube; the shoot root has zero images |
| `decatur-pubs/_contact_sheets/` | 1.4 | 4 lot sheets, no source; trivial size, not worth the risk |

## Phase 3 — file-level junk (6.5 MB)

Small, but it stops the tree lying about what is a photo.

- `Thumbs.db`, `*.thm`, `.picasa.ini` — 198 files, 1.7 MB.
- `apify_run_*.json` — 329 files across 138 shoots. Keep the newest per shoot,
  drop the other **191 (4.8 MB)**. `comps.csv` in the same shoot is the
  distilled form.
- `draft.md.bak-*` — leave, text and tiny.

## Phase 4a — 25 drafted SKUs eBay never received (report + act, do not delete)

Every one has a null `offer_id`, status `DRAFTED`, and an empty `listing/`.
These were **never pushed to eBay** — nothing was deleted account-side. The
blocker is local and it is the same blocker in almost every case:

| stall point | count |
|---|---|
| PREP never run | 20 shoots |
| rendered, no preset picked | 2 shoots (`FR/painted-dog-cat`, `FR/yule-log-candle`) |

Combined ask: **~$1,026**. This is not cleanup — it is priced, photographed
stock one PREP run short of listing. The list:

    cast-figurines/1..10 ($258)   more-tube-lamps-lots/{6cg7,sylvania,radio-tube-lots x4}
    eagle-art ($95)   m-agni ($110)   mask-red ($65)   mask-yel ($60)
    FR/painted-dog-cat ($99)   FR/yule-log-candle ($88)
    misc/board-spreader-set ($43)   more-mags-444/ben-silver-collection/{1,2} ($70)

Note `more-tube-lamps-lots/6cg7` has **no images in the shoot root** — its only
photos are in `cropped/`. Run `prep.py` against that subdir or move the frames
up before anything touches it.

The two `.prior-run-bak` entries (`mask-red`, `mask-yel`) are empty shells whose
SKUs belong to the parent shoots — fold them in, do not treat them as items.

## Phase 4b — 33 orphan roots, 2.5 GB (report, then decide per bucket)

Only **3** are inert. The other 30 are inventory, not litter.

**Sold, SKU never recorded — 7 roots, 667 MB. Archive, never delete.**
`hens` (8 sales), `softwaves`, `backgammon1`, `burberry-auth-cashmire-scarf`,
`mcspadden-mountain-dulcimer`, `Sell drum can`, `pool-balls`. Each carries a
`SOLD.md` and matches `sales_ledger.csv` by title only. **Action:** backfill the
`sku`/`shoot_dir` columns in `sales_ledger.csv` so the next audit joins cleanly.

**Drafted, never published — 7 roots, 416 MB.**
`tins`, `coke-tray`, `bb-cats-pair`, `hone`, `baby-boy`, `esquire-gentleman`,
`fenton-lamps`. **Action:** same as 4a — these want PREP and PUBLISH, not a broom.

**Hand-listed channel — 1 root, 275 MB. Not backlog, not cleanup.**
`hunter-33-carrabelle` is a 1982 Hunter 33 Cherubini sloop, $7,500 OBO, 98
photos. eBay Motors vehicle listings do not go through the Sell Inventory/Offer
API, so this is **listed by hand** and tracked outside the pipeline — the same
way case items go to the mall rather than to eBay. Its `draft.md` is copy to
paste into the Motors flow, not a `--list` target. Do not count it in any
pipeline backlog and do not chase it as a stalled draft. **Keep the shoot.**

**Priced or identified, no draft — 15 roots, 1,079 MB, ~$670 of parseable ask.**
`marbles-collection-1` and `-2` (647 MB together) are the known 3-bucket sorting
project, not a stall. The rest — `ej-07-21` (6 items, $260), `ring-b`,
`red-bottle`, `dumpster-books-hardbacks` ($45), `18k-mens-ring` ($50),
`mats-jonasson-folia-lead-glass` ($40), `expresso-cup` ($12), `08-19-art` ($125),
`peacock`, `p-ring` ($55), `ring-gr` ($55), `ring-bl` ($28),
`filigree-pendant-necklace` (0 MB — empty, drop) — are stalled at DRAFT.
**Action:** flag for DRAFT. Never delete.

**Inert — 3 roots, 72 MB.**
- `New folder` — empty. Remove.
- `backgammon2` — 18 photos, zero record files, camera clock reads 2011,
  superseded by the live `backgammon2-alt`. **Ask before touching**; it is the
  only genuine deletion candidate in the whole tree.
- `_refs` — a deliberate reference-image library. **Keep.**

## Verification after every phase

    python tools/ebay_sheet.py          # must still report 219 SKUs / 139 live
    python tools/inventory_sync.py      # every live SKU still resolves to a shoot

If either count moves, you removed something load-bearing — restore it from
`_quarantine/` immediately and report.

## Expected outcome

| phase | reclaim |
|---|---|
| 1 — preset renders (`tools/prep_gc.py`) | 9,187 MB |
| 2 — verified-duplicate intermediates | 591 MB |
| 3 — file-level junk | 6.5 MB |
| **total** | **~9.8 GB** (27.3 GB → ~17.5 GB) |

No original touched, no live listing's photos disturbed, 601 MB correctly held
back as sole-source, and a written report for phases 4a/4b — which together are
~$1,700 of photographed, priced stock that stalled at PREP, not junk.
