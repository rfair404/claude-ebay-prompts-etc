---
name: ad
description: Hand an art question to the art-director agent instead of asking Russell. Use whenever a run would otherwise put a visual call on Russell's card - any PREP decision (orientation, crop, colour look, hero frame, montage, listing photo order), a photo's look, an overlay, card, thumbnail or brand-look question. Russell does not answer these; the art director decides and the session acts on the call.
---

# /ad — art questions go to the art director

Russell doesn't answer art questions (his decision, 2026-10-22). The
`art-director` agent decides them, and its call is the sign-off. That covers
every operator decision in [`prompts/prep.md`](../../../prompts/prep.md):
orientation frames below the 95% bar, crop overrides, the colour look, the
hero and montage pick, and the listing photo order. It also covers the look of
anything else a buyer sees, such as overlays, show cards, thumbnails and
collateral.

Russell keeps money, prices, going live, permissions and merges. A question
that mixes the two is split: the AD takes the look, and Russell gets the rest
on his card.

## Hand it off

Dispatch the `art-director` agent (Agent tool, `run_in_background: true`)
with a brief it can act on cold:

- the item dir (absolute path) and one line on what the item is, and whether
  it's new or already live (a live item keeps its published look);
- the stage and the sheet to look at: `.prep/stage_N_*.jpg` or
  `.prep/review.html`, or `.prep/rotation_sheet.jpg` for orientation;
- every flag the pass raised, word for word (e.g. `no crop: no studio
  backdrop (luma 178)`), so it judges the exceptions and not the whole shoot;
- what to return: the decision per frame, with a one-line reason for any
  override, and nothing else.

The AD decides. It doesn't change PREP state. Run the commands yourself, in
the background as RUN.md requires:

    python -m lib.photo_prep.prep <dir> --crop NAME=off|on|padF ...
    python -m lib.photo_prep.prep <dir> --approve-stage orientation|crop|color
    python -m lib.photo_prep.prep <dir> --pick <preset>
    python -m lib.photo_prep.prep <dir> --apply

Keep going while it works. PRICE, INVESTIGATE and DRAFT don't wait on PREP.

## After

- Log the call in one `NEEDS_REVIEW.md` line, e.g. `[PREP] <item> — AD: all
  7 kept as shot, crisp, hero 117`, so the review card shows who decided.
- Once every PREP call for the item is applied, hand it to the listing
  manager with the five-line handoff (RUN.md). It repoints the draft and
  builds the review card, and the item joins the `/review` queue.
- The REVIEW card still goes to Russell, because publishing is his. Show the
  photos as decided. Don't reopen the AD's call as a question.
- If the AD can't decide without a new photo, that's a hands-on step. Put the
  reshoot on Russell's card as a shot list, not as a question.
