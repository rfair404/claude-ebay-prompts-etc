#!/usr/bin/env python3
"""Before/after review page for the printed-media crop fix.

BEFORE is downloaded from eBay, not read from disk. The local `listing/` files
were overwritten by the new render, and even where an old copy survives it is
only what we believe we sent. The live CDN image is what a buyer is looking at
right now, which is the only "before" worth approving against.

Frames are shown as two strips per shoot rather than paired one-to-one: the
crop fix changes framing and can change frame COUNT, so a positional pairing
would line up unrelated images and quietly misrepresent the change.

    python tools/media_review_card.py --shoots .media_shoots_final.txt
"""
from __future__ import annotations
import argparse, base64, io, json, re, sys, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
from PIL import Image                                             # noqa: E402
from ebay_client import load_credentials, get_user_access_token   # noqa: E402

SKU = re.compile(r'ebay_inventory_sku:\s*"?([0-9a-fA-F]{6,})')


def thumb(im: Image.Image, h: int = 150) -> str:
    im = im.convert("RGB")
    im.thumbnail((h * 3, h), Image.LANCZOS)
    b = io.BytesIO(); im.save(b, "JPEG", quality=72)
    return '<img src="data:image/jpeg;base64,%s">' % base64.b64encode(b.getvalue()).decode()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shoots", default=".media_shoots_final.txt")
    ap.add_argument("-o", "--out", default="media_crop_review.html")
    a = ap.parse_args()

    tok = get_user_access_token(load_credentials())
    shoots = [l.strip() for l in Path(a.shoots).read_text(encoding="utf-8").splitlines() if l.strip()]

    cards = []
    for n, sh in enumerate(shoots, 1):
        d = REPO / sh
        dm = d / "draft.md"
        sku = None
        if dm.exists():
            m = SKU.search(dm.read_text(encoding="utf-8", errors="replace"))
            sku = m.group(1) if m else None
        title, live_imgs, lid = sh, [], None
        if sku:
            try:
                req = urllib.request.Request(
                    f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
                    headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"})
                prod = (json.load(urllib.request.urlopen(req, timeout=40)).get("product") or {})
                title = prod.get("title") or sh
                for u in (prod.get("imageUrls") or []):
                    try:
                        live_imgs.append(Image.open(io.BytesIO(urllib.request.urlopen(u, timeout=50).read())))
                    except Exception:
                        pass
            except Exception:
                pass
        new_imgs = []
        for p in sorted((d / "listing").glob("*.jpg")) if (d / "listing").exists() else []:
            try:
                new_imgs.append(Image.open(p))
            except Exception:
                pass
        cards.append((sh, title, live_imgs, new_imgs))
        print(f"  [{n}/{len(shoots)}] {sh}  live={len(live_imgs)} new={len(new_imgs)}", flush=True)

    out = ['<title>Printed media — crop fix review</title>', '''<style>
body{font:14px/1.45 -apple-system,Segoe UI,sans-serif;background:#f6f6f4;color:#191919;margin:0;padding:24px}
h1{font-size:20px;margin:0 0 3px}.sub{color:#666;font-size:13px;margin-bottom:20px}
.card{background:#fff;border:1px solid #e4e4e0;border-radius:8px;margin-bottom:16px;overflow:hidden}
.hd{padding:9px 13px;border-bottom:1px solid #eee;display:flex;gap:10px;flex-wrap:wrap;align-items:baseline}
.sh{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:#555}
.ti{font-weight:600;font-size:13px}
.lane{padding:9px 13px;border-bottom:1px solid #f3f3f1}
.lab{font-size:10px;text-transform:uppercase;letter-spacing:.07em;color:#888;margin-bottom:5px}
.strip{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px}
img{height:150px;width:auto;display:block;border:1px solid #e8e8e4;border-radius:3px}
.before .lab{color:#a11}.after .lab{color:#161}
</style>''']
    out.append('<h1>Printed media &mdash; crop fix review</h1>')
    out.append(f'<p class="sub">{len(cards)} shoots. <b>BEFORE</b> is the live eBay image right now; '
               f'<b>AFTER</b> is the new render (crop off on every frame, preset asshot). '
               f'Nothing has been pushed.</p>')
    for sh, title, live, new in cards:
        out.append('<div class="card"><div class="hd">'
                   f'<span class="ti">{title[:78]}</span><span class="sh">{sh}</span></div>')
        out.append('<div class="lane before"><div class="lab">before &mdash; live on eBay '
                   f'({len(live)})</div><div class="strip">' + ''.join(thumb(i) for i in live) + '</div></div>')
        out.append('<div class="lane after"><div class="lab">after &mdash; new render '
                   f'({len(new)})</div><div class="strip">' + ''.join(thumb(i) for i in new) + '</div></div>')
        out.append('</div>')
    Path(a.out).write_text('\n'.join(out), encoding='utf-8')
    print('wrote', a.out, round(Path(a.out).stat().st_size/1e6, 1), 'MB')


if __name__ == "__main__":
    main()
