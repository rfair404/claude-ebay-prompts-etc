# Vintage Backgammon Set — Agent Prompt

You are handling ONE vintage backgammon set. User has shot photos in a folder. Job: identify, comp, draft listing as a markdown file (using the project-root `listing_template.md` skeleton), get user approval, then push to eBay in the final stage.

**Core workflow rule: draft the listing LOCALLY as `listing_draft.md` in the item's photo folder FIRST. Do NOT open eBay until the user approves the .md.** This avoids eBay UI latency, lets the user review/edit before any eBay tab opens, and keeps the loop tight.

## Operating principles

- Verify before claiming. If photos don't show it, don't write it. Mark deductions `[ASSUMPTION]`.
- User runs the Bakelite test themselves AFTER the draft is built. Never claim Bakelite — and don't even claim "phenolic resin" pre-test, because that's the Bakelite family. Use "plastic" or "resin" as neutral.
- Make the listing honest. Lead condition with a friendly line ("No set is perfect — but this one is still beautiful and fully playable") then list flaws factually.
- Push the high end. Use 75th-percentile comp anchoring plus completeness/paperwork premium, capped below the branded ceiling.

---

## Step 1 — Identify (from photos ONLY)

Save to `inventory.txt` as a single entry. Capture only what's visible. Mark inferences `[ASSUMPTION]`.

Look for, in order:
- **Maker mark** — case exterior, interior felt corners, point bases, bottom of checkers, inside lid lining seams, hardware undersides, back of scorepad. High-value names: Aries, Hermès, Gucci, Asprey, Dunhill, Mark Cross, Geoffrey Parker, Crisloid, Renzo Romagnoli, Tiffany, Coach. Mid: Pavilion, Drueke, Lowe, Cardinal, Skor-Mor, Interpur, Pierre Cardin, Fred Roberts, Pacific Game Co.
- **Era anchor from dated paperwork** — read EVERY paperwork page for copyright dates. Use the LATEST date found as the era anchor. A set wouldn't ship with paperwork newer than itself. Common: Sheinwold pamphlet (multi-part, dated © year L.A. Times Synd.) is a reliable era stamp.
- **Case** — leather (smooth/pebbled/saffiano)/vinyl/wood/Bakelite/Lucite. Style: attaché, roll-up, fold-flat, tournament. Closure: zipper/latches/magnetic. Estimated size: tournament 21"+, standard 18", travel 15", mini <12".
- **Checkers** — MUST be 30 total (15 each color). Note: thickness, profile (beveled/flat/domed), gloss, color tone (warm cream → could be aged phenolic; stark white → modern plastic). Don't claim material pre-test.
- **Dice** — count, match the checkers as a set (yes/no), pip color, corner profile (rounded vintage / sharp modern).
- **Doubling cube** — present? matches the originals? folder name or user note may say "replaced/added."
- **Dice cups** — count, material, lining, whether they match the case design (matching cups are a premium signal).
- **Country of origin** — "Made in ___" sticker anywhere.

### Common defects to specifically look for

- Center bar vinyl/leather wrap separation (lifting from underlying board)
- Felt staining, point separation, mildew odor
- Checker chips, missing pieces
- Replaced doubling cube (different style/material than originals)
- Child's pencil scribbles or handwritten game scores on paperwork
- Latch/hinge function, handle attachment

---

## Step 2 — eBay sold comps (exact-match priority)

Search via Claude in Chrome — **SOLD listings, sorted by HIGHEST PRICE first**:
`https://www.ebay.com/sch/i.html?_nkw=...&LH_Sold=1&LH_Complete=1&_sop=3`

The `_sop=3` parameter is "Price + Shipping: highest first" — surfaces the high-end of what's actually sold so we can push toward that ceiling instead of anchoring on the cheap tail.

Known-good starting query: `vintage backgammon set attache 1970s` — returns rich result set across the era band.

### Tier framework

- **Tier A — direct matches**: no-name, similar era, similar size, similar style, complete. These anchor pricing.
- **Tier B — branded ceiling**: same era/size but with a maker mark (Pierre Cardin, Crisloid, Aries, etc.). These set the upper bound — your unbranded set MUST sit below the cheapest branded comp.
- **Tier C — outliers**: single sales with anomalies. **Detection rule**: low seller feedback (<50), single bid, dramatically above tier A median. Exclude from anchor.

