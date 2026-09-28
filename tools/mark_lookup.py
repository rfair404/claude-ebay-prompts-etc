#!/usr/bin/env python3
"""Query the maker's-mark index — by a photo of a stamp, or in words.

CLIP puts images and text in one embedding space, so the same index answers
both halves of the question:

    # "what does this stamp look like?"  — crop the stamp first, tight
    python tools/mark_lookup.py --image inventory/.../mark_crop.jpg

    # "an eagle inside an oval"          — no letters legible, describe it
    python tools/mark_lookup.py --text "eagle inside an oval cartouche"

    # plain string search over the captions OCR'd from the register
    python tools/mark_lookup.py --maker "kipling"

WHAT A HIT MEANS. Nothing on its own. Retrieval is a CANDIDATE FINDER — the
same rule the marble index carries. Top-K means "look here", and two separate
things still have to happen before a name goes anywhere near a listing:

  1. Confirm the reading against the source page (each row carries `page_img`,
     the full register leaf it was cut from — open it and read the caption
     yourself; OCR on engraved script is unreliable and rows say so via
     `caption_conf`).
  2. Run the MARKET TEST from kb/articles/makers-marks.md — eBay sold + active
     counts for the name. A mark buyers never search pays nothing, so it stays
     out of the title whatever the cosine score said.

COVERAGE, HONESTLY. The index is seeded from the 1896 Jewelers' Circular
register: strong on ANTIQUE marks, and it ends well before Trifari, Monet,
Napier, Michael Anthony, Milor and ArtCarved. A confident hit on a 19th-century
cut is not evidence about a mid-century piece. If the item is post-1930s,
expect this index to miss and say so rather than reaching for the nearest match.

    --k N        how many candidates (default 8)
    --min S      hide hits below this cosine score
    --json       machine-readable
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))

from vindex import VIndex, embed_images, embed_texts        # noqa: E402

INDEX_NAME = "jewelry_marks"


def calibrate(sims):
    """Turn raw cosine into something honest.

    CLIP cosines are not calibrated and they read far higher than intuition
    expects: on this index an IDENTICAL crop scores ~0.98, while a photo of a
    modern stamp that appears nowhere in an 1896 register still tops out around
    0.72 against a median of 0.57. Reporting 0.72 unqualified invites exactly
    the false attribution this whole index is supposed to prevent, so score the
    top hit against the query's OWN background distribution instead.
    """
    import numpy as np
    srt = np.sort(sims)[::-1]
    med = float(np.median(sims))
    sd = float(np.std(sims)) or 1e-6
    top, second = float(srt[0]), float(srt[1] if srt.size > 1 else srt[0])
    z = (top - med) / sd
    # The discriminating signal is the GAP to the runner-up, not the z-score.
    # z alone called a known-identical crop (cosine 0.987) "weak", because an
    # exact match also drags its neighbours up and fattens the distribution.
    # A real hit stands clear of second place; noise does not — the pendant
    # stamp scored 0.724 with 0.723 behind it.
    gap = top - second
    if top >= 0.85 and gap >= 0.05:
        verdict = "STRONG — worth verifying on the page"
    elif gap >= 0.02 or z >= 5.0:
        verdict = "WEAK — could be coincidence, verify before believing"
    else:
        verdict = "NO MATCH — the top hit is within noise of every other row"
    return {"top": top, "second": second, "gap": gap,
            "median": med, "sd": sd, "z": z, "verdict": verdict}


def _fmt(rows):
    out = []
    for score, m in rows:
        conf = m.get("caption_conf", "?")
        name = m.get("maker") or "(no caption)"
        flag = {"clean": "", "partial": "  [caption partial]",
                "garbled": "  [caption garbled — read the page]",
                "none": "  [no caption — read the page]"}.get(conf, "")
        out.append(f"  {score:.3f}  {name[:46]:<46}{flag}")
        out.append(f"         {m.get('section') or '(unlabelled)'} · leaf {m.get('leaf')} · "
                   f"crop {m.get('crop')}")
        out.append(f"         page: {m.get('page_img')}")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--image", help="path to a tight crop of the stamp")
    g.add_argument("--text", help="describe the mark in words")
    g.add_argument("--maker", help="substring search over the OCR'd captions")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--min", type=float, default=0.0, dest="floor")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    idx = VIndex(INDEX_NAME)
    state = idx.load_state({})
    idx.check_model(state)
    emb, meta = idx.load()

    cal = None
    if a.maker:
        q = a.maker.lower()
        hits = [(1.0, m) for m in meta
                if q in (m.get("maker", "") + " " + m.get("caption", "")).lower()]
        hits = hits[:a.k]
    else:
        if a.image:
            from PIL import Image
            qv = embed_images([Image.open(a.image).convert("RGB")])[0]
        else:
            qv = embed_texts([a.text])[0]
        import numpy as np
        sims = emb @ np.asarray(qv, dtype="float32")
        cal = calibrate(sims)
        order = np.argsort(-sims)[:a.k]
        hits = [(float(sims[i]), meta[i]) for i in order if sims[i] >= a.floor]

    if a.json:
        print(json.dumps({"calibration": cal,
                          "hits": [{"score": s, **m} for s, m in hits]}, indent=2))
        return 0

    if not hits:
        print("no candidates above the floor.")
        return 0

    if cal:
        print(f"top {cal['top']:.3f} · runner-up {cal['second']:.3f} "
              f"(gap {cal['gap']:.3f}) · median {cal['median']:.3f} · "
              f"{cal['z']:.1f} sd above background")
        print(f"VERDICT: {cal['verdict']}")
        if cal["verdict"].startswith("NO MATCH"):
            print("\nA raw cosine near 0.7 is NOT a match here — an identical crop scores")
            print("~0.98. Treat the rows below as background, not candidates.")
        print()
    print(f"{len(hits)} candidate(s) — a LEAD, not an attribution:\n")
    print(_fmt(hits))
    print("\nBefore any of these reaches a listing: open the page image and read the")
    print("caption yourself, then run the market test in kb/articles/makers-marks.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
