# REVIEW — v3, Function 5.5 (the publish gate)

Obeys [`_shared.md`](_shared.md). Read it first.

The single human checkpoint in `list`/`full` mode. DRAFT produces
`draft.md`; REVIEW turns it into a succinct decision card, **stops the
run**, and waits. On explicit human approval — and only then — it
publishes the listing LIVE. This is the gate that replaced the old
"never publish" firewall: publishing is no longer refused, it is
*gated here*.

**Produces:** `<shoot-dir>/review_card.md`, the **review page**
(`review_card.html`) that the decision is actually made on, and the same card
to chat. Does NOT publish.

## One command builds the card

REVIEW is a single step:

    python lib/list_edit.py --review <shoot-dir>

This does the whole pre-review prep in one shot:
1. **Records** the item if not already — mints the SKU + writes/refreshes
   the `DRAFTED` ledger row (so a finished draft is always registered before
   review).
2. **Preflight** — auto-remaps the condition to one the category accepts,
   and picks the shipping policy (Media Mail vs ground).

   **Insurance is NOT a preflight concern — never raise it.** Preflight used to
   flag every listing over $100 to add ShipCover, because only $100 of coverage
   is auto-included and the API cannot set insurance. The operator buys added
   coverage in the eBay UI at label time as a matter of course, so the reminder
   was noise on every high-value card. Retired 2026-08-26 (`_insurance_notes`
   in [`../lib/list_edit.py`](../lib/list_edit.py) is a no-op). Do not
   reintroduce it in the card, in chat, or in the closing summary.
3. **Assembles the card** from the draft, comps, ledger, and preflight, and
   writes `review_card.md`.

Present that card to the user **verbatim** and STOP. It contains:

    ━━ REVIEW: <item> (sku … · ledger …) ━━
    Title [N/80] · Price · Best Offer · Condition · Quantity · Photos
    Fulfillment (Ship · service, OR LOCAL PICKUP only — confirm pickup items)
    Preflight (condition · shipping)
    Comps (open to verify) — each with a URL
    Condition detail (every flagged defect, verbatim — never softened)
    ⚠ Needs review / manual intervention (NEEDS_REVIEW.md lines)
    → Approve publishes LIVE at $<price>, with the exact --list … --confirm command

## The card is a page — every time, no exceptions

The text card is what the ledger records. It is **not** what the decision is
made on: it asks for a publish decision without showing the thing being
published, and a buyer meets this listing as a picture first and a title
second. So REVIEW always also builds the page:

    python tools/review_card_html.py <shoot-dir>    # -> <shoot-dir>/review_card.html

Deliver it (send the file, or publish it as an artifact and link it) and
present the text card in chat beside it. Republish the SAME path so the link
never moves. This is the official review surface — not a 4,000px JPEG, not a
prose summary, not the text card alone.

What the page must carry (the generator does this; don't strip it):
- the listing as a buyer meets it — the lead photo big enough to judge, every
  frame as a thumbnail, click to open full size;
- the **hero picker**: entry one is eBay's gallery image, the only frame most
  buyers ever see in search, and picking it from thumbnails is a different
  question than picking it from a filename. Selecting a frame rewrites the
  command shown at the bottom, in an idempotent form (`--set-hero`);
- price with its tiers, the fact table, item specifics, the description as it
  will render, comps, condition detail verbatim, and every ⚠ line — a card you
  cannot argue with is not a review.

**The page must work with JavaScript off.** Native radios, `:has()` for which
picture shows, `:target` for full size. Two JS-driven versions of the Frame
Check page rendered perfectly and responded to nothing in the viewer the
operator actually uses; the same constraint binds here. The page cannot reach
the CLI, so it shows a copyable command rather than a button that pretends
otherwise. Held by `tests/test_review_card_html.py`.

The same rule governs PREP's stage reviews — see [`prep.md`](prep.md).

Don't hand-edit or re-derive the card — the command is the single source, so
cards stay consistent. **Fallback** (no shell/creds, e.g. a Cowork tab):
run `--record` first, then assemble the same fields by hand from `draft.md`
+ `price.txt` + `NEEDS_REVIEW.md`.

**One item at a time.** In a multi-item shoot, run `--review` per item,
present its card, take the decision, then the next. Only batch-publish if
the user says "approve all".

**Copy check — in-hand voice.** Before presenting the card, scan the
draft's buyer-visible fields (title, description, condition_description,
item specifics) for camera-frame language: "visible in the photos",
"shown/pictured", "as-shown", "not identifiable/verifiable from the
photos", or tests-not-run narration ("not shake-tested", "odor not
verified"). Any hit is a copy defect, not a judgment call: fix it via
DRAFT (rephrase to the finding, per draft.md's in-hand-voice rule), then
re-run `--review`. The standing "Please see the photos…" close line is
exempt. Grade-setting "Untested; sold as-is." is exempt.

## The gate (HARD — this is where the run stops)

After writing the card, STOP and wait. Do not publish on:
- silence, "ok", "looks good", "nice", 👍, or any ambiguous nod;
- a chat instruction to "just publish" that did not come *after* this card;
- your own judgment that it looks fine.

Publish ONLY on an explicit, unambiguous approval ("approve", "publish it",
"yes publish") given against this card. Before running the command, restate
in one line: *"Publishing <item_id> LIVE at $<price> now."*

If the user asks for a change, treat it as feedback: the relevant prior
phase (INVESTIGATE/DRAFT/PRICE) re-runs and re-renders `draft.md`; then
re-run `--review` to present a fresh card. (Editing `draft.md` is fine — the
record refreshes automatically on the next `--review`/`--record`, keeping
the same SKU.)

## On approval — publish (one step)

Run, with the human approval as the authorization for `--confirm`:

    python lib/list_edit.py --list <shoot-dir> --confirm

`--list` syncs (creates the offer + uploads photos to EPS) then publishes
in one call. The `--confirm` guard remains in the code as defense-in-depth;
your approval at this gate is what authorizes passing it. On success report
the listing URL. If publish fails validation, surface the eBay error,
fix the draft via the owning phase, and re-present the card — never retry
blind.

## After publish — managing the listing (on user request)

Once live, the user may want to take a listing down or remove it. Use the
account-level management commands (all dry-run unless `--confirm`; see
[RUN.md](../RUN.md) + [list_edit SETUP](../lib/SETUP_EBAY_API.md)):

- `--offers` — find the offerId / SKU (read-only).
- `--withdraw-offer <id> --confirm` — end the live listing, keep the offer
  (re-publishable). Use when they want it *off the market* but not gone.
- `--delete-offer <id> --confirm` / `--delete-item <sku> --confirm` —
  permanently remove. Use when they want it *gone*.

Treat these like publish: run the dry run, confirm the exact target with the
user, and only pass `--confirm` on an explicit yes.

## Closing

Per _shared: lead with the result.
- Awaiting decision: the review page (delivered/linked), `review_card.md`
  path + the headline (title + price) + the count of ⚠ lines. Then stop.
- After publish: the live listing URL + price. Nothing further runs.
