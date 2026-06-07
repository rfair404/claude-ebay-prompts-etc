# LIST / EDIT (Chrome stand-in) — v3, Function 6 — INTERIM

Obeys [`_shared.md`](_shared.md). Read it first.

Pushes an approved `draft.md` into an eBay **DRAFT** listing by driving the
seller UI with the Claude-in-Chrome MCP. Interim until the eBay Sell API
path is built (`v2/lib/list_edit.py`). Same inputs, same firewall, same
output as the API path will have — only the mechanism differs.

**Not part of the automated run.** RUN.md's pipeline ends at DRAFT.
Function 6 runs ONLY on explicit user instruction ("push to eBay
draft"), one item at a time.

================================================================
HARD FIREWALL — NEVER PUBLISH
================================================================
Terminal action is **"Save for later" / "Save as draft"** — nothing
else. Forbidden, no matter who asks: "List it", "Publish", "Sell now",
"Submit listing", or any control that makes the listing live. If asked
to publish, refuse. Publication is the user's manual action in Seller
Hub. (Same firewall as `_shared.md`.)

## Preconditions (STOP if unmet)

1. `<shoot-dir>/draft.md` exists and passed DRAFT validation — required
   fields present (`title`, `quantity`, `price`, `item_specifics.type`),
   all caps satisfied. If `meta.notes` flags required-field gaps, STOP.
2. The user is signed into eBay in the connected Chrome browser. (Loading
   `https://www.ebay.com/sl/prelist/suggest` without a login redirect
   confirms it.)
3. `list_connected_browsers` returns a browser; `select_browser` it.

## Reliability rules (learned the hard way — follow these)

This eBay form is dynamic and auto-scrolls. The defaults below prevent
the failure modes from the first live run.

- **JS DOM state is ground truth; screenshots lag and mislead.** Verify
  every field with `javascript_tool` reads, not screenshots. A screenshot
  may show a modal that JS confirms is closed — trust JS.
- **Click via JS label-matching, not pixel coordinates.** Coordinate
  clicks land wrong after the form re-scrolls. Find elements by
  `aria-label` / `textContent` and call `.click()` / set value in JS.
  Reserve `find`+ref clicks for when JS matching is ambiguous.
- **Never create variations.** A stray click can open eBay's "Create
  your variations" editor and add Color/MPN options. This item is a
  single listing. If that editor appears, Cancel → confirm Yes, and
  verify `document.body.textContent` no longer contains "Create your
  variations" before continuing.
- **Set plain text/number fields with the React value-setter; set the
  description with the iframe execCommand+blur pattern** (both below).
- **Verify after every fill** via JS before moving on.

### JS patterns

React value-setter (inputs/textareas):
```js
const setVal=(el,val)=>{const p=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
Object.getOwnPropertyDescriptor(p,'value').set.call(el,val);
el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));};
```
Description RTE (iframe `se-rte-frame__summary`) — `innerHTML`/`setVal`
do NOT persist; only execCommand+blur survives a save:
```js
const f=document.getElementById('se-rte-frame__summary');
const d=f.contentDocument; const ed=d.querySelector('[contenteditable="true"]');
f.contentWindow.focus(); ed.focus(); d.execCommand('selectAll',false,null);
d.execCommand('insertHTML',false, HTML);          // <p>…</p>, <ul><li>…</li></ul>
ed.dispatchEvent(new FocusEvent('blur',{bubbles:true}));
ed.dispatchEvent(new FocusEvent('focusout',{bubbles:true}));
// verify: document.querySelector('textarea[aria-label="Description"]').value.length > 0
```

## Path selection (from price.txt)

- **Path A — Sell similar:** if price.txt has a Tier A **exact-match**
  sold comp with a URL, navigate there and click **"Sell similar"** /
  "Sell one like this" (NOT "Find similar"). Cloned category + aspect
  set are correct by definition. Preserve the cloned title (flag if it
  differs from draft); overwrite other fields from draft; delete cloned
  photos before uploading ours.
- **Path B — no exact match (common):** go to
  `https://www.ebay.com/sl/prelist/suggest`, type a short descriptor
  (not the full title), let eBay resolve a category, and proceed past
  any similar-items grid via **"Continue without match"** — never click
  an item card. Fill the title from draft. (The live hen run was Path B.)

## Workflow

1. **Land on the form** (Path A or B). eBay may show a **Confirm details**
   condition dialog (New / New other / Used) — pick per the draft's
   `condition` (USED_* → "Used"), Continue. Record the `draftId` from the
   URL (eBay auto-saves server-side).
2. **Core fields (JS, one batch):** title (input `maxLength===80` —
   Path A: don't overwrite, flag discrepancy), `aria-label="Item price"`,
   quantity (`maxLength===5`), condition-description (textarea
   `maxLength===1000`), weight `aria-label="Enter weight in pounds"` /
   `"…ounces"`, dims `"Enter package length/width/depth in inches"`.
3. **Description:** RTE execCommand+blur pattern; verify outer textarea
   length > 0.
4. **Item specifics:** eBay shows AI-suggested specifics with an
   **"Apply all"**. Prefer it, THEN correct anything wrong — the
   suggester makes category-plausible but item-wrong guesses (the hen run
   got "Subject: Rooster"; cleared it — it's a poule). Per `_shared`
   honesty, never leave an inaccurate specific. Required aspects not
   suggested (e.g. **Brand**): find the aspect's "Search or enter your
   own" textbox, `form_input` the canonical value, dispatch Enter to
   commit. Skip aspects the draft leaves empty.
5. **Best Offer (gate per draft.md):** Best Offer controls sit behind a
   pricing expander — expand it. If `best_offer.enabled` is true, check
   "Allow offers" and set auto-decline to `best_offer.auto_decline_amount`
   (= the Recommended tier). Leave auto-accept empty. If
   `best_offer.enabled` is false (list price ≤ Recommended), leave offers
   off.
6. **Shipping:** confirm/set free shipping + `primary_service` (three-dot
   → Change service → search → select → Done). Weight/dims already set.
7. **Photos:** upload from draft `photos:` via `file_upload`.
   **Known limitation:** `file_upload` accepts only files shared with the
   session, so arbitrary `<shoot-dir>` paths are REJECTED. If blocked, do
   NOT fake it — flag photos for manual drag-drop (DSC order, first =
   hero) in the status report. (The Sell-API path will fix this via EPS.)
8. **Pre-save verification (JS):** read back title, price,
   condition-description, description length, weight/dims; confirm no
   variations modal. Re-apply once if a value is wrong; if still wrong,
   report it — never save a silently-broken field.
9. **Save:** click **"Save for later"** (find by text; it may report
   width 0 in a sticky footer — `.click()` it anyway). Confirm you land
   on Seller Hub → Drafts and the item shows as a single-qty BIN draft.
10. Write the `draftId` back into draft.md `meta.ebay_offer_id` and
    `meta.last_synced`, and append a `[LIST/EDIT]` line to NEEDS_REVIEW
    with the draftId + every manual-completion item.

## Status report (back to the user)

- Path taken (A/B) + draftId + "Saved as draft; not published."
- Title in the saved draft + char count (Path A: discrepancy block if
  the cloned and draft titles differ).
- Fields set & verified (price, condition, description, weight/dims,
  specifics, Best Offer).
- **Manual completion needed** (bullets) — photos if upload was blocked,
  Best Offer if the expander wasn't reachable, any aspect that didn't
  apply, any field that failed verification.
- Next step: "Open the draft in Seller Hub, finish the flagged items,
  review, and List when ready. This tool does not publish."

## Failure escapes (STOP and ask)

- "Sell similar" missing (Path A) → fall back to Path B, log it.
- eBay forces a similar-item pick with no "Continue without match" → STOP.
- Category-change or policy-violation prompt → STOP, report verbatim.
- Save gated behind a violation → report; do not work around it.

When `v2/lib/list_edit.py` gains the live Sell API, this prompt is
deprecated (the API path avoids the RTE quirks, the scroll/coordinate
fragility, and the photo-upload limitation via EPS).
