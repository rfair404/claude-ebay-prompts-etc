# LIST/EDIT Chrome stand-in — rationale, history, and evidence

Companion to [prompts/list_edit_chrome.md](../list_edit_chrome.md). Read
only when a rule is disputed or being changed.

## Status: interim fallback, not deprecated

The prompt was written before `lib/list_edit.py` had the live Sell API and
was expected to be deprecated once it did. The API path is now primary
(EPS photo upload, offer publish, live revision), but the stand-in stays
as the fallback for categories the API cannot publish (e.g. eBay Motors —
see the `[CHANNEL]` convention in NEEDS_REVIEW.md) and for runs where the
API credentials are unavailable.

## The trusted-input discovery (why the description went missing)

The #1 recurring failure was a saved draft with a blank description. Two
compounding root causes, found on the first live run (the hen listing):

1. **Untrusted events are ignored by the save-model.** The RTE commits to
   eBay's save-state only on `isTrusted === true` input. JS value-setters,
   `innerHTML`, dispatched Events and `execCommand` all update what the
   editor *shows* but never reach the save-state — the draft saves blank
   no matter how many times the fill is re-applied. Chrome MCP `computer`
   `type`/`key` actions dispatch real CDP keystrokes, which are trusted;
   that is the entire basis of the "type, don't inject" rule.
2. **Debounced sync.** Even trusted input flushes to the mirror textarea
   on a debounce (a few hundred ms after blur), so an immediate save races
   it — hence the settle-and-verify gate and the post-save reload check.

The same trusted-input rule is why item-specifics tag-selects need real
typing + Enter rather than a JS value set.

## The hen run (first live run, Path B)

- eBay's AI-suggested specifics made a category-plausible but item-wrong
  guess ("Subject: Rooster" on a poule) — the origin of the "Apply all,
  THEN correct" rule.
- The similar-items grid was passed via "Continue without match"; clicking
  an item card adopts its identity, hence the never-click rule.

## Photo upload findings (2026-06-07 live run)

- Native Windows Open dialog: blocked at computer-use **read** tier —
  Chrome and its dialog reject typing, and the Chrome MCP refuses the
  file-button click. Confirmed live, hence "FULL tier only".
- `file_upload` on arbitrary disk paths: project paths, `~/Downloads`,
  and folders connected via `request_directory` were all rejected in
  testing — it accepts only session-shared files.
- Granting computer-use over Chrome flipped the Chrome MCP to read-only
  on the active tab; the user re-clicking the extension restored it —
  hence "field-fills before computer-use access".
