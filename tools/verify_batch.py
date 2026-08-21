#!/usr/bin/env python3
"""Visual maker discrimination for a whole shoot — head-to-head or matrix.

The batch form of `marble_index.py verify`: loads the CLIP model + forum index
ONCE and scores every (marble crop x candidate maker) cell, matching the maker
keyword on the EXPERT ANSWER field (forum-read-replies — never the OP's guess).

Two readouts:
  --head-to-head (default)  Rank the candidate makers AGAINST EACH OTHER for
        each marble and report the winner + margin over the runner-up. This is
        the mode that works inside a visually-homogeneous family (WV swirls),
        where "is maker X near the GLOBAL best look-alike" saturates (every
        maker has some near-twin) but "is X distinctively closer than Y" still
        separates.
  --matrix                  The raw per-maker similarity grid (diagnostic).

Robustness: a maker's affinity is the MEAN of its top-K most-similar threads
(default K=5), not its single best — so one lucky near-duplicate can't crown a
maker. Makers with thin support (< K matching threads) are flagged.

Honest read only: the winner is a CANDIDATE to look at, not an ID. A small
margin = genuine split; show the user and decide (forum-match-policy).

Usage:
  python tools/verify_batch.py <crop>... --makers alley jabo vitro akro [...]
  python tools/verify_batch.py <crop>... --makers ... --matrix
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from vindex import embed_images  # noqa: E402
from marble_index import (  # noqa: E402
    IDX, load_refs_aligned, _text_matcher, _row_blob, FIELD_SETS)


def _marble_name(crop: str) -> str:
    return Path(crop).stem.replace("DSC_", "").replace("_HDR", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("crops", nargs="+", help="our marble crop(s)")
    ap.add_argument("--makers", nargs="+", required=True,
                    help="candidate maker keywords; ranked against each other")
    ap.add_argument("--field", choices=("all", "question", "answer"),
                    default="answer")
    ap.add_argument("--topk", type=int, default=5,
                    help="affinity = mean of a maker's top-K most-similar threads "
                         "(robust to a single near-duplicate; default 5)")
    ap.add_argument("--sep", type=float, default=0.008,
                    help="margin (winner minus runner-up affinity) needed to call "
                         "a LEAN; 2x this calls a DISTINCT winner (default 0.008)")
    ap.add_argument("--matrix", action="store_true",
                    help="show the raw similarity grid instead of head-to-head")
    args = ap.parse_args()

    import numpy as np
    emb, meta = IDX.load()
    fields = FIELD_SETS[args.field]

    # One pass: row indices per maker (reused across all marbles).
    masks = {}
    for mk in args.makers:
        hit = _text_matcher([mk], any_=False, regex=False, case=False)
        masks[mk] = np.array(
            [i for i, m in enumerate(meta)
             if (args.field != "answer" or m.get("answer")) and hit(_row_blob(m, fields))],
            dtype=np.int64)

    # Load crops KEEPING name<->embedding alignment: a silently-dropped crop must
    # not shift later crops onto the wrong embedding row (see load_refs_aligned).
    loaded = load_refs_aligned(args.crops)
    if not loaded:
        raise SystemExit("No usable crop.")
    crop_names = [_marble_name(ref) for ref, _ in loaded]
    q = embed_images([im for _, im in loaded])        # [len(loaded), D], row-aligned

    if args.matrix:
        _print_matrix(emb, q, crop_names, masks, args)
        return

    # ---- head-to-head -------------------------------------------------------
    print(f"\nHead-to-head maker discrimination — keyword on '{args.field}' "
          f"(expert's call), affinity = mean of top-{args.topk} similar threads.")
    print("Makers ranked AGAINST EACH OTHER per marble; margin = winner - #2.\n")
    win_tally = {}        # maker -> times ranked #1
    top2_tally = {}       # maker -> times in the top-2 (batch-lean signal)
    for ci, name in enumerate(crop_names):
        sims = (emb @ q[ci:ci + 1].T).max(axis=1)     # [N]
        aff = {}
        for mk, rows in masks.items():
            if rows.size == 0:
                continue
            topv = np.sort(sims[rows])[::-1][:args.topk]
            aff[mk] = (float(topv.mean()), int(rows.size))   # thin support (< topk) shown with * below
        ranked = sorted(aff.items(), key=lambda kv: kv[1][0], reverse=True)
        if len(ranked) < 2:
            print(f"{name}:  only one candidate maker matched — nothing to compare.")
            continue
        (w_mk, (w_aff, w_n)), (r_mk, (r_aff, r_n)) = ranked[0], ranked[1]
        margin = w_aff - r_aff
        win_tally[w_mk] = win_tally.get(w_mk, 0) + 1
        for mk, _ in ranked[:2]:
            top2_tally[mk] = top2_tally.get(mk, 0) + 1
        if margin >= 2 * args.sep:
            verdict, mark = f"DISTINCT → {w_mk.upper()}", "✅"
        elif margin >= args.sep:
            verdict, mark = f"lean {w_mk}", "🟡"
        else:
            verdict, mark = f"SPLIT {w_mk}/{r_mk} (too close — show user)", "❓"
        spread = "  ".join(
            f"{mk}{'*' if n < args.topk else ''} {a:.3f}" for mk, (a, n) in ranked)
        print(f"{mark} {name:<12} {verdict:<34} margin {margin:+.3f}")
        print(f"     {spread}")
    # ---- batch aggregate: turn N individual splits into one lean -----------
    n = len(args.crops)
    if n > 1 and top2_tally:
        lean = sorted(top2_tally.items(), key=lambda kv: kv[1], reverse=True)
        wins = sorted(win_tally.items(), key=lambda kv: kv[1], reverse=True)
        top_mk, top_n = lean[0]
        win_str = ", ".join(f"{mk} {c}" for mk, c in wins)
        lean_str = ", ".join(f"{mk} {c}/{n}" for mk, c in lean[:4])
        print(f"\nBATCH LEAN (across {n} marbles):")
        print(f"  top-2 appearances: {lean_str}")
        print(f"  outright winners:  {win_str}")
        if top_n >= 0.6 * n:
            print(f"  ➜ The lot skews {top_mk.upper()} ({top_n}/{n} top-2). Even "
                  f"where individual marbles split, the family lean is consistent "
                  f"— use it for bucketing, not per-marble maker assertion.")
    print("\n(✅ distinct visual winner · 🟡 leans · ❓ genuine split — show the "
          "user. Candidate signal, NOT an ID; trust the expert ANSWER + "
          "specializations/marbles.md.)")


def _print_matrix(emb, q, crop_names, masks, args):
    """Raw per-maker best-thread similarity grid (diagnostic). Uses single-best
    sim, so it saturates within a homogeneous family — that's WHY head-to-head
    exists; keep this only for ad-hoc inspection."""
    makers = args.makers
    print(f"\nSimilarity matrix — best forum-thread cosine per maker "
          f"(keyword on '{args.field}'). NOTE: single-best, saturates in-family.")
    hdr = "marble".ljust(14) + "  glob   " + "  ".join(
        mk.rjust(max(6, len(mk))) for mk in makers)
    print(hdr + "\n" + "-" * len(hdr))
    for ci, name in enumerate(crop_names):
        sims = (emb @ q[ci:ci + 1].T).max(axis=1)
        gtop = float(sims.max())
        cells = {mk: (float(sims[rows].max()) if rows.size else float("nan"))
                 for mk, rows in masks.items()}
        best_mk = max((mk for mk in makers if cells[mk] == cells[mk]),
                      key=lambda mk: cells[mk], default=None)
        parts = []
        for mk in makers:
            v = cells[mk]
            cw = max(6, len(mk))
            parts.append(("  —".rjust(cw)) if v != v else
                         (f"{v:.3f}{'*' if mk == best_mk else ' '}").rjust(cw))
        print(name.ljust(14) + f"  {gtop:.3f}  " + "  ".join(parts))


if __name__ == "__main__":
    main()
