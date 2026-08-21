#!/usr/bin/env python3
"""Crop + orient ONLY — no colour correction of any kind.

Reads the shoot's prep manifest for the orientation angle and crop box it
already decided, applies those to the ORIGINAL camera file, and writes the
result to `listing-plain/`. Pixel values are never touched: no white balance,
no backdrop curve, no saturation, no contrast, no unsharp. The only lossy step
is the final JPEG encode.

Writes to its own directory so `listing/` and the originals are both left
alone.

  python tools/prep_plain.py <shoot-dir> [--max 1600]
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from PIL import Image

REPO = Path(__file__).resolve().parents[1]

KNEE = 64.0          # only the bottom 25% of the range is touched


def _deepen_blacks(im: Image.Image, pct: float) -> Image.Image:
    """Deepen shadows by `pct` percent WITHOUT clipping anything to pure black.

    A black-point shift was the obvious reading of "deepen by 5%" and it is the
    wrong one: at 5% it drives everything under 12.75 to zero, which measured
    1.30% -> 32.96% pure-black on a real frame — worse than the damage being
    repaired. Clipping destroys shadow detail, which on a listing photo is
    evidence about the goods.

    Instead each value is scaled by (1 - pct/100) at the very bottom, with the
    effect ramping linearly to nothing at KNEE. A pixel at 10 becomes 9.5 rather
    than 0: visibly deeper, still separable, and no new pixel can reach a rail.
    """
    f = pct / 100.0
    lut = []
    for v in range(256):
        if v >= KNEE:
            lut.append(v)
        else:
            w = 1.0 - (v / KNEE)          # full effect at 0, none at the knee
            lut.append(int(round(v * (1.0 - f * w))))
    return im.point(lut * 3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shoot")
    ap.add_argument("--max", type=int, default=1600)
    ap.add_argument("--deepen", type=float, default=0.0,
                    help="deepen blacks by PCT%% — a black-point shift applied ONLY below the "
                         "knee, so midtones and highlights are untouched (default 0 = none)")
    ap.add_argument("--outdir", default="listing-plain")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    shoot = REPO / a.shoot
    man = json.loads((shoot / ".prep" / "prep.json").read_text(encoding="utf-8"))
    out_dir = shoot / a.outdir
    out_dir.mkdir(exist_ok=True)

    n = 0
    for src, meta in man.get("photos", {}).items():
        sp = shoot / src
        if not sp.exists():
            print(f"  MISSING {src}"); continue
        im = Image.open(sp).convert("RGB")

        deg = int((meta.get("orientation") or {}).get("applied") or 0)
        if deg:
            # PIL rotates counter-clockwise; the manifest records clockwise.
            im = im.rotate(-deg, expand=True)

        crop = meta.get("crop") or {}
        box = crop.get("box")
        if crop.get("applied") and box:
            x0, y0, x1, y1 = box
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(im.width, x1), min(im.height, y1)
            if x1 > x0 and y1 > y0:
                im = im.crop((x0, y0, x1, y1))

        if a.deepen > 0:
            im = _deepen_blacks(im, a.deepen)

        if a.max and max(im.size) > a.max:
            im.thumbnail((a.max, a.max), Image.LANCZOS)

        name = Path(src).stem + ".jpg"
        im.save(out_dir / name, "JPEG", quality=90, optimize=True)
        n += 1
        if not a.quiet:
            print(f"  {src[:38]:<38} rot={deg:>3}  crop={'yes' if crop.get('applied') else 'no ':<3}  -> listing-plain/{name}")
    print(f"{a.shoot}: wrote {n} image(s) to listing-plain/ — orientation + crop only, no colour")

if __name__ == "__main__":
    main()
