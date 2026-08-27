# LIST / EDIT (Chrome stand-in) — v4, Function 6 — INTERIM

Obeys [`_shared.md`](_shared.md). Read it first.

Pushes an approved `draft.md` into an eBay **DRAFT** listing by driving the
seller UI with the Claude-in-Chrome MCP. **Fallback only** — the eBay Sell
API path (`lib/list_edit.py`) is primary; use this when the API path isn't
available or can't handle the category. Incident history + evidence:
[reference/list-edit-chrome-notes.md](reference/list-edit-chrome-notes.md).

**Listing management is API-only.** Querying, withdrawing, deleting
(`list_edit.py --offers` / `--withdraw-offer` / `--delete-offer` /
`--delete-item`) go through the Sell API — see [RUN.md](../RUN.md)
"Managing live listings".

**Reached via the REVIEW gate.** Runs only after a human approves the
review card, one item at a time.

================================================================
THIS STAND-IN IS DRAFT-ONLY (publish via the API path)
================================================================
Terminal action here is **"Save for later" / "Save as draft"** — nothing
else. The browser is granted read-tier OS automation and the publish
controls are unreliable to drive safely, so going LIVE is reserved for the
API path (`list_edit.py --list <dir> --confirm`, after REVIEW approval).
An approved card still stops at a saved draft here; hand off to the API
command for live. The firewall against *automatic* publishing holds (see
`_shared.md`).

## Preconditions (STOP if unmet)

1. `<shoot-dir>/draft.md` exists and passed DRAFT validation — required
   fields present (`title`, `quantity`, `price`, `item_specifics.type`),
   all caps satisfied. `meta.notes` flags required-field gaps → STOP.
2. The user is signed into eBay in the connected Chrome browser. (Loading
   `https://www.ebay.com/sl/prelist/suggest` without a login redirect
   confirms it.)
3. `list_connected_browsers` returns a browser; `select_browser` it.

## Reliability rules

The eBay form is dynamic and auto-scrolls; these defaults prevent the
known failure modes:

- **JS DOM state is ground truth; screenshots lag and mislead.** Verify
  every field with `javascript_tool` reads. A screenshot may show a modal
  JS confirms is closed — trust JS.
- **Click via JS label-matching, not pixel coordinates.** Coordinate
  clicks land wrong after a re-scroll. Find elements by `aria-label` /
  `textContent` and `.click()` / set value in JS. Reserve `find`+ref
  clicks for when JS matching is ambiguous.
- **Never create variations.** A stray click can open "Create your
  variations" and add Color/MPN options. If that editor appears: Cancel →
  confirm Yes → verify `document.body.textContent` no longer contains
  "Create your variations" before continuing.
