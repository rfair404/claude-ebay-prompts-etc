"""lib/live_marquee.py — the promo image (marquee) for an eBay Live show.

A 1050x1575 poster: show title, the show's start time, offer badges ($1
starts, free shipping), four large hero photos and up to 21 thumbnails. The
eBay calls and the PNG render live in tools/live_marquee.py; nothing here
touches the network.

Picking what goes on it is the other half: select_items() takes Browse API
item summaries of a seller's live listings and keeps the oldest ones that
pass the show's filters (category, price window, no seasonal pieces).

The spec is inventory/_live/<show>/marquee.json, next to the lineup that
tools/live_lineup.py writes there. Edit it (labels, heroes, copy) and
re-render; --pick rebuilds only its item list.
"""
from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

WIDTH, HEIGHT = 1050, 1575
MAX_HEROES = 4
MAX_GRID = 21            # 7 columns x 3 rows is all the poster has room for
EASTERN = ZoneInfo("America/New_York")
PACIFIC = ZoneInfo("America/Los_Angeles")
UTC = ZoneInfo("UTC")

# Leaf categories under Jewelry & Watches that aren't jewelry. Measured
# 2026-10-13 on the default store: balance-staff kits, a watch band and a
# watch brochure all sat in category 281.
NON_JEWELRY_LEAVES = frozenset({
    "Other Watch Parts", "Watch Parts", "Parts, Tools & Guides",
    "Manuals, Guides & Catalogs", "Wristwatch Bands", "Watch Accessories",
})
SEASONAL_RE = re.compile(r"\b(christmas|xmas|holly|santa|stocking|wreath|nativity|"
                         r"halloween|easter|valentine)", re.I)
_LABEL_DROP = re.compile(r"^(vintage|vtg|nos|excellent|estate|new|antique)\b\s*", re.I)


# ---------------------------------------------------------------------------
# picking
# ---------------------------------------------------------------------------

def summary_record(it: dict) -> dict:
    """The fields the marquee needs from a Browse item summary (or getItem)."""
    price = (it.get("price") or it.get("currentBidPrice") or {}).get("value")
    cats = [c.get("categoryName", "") for c in it.get("categories") or []]
    if not cats and it.get("categoryPath"):
        cats = list(reversed(it["categoryPath"].split("|")))
    return {
        "item_id": str(it.get("legacyItemId") or ""),
        "title": it.get("title") or "",
        "created": it.get("itemCreationDate") or "",
        "price": float(price or 0),
        "leaf": cats[0] if cats else "",
        "image": (it.get("image") or {}).get("imageUrl") or "",
    }


def select_items(records: list[dict], *, limit: int = 25,
                 min_price: Optional[float] = None, max_price: Optional[float] = None,
                 skip_seasonal: bool = False, exclude: frozenset = frozenset(),
                 include: frozenset = frozenset()) -> tuple[list[dict], dict]:
    """Oldest-first records that pass the filters, and a count of why the
    rest were dropped. `include` ids skip the category check (pendants filed
    under Collectibles > Medals, say) but not the price or seasonal ones."""
    dropped = {"excluded": 0, "not jewelry": 0, "seasonal": 0,
               "under min price": 0, "over max price": 0}
    keep = []
    for r in sorted(records, key=lambda r: r.get("created") or "9"):
        if r["item_id"] in exclude:
            dropped["excluded"] += 1
        elif r["item_id"] not in include and r.get("leaf") in NON_JEWELRY_LEAVES:
            dropped["not jewelry"] += 1
        elif skip_seasonal and SEASONAL_RE.search(r.get("title") or ""):
            dropped["seasonal"] += 1
        elif min_price is not None and r["price"] < min_price:
            dropped["under min price"] += 1
        elif max_price is not None and r["price"] > max_price:
            dropped["over max price"] += 1
        else:
            keep.append(r)
    return keep[:limit], dropped


def short_label(title: str, max_len: int = 26) -> str:
    """A caption that fits under a thumbnail: marketing lead words dropped,
    then whole words up to max_len. A first guess; marquee.json is the
    place to fix it."""
    t = title.strip()
    while True:
        s = _LABEL_DROP.sub("", t)
        if s == t:
            break
        t = s
    out = ""
    for w in t.split():
        if len(out) + len(w) + (1 if out else 0) > max_len:
            break
        out = f"{out} {w}".strip()
    return out or title[:max_len]


# ---------------------------------------------------------------------------
# time lines
# ---------------------------------------------------------------------------

def parse_start(text: str) -> datetime:
    """'2026-10-14 20:00' (Eastern unless it carries an offset) -> aware datetime."""
    dt = datetime.fromisoformat(text.strip().replace(" ", "T"))
    return dt if dt.tzinfo else dt.replace(tzinfo=EASTERN)


def _clock(dt: datetime) -> str:
    h = dt.strftime("%I").lstrip("0")
    return f"{h} {dt.strftime('%p')}" if dt.minute == 0 else f"{h}:{dt.strftime('%M %p')}"


