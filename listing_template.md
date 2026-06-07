# Listing Template — eBay Draft (fill locally first, push to eBay last)

Copy this file for each new item: `cp listing_template.md <folder>/listing_draft.md`
Fill in every `[BRACKETED]` field. Delete sections marked OPTIONAL if not used.
Get user approval on this file BEFORE opening eBay. Then push to eBay in one batched pass at the end.

---

## META (not pushed to eBay)

- **Item folder**: `[path]`
- **Date drafted**: `[YYYY-MM-DD]`
- **Pre-publish blockers**: `[e.g. Bakelite test pending, measurement needed]`
- **Draft ID** (filled after eBay push): `[eBay draftId from URL]`

---

## PHOTOS

Pick 8–12 photos for the listing. Edit pipeline: **deskew/rotate/crop only — no background removal**.

Selected (in upload order):

| # | Filename | Purpose |
|---|---|---|
| 1 | `[hero open shot]` | main listing image |
| 2 | `[closed/front view]` | alternate angle |
| 3 | `[detail 1]` | |
| 4 | `[detail 2]` | |
| 5 | `[detail 3]` | |
| 6 | `[detail 4]` | |
| 7 | `[defect closeup 1]` | condition disclosure |
| 8 | `[defect closeup 2]` | condition disclosure |
| 9 | `[maker mark or label]` | provenance |
| 10 | `[paperwork / extras]` | |

Total combined size must stay under 10 MB for single batch upload.

---

## CATEGORY

- **Path**: `[e.g. Toys & Hobbies > Games > Board & Traditional Games > Vintage Manufacture]`
- **eBay auto-detects** — accept its suggestion unless clearly wrong.

---

## TITLE

- **Item title** (max 80 chars): `[Title here]`
- Char count: `[X/80]`
- **Subtitle** OPTIONAL (fee applies): `[skip unless niche keyword needed]`
- **SKU / Custom label** OPTIONAL: `[internal tracking only, not buyer-visible]`

Title formula: `Vintage [Era] [Brand/Unbranded] [Item Type] [Size] [Color/Material] [Style] [Distinguishing Feature]`

---

## ITEM SPECIFICS

Fill only fields you can verify. Skip rather than guess. Field availability varies by category.

| Field | Value | Confident? |
|---|---|---|
| Brand | `[value or Unbranded]` | yes/no |
| Type | `[value]` | yes/no |
| Year Manufactured | `[YYYY]` | yes/no |
| Country/Region of Manufacture | `[value]` | yes/no |
| Material | `[value]` | yes/no |
| Color | `[value]` | yes/no |
| Style | `[value]` | yes/no |
| Features | `[comma-separated]` | yes/no |
| Number of Players | `[value]` | yes/no |
| (other category-specific) | `[value]` | yes/no |

Wording rules:
- "Unbranded" is a valid Brand value when no maker mark is visible.
- Never claim a material or brand without verification (no "Bakelite", "leather", "Hermès" pre-evidence).
- "Resin" or "plastic" is safe neutral wording for unverified materials.

---

## DESCRIPTION

```
[Opening hook — 1-2 sentences. Era, distinguishing feature, why this set
matters to a collector.]

WHAT'S INCLUDED
• [Bullet each component, group by category]
• [Be specific about counts: 30 checkers, 4 dice, etc.]
• [List original paperwork by name + dated copyright if visible]
• [Distinguish original vs replacement pieces]

CONDITION — HONEST DISCLOSURE
[One friendly framing sentence — e.g. "This is a vintage piece with
vintage character. No item is perfect — but this one is still beautiful
and fully functional."]

• [Exterior condition — wear, finish, hardware]
• [Interior / functional condition]
• [Specific defects, described not minimized]
• [Paperwork or extras condition]
• [Any replacement parts or non-original components]

[Closing sentence — collector hook, scarcity, "ready to use" / "ready to
display"]
```

---

## PRICING