- **Trusted input rule (the #1 failure class).** Plain
  `<input>`/`<textarea>` fields take the React value-setter below. But
  **controlled save-models ignore untrusted (JS) input**: the rich-text
  description and tag-select item-specifics commit ONLY on TRUSTED
  keystrokes (`isTrusted === true`). Enter those by **typing with the
  Chrome MCP `computer` `type`/`key`** (CDP keystrokes are trusted) — JS
  `setVal`, `innerHTML`, dispatched events and `execCommand` change what
  you SEE but never reach the save-state, so the draft saves blank no
  matter how often you re-apply.
- **Verify after every fill** via JS before moving on.
- **Never save until the save-state has settled and verified.** eBay syncs
  fields (especially the description) on a debounce; saving too soon
  persists a draft with missing fields. Always run the settle-and-verify
  gate (step 8) and the post-save reload check (step 10) — a hard gate,
  not a nicety.

### JS patterns

React value-setter (inputs/textareas):
```js
const setVal=(el,val)=>{const p=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
Object.getOwnPropertyDescriptor(p,'value').set.call(el,val);
el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));};
```

Description RTE: the iframe `se-rte-frame__summary` holds a
`[contenteditable]` editor; `textarea[aria-label="Description"]` is its
read-only save-state mirror. JS does ONLY focus + clear:
```js
const ed=document.getElementById('se-rte-frame__summary')
  .contentDocument.querySelector('[contenteditable="true"]');
ed.focus(); document.execCommand?.('selectAll');
```
Then, editor focused: `computer` `key` "Delete" (clear selection), then
`computer` `type` with the description **plain text**, paragraph breaks
via `key` "Enter" (rich formatting is lost; plain prose is what persists).
Blur, **wait ~1.5–2s** for the debounce, verify
`textarea[aria-label="Description"]`.value.length > 0; re-focus and
re-type up to 3×. `execCommand('insertHTML')` + dispatched blur is a
LAST-RESORT fallback only — its events are untrusted and the mirror
usually stays empty. Item-specifics tag-selects likewise need real typing
+ Enter (`form_input`/`computer`), not a JS value set.

## Path selection (from price.txt)

- **Path A — Sell similar:** price.txt has a Tier A **exact-match** sold
  comp with a URL → navigate there, click **"Sell similar"** / "Sell one
  like this" (NOT "Find similar"). Cloned category + aspect set are
  correct by definition. Preserve the cloned title (flag if it differs
  from draft); overwrite other fields from draft; delete cloned photos
  before uploading ours.
- **Path B — no exact match (common):** go to
  `https://www.ebay.com/sl/prelist/suggest`, type a short descriptor (not
  the full title), let eBay resolve a category, proceed past any
  similar-items grid via **"Continue without match"** — never click an
  item card. Fill the title from draft.

## Workflow

1. **Land on the form** (Path A or B). A **Confirm details** condition
   dialog (New / New other / Used) → pick per the draft's `condition`
   (USED_* → "Used"), Continue. Record the `draftId` from the URL (eBay
   auto-saves server-side).
2. **Core fields (JS, one batch):** title (input `maxLength===80` —
   Path A: don't overwrite, flag discrepancy), `aria-label="Item price"`,
   quantity (`maxLength===5`), condition-description (textarea
   `maxLength===1000`), weight `aria-label="Enter weight in pounds"` /
   `"…ounces"`, dims `"Enter package length/width/depth in inches"`.
3. **Description:** focus + clear via JS, then **TYPE the body with
   `computer` `type`/`key`** (trusted input). Wait ~1.5–2s, poll until the
   mirror textarea is non-empty; re-type up to 3×.
4. **Item specifics:** eBay shows AI-suggested specifics with an
   **"Apply all"**. Prefer it, THEN correct anything wrong — the suggester
   makes category-plausible but item-wrong guesses. Per `_shared` honesty,
   never leave an inaccurate specific. Required aspects not suggested
   (e.g. **Brand**): find the aspect's "Search or enter your own" textbox,
   `form_input` the canonical value, dispatch Enter. Skip aspects the
   draft leaves empty.
5. **Best Offer (gate per draft.md):** controls sit behind a pricing
   expander — expand it. `best_offer.enabled` true → check "Allow offers",
   set auto-decline to `best_offer.auto_decline_amount`, leave auto-accept
   empty. False → leave offers off.
6. **Shipping:** `fulfillment_mode` **LOCAL_PICKUP** → do NOT set a parcel
   service; choose eBay's **local pickup** option and leave the service
   empty (weight/dims may stay for reference; the description already
   carries the freight-by-quote line). Otherwise (**SHIP**): confirm/set
   free shipping + `primary_service` (three-dot → Change service → search
   → select → Done).
7. **Photos:** see "Photo upload" below — first available method; never
   fake success.
8. **Settle + verify before save (MANDATORY).** Never save on a timer or
   right after the last fill:
   a. **Blur the active field** (`document.activeElement.blur()`), **wait
      ~1.5–2s** so every debounced sync flushes.
   b. **Re-read the save-state via JS**: title, price, quantity,
      condition-description, **description mirror non-empty**, each filled
      item-specific shows its value, Best Offer reflects the gate,
      weight/dims. Confirm no variations modal.
   c. **Re-apply any field that didn't persist**, wait, re-read. Loop up
      to 3×.
   d. **Hard gate:** description mirror still empty, or any required field
      missing after 3 tries → **DO NOT SAVE** — report exactly which field
      failed. An unsaved draft you can retry beats a silently-broken one.
9. **Save:** only on a fully green checklist, click **"Save for later"**
   (find by text; it may report width 0 in a sticky footer — `.click()`
   anyway).
10. **Post-save persistence check:** reopen the draft from Seller Hub →
    Drafts (`lstng?draftId=…&mode=AddItem`), wait for load, re-read the
    description mirror + title + price. Description came back empty → the
    debounce lost it on save: re-apply, re-run step 8, Save again. Only
    report success once the RELOADED draft shows a non-empty description.
11. Write the `draftId` into draft.md `meta.ebay_offer_id` +
    `meta.last_synced`; append a `[LIST/EDIT]` line to NEEDS_REVIEW with
    the draftId + every manual-completion item.

## Status report (back to the user)

- Path taken (A/B) + draftId + "Saved as draft; not published."
- Title in the saved draft + char count (Path A: discrepancy block if
  cloned and draft titles differ).
- Fields set & verified (price, condition, description, weight/dims,
  specifics, Best Offer).
- **Manual completion needed** (bullets) — photos if upload was blocked,
  Best Offer if the expander wasn't reachable, any aspect that didn't
  apply, any field that failed verification.
- Next step: "Open the draft in Seller Hub, finish the flagged items,
  review, and List when ready. This tool does not publish."

## Photo upload (methods, in priority order)

The eBay photo input is a hidden `input[type=file]` (multi) plus a "Drag
and drop files" zone. Use the first method that works:

1. **eBay Sell API → EPS (primary, full-res, no UI).** Photos POST to
   eBay Picture Services and attach by URL — no sandbox, no dialog,
   correct order. Prefer whenever the API path is available.
2. **`upload_image` injection (full-res, no API) — preferred interim.**
   The user drags the shoot photos into chat once; each becomes an
   in-session image with an `imageId`;
   `upload_image({imageId, ref:<file input>, filename})` injects into the
   hidden file input at full resolution, in call order (first = hero).
3. **Native Windows file dialog — only if the browser is FULL tier.**
   Click "Upload from computer", type the path + quoted filenames into the
   **Open** dialog. Blocked at computer-use **read** tier (Chrome rejects
   typing and the MCP refuses the click) — only viable when
   `list_granted_applications` shows the browser at `full`.

**Does NOT work — do not retry:** `file_upload` on arbitrary disk paths
(project paths, `~/Downloads`, and `request_directory` folders all
rejected). Only viable for genuinely session-shared files.

**Tooling note:** granting computer-use over Chrome can flip the Chrome
MCP to read-only on the active tab ("Permission denied … on this
domain") — the user re-clicks the extension on the tab to restore write
access. Finish all Chrome MCP field-fills BEFORE requesting computer-use
access.

If no method is available this run, flag photos for manual drag-drop (DSC
order, first = hero) in the status report and continue — the rest of the
draft is still worth saving.

## Failure escapes (STOP and ask)

- "Sell similar" missing (Path A) → fall back to Path B, log it.
- eBay forces a similar-item pick with no "Continue without match" → STOP.
- Category-change or policy-violation prompt → STOP, report verbatim.
- Save gated behind a violation → report; do not work around it.