def when_lines(start: datetime) -> dict:
    """Header line, footer line, and the GMT time eBay's show form wants.

    eBay's scheduler showed GMT (2026-10-13), and Eastern-to-GMT shifts with
    daylight time, so the GMT line carries its own date — 8 PM EDT on the
    14th is 00:00 GMT on the 15th."""
    et, pt, gmt = start.astimezone(EASTERN), start.astimezone(PACIFIC), start.astimezone(UTC)
    day = f"{et.strftime('%b').upper()} {et.day}"
    return {
        "kicker": f"{et.strftime('%a').upper()} {day} · {_clock(et)} ET",
        "footer": f"{et.strftime('%A').upper()}, {day} · {_clock(et)} ET / {_clock(pt)} PT",
        "gmt": f"{gmt.strftime('%a %b')} {gmt.day} {gmt.strftime('%H:%M')} GMT",
    }


# ---------------------------------------------------------------------------
# spec
# ---------------------------------------------------------------------------

def default_spec(show: str) -> dict:
    return {
        "show": show,
        "start": None,
        "brand": "POP'S GAMES",
        "headline": "Vintage Jewelry",
        "subhead": "Live Auction",
        "deck": "",
        "badges": [["Every lot", "$1 Starts"], ["Every lot", "Free Shipping"]],
        "ribbon": ["FIRST SHOW!", "NEW eBay LIVE SELLER"],
        "footer_title": "Our first eBay Live show —",
        "footer_title_em": "help us build it.",
        "footer_text": ("Pull up a chair, bid a buck, and tell us in chat what works and "
                        "what doesn't:\npricing, photos, pacing, and what you want on the "
                        "table next."),
        "footer_cta": "FOLLOW THE STORE",
        "heroes": [],
        "items": [],
    }


def set_items(spec: dict, records: list[dict]) -> dict:
    """Replace the spec's items, keeping labels already written for an id and
    heroes that are still in the list (topped up from the front)."""
    old = {i["item_id"]: i for i in spec.get("items") or []}
    spec["items"] = [{**r, "label": (old.get(r["item_id"]) or {}).get("label")
                      or short_label(r["title"])} for r in records]
    ids = [r["item_id"] for r in records]
    heroes = [h for h in spec.get("heroes") or [] if h in ids][:MAX_HEROES]
    heroes += [i for i in ids if i not in heroes][:MAX_HEROES - len(heroes)]
    spec["heroes"] = heroes
    return spec


def check_spec(spec: dict) -> list[str]:
    """Problems that would break the layout, as messages; empty when fine."""
    ids = [i["item_id"] for i in spec.get("items") or []]
    problems = []
    if len(spec.get("heroes") or []) > MAX_HEROES:
        problems.append(f"{len(spec['heroes'])} heroes; the poster has room for {MAX_HEROES}")
    for h in spec.get("heroes") or []:
        if h not in ids:
            problems.append(f"hero {h} is not in items")
    grid = len(set(ids) - set(spec.get("heroes") or []))
    if grid > MAX_GRID:
        problems.append(f"{grid} thumbnails; the poster has room for {MAX_GRID}")
    return problems


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------

_e = html.escape

