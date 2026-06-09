# eBay Flip Prompt

Paste this with a photo of items to flip. Skip sections by saying so. Be terse, parallelize, use tools without asking. Pause between stages for my input.

**Core workflow rule: draft listings LOCALLY as `.md` files first, push to eBay only in the final stage.** Use `listing_template.md` in the project root as the skeleton. This avoids eBay UI latency, lets the user review/edit before any eBay tab opens, and lets multiple drafts be prepped in parallel without browser overhead.

---

**1. Identify**
List every item visible. Brand, type (book / catalog / mailer / magazine / object), rough era. Save to `inventory.txt`. Flag obscured items as assumptions.

**2. eBay sold comps**
For each item, search eBay **SOLD listings, sorted HIGHEST PRICE first** via Claude in Chrome:
`https://www.ebay.com/sch/i.html?_nkw=...&LH_Sold=1&LH_Complete=1&_sop=3`
The `_sop=3` parameter is "Price + Shipping: highest first" — start from the high end of actual sales so we anchor at the ceiling instead of the cheap tail. Push high-end starting prices.
Web search alone won't return prices. Note top 2–3 comps + avg high. Save to `ebay_sold_comps.txt`. Mark items with no comps. Apply Tier A/B/C framework (direct match / branded ceiling / outliers — exclude single-bid low-feedback outliers from anchor).

**3. Pricing v1**
For each: LOW / HIGH / Recommended. Factor in:
- Free shipping (seller pays). Weight rounded up to nearest lb, dims to nearest inch. Media Mail for books/catalogs, Ground Advantage for mailers.
- Collectability for niche buyers — push high at 75th percentile of Tier A comps if comps support it.
Save to `pricing_analysis.txt`.

**4. Draft listings LOCALLY (one .md file per item)**
For each item: `cp listing_template.md <item_slug>/listing_draft.md` then fill in every bracketed field. Get user approval per item (or batched) before any eBay push.

**5. Item descriptions (in the local .md)**
Brief, unique, compelling. Hook the niche collector. NEVER invent facts (dates, photographers, page counts, seasons). If unknown, omit. Use the description template inside `listing_template.md`. Honest condition disclosure with one friendly framing sentence ("No piece is perfect — but this one is still desirable").

**6. Photo shot list + photo edits**
Numbered list, grouped by item, one short slug folder per item (`polo`, `rrl`, ...). User shoots in order, drops in folders. Save to `photo_shot_list.txt`.

