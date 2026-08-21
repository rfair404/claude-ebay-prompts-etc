#!/usr/bin/env python3
"""Build an ORIGINAL vs LIVE-ON-EBAY contact sheet for one shoot.

The point is to judge what a buyer is actually looking at right now, so the
right-hand column is downloaded from eBay's CDN rather than read from listing/.
A local render can differ from what was pushed; only the live image is evidence.

  python tools/compare_live.py <shoot-dir> [out.jpg]
"""
from __future__ import annotations
import io, json, re, sys, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))
from ebay_client import load_credentials, get_user_access_token   # noqa: E402

def live_urls(sku: str) -> list[str]:
    tok = get_user_access_token(load_credentials())
    req = urllib.request.Request(
        f"https://api.ebay.com/sell/inventory/v1/inventory_item/{sku}",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=45))["product"].get("imageUrls", [])

def fetch(u: str) -> Image.Image:
    r = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    return Image.open(io.BytesIO(urllib.request.urlopen(r, timeout=40).read())).convert("RGB")

def main() -> None:
    shoot = REPO / sys.argv[1]
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else REPO / ".compare.jpg"
    d = json.loads((shoot / ".prep" / "prep.json").read_text(encoding="utf-8"))
    t = (shoot / "draft.md").read_text(encoding="utf-8", errors="replace")
    sku = re.search(r'ebay_inventory_sku:\s*"?([0-9a-f]{8})"?', t).group(1)

    srcs = [s for s, m in d.get("photos", {}).items() if m.get("output")]
    urls = live_urls(sku)
    n = min(len(srcs), len(urls), 6)
    W, ROW = 460, 470
    sheet = Image.new("RGB", (W * 2 + 30, ROW * n + 40), "white")
    dr = ImageDraw.Draw(sheet)
    dr.text((10, 12), f"{shoot.name}   sku {sku}   LEFT = your original   RIGHT = LIVE on eBay", fill="black")
    for i in range(n):
        y = 40 + i * ROW
        for col, im in ((0, Image.open(shoot / srcs[i]).convert("RGB")), (1, fetch(urls[i]))):
            im.thumbnail((W, ROW - 26))
            sheet.paste(im, (col * (W + 30) + (W - im.width) // 2, y))
        dr.text((10, y + ROW - 22), srcs[i][:44], fill="black")
    sheet.save(out, quality=88)
    print(f"wrote {out}  ({n} pairs)")

if __name__ == "__main__":
    main()
