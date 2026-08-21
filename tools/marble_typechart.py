#!/usr/bin/env python3
"""Build a labeled marble TYPE-REFERENCE chart from the MCSA studio references.

The visual-triangulation half of the decide workflow (prompts/marble_decide.md):
show the user a grid of clean, LABELED reference marbles and ask "which does
yours look most like?" — the human's eye, not a CLIP score. Uses the MCSA index
(kb/index/marblecollecting) which is maker/type-labeled studio photography.

  python tools/marble_typechart.py                 # one rep per type, all 27
  python tools/marble_typechart.py --per 2          # 2 reps per type
  python tools/marble_typechart.py --only "Vitro Agate,Marble King,Akro Agate,Peltier"
  python tools/marble_typechart.py --out chart.jpg

Downloads are cached under inventory/_refs/ (gitignored).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
from vindex import download_pil  # noqa: E402

META = ROOT / "kb" / "index" / "marblecollecting" / "meta.jsonl"
CACHE = ROOT / "inventory" / "_refs"

# a sensible decision order: machine-made glass first, then handmade, then non-glass
ORDER = ["Marble King", "Vitro Agate", "Akro Agate", "Peltier", "Master Marble",
         "Alley Agate", "Heaton/Bogard/JABO", "Other U.S.", "Christensen Agate",
         "Machine-made", "Foreign", "Transitional", "M.F. Christensen",
         "Core Swirl", "Swirl", "End of Day", "Lutz", "Clambroth/Indian",
         "Sulphide", "Handmade", "Handmade (other)",
         "Agate And Other Mineral", "Earthenware", "Stoneware", "China",
         "Other Non Glass Materials", "Non Glass Marbles"]


def _safe(name):
    return "".join(c if c.isalnum() else "_" for c in name)


def cached(maker, idx, url):
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / f"{_safe(maker)}_{idx}.jpg"
    if p.exists():
        from PIL import Image
        return Image.open(p).convert("RGB")
    try:
        im = download_pil(url)
        im.save(p, quality=90)
        return im
    except Exception:
        return None


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--per", type=int, default=1, help="representatives per type")
    ap.add_argument("--only", default=None, help="comma list of type labels to include")
    ap.add_argument("--out", default=str(ROOT / "inventory" / "_refs" / "typechart.jpg"))
    a = ap.parse_args()

    if not META.exists():
        raise SystemExit(f"MCSA reference index not found ({META}) — build it first "
                         "(python lib/mcsa_index.py ...).")
    import collections
    by = collections.defaultdict(list)
    for line in META.open(encoding="utf-8"):
        line = line.strip()
        if line:
            r = json.loads(line)
            mk = r.get("maker")
            if mk:                       # skip rows with no maker label (would crash _safe(None))
                by[mk].append(r.get("img"))

    if a.only:
        labels = [x.strip() for x in a.only.split(",")]
    else:
        labels = [m for m in ORDER if m in by] + [m for m in by if m not in ORDER]

    cells = []
    for m in labels:
        for idx in range(min(a.per, len(by.get(m, [])))):
            im = cached(m, idx, by[m][idx])
            cells.append((m, im))
    if not cells:
        raise SystemExit("No reference images resolved.")

    from PIL import Image, ImageDraw
    cell, cols, lab, pad = 200, 5, 26, 6
    rows = math.ceil(len(cells) / cols)
    W = cols * (cell + pad) + pad
    H = rows * (cell + lab + pad) + pad
    sheet = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    got = 0
    for i, (label, im) in enumerate(cells):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad); y = pad + r * (cell + lab + pad)
        if im is not None:
            got += 1
            t = im.copy(); t.thumbnail((cell, cell))
            sheet.paste(t, (x + (cell - t.width) // 2, y + lab + (cell - t.height) // 2))
        else:
            d.rectangle([x, y + lab, x + cell, y + lab + cell], fill=(50, 30, 30))
            d.text((x + 10, y + lab + cell // 2), "dl fail", fill=(220, 180, 180))
        d.text((x + 3, y + 4), label, fill=(255, 215, 0))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(a.out, quality=90)
    print(f"type chart: {got}/{len(cells)} images -> {a.out}")


if __name__ == "__main__":
    main()