**Edit pipeline (staged — `edit_lot.py`, parallel with 2 workers = nproc):**
1. **Load raw pixels** — do NOT `exif_transpose`. Phone EXIF Orientation is unreliable across cameras (verified case: Pixel HDR mis-tags Orientation=3).
2. **Scale down to 2500px longest edge** FIRST — every downstream cv2/Tesseract op becomes 3–5× faster. Output is bounded anyway (1600 square or 2400 long edge), so no quality lost.
3. **Strip EXIF** — `Image.fromarray()` at save time produces a clean file. No orientation tag = no viewer ambiguity.
4. **Rotate via 4-way OSD vote** — test 0°, 90°, 180°, 270° rotations; pick the orientation Tesseract OSD reports as upright (`rotate==0`) with highest confidence (`OSD_CONF_TRUST=1.0`). If all silent, keep raw.
5. **Deskew ±5° max** — Canny + HoughLines median; cap protects against false rotations on busy interiors.
6. **Smart crop + final framing**:
   - Subject bbox = UNION of Otsu (printed content) + Canny edges (paper-vs-table boundary). The Canny pass solves white-page-on-white-sheet — Otsu alone misses white margins.
   - Crop only if coverage is in (0.30, 0.60). Below 30% = bbox detection unreliable (caught just a label), above 60% = subject fills frame already.
   - `--style paper` (default): square-pad to 1600×1600 white (eBay's preferred shape).
   - `--style natural` (backgammon-type 3D objects): no white-fill, longest edge 2400px.
7. **Save** JPEG q90, no EXIF.

**Before running — always verify the script first:**
```bash
python3 -m py_compile edit_lot.py && echo "ok"
```
If it errors, the file was left broken by a prior partial edit. Write the corrected version to `/tmp` and `dd` it to the mount (FUSE truncates large file writes from Python/Edit tools).

**OSD vs --fast:** OSD only helps when photos have enough text for Tesseract. Clothing, objects, and most catalog shots return `no-OSD-signal` — `--fast` gives identical results and is 3× faster. Reserve OSD for text-dense items (books, mailers, dense catalog spreads).

**Timing (~14-photo lot, 45s bash timeout per call):**
- OSD: ~22s/4 files. Script auto-resumes via `{dst}.staging` — call repeatedly, then `--finish`.
- `--fast`: all files in one call (~16s). Spot-check rotation; fix with `mogrify -rotate <deg> file.jpg`.

**FUSE write rule — always use a fresh dst dir.** Never reuse a destination that already exists — FUSE whiteouts block writes to same filenames, giving silent `ENOENT`. Name outputs `edited_v1`, `edited_v2`, etc.

```bash
# Fast mode (clothing/objects — photos already upright from phone):
python3 edit_lot.py lot1 lot1/edited_v1 --fast

# OSD mode (text-heavy items — call repeatedly until all staged, then --finish):
python3 edit_lot.py lot1 lot1/edited_v1          # chunk 1 of N
python3 edit_lot.py lot1 lot1/edited_v1          # chunk 2 of N  (same dst — resumes)
python3 edit_lot.py lot1 lot1/edited_v1 --finish # flush staging -> final dst

# Natural style (3D objects like backgammon sets):
python3 edit_lot.py lot1 lot1/edited_v1 --fast --style natural
```

See `photo_editing_capabilities.md` for full pipeline details, tunables, and known limits.

**7. Re-validate pricing**
After descriptions exist, re-check whether comps support higher prices. Push items with strong collectability/scarcity narrative to HIGH end. Skip items where I had no comps and brand is too obscure (keep mid). Report which moved + why.

**8. Ask before key decisions**
If a missing fact (e.g. year on a Polo catalog) would meaningfully change pricing tier, ask the user via AskUserQuestion before guessing.

**9. Push to eBay (FINAL STAGE ONLY — after user signs off on the .md drafts)**
For each approved draft: navigate `https://www.ebay.com/sl/prelist/suggest`, follow the "PUSH TO EBAY" section in `listing_template.md` exactly. Run in parallel tabs (one per item) if drafting multiple.
- Title search → category auto-select → continue without match
- **Set condition at the prelist "Confirm details" dialog** (radio button) — this is the only place it can be set
- Upload photos one at a time via `file_upload` with Windows-style paths
- Title: find by `maxLength === 80`; set via React native value setter
- Format → Buy It Now; price: find by aria-label containing "price"; set via React native value setter
- Best Offer ON; set auto-decline at ~70% of asking
- Condition description: first unlabeled textarea; React native setter
- Description: set BOTH the outer `aria-label="Description"` textarea AND the RTE iframe's contenteditable div (see `listing_template.md` for JS snippets) — setting only one will not persist
- Item specifics: Publication Year and Publisher use button → menuitemradio / search-textbox pattern, not plain inputs
- Shipping: change from Ground Advantage to USPS Media Mail via three-dot → Change service → search → select → Done; then check free shipping
- Record draft URL in the .md's META section
- **Click "Save for later" — never "List it".** Report draft URLs + pending blockers. Never publish.

**Conventions**
- All outputs to the workspace dir.
- Task list for every stage; mark in_progress / completed as you go.
- Drafts auto-save server-side once pushed — safe to navigate tabs away and back via `https://www.ebay.com/lstng?draftId=...&mode=AddItem`.
- Never publish/submit a listing — drafts only.
- Never enter payment info or accept terms beyond what's needed to create the draft.
- Stop and ask before any irreversible action.

**Chrome tactics (only relevant during stage 9)**
- Prefer `browser_batch` for sequences of clicks/types/screenshots.
- `find` per tab — refs don't carry across tabs or page transitions.
- After clicking dropdowns / page-changing buttons, `find` again before next action.
- Don't take screenshots when the renderer is frozen — wait 3–5s and retry once.

**Known gotchas**
- "Claude is active in this tab group" overlay can block bottom-center clicks. Re-click or close the overlay X.
- eBay bot challenge (`splashui/challenge`) on rapid eBay searches — wait, retry, or use a different tab.
- Per-tab refs aren't interchangeable.
- Tab title can lag behind saved title.
- eBay default Format is Auction — must switch to Buy It Now before price/Best Offer fields appear correctly.
