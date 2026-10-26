---
name: cards
description: The listing manager steps Russell through the review card of every ready-to-list item, one at a time - the item's photo and card link first, then one publish yes/no - and publishes each yes. Items the art director has signed through PREP but that have no card yet are brought to review first. Use when Russell types /cards, or asks to "go through the review cards", "what's ready to list" or "let's approve listings".
---

# /cards: every ready-to-list card, one at a time

Typing `/cards` is the ask, so start without confirming first. You run the
listing manager's REVIEW desk (`.claude/agents/listing-manager.md`, RUN.md
"Item states and handoffs"). The listing manager owns REVIEW; you show the
cards in its place, because only this session can ask Russell a question.

## 1. Collect the queue

List every item with a finished draft that isn't live yet, sorted by how far
it has got. Run it from the main checkout: a worktree has no
`listings_ledger*.csv`, so there every live item reads as unlisted.

    cd /home/russ/EBAYBIZ && python - <<'EOF'
    import sys
    from pathlib import Path
    sys.path.insert(0, "lib")
    from status import gather
    for d in sorted({p.parent for p in Path("inventory").rglob("draft.md") if "/." not in str(p)}):
        s = gather(d)
        if s["listing_id"] or s["ledger_status"] not in (None, "DRAFTED", "SYNCED"):
            continue
        prepped = not s["stages"]["prep"]["pending"]
        card = d / "review_card.html"
        fresh = card.exists() and card.stat().st_mtime >= (d / "draft.md").stat().st_mtime
        kind = "CARD" if prepped and fresh else "PREPPED" if prepped else "PREP"
        print(f"{kind}\t{s['sku'] or '-'}\t{s['store']}\t{d}\t{s['next_action']}")
    EOF

- **CARD**: PREP signed and a current review card. These are the queue.
- **PREPPED**: the art director's PREP calls are applied but there's no
  current card. Dispatch one `listing-manager` agent
  (`run_in_background: true`) with the list: repoint the draft at
  `listing/`, run `list_edit.py --review` and `review_card_html.py` for each,
  ledger calls one at a time. Each item joins the queue when its card lands.
- **PREP**: still waiting on PREP sign-off. Count them for the closing lines.
  Don't start PREP from here.

If the queue and the PREPPED list are both empty, say so in one line.

A draft newer than its card on an item that is already live is a revision,
not a publish. It isn't in this queue.

## 2. Show, then ask, per item

Take the CARD items in order (then each PREPPED item as its card lands):

1. Read `review_card.md`. If it no longer passes (`--review` flags a
   blocker), send the item to its owner as below and move on.
2. Share the card as an R2 link. Copy `review_card.html` to
   `reports/review_cards/<today>/<n>-<plain-name>.html`, then from the main
   checkout, outside the sandbox:

       cd /home/russ/EBAYBIZ && .venv/bin/python -m lib.cli offsite share reports/review_cards/<today>/<file>

3. Send the hero (`listing/00_hero.jpg`, or the first `photos:` frame) with
   `SendUserFile` (display: render), captioned with the item's name, price,
   store and the R2 link.
4. Call `AskUserQuestion` with one question, "Publish <item> at $<price> on
   <store>?":
   - **Publish (Recommended)**: goes live at the card's price. Recommend it
     only when the card has no open ⚠; otherwise recommend Not yet.
   - **Not yet**: a second question picks the owner: price
     (price-researcher), photos (`/ad`), words (copywriter), hold it.
   - **Skip**: stays in the queue for next time.

   An open ⚠ line that needs Russell's money or publish call gets its own
   yes/no in the same `AskUserQuestion` call. Art ⚠s go to `/ad`, not to
   Russell.
5. Route the answer (step 3), then move to the next item.

Never batch items into one question, and never put the card text in chat.
One photo, one link, one question.

## 3. Route each answer

- **Publish**: add the item to the publish list. Hand the list to one
  `listing-manager` agent (`run_in_background: true`) as yeses come in; if
  one is already publishing, the next yeses wait for it. It runs
  `list_edit.py --list <shoot-dir> --confirm` from the main checkout, one
  item per yes, reads the live listing back against the card and returns the
  URL.
- **Not yet**: hand the item to the owner Russell picked with the five-line
  handoff (RUN.md). It stays in REVIEW with the listing manager.
- **Skip**: nothing.

Finish with three lines: published (with URLs as they land), sent back and
to whom, and what's still waiting (skipped, cards building, PREP count).

## Rules

- Publish only on an explicit Publish answer for that item. A yes covers
  that item, at that price, once.
- Every item gets its name and its picture. A SKU alone means nothing to
  Russell.
- Ledger writes (`--record`, `--review`, `--list --confirm`) belong to one
  listing-manager job at a time, never two in parallel.
- Write in positive voice: say what is there.