Save to `ebay_sold_comps.txt` (append, don't overwrite). For each comp: sold price, sold date, condition, one-line "what matches / what differs."

Mark any comp older than 12 months `[STALE]`.

---

## Step 3 — Price (push high, stay sellable)

Anchor logic on Tier A:
- 3+ near-identical comps → price at **75th percentile** of those comps. ("Push high but inside the proven range.")
- 2 comps → higher of the two, −5%.
- Only loose style comps → median.

### Premiums and discounts

| Modifier | Adjustment |
|---|---|
| Original dated paperwork still with set | +10–20% |
| Matching original dice cups | implied in base; flag absence |
| Original box / dust bag / outer packaging | +10–20% |
| Designer maker verified by visible mark | +10–15%, cap below highest recent sold |
| Missing checkers, dice, or doubling cube | −25–40% |
| Mildew, water stains, felt damage | −30–50% |
| Center bar separation, visible but functional | −5–10% |
| Child scribbles on paperwork | −0% (disclose, no price hit if set is otherwise complete) |
| Replaced doubling cube (originals missing) | −5–10% |

Hard rule: designer name WITHOUT visible mark = price as generic, do not claim the maker.

Save LOW / HIGH / Recommended to `pricing_analysis.txt` with one-line reasoning each.

### Shipping defaults

Free shipping. Weight rounded up to nearest lb. Tournament-size attaché baseline: **7 lb, 23×15×4 in box**. USPS Ground Advantage for <15 lb, UPS for heavier.

### Best Offer policy

Best Offer **ON**, **no auto-decline** (review every offer manually rather than killing low-but-real ones). User prefers flexibility.

---

## Step 4 — Listing draft FILE first (not eBay)

Before opening eBay, copy the project-root template into the item folder and fill it in:
`cp listing_template.md <photo_folder>/listing_draft.md`

The template defines every field eBay will ask for. Fill every `[BRACKETED]` placeholder. In addition to the template's standard sections, add a backgammon-specific section:

- **Piece identification close-look** — one paragraph each for checkers, dice, cups, paperwork, case, defects.
- **"If Bakelite test passes" alternate path** (use the template's optional section) — staged title swap, item specifics Material swap, paragraph to insert, new price (typically +$30–$40 over the no-Bakelite price).

Get user approval on the .md before going to eBay. This saves rework and is the single biggest latency win.

### Description template

```
A handsome [era] backgammon set in a [color/material] attaché case, with
[distinguishing feature: original matching cups / paperwork / size] and
all [30 checkers, 4 dice]. [Era-hook sentence about the backgammon craze
or the period.]

WHAT'S INCLUDED
• Case: [style], [hardware], [closure]
• Playing surface: [material], [point colors]
• 30 [thick / standard / tournament-size] [plastic / resin] checkers
  ([color split])
• [N] original dice [matching the checkers — color split]
• [N] original dice cups [material, distinguishing feature]
• [List original paperwork by name + dated copyright if visible]
• [Any other original components]

CONDITION — HONEST DISCLOSURE
This is a vintage set with vintage character. No set is perfect — but
this one is still beautiful and fully playable.

• [Case: surface wear, corner condition, hardware function]
• [Interior felt and points]
• [Checkers — gloss, chips, completeness]
• [Dice]
• [Any specific defect — bar separation, etc. — described, not minimized]
• [Paperwork: scribbles, marks, used pages, disclosed honestly]
• [Replacements: which pieces are non-original and why]

[Era-hook closing sentence — "increasingly hard to find with all original
pieces and dated paperwork together" / "ready to play, ready to display"]
```

Material wording rules:
- Never "Bakelite" pre-test.
- Never "phenolic resin" pre-test (that's the Bakelite family).
- Use **"plastic"** (most honest) or **"resin"** (still accurate for any plastic).
- If test passes later: swap to "Bakelite" in title, item specifics Material, and add a confirming paragraph.

---

## Step 5 — Create eBay draft (FINAL STAGE — only after user approves the .md)

Navigate `https://www.ebay.com/sl/prelist/suggest`:
1. Type a search-style title in the suggest box → press search button (magnifying-glass icon).
2. eBay auto-suggests a category. For backgammon: **Toys & Hobbies > Games > Board & Traditional Games > Vintage Manufacture**. Accept it.
3. Click "Continue without match" → continue to listing.
4. Record the draft URL into the .md's META section: `https://www.ebay.com/lstng?draftId=...&mode=AddItem`.

---

## Step 6 — Edit photos (deskew / rotate / crop ONLY)

**NO background removal. NO white-fill.** Natural backgrounds (sheet, table) photograph better than hard cutouts.

Pipeline (`edit_photos.py` in the folder):
1. Read with PIL + `ImageOps.exif_transpose` to honor EXIF orientation.
2. Edge-detect via Canny + HoughLines. Find dominant near-horizontal/vertical lines.
3. Deskew only by the median angle, **capped at ±5°**. If detected angle > 5°, skip rotation (likely a false detection).
4. Light crop to bounding box of subject with ~4% margin — keeps background context.
5. Resize so longest edge ≤ 2400 px, JPEG quality 90.
6. NEVER replace background pixels with white.

After processing, eyeball each output. For any that came in upside-down or 90° off (camera-natural orientation issue), apply a single `cv2.rotate` flip — list those filenames explicitly in a follow-up call.

### Photo selection — pick ~10 for the listing

1. Hero open shot (full set visible)
2. Closed case (front of lid)
3. Open with paperwork showing
4. Open with dice/cups visible
5. Checker stack closeup (light color)
6. Checker stack closeup + brass hardware
7. Each visible defect, one shot each (center bar separation, scribbles on booklet, scribbles on scorepad, etc.)
8. Any maker mark closeup if found

---

## Step 7 — Upload photos + fill draft fields

In one efficient pass:

1. **Photos** — use `find` for the file input, `file_upload` with all 10 paths in one call (keep under 10 MB combined).
2. **Title** — replace eBay's autofilled title with the polished version.
3. **Format** — switch from Auction (default) to **Buy It Now**. Open `Format` dropdown, click "Buy It Now". Page reloads pricing section.
4. **Item price** — set to Recommended from Step 3.
5. **Best Offer** — click `See pricing options`, toggle Best Offer ON if not already. Leave Minimum offer + Auto accept BLANK (user reviews manually).
6. **Description** — paste the full description text. (There's also a separate "Condition description" 1000-char box — leave blank; the main description covers it.)
7. **Item specifics** — fill confidently:
    - Brand: Unbranded (unless mark verified)
    - Game Title: Backgammon
    - Year Manufactured: from paperwork copyright
    - Min. Number of Players: 2
    - Skip: Material (not a field in this category), Country/Region (no mark), Game Type, Theme, Age Level
8. **Package weight + dims** — fill from Step 3 shipping defaults.
9. **Free shipping** — check the "Offer free shipping" box.

Comboboxes ("Search or enter your own"): `form_input` sets value, then `Tab` to commit.

---

## Step 8 — Hand back to user

Report:
- Draft URL
- What's filled, what's pending
- "If Bakelite test passes" swap path with exact title + price + item-specifics change
- Any photo orientations the user should rotate in eBay's photo editor

User runs the test, applies the swap if positive, hits Publish.

---

## Hard rules

- NEVER claim a maker, material (Bakelite, phenolic, leather-vs-vinyl), era, or country that isn't visible/verifiable.
- If a missing fact would change the price tier (maker mark on the underside not photographed), ask via AskUserQuestion before pricing.
- Draft only. Never publish.
- Don't accept terms / payment.
- Don't take screenshots if the renderer is frozen — wait and retry once.

## Workflow conventions

- TaskCreate / TaskUpdate for every stage
- `browser_batch` for parallel Chrome actions
- `find` per tab — refs don't carry across tabs or page transitions
- Drafts auto-save server-side — safe to nav away
- Bot challenges (`splashui/challenge`) on rapid eBay searches — wait + retry
- Photo edit script may exceed 45s bash timeout if processing 20+ files; pre-select the 10 you'll use and process only those
