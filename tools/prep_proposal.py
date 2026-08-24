"""The auto first pass, as one page: what PREP decided before anyone was asked.

`--auto` makes two decisions per frame — which way is up, and where the crop
lands — and the operator's whole job at that point is to look once and say yes
or no. That is a picture question, so this builds the picture: every frame at
the rotation it will ship, with the proposed crop drawn ON it, the discarded
margin dimmed, and the frames whose rotation was GUESSED marked as guessed.

Two outputs, same data:

    --html   a standalone page (embedded JPEG data URIs), for publishing
    --widget the same markup with no <style> scoping assumptions, for pasting
             into a chat widget — thumbnails deliberately small, because the
             detailed look is the card that follows

Drawing the box rather than showing the cropped result is the point: the
question is "was the right thing removed", and only the before-with-box answers
it. See the locked template in prompts/prep.md.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lib.photo_prep.prep import _load_bgr, load_manifest, prepared   # noqa: E402


def _thumb_uri(bgr: np.ndarray, long_side: int, quality: int) -> str:
    h, w = bgr.shape[:2]
    s = long_side / max(h, w)
    if s < 1:
        bgr = cv2.resize(bgr, (max(1, int(w * s)), max(1, int(h * s))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise SystemExit("could not encode a thumbnail")
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def _with_box(img: np.ndarray, box) -> np.ndarray:
    """The frame with the crop drawn on it, everything outside it dimmed.

    Dimming rather than cropping keeps both halves of the question in one
    picture: what ships, and what it cost.
    """
    if not box:
        return img
    x0, y0, x1, y1 = [int(v) for v in box]
    out = (img * 0.34).astype(np.uint8)
    out[y0:y1, x0:x1] = img[y0:y1, x0:x1]
    cv2.rectangle(out, (x0, y0), (x1 - 1, y1 - 1), (120, 235, 110),
                  max(4, int(0.005 * max(img.shape[:2]))))
    return out


def frames(shoot: Path, long_side: int, quality: int) -> list:
    m = load_manifest(shoot)
    if not m.get("photos"):
        raise SystemExit(f"{shoot.name}: nothing in the manifest — run --auto first")
    out = []
    for name, rec in (m.get("photos") or {}).items():
        if not (shoot / name).exists():
            continue
        o = rec.get("orientation") or {}
        crop = rec.get("crop") or {}
        img = prepared(shoot, name, rec)
        H, W = img.shape[:2]
        box = crop.get("box") if crop.get("applied") else None
        kept = (((box[2] - box[0]) * (box[3] - box[1])) / (W * H)) if box else 1.0
        out.append({
            "name": name,
            "img": _thumb_uri(_with_box(img, box), long_side, quality),
            "rot": int(o.get("applied") or 0),
            "guessed": bool(o.get("guessed")),
            "source": o.get("source") or "?",
            "crop": bool(box),
            "kept": round(kept * 100),
            "why": (crop.get("reason") or "").strip(),
        })
    return out, m


CSS = """
.pp{font:13px/1.5 var(--font-sans,ui-sans-serif,system-ui,sans-serif);color:var(--text-primary,#111)}
.pp .grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}
.pp .card{background:var(--surface-1,#f4f3ef);border-radius:12px;overflow:hidden}
.pp .card img{display:block;width:100%;height:auto}
.pp .meta{padding:8px 10px}
.pp .nm{font-weight:500;font-size:12px;word-break:break-all}
.pp .ln{color:var(--text-secondary,#555);font-size:11.5px;margin-top:3px}
.pp .ok{color:var(--text-success,#1baf7a)}.pp .warn{color:var(--text-warning,#c98500)}
.pp .hd{margin:0 0 12px;color:var(--text-secondary,#555)}
"""


def render(shoot: Path, rows: list, m: dict, standalone: bool) -> str:
    guessed = sum(1 for r in rows if r["guessed"])
    cropped = sum(1 for r in rows if r["crop"])
    cards = []
    for r in rows:
        rot = f"turned {r['rot']}°" if r["rot"] else "already upright"
        rot_cls = "warn" if r["guessed"] else "ok"
        if r["guessed"]:
            rot += " · GUESSED"
        if r["crop"]:
            crop_line = f"<span class='ok'>crop keeps {r['kept']}% of the frame</span>"
        else:
            crop_line = f"<span class='warn'>no crop</span> — {r['why'] or 'no reason recorded'}"
        cards.append(
            f"<div class='card'><img src=\"{r['img']}\" alt=\"{r['name']}\">"
            f"<div class='meta'><div class='nm'>{r['name']}</div>"
            f"<div class='ln {rot_cls}'>{rot}</div>"
            f"<div class='ln'>{crop_line}</div></div></div>")
    head = (f"<p class='hd'><b>{shoot.name}</b> — auto first pass: {len(rows)} frames, "
            f"{cropped} cropped, {guessed} orientation guessed. Nothing is approved. "
            f"Green box = what ships; dimmed = what the crop drops.</p>")
    body = f"<div class='pp'>{head}<div class='grid'>{''.join(cards)}</div></div>"
    if not standalone:
        return f"<style>{CSS}</style>{body}"
    return (f"<title>{shoot.name} — auto first pass</title><style>"
            f"body{{background:#111;margin:0;padding:18px}}{CSS}</style>{body}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("shoot_dir")
    ap.add_argument("--out", help="write the markup here (default: .prep/proposal.html)")
    ap.add_argument("--widget", action="store_true",
                    help="fragment for a chat widget rather than a standalone page")
    ap.add_argument("--long", type=int, default=340, help="thumbnail long side in px")
    ap.add_argument("--quality", type=int, default=62, help="thumbnail JPEG quality")
    ap.add_argument("--json", action="store_true", help="print the decisions, no markup")
    a = ap.parse_args(argv)

    shoot = Path(a.shoot_dir)
    rows, m = frames(shoot, a.long, a.quality)
    if a.json:
        print(json.dumps([{k: v for k, v in r.items() if k != "img"} for r in rows],
                         indent=2))
        return 0
    out = Path(a.out) if a.out else shoot / ".prep" / (
        "proposal_widget.html" if a.widget else "proposal.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(shoot, rows, m, standalone=not a.widget), encoding="utf-8")
    print(f"{out}   {out.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
