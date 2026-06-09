# Polo Ralph Lauren Lot — Agent Prompt

You are handling ~11 pieces of Polo Ralph Lauren ephemera (see `inventory.txt`). The user already has new titles in progress. Your job: comps, pricing strategy, draft listings.

**Core workflow rule: draft listings LOCALLY as `.md` files first, push to eBay only in the final stage.** Use `listing_template.md` in the project root as the skeleton (`cp listing_template.md polo_<slug>/listing_draft.md` per piece). This avoids eBay UI latency, lets the user review/edit before any eBay tab opens, and enables parallel drafting without browser overhead.

---

## Context

Existing single-item draft exists: `https://www.ebay.com/lstng?draftId=5166927295920&mode=AddItem` ("Vintage Polo Ralph Lauren Catalog Lookbook Menswear Fashion Brochure"). Decide whether to keep, split, or restructure once you assess the full lot.

Era estimate (per inventory): late 1980s–1990s. Sweet spot for collector demand.

## Pricing strategy — niche collections

**Step 1: classify each piece**
- HERO piece: rare, dated, identifiable era, photographer credit (Bruce Weber), or unusual subject (e.g. "On Safari," equestrian, Ralph himself on cover). List INDIVIDUALLY at high end.
- STANDARD piece: typical season catalog, identifiable but not rare. List individually mid-range OR bundle 2–3 similar.
- FILLER: small mailers, generic promo, duplicates. Bundle into a lot.

**Step 2: comp anchors (already pulled, 2026 sold prices)**

When re-running comps or comping new pieces, search **SOLD listings sorted HIGHEST PRICE first**:
`https://www.ebay.com/sch/i.html?_nkw=...&LH_Sold=1&LH_Complete=1&_sop=3`
The `_sop=3` parameter is "Price + Shipping: highest first" — surfaces the ceiling of actual sales so we anchor on the high end and push toward it.

- Polo RL "On Safari" — $300
- Polo RL Fall 1991 store catalog — $280
- Polo RL Spring Illustrated — $225
- Polo RL Fall 1977 vol. 2 — $214
- Polo RL Fall 1987 — $200
- Polo RL Fall 1986 brochure — $120
- Polo RL 1985 booklet — $110
- Polo RL Fall 1988 — $94
- Vintage RL Paint catalogs lot of 7 — $119
- Vintage Polo RL Store Design Binder (rare) — $350
- 29-book RRL Japan lookbook lot — $1,700 (proves lot demand for serious collectors)

**Step 3: niche-collection pricing rules**
1. Date-identifiable 80s/early-90s catalogs = $150–$280 individually
2. Equestrian / Safari / hero-subject covers = top tier, $200–$300
3. Generic / undated = $75–$150
4. Mailer envelopes alone = $25–$50
5. Folders/portfolios with embossed branding = $40–$100 (dealer/collector item)
6. Duplicates: list 1 active, keep duplicate as backup OR bundle with filler
7. Hardcover RL books (if confirmed) = $30–$150 depending on title; verify title first
8. Textile/fabric samples: only list if you confirm it's RL Home / branded — otherwise skip
9. **Free shipping on everything.** Weight rounded up to nearest lb, dims to nearest inch. Media Mail for catalogs. Ground Advantage for mailers/folders/textiles.
10. **Best Offer ON.** Niche items convert via negotiation. Set auto-decline at ~70% of asking.
11. **Lot premium:** a curated 4–6 piece "Vintage Polo RL Collector's Lot" can ask $400–$600 if it includes a hero piece + supporting catalogs. Lots > $700 only with a documented hero (Safari, dated 80s).
12. **Don't undersell duplicates.** A duplicate of a $200 catalog = a second $200 sale, not a free throw-in.

**Step 4: shoot list per piece**
Use existing `photo_shot_list.txt` standard order. For Polo specifically also capture: copyright/date page, photographer credit page, season header, Polo Player logo close-up.

## Output deliverables

1. Updated `pricing_analysis.txt` — per-piece HI/LOW/Rec with reasoning
2. Decision per piece: list individually, bundle, or skip
3. Draft listings in eBay (one per individual + one per bundle)
4. Updated `photo_shot_list.txt` reflecting per-piece needs
5. Final summary: total recommended ask across the lot

## Hard rules

- NEVER invent dates, photographers, seasons, page counts. If not visible/known, omit from listing.
- Ask the user before any irreversible action (publish, accept terms, payment).
- Draft only. Never submit listings.
- Don't touch the other 5 drafts (RRL, Britches, Gilhe's, Joseph Abboud, Tommy Hilfiger) — those are finalized awaiting photos.

## Workflow conventions

- Use `browser_batch` for parallel tab work
- Use `find` tool per tab (refs don't carry across tabs)
- Mark tasks in_progress / completed as you go
- Drafts auto-save server-side — safe to nav tabs away
- Bot challenges (`splashui/challenge`) on rapid eBay searches — wait + retry

## Photo upload — confirmed working method

**Use Windows-style paths with the `file_upload` tool.** Linux-style paths (`/sessions/...`) are rejected. The correct format is:

```
C:\\Users\\Reuseum\\Documents\\Claude\\Projects\\ebaybiz\\PRL-batches\\polo-RL-cats\\<lot>\\edited_v1\\<filename>.jpg
```

Upload one file at a time — eBay's input accepts sequential single-file uploads and accumulates them. All 9 photos for a lot take ~9 calls but work reliably. Do NOT attempt: Linux paths, base64 JS injection, localhost HTTP server (blocked by eBay CSP), or `present_files` alone without the Windows path follow-up.

---

## eBay form quirks (catalog listings)

These are confirmed behaviors — do not deviate without testing:

**Condition** — set at the prelist "Confirm details" dialog (radio button), NOT on the main listing form. Once past that screen it cannot be changed via the form.

**Condition description vs. Description** — two separate textareas exist:
- First unlabeled `<textarea>` = condition description field
- `aria-label="Description"` textarea = description (React state only)
- Description also has an RTE iframe (`id="se-rte-frame__summary"`) — BOTH the outer textarea AND the iframe's `[contenteditable="true"]` div must be set or the description won't persist on save.

**Title field** — find by `maxLength === 80`, not by aria-label.

**Price field** — find by `aria-label` containing `"price"` (eBay labels it "Item price"), not by placeholder.

**Best Offer auto-decline** — set at ~70% of asking (e.g. $179 → $125, $89 → $62, $249 → $174).

**Shipping service** — eBay defaults to USPS Ground Advantage. Catalogs/books must be changed to USPS Media Mail: three-dot "More options" → "Change service" → search "Media Mail" → select → Done → then check "Offer free shipping."

**Publisher / Publication Year item specifics** — these are not standard text inputs:
- Publication Year: click the "Publication Year" button → type year → click the matching `menuitemradio`
- Publisher: click "Publisher" button → click the inner search textbox (`aria-label="Search or enter your own..."`) → type value

**React native value setter** — standard `.value =` assignment doesn't trigger React's state. Always use:
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

**Save vs. publish** — always click "Save for later." Never click "List it." Let the user publish.
