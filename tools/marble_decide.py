#!/usr/bin/env python3
"""marble_decide — a never-dead-end DECISION PACKET for an uncertain marble.

Per prompts/marble_decide.md. For a marble close-up it writes:
  <stem>_panel.jpg   — the marble beside the LABELED reference types in its family
                       ("which looks closest?" — the human's eye, not a CLIP score)
  <stem>_decide.md   — a provisional DIRECTION + the 2-3 highest-leverage in-hand
                       QUESTIONS (human-as-sensor) + the action for each answer.

Always outputs a direction. Never "re-shoot" as the answer.

  python tools/marble_decide.py <closeup.jpg> [--age estate-typical] [--label M1]
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from vindex import embed_images  # noqa: E402
from marble_classify import Classifier  # noqa: E402

REFS = ROOT / "inventory" / "_refs"

LANES = {
    "machine": ["Marble King", "Vitro Agate", "Akro Agate", "Peltier", "Master Marble",
                "Alley Agate", "Heaton/Bogard/JABO", "Other U.S.", "Christensen Agate", "Foreign"],
    "handmade": ["Core Swirl", "Swirl", "End of Day", "Lutz", "Clambroth/Indian",
                 "Sulphide", "Handmade", "Handmade (other)"],
    "transitional": ["Transitional", "M.F. Christensen", "Christensen Agate", "Master Marble", "Akro Agate"],
    "nonglass": ["Earthenware", "Stoneware", "China", "Agate And Other Mineral", "Other Non Glass Materials"],
}

QUESTIONS = {
    "machine": [
        ("Backlit base", "Hold it to a bright bulb/window — does light (a) pass through clearly, (b) glow but cloudy/milky, (c) stay solid/opaque?", "base type: swirl/cat's-eye vs opal vs patch/Indian"),
        ("Seams", "Where the pattern closes, how many lines — 1 or 2? Straight, or V/U-shaped?", "Master leans short-V; Akro/Pelt/MK/Vitro = 2 seams"),
        ("Surface", "Tilt under ONE light: smooth glossy, or a dimpled orange-peel skin? Any chips/pits?", "orange-peel = modern Vacor; + the grade"),
        ("Oxblood", "Any band that's a dense dark brick-red, stays dark even held to light, feels slightly raised/grainy?", "oxblood = real value-adder"),
        ("Colour edges", "Are the colour edges crisp and sharp, or soft and bleeding?", "crisp+intense = Christensen Agate (rare); soft = common"),
    ],
    "handmade": [
        ("Poles", "Look at the two ends: (a) two rough/grainy spots, (b) one, (c) smooth?", "two pontils = cane-cut handmade; one = single-gather"),
        ("Backlit core", "Held to light: a twisted core of fine threads? separate bands? a cloud of flecks?", "latticinio / divided / end-of-day"),
        ("Glitter", "Any GLITTERY gold/copper flecks (Lutz) or SILVERY flecks (mica)?", "Lutz/mica = premium handmade"),
        ("Figure", "A solid 3-D figure (animal/person) suspended inside?", "sulphide = pull"),
    ],
    "transitional": [
        ("Poles", "One end with a single rough/ground/melted/creased spot, the other machine-like?", "single-gather transitional (MFC/Navarre)"),
        ("Backlit base", "Transparent with a single colour streak (slag), or opaque?", "slag = MFC/transitional"),
        ("Oxblood", "Any dense dark brick-red (MFC is the oxblood origin)?", "oxblood/MFC value"),
    ],
    "nonglass": [
        ("Material", "Glass, or does a magnet grab it (steel), or is it clay/stone/china?", "non-glass carve-out"),
        ("Surface", "Glazed/painted (china/Bennington) or bare fired clay?", "china/stoneware vs common clay"),
    ],
}


def _safe(name):
    return "".join(c if c.isalnum() else "_" for c in name)


def load_ref(maker):
    p = REFS / f"{_safe(maker)}_0.jpg"
    if p.exists():
        from PIL import Image
        return Image.open(p).convert("RGB")
    return None


def panel(marble_im, lane_makers, out):
    from PIL import Image, ImageDraw
    cells = [("YOUR MARBLE", marble_im)] + [(m, load_ref(m)) for m in lane_makers]
    cell, cols, lab, pad = 200, 4, 26, 6
    rows = math.ceil(len(cells) / cols)
    W = cols * (cell + pad) + pad
    H = rows * (cell + lab + pad) + pad
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(cells):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad); y = pad + r * (cell + lab + pad)
        if im is not None:
            t = im.copy(); t.thumbnail((cell, cell))
            sheet.paste(t, (x + (cell - t.width) // 2, y + lab + (cell - t.height) // 2))
        else:
            d.rectangle([x, y + lab, x + cell, y + lab + cell], fill=(50, 30, 30))
        d.text((x + 3, y + 4), label, fill=(255, 215, 0))
    sheet.save(out, quality=90)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--age", default="estate-typical")
    ap.add_argument("--label", default=None, help="marble label (e.g. M1) for the packet")
    a = ap.parse_args()

    clf = Classifier()
    from marble_crop import detect_and_crop
    from PIL import Image
    for ref in a.images:
        crops = detect_and_crop(ref)
        mim = crops[0][1] if crops else Image.open(ref).convert("RGB")
        res = clf.predict(embed_images([mim])[0], age=a.age)
        fam = (res.get("families_adj") or res["families"])[0][0]
        lane = list(LANES.get(fam, LANES["machine"]))
        # always include the premium/upside types so the user can compare against
        # them too (the value-movers — esp. for dark/Indian/oxblood candidates)
        WATCH = ["Clambroth/Indian", "Lutz", "Sulphide", "End of Day", "M.F. Christensen"]
        lane += [w for w in WATCH if w not in lane]
        qs = QUESTIONS.get(fam, QUESTIONS["machine"])
        stem = Path(ref).stem
        label = a.label or stem
        ppath = Path(ref).with_name(stem + "_panel.jpg")
        panel(mim, lane, ppath)
        # provisional DIRECTION
        soft = res.get("family_soft")
        direction = ("D (split, value-moving) — provisional: vintage machine swirl/patch"
                     if soft else
                     f"B (family lot) — provisional: vintage {fam} family, common")
        out = Path(ref).with_name(stem + "_decide.md")
        lines = [f"# DECIDE — {label}  ({Path(ref).name})", "",
                 f"**Family (estate prior):** {fam}"
                 + ("  ⚠ handmade/transitional is a live runner-up" if soft else ""),
                 f"**Provisional DIRECTION {direction}.**  (we ACT on this now; the "
                 "questions below only sharpen it — no re-shoot needed)", "",
                 f"**Triangulate:** open `{ppath.name}` — which reference does yours "
                 "look most like? (or 'none'). That pick + the answers = the call.", "",
                 "**Answer the 2-3 that would change the call (human-as-sensor):**"]
        for name, q, resolves in qs:
            lines.append(f"- **{name}:** {q}")
            lines.append(f"    → resolves: {resolves}")
        lines += ["", "**Action map:**",
                  "- transparent base + crisp colour → named-swirl direction (pull/price).",
                  "- opaque/opal base + soft colour, no tell → **family lot** ($–$$). Default.",
                  "- orange-peel / bag-fresh → **filler** lot.",
                  "- oxblood / Lutz / figure / printed image → **pull** (premium).",
                  "", "_No answer needed to keep moving: act on the provisional direction; "
                  "the questions just let us upgrade or downgrade it._"]
        out.write_text("\n".join(lines), encoding="utf-8")
        print(f"{label}: family={fam} dir={direction.split(' ')[0]}  -> {out.name} + {ppath.name}")


if __name__ == "__main__":
    main()
