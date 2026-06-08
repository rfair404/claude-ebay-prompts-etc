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
- **Never save until the save-state has settled and verified.** eBay syncs
  fields (especially the rich-text description) to its save-state on a
  debounce; saving too soon persists a draft with missing fields. Always
  run the settle-and-verify gate (workflow step 8) and the post-save
  reload check (step 10) before reporting success. This is the single most
  common cause of "missing fields" — treat it as a hard gate, not a nicety.

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

**Why the description goes missing (the #1 recurring failure) and the fix.**
The outer `textarea[aria-label="Description"]` is eBay's save-state mirror;
it is read-only to you and is written FROM the iframe editor by a
**debounced** sync (a few hundred ms after the editor blurs). "Save for
later" reads that mirror — so if you save before the debounce flushes, the
draft persists with an EMPTY description even though the editor showed
text. The `blur`+`focusout` dispatch starts the sync but does not finish
it. Therefore: after editing the description, **wait ~1.5–2s for the
debounce, then poll the outer textarea until `.value.length > 0`, and treat
that non-zero length as the gate — never save until it is met.** If it's
still 0 after the wait, re-run the execCommand+blur sequence and wait
again (up to 3×). This same debounce can affect item-specifics and Best
Offer; the settle-and-verify pass in the workflow covers them too.

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
3. **Description:** RTE execCommand+blur pattern, then **wait ~1.5–2s and
   poll until `textarea[aria-label="Description"]`.value.length > 0** (the
   debounced save-state mirror). Do not proceed until it's non-zero;
   re-run the sequence up to 3× if needed. (See "Why the description goes
   missing" above.)
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
7. **Photos:** see "Photo upload" below — use the first method that's
   available; never fake success.
8. **Settle + verify before save (MANDATORY — this is what stops missing
   fields).** Do NOT save on a timer or right after the last fill. Instead:
   a. **Blur the active field** (`document.activeElement.blur()`) and **wait
      ~1.5–2s** so every debounced React/eBay sync flushes to save-state.
   b. **Re-read the save-state via JS** and build a checklist of REQUIRED,
      persisted values: title (input value), price, quantity,
      condition-description, **description = outer `textarea[aria-label=
      "Description"]`.value.length > 0**, each filled item-specific shows
      its value, Best Offer reflects the gate, weight/dims. Confirm no
      variations modal.
   c. **Re-apply any field that didn't persist** (description → re-run the
      execCommand+blur+wait; others → re-run setVal/tag-select), then wait
      and re-read again. Loop up to 3×.
   d. **Hard gate:** if the description mirror is still empty, or any
      required field is still missing after 3 tries, **DO NOT SAVE** —
      report exactly which field failed. A saved draft with a blank
      description is the failure we are preventing; an unsaved draft you
      can retry is better than a silently-broken one.
9. **Save:** only after step 8's checklist is fully green, click **"Save
   for later"** (find by text; it may report width 0 in a sticky footer —
   `.click()` it anyway).
10. **Post-save persistence check (catch the debounce that slipped):**
    after landing on Seller Hub → Drafts, **reopen the draft**
    (`lstng?draftId=…&mode=AddItem`), wait for load, and re-read the
    description mirror + title + price. If the description came back empty,
    the debounce lost it on save — re-apply the description, re-run the
    settle gate (step 8), and Save again. Only report success once the
    reloaded draft shows a non-empty description.
11. Write the `draftId` back into draft.md `meta.ebay_offer_id` and
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

## Photo upload (methods, in priority order)

Findings from the 2026-06-07 live run. The eBay photo input is a hidden
`input[type=file]` (multi) plus a "Drag and drop files" zone. Use the
first method that works in the current environment:

1. **eBay Sell API → EPS (target, full-res, no UI).** The real fix. When
   `v2/lib/list_edit.py` is implemented, photos POST to eBay Picture
   Services and attach by URL — no sandbox, no dialog, full resolution,
   correct order. Prefer this once the dev key exists; it makes upload
   fully hands-off.

2. **`upload_image` injection (full-res, no API) — preferred interim.**
   If the user drags the shoot photos into the chat once, each becomes an
   in-session image with an `imageId`;
   `upload_image({imageId, ref:<file input>, filename})` injects them into
   the hidden file input at full resolution, in call order (first = hero).
   This is the most reliable local-disk method when the Sell API isn't
   available — it sidesteps both the read-tier wall and the file_upload
   sandbox.

3. **Native Windows file dialog — only if the browser is FULL tier.** Click
   "Upload from computer", then type the shoot-dir path + quoted filenames
   into the Windows **Open** dialog and click Open. **Confirmed blocked
   2026-06-07** when the browser is granted at computer-use **read** tier:
   Chrome (and its Open dialog) reject typing, and the Chrome MCP refuses
   the file-button click. Only viable if `list_granted_applications` shows
   the browser at `full` tier. Do not rely on it otherwise.

**Does NOT work — do not retry:** `file_upload` on arbitrary disk paths.
It accepts only files shared with the session; project paths, `~/Downloads`,
AND folders connected via `request_directory` were all REJECTED in testing.
`file_upload` is only viable for genuinely session-shared files.

**Tooling note:** granting computer-use over Chrome can flip the Chrome MCP
to read-only on the active tab ("Permission denied … on this domain"). If
that happens, the user re-clicks the Claude-in-Chrome extension on the tab
to restore write access. Prefer finishing all Chrome MCP field-fills BEFORE
requesting computer-use access.

If none are available this run, flag photos for manual drag-drop (DSC
order, first = hero) in the status report and continue — the rest of the
draft is still worth saving.

## Failure escapes (STOP and ask)

- "Sell similar" missing (Path A) → fall back to Path B, log it.
- eBay forces a similar-item pick with no "Continue without match" → STOP.
- Category-change or policy-violation prompt → STOP, report verbatim.
- Save gated behind a violation → report; do not work around it.

When `v2/lib/list_edit.py` gains the live Sell API, this prompt is
deprecated (the API path avoids the RTE quirks, the scroll/coordinate
fragility, and the photo-upload limitation via EPS).