_CSS = """
:root{--ink:#141210;--cream:#f4ecdc;--red:#a8322b;--gold:#d4ad5c;--grey:#b9ae9b}
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1050px;height:1575px;background:var(--ink);overflow:hidden}
body{color:var(--cream);font-family:"URW Gothic","Century Gothic",sans-serif;position:relative}
.frame{position:absolute;inset:18px;border:2px solid var(--gold);pointer-events:none}
.frame:after{content:"";position:absolute;inset:6px;border:1px solid rgba(212,173,92,.45)}
.ribbon{position:absolute;left:727px;top:36px;width:480px;height:92px;transform:rotate(45deg);z-index:5;
  background:var(--gold);color:var(--ink);text-align:center;box-shadow:0 4px 14px rgba(0,0,0,.55);
  border-top:2px solid var(--ink);border-bottom:2px solid var(--ink);outline:2px solid var(--gold);padding-top:17px}
.ribbon .b{font-family:"Nimbus Sans Narrow","Arial Narrow",sans-serif;font-weight:700;font-size:34px;line-height:1;
  letter-spacing:.03em;color:var(--red)}
.ribbon .a{font-weight:700;font-size:15px;letter-spacing:.14em;line-height:1;margin-top:9px}
header{padding:62px 60px 0;text-align:center}
.kicker{font-weight:600;letter-spacing:.22em;font-size:20px;color:var(--gold)}
.kicker b{color:var(--cream);font-weight:600}
.live{display:inline-block;background:var(--red);color:#fff;font-weight:700;letter-spacing:.2em;
  font-size:18px;padding:6px 14px 5px;margin:0 10px;vertical-align:2px}
.live i{display:inline-block;width:10px;height:10px;border-radius:50%;background:#fff;margin-right:8px;vertical-align:1px}
h1{font-family:"P052","Palatino Linotype",Palatino,serif;font-weight:700;font-size:108px;line-height:.95;margin-top:26px}
h1 em{display:block;font-style:italic;font-weight:400;color:var(--gold);font-size:90px;margin-top:4px}
.deck{font-family:"P052","Palatino Linotype",Palatino,serif;font-style:italic;font-size:25px;color:var(--grey);margin-top:16px}
.badges{display:flex;justify-content:center;gap:22px;margin-top:28px}
.badge{background:var(--cream);color:var(--ink);padding:12px 30px 10px;font-family:"Nimbus Sans Narrow","Arial Narrow",sans-serif;
  font-weight:700;font-size:58px;line-height:1;text-transform:uppercase;box-shadow:6px 6px 0 var(--red)}
.badge small{display:block;font-family:"URW Gothic","Century Gothic",sans-serif;font-weight:600;font-size:15px;
  letter-spacing:.28em;color:var(--red);margin-bottom:4px}
.hero{display:grid;grid-template-columns:repeat(4,1fr);gap:18px;padding:36px 60px 0}
.hero .ph{aspect-ratio:1;border:3px solid var(--gold)}
.hero figcaption{font-family:"P052","Palatino Linotype",Palatino,serif;font-style:italic;font-size:19px;line-height:1.15;
  text-align:center;margin-top:9px}
.ph{background-size:cover;background-position:center;background-color:#2a251f}
.rule{display:flex;align-items:center;gap:16px;padding:26px 60px 14px;font-weight:600;letter-spacing:.3em;font-size:16px;color:var(--gold)}
.rule:before,.rule:after{content:"";flex:1;height:1px;background:rgba(212,173,92,.55)}
.grid{display:grid;grid-template-columns:repeat(7,1fr);gap:12px;padding:0 60px}
.grid .ph{aspect-ratio:1;border:1px solid rgba(212,173,92,.6)}
.grid figcaption{font-size:11.5px;line-height:1.2;text-align:center;margin-top:6px;color:var(--grey);font-weight:500;height:28px}
footer{position:absolute;left:18px;right:18px;bottom:18px;background:var(--red);padding:26px 60px 28px;text-align:center}
footer h2{font-family:"P052","Palatino Linotype",Palatino,serif;font-weight:700;font-size:38px;line-height:1.1}
footer h2 em{font-weight:400}
footer p{font-size:19px;line-height:1.4;margin-top:10px;color:#fbe9e4}
footer .cta{margin-top:12px;font-weight:600;letter-spacing:.28em;font-size:15px;color:#fff}
"""


def _tile(item: dict, img_dir: str) -> str:
    src = f"{img_dir}/{item['item_id']}.jpg"
    return (f'<figure><div class="ph" style="background-image:url(\'{_e(src)}\')"></div>'
            f'<figcaption>{_e(item.get("label") or "")}</figcaption></figure>')


def render(spec: dict, img_dir: str = "marquee_img") -> str:
    """The marquee as a standalone HTML page, WIDTH x HEIGHT. Photos are read
    from <img_dir>/<item_id>.jpg relative to the page."""
    by_id = {i["item_id"]: i for i in spec.get("items") or []}
    heroes = [by_id[h] for h in spec.get("heroes") or [] if h in by_id][:MAX_HEROES]
    hero_ids = {h["item_id"] for h in heroes}
    grid = [i for i in spec.get("items") or [] if i["item_id"] not in hero_ids][:MAX_GRID]

    when = when_lines(parse_start(spec["start"])) if spec.get("start") else None
    kicker_when = f' <b>{_e(when["kicker"])}</b>' if when else ""
    footer_cta = " · ".join(x for x in ((when or {}).get("footer"), spec.get("footer_cta")) if x)
    ribbon = spec.get("ribbon")
    ribbon_html = (f'<div class="ribbon"><div class="b">{_e(ribbon[0])}</div>'
                   f'<div class="a">{_e(ribbon[1])}</div></div>') if ribbon else ""
    badges = "".join(f'<div class="badge"><small>{_e(s)}</small>{_e(b)}</div>'
                     for s, b in spec.get("badges") or [])
    footer_text = "<br>".join(_e(l) for l in (spec.get("footer_text") or "").split("\n"))

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{_e(spec.get("show") or "marquee")}</title><style>{_CSS}</style></head><body>
<div class="frame"></div>{ribbon_html}
<header>
 <div class="kicker">{_e(spec.get("brand") or "")}<span class="live"><i></i>eBay LIVE</span>{kicker_when}</div>
 <h1>{_e(spec.get("headline") or "")}<em>{_e(spec.get("subhead") or "")}</em></h1>
 <div class="deck">{_e(spec.get("deck") or "")}</div>
 <div class="badges">{badges}</div>
</header>
<section class="hero">{"".join(_tile(h, img_dir) for h in heroes)}</section>
<div class="rule">ALSO ON THE TABLE</div>
<section class="grid">{"".join(_tile(g, img_dir) for g in grid)}</section>
<footer>
 <h2>{_e(spec.get("footer_title") or "")} <em>{_e(spec.get("footer_title_em") or "")}</em></h2>
 <p>{footer_text}</p>
 <div class="cta">{_e(footer_cta.upper())}</div>
</footer>
</body></html>
"""
