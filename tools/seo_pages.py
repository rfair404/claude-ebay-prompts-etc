#!/usr/bin/env python3
"""Build standalone SEO landing pages for an eBay Ambassador collection.

Every page drives to the eBay listings through Ambassador/EPN-tracked links.
Copy comes from a content file we write (seo/collections/<slug>.json). Live
facts come from the Browse API at build time: price, photos, condition,
availability and seller. Re-run it to refresh prices. An item that has sold or
ended keeps its page, which then sends the visitor to a tracked eBay search for
similar pieces.

    python tools/seo_pages.py seo/collections/stunning-collection.json
    python tools/seo_pages.py <content.json> --base-url https://example.com/finds
    python tools/seo_pages.py <content.json> --offline   # reuse <out>/_data.json

Output (default seo/dist/<slug>/, gitignored):
    index.html                hub: every item, grouped by topic
    <topic-slug>/index.html   topic page
    <item-slug>/index.html    one page per item
    sitemap.xml               only with a base URL, which canonical tags need too.
                              It lists just these pages, so it can be added to the
                              host site as a supplemental sitemap.

The pages are not linked from anywhere else. Search engines find them through
the sitemap, so submit it in Search Console once the pages are hosted.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote_plus, urlencode

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "lib"))

GET_ITEM = "/buy/browse/v1/item/get_item_by_legacy_id"
GALLERY_MAX = 6


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def img_size(url: str, px: int) -> str:
    return re.sub(r"s-l\d+", f"s-l{px}", url or "")


# --------------------------------------------------------------- live data
def fetch_items(ids: list[int]) -> dict[str, dict]:
    import ebay_client as ec
    out: dict[str, dict] = {}
    for i in ids:
        try:
            d = ec.api_get(GET_ITEM, {"legacy_item_id": str(i)})
        except Exception as e:  # ended/sold items 404; keep the page, mark it
            out[str(i)] = {"available": False, "error": str(e)[:200]}
            print(f"  {i}: unavailable ({str(e)[:80]})")
            continue
        imgs = [(d.get("image") or {}).get("imageUrl")]
        imgs += [a.get("imageUrl") for a in d.get("additionalImages") or []]
        avail = (d.get("estimatedAvailabilities") or [{}])[0]
        out[str(i)] = {
            "available": avail.get("estimatedAvailabilityStatus", "IN_STOCK") != "OUT_OF_STOCK",
            "title": d.get("title"),
            "price": (d.get("price") or {}).get("value"),
            "currency": (d.get("price") or {}).get("currency", "USD"),
            "condition": d.get("condition"),
            "condition_note": d.get("conditionDescription"),
            "images": [u for u in imgs if u],
            "brand": d.get("brand"),
            "seller": (d.get("seller") or {}).get("username"),
            "seller_pct": (d.get("seller") or {}).get("feedbackPercentage"),
            "seller_score": (d.get("seller") or {}).get("feedbackScore"),
            "free_shipping": any(str((o.get("shippingCost") or {}).get("value")) in ("0.0", "0.00", "0")
                                 for o in d.get("shippingOptions") or []),
            "returns": bool((d.get("returnTerms") or {}).get("returnsAccepted")),
            "category": d.get("categoryPath"),
        }
        print(f"  {i}: ${out[str(i)]['price']}  {len(out[str(i)]['images'])} photos")
    return out


# --------------------------------------------------------------- links
class Links:
    def __init__(self, tracking: dict):
        self.t = tracking

    def _q(self, page: str) -> str:
        t = self.t
        params = {k: t[k] for k in ("mkcid", "mkrid", "siteid", "campid", "toolid")}
        if t.get("customid_prefix"):
            params["customid"] = f"{t['customid_prefix']}-{page}"[:64]
        params["mkevt"] = "1"
        return urlencode(params)

    def item(self, item_id, page: str) -> str:
        return f"https://www.ebay.com/itm/{item_id}?{self._q(page)}"

    def search(self, keywords: str, page: str, seller: str | None = None) -> str:
        q = f"_nkw={quote_plus(keywords)}" + (f"&_ssn={quote_plus(seller)}" if seller else "")
        return f"https://www.ebay.com/sch/i.html?{q}&{self._q(page)}"


# --------------------------------------------------------------- page shell
CSS = """
:root{--bg:#faf8f4;--fg:#1f1c18;--muted:#6b645a;--line:#e4ded3;--card:#fff;--accent:#7a4b1e;--cta:#1f5fbf;--cta-fg:#fff;--ok:#2d6a3e;--warn:#9a3b1b}
@media (prefers-color-scheme:dark){:root{--bg:#171513;--fg:#ece7df;--muted:#a59d91;--line:#33302b;--card:#211f1c;--accent:#d9a066;--cta:#5b9cf0;--cta-fg:#0b1220;--ok:#7fc795;--warn:#f0a080}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
a{color:var(--accent)}img{max-width:100%;height:auto;display:block}
.wrap{max-width:1080px;margin:0 auto;padding:0 16px}
header.site{border-bottom:1px solid var(--line);padding:12px 0;font-size:14px;color:var(--muted)}
header.site b{color:var(--fg);font-family:Georgia,"Times New Roman",serif;font-size:18px;margin-right:8px}
.disclosure{font-size:13px;color:var(--muted);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:16px 0}
nav.crumbs{font-size:14px;color:var(--muted);margin:16px 0 4px}nav.crumbs a{color:var(--muted)}
h1,h2,h3{font-family:Georgia,"Times New Roman",serif;line-height:1.25;font-weight:600}
h1{font-size:clamp(26px,4vw,38px);margin:8px 0 16px}h2{font-size:24px;margin:40px 0 12px}h3{font-size:18px;margin:0 0 6px}
.lede{font-size:18px;max-width:72ch}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(230px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card .ph{aspect-ratio:1/1;background:#fff;display:flex;align-items:center;justify-content:center;overflow:hidden}
.card .ph img{width:100%;height:100%;object-fit:contain}
.card .bd{padding:12px;display:flex;flex-direction:column;gap:6px;flex:1}
.card h3 a{color:var(--fg);text-decoration:none}.card h3 a:hover{text-decoration:underline}
.meta{font-size:13px;color:var(--muted)}.price{font-size:20px;font-weight:700}
.btn{display:inline-block;text-align:center;background:var(--cta);color:var(--cta-fg);text-decoration:none;font-weight:600;border-radius:999px;padding:10px 18px;margin-top:auto}
.btn:hover{filter:brightness(1.08)}.btn.ghost{background:transparent;color:var(--cta);border:1.5px solid var(--cta)}
.sold{color:var(--warn);font-weight:700;text-transform:uppercase;font-size:13px;letter-spacing:.04em}
.item{display:grid;gap:24px;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);align-items:start}
@media (max-width:760px){.item{grid-template-columns:1fr}}
.gallery .main{display:block;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.gallery .main img{width:100%;aspect-ratio:1/1;object-fit:contain}
.thumbs{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:8px}
.thumbs a{display:block;background:#fff;border:1px solid var(--line);border-radius:6px;overflow:hidden}.thumbs img{aspect-ratio:1/1;object-fit:cover;width:100%}
.buy{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;position:sticky;top:12px}
.buy .price{font-size:30px}.buy ul{padding-left:18px;margin:10px 0 16px}.buy .btn{display:block}
.buy .ok{color:var(--ok)}
table.spec{border-collapse:collapse;width:100%;max-width:720px}
table.spec th,table.spec td{text-align:left;vertical-align:top;padding:8px 10px;border-bottom:1px solid var(--line)}
table.spec th{width:32%;color:var(--muted);font-weight:500}
blockquote{margin:0;padding:12px 16px;border-left:3px solid var(--accent);background:var(--card);max-width:72ch}
.check li{margin:4px 0}
.prose{max-width:72ch}
.topics{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 24px}.topics a{border:1px solid var(--line);border-radius:999px;padding:6px 12px;text-decoration:none;font-size:14px;background:var(--card)}
.sec-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
footer{border-top:1px solid var(--line);margin-top:56px;padding:20px 0 40px;font-size:13px;color:var(--muted)}
"""


def page(*, title: str, description: str, body: str, canonical: str | None,
         og_image: str | None, jsonld: list[dict], site: str, disclosure: str,
         asof: str) -> str:
    head = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{esc(title)}</title>",
        f'<meta name="description" content="{esc(description)}">',
        '<meta name="robots" content="index,follow,max-image-preview:large">',
        f'<meta property="og:type" content="website"><meta property="og:site_name" content="{esc(site)}">',
        f'<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(description)}">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="referrer" content="no-referrer-when-downgrade">',
    ]
    if canonical:
        head.append(f'<link rel="canonical" href="{esc(canonical)}"><meta property="og:url" content="{esc(canonical)}">')
    if og_image:
        head.append(f'<meta property="og:image" content="{esc(og_image)}">')
    for block in jsonld:
        head.append('<script type="application/ld+json">'
                    + json.dumps(block, ensure_ascii=False).replace("</", "<\\/") + "</script>")
    head.append(f"<style>{CSS}</style>")
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n" + "\n".join(head) + "\n</head>\n<body>\n"
            f'<header class="site"><div class="wrap"><b>{esc(site)}</b>curated antique &amp; vintage finds on eBay</div></header>\n'
            f'<main class="wrap">\n<p class="disclosure">{disclosure}</p>\n{body}\n</main>\n'
            f'<footer><div class="wrap"><p>{disclosure}</p><p>Prices, photos and availability were taken from eBay on {esc(asof)} '
            'and may have changed. The eBay listing is the final word on price, condition and terms. '
            f'{esc(site)} is not the seller of these items.</p></div></footer>\n</body>\n</html>\n')


# --------------------------------------------------------------- builder
class Build:
    def __init__(self, content: dict, data: dict, base_url: str, asof: str):
        self.c, self.d, self.asof = content, data, asof
        self.base = base_url.rstrip("/") + "/" if base_url else ""
        self.links = Links(content["tracking"])
        self.site = content["site_name"]
        self.items = {it["id"]: it for it in content["items"]}
        self.topic_of = {i: t for t in content["topics"] for i in t["items"]}
        self.disclosure = ("Affiliate disclosure: links on this page go to eBay, and we may earn a commission "
                           "if you buy through them. It costs you nothing extra.")

    def live(self, item_id) -> dict:
        return self.d.get(str(item_id)) or {"available": False}

    def abs(self, rel: str) -> str | None:
        return self.base + rel if self.base else None

    @staticmethod
    def money(v, cur="USD") -> str:
        try:
            return ("$" if cur == "USD" else cur + " ") + f"{float(v):,.2f}".replace(".00", "")
        except (TypeError, ValueError):
            return ""

    # --- shared pieces
    def card(self, item_id, here: str, prefix: str) -> str:
        it, lv = self.items[item_id], self.live(item_id)
        img = img_size((lv.get("images") or [""])[0], 500)
        page_link = f"{prefix}{it['slug']}/"
        if lv.get("available"):
            cta = (f'<a class="btn" rel="sponsored noopener" target="_blank" '
                   f'href="{esc(self.links.item(item_id, here))}">View on eBay</a>')
            status = (f'<div class="price">{esc(self.money(lv.get("price"), lv.get("currency")))}</div>'
                      f'<div class="meta">{esc(lv.get("condition") or "")}'
                      f'{" · Free shipping" if lv.get("free_shipping") else ""}</div>')
        else:
            cta = (f'<a class="btn ghost" rel="sponsored noopener" target="_blank" '
                   f'href="{esc(self.links.search(it["search"], here))}">Find similar on eBay</a>')
            status = '<div class="sold">Sold</div>'
        return (f'<article class="card"><a class="ph" href="{esc(page_link)}">'
                f'<img src="{esc(img)}" alt="{esc(it["h1"])}" loading="lazy" width="500" height="500"></a>'
                f'<div class="bd"><h3><a href="{esc(page_link)}">{esc(it["h1"])}</a></h3>{status}{cta}</div></article>')

    def item_list_ld(self, ids, rel_prefix: str) -> dict:
        return {"@context": "https://schema.org", "@type": "ItemList",
                "itemListElement": [{"@type": "ListItem", "position": n + 1, "name": self.items[i]["h1"],
                                     **({"url": self.abs(rel_prefix + self.items[i]["slug"] + "/")} if self.base else {})}
                                    for n, i in enumerate(ids)]}

    def crumbs_ld(self, trail: list[tuple[str, str]]) -> list[dict]:
        if not self.base:
            return []
        return [{"@context": "https://schema.org", "@type": "BreadcrumbList",
                 "itemListElement": [{"@type": "ListItem", "position": n + 1, "name": name, "item": self.abs(rel)}
                                     for n, (name, rel) in enumerate(trail)]}]

    # --- pages
    def hub(self) -> str:
        h = self.c["hub"]
        topics_nav = "".join(f'<a href="{esc(t["slug"])}/">{esc(t["h1"])}</a>' for t in self.c["topics"])
        secs = []
        for t in self.c["topics"]:
            cards = "".join(self.card(i, "hub", "") for i in t["items"])
            secs.append(f'<section><div class="sec-head"><h2>{esc(t["h1"])}</h2>'
                        f'<a href="{esc(t["slug"])}/">More about these →</a></div>'
                        f'<div class="grid">{cards}</div></section>')
        body = (f'<h1>{esc(h["h1"])}</h1>' + "".join(f'<p class="lede">{esc(p)}</p>' for p in h["intro"])
                + f'<nav class="topics" aria-label="Topics">{topics_nav}</nav>' + "".join(secs)
                + f'<p style="margin-top:32px"><a class="btn ghost" rel="sponsored noopener" target="_blank" '
                  f'href="{esc(self.c["storefront_url"])}">See the full collection on eBay</a></p>')
        ids = [i for t in self.c["topics"] for i in t["items"]]
        first = img_size((self.live(ids[0]).get("images") or [""])[0], 1200)
        return page(title=h["title"], description=h["description"], body=body, canonical=self.abs(""),
                    og_image=first, jsonld=[self.item_list_ld(ids, "")], site=self.site,
                    disclosure=self.disclosure, asof=self.asof)

    def topic(self, t: dict) -> str:
        here = f"t-{t['slug']}"
        cards = "".join(self.card(i, here, "../") for i in t["items"])
        others = "".join(f'<a href="../{esc(o["slug"])}/">{esc(o["h1"])}</a>'
                         for o in self.c["topics"] if o is not t)
        body = (f'<nav class="crumbs"><a href="../">Collection</a> › {esc(t["h1"])}</nav>'
                f'<h1>{esc(t["h1"])}</h1><p class="lede">{esc(t["intro"])}</p>'
                f'<div class="grid">{cards}</div>'
                f'<p style="margin-top:28px"><a class="btn ghost" rel="sponsored noopener" target="_blank" '
                f'href="{esc(self.links.search(t["search"], here))}">Shop more like these on eBay</a></p>'
                f'<h2>Also in the collection</h2><nav class="topics">{others}</nav>')
        first = img_size((self.live(t["items"][0]).get("images") or [""])[0], 1200)
        return page(title=t["title"], description=t["description"], body=body,
                    canonical=self.abs(t["slug"] + "/"), og_image=first,
                    jsonld=[self.item_list_ld(t["items"], "")]
                    + self.crumbs_ld([("Collection", ""), (t["h1"], t["slug"] + "/")]),
                    site=self.site, disclosure=self.disclosure, asof=self.asof)

    def item(self, item_id) -> str:
        it, lv = self.items[item_id], self.live(item_id)
        t = self.topic_of.get(item_id)
        here = it["slug"]
        imgs = lv.get("images") or []
        buy_url = self.links.item(item_id, here)
        similar_url = self.links.search(it["search"], here)
        main = (f'<a class="main" rel="sponsored noopener" target="_blank" href="{esc(buy_url)}">'
                f'<img src="{esc(img_size(imgs[0], 1200))}" alt="{esc(it["h1"])}" width="1200" height="1200"></a>'
                if imgs else "")
        thumbs = "".join(f'<a rel="sponsored noopener" target="_blank" href="{esc(buy_url)}">'
                         f'<img src="{esc(img_size(u, 300))}" alt="{esc(it["h1"])}, photo {n + 2}" loading="lazy" '
                         f'width="300" height="300"></a>' for n, u in enumerate(imgs[1:GALLERY_MAX]))
        if lv.get("available"):
            perks = [lv.get("condition") or ""]
            if lv.get("free_shipping"):
                perks.append('<span class="ok">Free shipping</span>')
            if lv.get("returns"):
                perks.append("Returns accepted")
            if lv.get("seller_pct"):
                perks.append(f'Seller feedback {float(lv["seller_pct"]):g}% '
                             f'({int(lv.get("seller_score") or 0):,} ratings)')
            more = f"See all {len(imgs)} photos on eBay" if len(imgs) > GALLERY_MAX else "View on eBay"
            buy = (f'<div class="price">{esc(self.money(lv.get("price"), lv.get("currency")))}</div>'
                   f'<div class="meta">Price checked {esc(self.asof)}</div>'
                   f'<ul>{"".join(f"<li>{p}</li>" for p in perks if p)}</ul>'
                   f'<a class="btn" rel="sponsored noopener" target="_blank" href="{esc(buy_url)}">{more}</a>')
        else:
            buy = ('<div class="sold">This piece has sold</div>'
                   '<p>It is no longer available, but similar pieces are listed on eBay now.</p>'
                   f'<a class="btn" rel="sponsored noopener" target="_blank" href="{esc(similar_url)}">Find similar on eBay</a>')
        rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in it["details"])
        note = (f'<h2>Seller\'s condition note</h2><blockquote>{esc(lv["condition_note"])}</blockquote>'
                if lv.get("condition_note") else "")
        checks = "".join(f"<li>{esc(c)}</li>" for c in it.get("check") or [])
        related_ids = [i for i in (t["items"] if t else []) if i != item_id]
        related_ids += [i for i in self.items if i != item_id and i not in related_ids]
        related = "".join(self.card(i, here, "../") for i in related_ids[:4])
        crumb_topic = f' › <a href="../{esc(t["slug"])}/">{esc(t["h1"])}</a>' if t else ""
        body = (f'<nav class="crumbs"><a href="../">Collection</a>{crumb_topic}</nav>'
                f'<h1>{esc(it["h1"])}</h1>'
                f'<div class="item"><div class="gallery">{main}<div class="thumbs">{thumbs}</div></div>'
                f'<aside class="buy">{buy}</aside></div>'
                f'<div class="prose"><p class="lede" style="margin-top:24px">{esc(it["intro"])}</p></div>'
                f'<h2>Details</h2><table class="spec">{rows}</table>'
                f'<h2>{esc(it["about_heading"])}</h2><div class="prose"><p>{esc(it["about"])}</p></div>'
                f'{note}'
                + (f'<h2>Before you buy</h2><ul class="check prose">{checks}</ul>' if checks else "")
                + f'<p style="margin-top:24px"><a class="btn ghost" rel="sponsored noopener" target="_blank" '
                  f'href="{esc(similar_url)}">Browse more {esc(it["search"])} on eBay</a></p>'
                + f'<h2>More from this collection</h2><div class="grid">{related}</div>')
        ld = [self.product_ld(item_id)]
        trail = [("Collection", "")] + ([(t["h1"], t["slug"] + "/")] if t else []) + [(it["h1"], here + "/")]
        return page(title=it["title"], description=it["description"], body=body, canonical=self.abs(here + "/"),
                    og_image=img_size(imgs[0], 1200) if imgs else None, jsonld=ld + self.crumbs_ld(trail),
                    site=self.site, disclosure=self.disclosure, asof=self.asof)

    def product_ld(self, item_id) -> dict:
        it, lv = self.items[item_id], self.live(item_id)
        ld = {"@context": "https://schema.org", "@type": "Product", "name": it["h1"],
              "description": it["intro"], "sku": str(item_id),
              "image": [img_size(u, 1600) for u in (lv.get("images") or [])[:6]]}
        if lv.get("brand"):
            ld["brand"] = {"@type": "Brand", "name": lv["brand"]}
        if lv.get("price"):
            ld["offers"] = {
                "@type": "Offer", "url": f"https://www.ebay.com/itm/{item_id}",
                "price": str(lv["price"]), "priceCurrency": lv.get("currency", "USD"),
                "availability": "https://schema.org/" + ("InStock" if lv.get("available") else "SoldOut"),
                "itemCondition": "https://schema.org/UsedCondition",
                **({"seller": {"@type": "Organization", "name": lv["seller"]}} if lv.get("seller") else {}),
            }
        return ld

    def write(self, out: Path) -> list[str]:
        rels = [""]
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(self.hub(), encoding="utf-8")
        for t in self.c["topics"]:
            (out / t["slug"]).mkdir(exist_ok=True)
            (out / t["slug"] / "index.html").write_text(self.topic(t), encoding="utf-8")
            rels.append(t["slug"] + "/")
        for i, it in self.items.items():
            (out / it["slug"]).mkdir(exist_ok=True)
            (out / it["slug"] / "index.html").write_text(self.item(i), encoding="utf-8")
            rels.append(it["slug"] + "/")
        if self.base:
            urls = "".join(f"\n  <url>\n    <loc>{esc(self.abs(r))}</loc>\n    <lastmod>{self.asof}</lastmod>\n  </url>" for r in rels)
            (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns='
                                             f'"http://www.sitemaps.org/schemas/sitemap/0.9">{urls}\n</urlset>\n',
                                             encoding="utf-8")
        return rels


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("content", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--base-url", help="public URL the pages will live under; enables canonical tags + sitemap")
    ap.add_argument("--offline", action="store_true", help="reuse <out>/_data.json instead of calling eBay")
    a = ap.parse_args()

    content = json.loads(a.content.read_text(encoding="utf-8"))
    out = a.out or REPO / "seo" / "dist" / content["slug"]
    base = a.base_url if a.base_url is not None else content.get("base_url", "")
    cache = out / "_data.json"
    if a.offline:
        snap = json.loads(cache.read_text(encoding="utf-8"))
        data, asof = snap["items"], snap["asof"]
    else:
        print(f"Fetching {len(content['items'])} items from the Browse API…")
        data, asof = fetch_items([it["id"] for it in content["items"]]), date.today().isoformat()
        out.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"asof": asof, "items": data}, indent=1), encoding="utf-8")
    rels = Build(content, data, base, asof).write(out)
    sold = [k for k, v in data.items() if not v.get("available")]
    print(f"Wrote {len(rels)} pages to {out}" + (f"; {len(sold)} sold/ended: {', '.join(sold)}" if sold else ""))
    if not base:
        print("No base URL: canonical tags, og:url and sitemap.xml were skipped. Re-run with --base-url once hosted.")
    else:
        print(f"Supplemental sitemap: {base.rstrip('/')}/sitemap.xml")


if __name__ == "__main__":
    main()
