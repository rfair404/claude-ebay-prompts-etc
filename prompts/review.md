# REVIEW — v3, Function 5.5 (the publish gate)

Obeys [`_shared.md`](_shared.md). Read it first.

The single human checkpoint in `list`/`full` mode. DRAFT produces
`draft.md`; REVIEW turns it into a succinct decision card, **stops the
run**, and waits. On explicit human approval — and only then — it
publishes the listing LIVE. This is the gate that replaced the old
"never publish" firewall: publishing is no longer refused, it is
*gated here*.

**Reads:** `draft.md` (authoritative for the listing) · `price.txt`
(comp URLs + tiers) · `NEEDS_REVIEW.md` (deferred decisions for this item).
**Writes:** `<shoot-dir>/review_card.md` (the card, for the record) and
the same card to chat.

## The card (succinct — fits on a screen)

Lead with the headline line, then the sections. No preamble. Pull every
value from the files above; never re-derive or invent.

    ━━ REVIEW: <item_id> ━━
    Title:     "<title>"  [N/80]
    Price:     $<price> (<tier> tier)  ·  Best Offer: <on @ auto-decline $X | off>
    Condition: <grade> — <one-line summary>
    Quantity:  <n> (<unit_type>)   ·   Photos: <n> (hero: <file>)

    Comps (supporting the price):
      • $<price> — "<title>"  ·  <A/B/C>  ·  <url>
      • $<price> — "<title>"  ·  <A/B/C>  ·  <url>
      • (list the comps price.txt cited for the chosen tier; each with its URL)

    Condition detail:
      • <every defect the draft's Condition section flagged — verbatim, no minimizing>

    ⚠ Needs review / manual intervention:
      • <each NEEDS_REVIEW.md line for this item>
      • <each DRAFT meta.notes gap, substitution, or rephrase worth a human eye>
      • (write "None" if genuinely clean)

    → Approve publishes this LIVE on eBay at $<price>. Reply "approve" to
      publish, or tell me what to change.

### Rules for filling it
- **Comps:** take the URLs `price.txt` listed for the working/Recommended
  tier. A price with no supporting comp URL is itself a ⚠ line, not a
  silent omission.
- **Condition:** copy the draft's flagged defects exactly. The whole point
  of the gate is that the human sees them before money is on the line —
  never soften or drop one.
- **Needs review:** merge `NEEDS_REVIEW.md` entries for this item with any
  DRAFT-flagged gap/substitution/rephrase. If a *required* field is empty
  or a price has no comp, say so plainly and recommend resolving it before
  approval — but the human decides.
- **One item at a time.** In a multi-item shoot, present one card, take its
  decision, then the next. Only batch-publish if the user says "approve all".

## The gate (HARD — this is where the run stops)

After writing the card, STOP and wait. Do not publish on:
- silence, "ok", "looks good", "nice", 👍, or any ambiguous nod;
- a chat instruction to "just publish" that did not come *after* this card;
- your own judgment that it looks fine.

Publish ONLY on an explicit, unambiguous approval ("approve", "publish it",
"yes publish") given against this card. Before running the command, restate
in one line: *"Publishing <item_id> LIVE at $<price> now."*

If the user asks for a change, treat it as feedback: the relevant prior
phase (INVESTIGATE/DRAFT/PRICE) re-runs, DRAFT re-renders `draft.md`, and
REVIEW presents a fresh card. Never edit `draft.md` by hand here.

## On approval — publish (one step)

Run, with the human approval as the authorization for `--confirm`:

    python lib/list_edit.py --list <shoot-dir> --confirm

`--list` syncs (creates the offer + uploads photos to EPS) then publishes
in one call. The `--confirm` guard remains in the code as defense-in-depth;
your approval at this gate is what authorizes passing it. On success report
the listing URL. If publish fails validation, surface the eBay error,
fix the draft via the owning phase, and re-present the card — never retry
blind.

## Closing

Per _shared: lead with the result.
- Awaiting decision: `review_card.md` path + the headline (title + price)
  + the count of ⚠ lines. Then stop.
- After publish: the live listing URL + price. Nothing further runs.