- **Format**: Buy It Now (default to BIN; only choose Auction for hot-trend or scarce/no-comps items)
- **Item price**: `$[X.XX]`
- **Quantity**: 1
- **Best Offer**: ON
- **Auto-decline**: set at ~70% of asking price (e.g. $179 ask → $125 floor)
- **Minimum offer**: blank (manual review)
- **Auto accept**: blank (manual review)
- **Require immediate payment** (BIN): unchecked
- **Reserve price**: none
- **Scheduling**: none (publish immediately when user approves)
- **Your cost** (private): `$[X.XX]` OPTIONAL for profit tracking

Pricing rationale (delete before push):
- Comp search: SOLD listings sorted by HIGHEST PRICE first
  → `https://www.ebay.com/sch/i.html?_nkw=...&LH_Sold=1&LH_Complete=1&_sop=3`
  → start from the ceiling, work down; the goal is to push high-end starting price
- Anchor: `[Tier A comp median or 75th percentile from top of sort]`
- Premium: `[+X% for paperwork / cups / completeness]`
- Discount: `[−X% for known defects]`
- Final: `$[X.XX]` — push-high inside proven range, below branded ceiling

---

## SHIPPING

- **Package weight**: `[N]` lb `[M]` oz (round UP to nearest lb)
- **Package dimensions**: `[L]` × `[W]` × `[D]` inches (boxed, round up)
- **Primary service**: USPS Ground Advantage (default for <15 lb; switch to UPS Ground if heavier or oversized)
- **Offer free shipping**: ✓ checked
- **Additional services**: none
- **Handling time**: 1 business day (default)
- **Item location**: `[ZIP from seller profile]`

