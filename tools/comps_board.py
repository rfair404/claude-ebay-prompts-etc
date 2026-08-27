#!/usr/bin/env python3
"""Build a self-contained comps board: thumbnail + clickable listing + delivered price.

Thumbnails are downloaded, resized and inlined as data: URIs — the artifact/widget
CSP blocks every remote image host, so a remote <img src> renders broken.
Usage: python tools/comps_board.py <run.json> [<run2.json> ...] --out <file.html> --title "..."
"""
import argparse, base64, io, json, sys, urllib.request
from PIL import Image

def thumb_data_uri(url, px=150):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        raw = urllib.request.urlopen(req, timeout=20).read()
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((px, px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print(f"  thumb FAILED {url}: {e}", file=sys.stderr)
        return None

def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="eBay sold comps")
    ap.add_argument("--tag", action="append", default=[],
                    help="item_id=LABEL — badge on a comp (e.g. ceiling, closest)")
    ap.add_argument("--drop", action="append", default=[],
                    help="item_id=REASON — mark a comp excluded")
    args = ap.parse_args()

    tags = dict(t.split("=", 1) for t in args.tag)
    drops = dict(d.split("=", 1) for d in args.drop)

    seen, comps = set(), []
    for r in args.runs:
        for c in json.load(open(r, encoding="utf-8"))["comps"]:
            if c["item_id"] in seen:
                continue
            seen.add(c["item_id"])
            comps.append(c)
    comps.sort(key=lambda c: -(c.get("total_price") or 0))

    rows = []
    for c in comps:
        uri = thumb_data_uri(c.get("thumbnail") or "")
        img = (f'<img src="{uri}" alt="">' if uri
               else '<div class="noimg">no<br>image</div>')
        badge = ""
        if c["item_id"] in tags:
            badge = f'<span class="badge">{esc(tags[c["item_id"]])}</span>'
        if c["item_id"] in drops:
            badge += f'<span class="badge drop">excluded · {esc(drops[c["item_id"]])}</span>'
        bo = '<span class="bo">Best Offer accepted — soft ceiling</span>' if c.get("bo_accepted") else ""
        ship = c.get("shipping_cost")
        shiptxt = "free ship" if ship == 0 else f"+${ship:.2f} ship"
        cls = ' class="dropped"' if c["item_id"] in drops else ""
        rows.append(f"""<tr{cls}>
  <td class="th">{img}</td>
  <td class="ti"><a href="{esc(c['url'])}" target="_blank" rel="noopener">{esc(c['title'])}</a>
      <div class="meta">sold {esc(c.get('sold_date'))} · {esc(c.get('listing_type'))} ·
      seller {esc(c.get('seller_username'))} ({c.get('seller_feedback_score')})</div>{badge}{bo}</td>
  <td class="pr"><b>${c['total_price']:.2f}</b><div class="meta">${c['sold_price']:.2f} {shiptxt}</div></td>
</tr>""")

    html = f"""<!doctype html><meta charset="utf-8">
<title>{esc(args.title)}</title>
<style>
:root{{color-scheme:light dark}}
body{{font:15px/1.45 system-ui,sans-serif;margin:1.2rem;max-width:900px}}
h1{{font-size:1.15rem;margin:0 0 .2rem}}
p.sub{{margin:0 0 1rem;opacity:.7;font-size:.85rem}}
table{{border-collapse:collapse;width:100%}}
td{{border-top:1px solid #8884;padding:.55rem .5rem;vertical-align:top}}
.th{{width:110px}} .th img{{width:100px;height:auto;border-radius:4px;display:block}}
.noimg{{width:100px;height:75px;background:#8882;border-radius:4px;display:grid;
  place-items:center;font-size:.7rem;text-align:center;opacity:.7}}
.ti a{{font-weight:600;text-decoration:none}} .ti a:hover{{text-decoration:underline}}
.meta{{font-size:.78rem;opacity:.65;margin-top:.15rem}}
.pr{{text-align:right;white-space:nowrap;width:110px}}
.badge{{display:inline-block;margin-top:.3rem;margin-right:.3rem;padding:.1rem .45rem;
  border-radius:99px;background:#2b7;color:#fff;font-size:.72rem;font-weight:600}}
.badge.drop{{background:#c44}}
.bo{{display:block;font-size:.75rem;color:#c80;margin-top:.2rem}}
tr.dropped{{opacity:.45}}
</style>
<h1>{esc(args.title)}</h1>
<p class="sub">{len(comps)} sold comps · prices are DELIVERED (sold + shipping) · click any title to open the listing</p>
<table>{''.join(rows)}</table>
"""
    open(args.out, "w", encoding="utf-8").write(html)
    print(f"{args.out}  ({len(html)/1024:.0f} KB, {len(comps)} comps)")

if __name__ == "__main__":
    main()
