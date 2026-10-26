---
name: yn
description: Walk Russell through every pending yes/no decision, one item at a time - the item's photo sheet first, then one interactive question with the recommendation first - and route each answer to its owner. Covers money, price, publish, ownership and channel questions only. Use when Russell types /yn, or asks to "clear my questions", "what's waiting on me" or "let's do the yes/nos".
---

# /yn: Russell's yes/no queue, one item at a time

Typing `/yn` is the ask, so start without confirming first.

## 1. Collect what's pending

Gather every open decision that is Russell's:

- open questions from this session;
- `NEEDS_REVIEW.md` lines (RUN.md, "NEEDS_REVIEW.md format") that name
  Russell or need his money, price or publish call;
- `client_ledger.csv` `state_note`s pending on Russell (list or return,
  merges);
- review cards waiting on a publish yes (`review_card.md` with no live
  listing, `reports/review_cards/`).

Keep only money, price, publish, ownership and channel questions. Send art
questions to `/ad` and process questions to the technical-director, and
answer those from their calls. If nothing is left, say so in one line.

## 2. Show, then ask, per question

For each question, in turn:

1. Build one sheet of 2-3 labelled frames with the price on it. Take the
   frames from the ledger `photos` field or the item folder, or the intake
   frame when ownership is the question:

       python tools/photo_sheet.py --title "<item name>" --price '<$ask or est>' \
           --dir <item folder> <frame>=<label> <frame>=<label>

   It prints the JPEG's path (a temp dir, never `inventory/`).
2. Send it with `SendUserFile` (display: render).
3. Call `AskUserQuestion` with that one question: the recommendation first,
   marked "(Recommended)", and a one-line consequence on every option.
4. Route the answer (step 3), then move to the next question.

Never batch questions into a text card. One photo, one question.

## 3. Route each answer

Hand each answer to its owner as soon as it comes in:

- ownership, list-or-return, merges, `client_ledger.csv` states: the
  inventory-manager (the ledger's writer);
- publish, revise, end, relist, channel: the listing-manager;
- a price or a re-price: the price-researcher.

Finish with three lines: how many were answered, what was handed to whom,
and anything still open.

## Rules

- Every item gets its name and its picture. An ID alone means nothing to
  Russell.
- Write in positive voice: say what is there.
- Publish only on an explicit yes to that item.