Shipping defaults reference:
- Tournament-size attaché (~21"): 7 lb, 23 × 15 × 4 in
- Standard book/catalog (single): 1–2 lb, 12 × 10 × 2 in
- Lot of magazines (3–5): 4–6 lb, 14 × 11 × 4 in
- Small accessory/folder: 1 lb, 12 × 10 × 1 in

---

## RETURNS

- **Returns accepted**: 30-day buyer-paid return (default — adjust per seller policy)
- Inherits from saved seller preferences if profile is set up

---

## PRE-PUBLISH CHECKLIST

- [ ] Title under 80 chars
- [ ] All confident item specifics filled
- [ ] Description proofread — no invented facts
- [ ] Condition section discloses every visible defect
- [ ] Price supported by ≥2 recent Tier A comps in `ebay_sold_comps.txt`
- [ ] Photos edited (deskew/rotate/crop only) — natural backgrounds preserved
- [ ] Photo orientations all correct (eyeball each one)
- [ ] Weight + dimensions measured or estimated conservatively
- [ ] Any pending tests/verifications resolved (Bakelite, etc.)
- [ ] "If [test] passes" alternate path documented (price + title swap)

---

## "IF [TEST/VERIFICATION] PASSES" ALTERNATE PATH

OPTIONAL — use when a pending verification could change pricing tier.

| Change | Pre-test value | If passes |
|---|---|---|
| Title | `[current]` | `[updated]` |
| Material spec | `[current]` | `[updated]` |
| Price | `$[X]` | `$[Y]` |
| Description add-on | n/a | `[paragraph to insert]` |

---

## PUSH TO EBAY (final stage only — after user approval of this file)

Order of operations in one efficient pass:

1. Navigate `https://www.ebay.com/sl/prelist/suggest` → type search-style title → click search → accept category → "Continue without match" → continue to listing form
   - **Condition is set HERE** at the prelist "Confirm details" dialog (Good / Very Good / etc. radio) — it does NOT appear on the main listing form. Select before continuing.
2. Record draft URL (`draftId=...`) into META section above.
3. Find file input → `file_upload` with Windows-style path, one file at a time. eBay accumulates sequentially.
4. **Title** — find by `maxLength === 80` (NOT by aria-label). Use React native value setter:
   ```js
   const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
   setter.call(el, 'YOUR TITLE');
   el.dispatchEvent(new Event('input', { bubbles: true }));
   el.dispatchEvent(new Event('change', { bubbles: true }));
   ```
5. Format dropdown → Buy It Now.
6. **Price** — find by `aria-label` containing "price" (label is "Item price", NOT by placeholder). Use React native value setter (same pattern as title but for `HTMLInputElement`).
7. "See pricing options" → Best Offer toggle ON → set auto-decline at ~70% of asking.
8. **Condition description** — first unlabeled `<textarea>` on the page. Use React native value setter (use `HTMLTextAreaElement.prototype`).
9. **Description** — eBay's description is a rich text editor inside an iframe. Setting the `aria-label="Description"` textarea alone does NOT persist on save. Must set BOTH:
   - Outer textarea (`aria-label="Description"`) via React native setter
   - Inner iframe: `document.getElementById('se-rte-frame__summary')` → `contentDocument.querySelector('[contenteditable="true"]')` → set `.innerHTML` as `<p>` tags → dispatch `input` event
10. **Item specifics** — fill from ITEM SPECIFICS table.
    - Standard fields: `form_input` or React native setter + Tab to commit comboboxes.
    - **Publication Year**: click the "Publication Year" button → type year → click the matching `menuitemradio`.
    - **Publisher**: click "Publisher" button → click the search textbox (`aria-label="Search or enter your own..."`) → type value.
11. Package weight + dims → from SHIPPING section.
12. **Shipping service** — eBay defaults to USPS Ground Advantage. For catalogs/books change to Media Mail:
    - Click three-dot "More options" button next to the shipping service → "Change service" → search "Media Mail" → select USPS Media Mail radio → Done.
13. Offer free shipping → check the checkbox.
14. **Stop. Do NOT click "List it". Click "Save for later" only.** Report draft URL + any pending blockers back to user.

### React native value setter (reusable pattern)
```js
const setVal = (el, val) => {
  const proto = el.tagName === 'TEXTAREA'
    ? window.HTMLTextAreaElement.prototype
    : window.HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(el, val);
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.dispatchEvent(new Event('change', { bubbles: true }));
};
```

### RTE description setter
```js
const iframe = document.getElementById('se-rte-frame__summary');
const iDoc = iframe.contentDocument || iframe.contentWindow.document;
const editor = iDoc.querySelector('[contenteditable="true"]');
editor.innerHTML = desc.split('\n\n').map(p => `<p>${p.replace(/\n/g,'<br>')}</p>`).join('');
editor.dispatchEvent(new Event('input', { bubbles: true }));
```

---

## eBay form field reference (read-only — what eBay's form contains)

For agent reference when filling fields. Sections appear in this order on the eBay listing form:

1. **Photos & Video** — up to 24 photos + 1 minute video. File input accepts batched uploads.
2. **Title** — Item title (80 char max), optional Subtitle (fee), optional SKU.
3. **Category** — auto-suggested from title search; rarely needs manual change.
4. **Item specifics** — Suggested (auto-extracted from photos/title) + Additional (full list, varies by category). Comboboxes have placeholder "Search or enter your own"; `form_input` + Tab to commit.
5. **Variations** — only for multi-SKU items; usually skip.
6. **Description** — rich text editor inside `id="se-rte-frame__summary"` iframe. There is also an outer `aria-label="Description"` textarea (React state) and a separate unlabeled textarea (condition description). Both the outer textarea AND the iframe's `[contenteditable="true"]` div must be set for description to persist on save. Condition description = first unlabeled textarea only.
7. **Pricing**:
   - Format dropdown (Auction / Buy It Now)
   - Item price (or Starting bid + optional Buy It Now for Auction)
   - "See pricing options" dropdown reveals toggles: Autofill pricing details, Immediate payment, Your cost, Best offer, Volume Pricing, Scheduling, Sell as a lot
   - Allow offers block: Minimum offer, Auto accept
   - Quantity
8. **Shipping**:
   - Package weight (lbs + oz)
   - Package dimensions (L × W × D inches)
   - Primary service (USPS Ground Advantage default with rate range shown)
   - Offer free shipping checkbox
   - Add additional services
9. **Preferences / Your settings**:
   - Returns policy
   - Handling time
   - Item location
   - Payment preferences (inherited from seller profile)
10. **Listing options** (in 3-dot or "See options" menus):
    - Bold title (fee)
    - Gallery Plus (fee)
    - Subtitle (fee)
    - Auto relist
    - Private listing
