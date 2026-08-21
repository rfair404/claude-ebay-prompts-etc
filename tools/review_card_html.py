#!/usr/bin/env python3
"""Render the REVIEW gate as one page: the listing as a buyer will meet it,
plus the one control worth having at this moment — which frame leads.

The text card that `list_edit.py --review` prints is complete and it is what
the ledger records, but it asks for a publish decision without showing the
thing being published. A buyer meets this listing as a picture first and a
title second; a gate that shows neither is asking the operator to approve from
memory. So this renders the same card with the photos in it.

The cover shot gets a picker for the same reason PREP gives orientation and
crop their own stages: entry one is eBay's gallery image, the only frame most
buyers ever see in search results, and picking it from thumbnails is a
different question than picking it from a filename. Selecting a frame here
rewrites the command shown at the bottom; the page cannot reach the CLI, and a
button that pretended otherwise would be worse than admitting it.

Everything is CSS. Selection is native radio inputs, the shown picture is a
`:has()` rule, the full-size view is `:target` — the same constraint the Frame
Check page is built under, and for the same reason: two versions of that page
built their DOM in JavaScript, rendered perfectly, and did not respond to a
single click in the viewer the operator actually uses.

    python tools/review_card_html.py <shoot-dir>     # -> <shoot-dir>/review_card.html
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

from PIL import Image                                          # noqa: E402

from draft_io import parse_draft                               # noqa: E402

PREVIEW_PX = 1100          # one copy per frame, used by preview and chip alike
JPEG_Q = 80


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def _uri(path: Path) -> str:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((PREVIEW_PX, PREVIEW_PX), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=JPEG_Q, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def _price_tiers(shoot: Path) -> list[tuple[str, str, str]]:
    """(tier, price, note) rows from price.txt's Tiers block, if there is one."""
    p = shoot / "price.txt"
    if not p.exists():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s{2}(Conservative|Recommended|PUSH-HIGH \(list\)|Push-high)\s+"
                     r"\$?\s*([\d,]+(?:\.\d\d)?)\s*(.*)$", ln)
        if m:
            rows.append((m.group(1), m.group(2), m.group(3).strip()))
    return rows


def _card_lines(shoot: Path) -> tuple[list[str], list[str]]:
    """(preflight bullets, warning bullets) from the text review card."""
    p = shoot / "review_card.md"
    if not p.exists():
        return [], []
    pre, warn, mode = [], [], None
    for ln in p.read_text(encoding="utf-8").splitlines():
        low = ln.strip().lower()
        if low.startswith("preflight"):
            mode = "pre"; continue
        if low.startswith(("international", "comps", "condition detail")):
            mode = None; continue
        if "needs review" in low or "manual intervention" in low:
            mode = "warn"; continue
        m = re.match(r"\s*[•*-]\s+(.*)$", ln)
        if m and mode == "pre":
            pre.append(m.group(1))
        elif m and mode == "warn":
            warn.append(m.group(1))
    return pre, [w for w in warn if w.strip().lower() not in ("none", "none.")]


def _body_html(body: str) -> str:
    """The description, as blocks. Deliberately small: headings, bullets and
    paragraphs are the only things a listing body uses."""
    out, buf, lst = [], [], []

    def flush():
        if buf:
            out.append("<p>" + html.escape(" ".join(buf)) + "</p>")
            buf.clear()
        if lst:
            out.append("<ul>" + "".join(f"<li>{html.escape(i)}</li>" for i in lst) + "</ul>")
            lst.clear()

    for raw in body.splitlines():
        ln = raw.rstrip()
        if not ln.strip():
            flush(); continue
        if ln.startswith("## "):
            flush(); out.append(f"<h3>{html.escape(ln[3:].strip())}</h3>")
        elif ln.startswith("# "):
            flush()                       # the body's own "# Description" heading
        elif ln.lstrip().startswith("- "):
            if buf:
                flush()
            lst.append(ln.lstrip()[2:].strip())
        elif lst:
            # A wrapped bullet. Without this the continuation becomes its own
            # paragraph and the listing reads as if a defect were an aside:
            # "Sky field: two to three pinpoint dark specks" would end there,
            # and "studio dust, not damage" would float away from it.
            lst[-1] += " " + ln.strip()
        else:
            buf.append(ln.strip())
    flush()
    return "\n".join(out)


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------

def _fact(label: str, value: str, cls: str = "") -> str:
    return (f'<div class="fact {cls}"><dt>{html.escape(label)}</dt>'
            f'<dd>{value}</dd></div>')


def build(shoot: Path, out: Path | None = None) -> Path:
    shoot = Path(shoot)
    draft = parse_draft(shoot / "draft.md")
    fm = draft.frontmatter
    out = out or (shoot / "review_card.html")

    photos = [str(p) for p in (fm.get("photos") or [])]
    frames = []
    for i, rel in enumerate(photos):
        f = shoot / rel
        if f.exists():
            frames.append({"i": i, "rel": rel, "name": Path(rel).name, "uri": _uri(f)})

    title = str(fm.get("title") or "")
    price = str(fm.get("price") or "")
    bo = fm.get("best_offer") or {}
    ship = fm.get("shipping") or {}
    wt = ship.get("weight") or {}
    pk = ship.get("package_in") or {}
    spec = fm.get("item_specifics") or {}
    extra = spec.get("extra") or {}
    sku = fm.get("meta", {}).get("ebay_inventory_sku") or "not recorded"
    pre, warn = _card_lines(shoot)
    tiers = _price_tiers(shoot)

    # --- per-frame CSS: which preview shows, which command shows -----------
    sel = "\n".join(
        f'body:has(#h{f["i"]}:checked) .shot[data-i="{f["i"]}"],'
        f'body:has(#h{f["i"]}:checked) .cmd[data-i="{f["i"]}"]{{display:block}}\n'
        f'body:has(#h{f["i"]}:checked) label[for="h{f["i"]}"]{{'
        f'outline:2px solid var(--accent);outline-offset:2px}}'
        for f in frames)
    vars_ = "\n".join(f'  --p{f["i"]}:url({f["uri"]});' for f in frames)

    chips = "".join(
        f'<input class="pick" type="radio" name="hero" id="h{f["i"]}"'
        f'{" checked" if f["i"] == 0 else ""}>'
        f'<label for="h{f["i"]}" title="{html.escape(f["name"])}">'
        f'<span class="chipimg" style="background-image:var(--p{f["i"]})"></span>'
        f'<span class="chipn">{f["i"] + 1}</span></label>'
        for f in frames)

    shots = "".join(
        f'<a class="shot" data-i="{f["i"]}" href="#big{f["i"]}">'
        f'<span class="shotimg" style="background-image:var(--p{f["i"]})"></span>'
        f'<span class="shotcap">{html.escape(f["name"])} · click to enlarge</span></a>'
        for f in frames)

    bigs = "".join(
        f'<div class="big" id="big{f["i"]}"><a class="shut" href="#cover">Close</a>'
        f'<div class="bigimg" style="background-image:var(--p{f["i"]})"></div></div>'
        for f in frames)

    shoot_posix = shoot.as_posix()
    cmds = "".join(
        f'<pre class="cmd" data-i="{f["i"]}">'
        + (f'# frame {f["i"] + 1} already leads — nothing to run.\n'
           if f["i"] == 0 else
           f'python lib/list_edit.py --set-hero {html.escape(shoot_posix)} '
           f'{html.escape(f["name"])}\n')
        + "</pre>"
        for f in frames)

    bo_line = ("on · auto-decline $" + str(bo.get("auto_decline_amount"))
               if bo.get("enabled") else "off")
    dims = f'{pk.get("length")} × {pk.get("width")} × {pk.get("depth")} in'
    weight = f'{wt.get("major_lb")} lb {wt.get("minor_oz")} oz'

    specs_rows = "".join(
        f"<tr><th>{html.escape(k.replace('_', ' ').title())}</th>"
        f"<td>{html.escape(str(v))}</td></tr>"
        for k, v in list(spec.items()) + list(extra.items())
        if k != "extra" and str(v).strip())

    tier_rows = "".join(
        f'<tr class="{"lead" if "PUSH" in t.upper() else ""}">'
        f"<th>{html.escape(t)}</th><td class='n'>${html.escape(v)}</td>"
        f"<td class='note'>{html.escape(n)}</td></tr>"
        for t, v, n in tiers)

    warn_html = ("".join(f"<li>{html.escape(w)}</li>" for w in warn)
                 if warn else '<li class="clear">Nothing flagged.</li>')
    pre_html = "".join(f"<li>{html.escape(x)}</li>" for x in pre)

    page = f"""<title>{html.escape(_page_name(title))}</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=Newsreader:opsz,wght@6..72,400;6..72,500&display=swap">
<style>
:root{{
{vars_}
  --ground:#ECEDEF; --surface:#F8F8F9; --sunk:#E3E5E8;
  --ink:#17181C; --muted:#6A6E76; --rule:#D7D9DD;
  --accent:#6B5E8C; --ok:#3E7A5E; --warn:#A6702A;
  --shadow:0 1px 2px rgba(20,22,26,.07), 0 14px 34px -22px rgba(20,22,26,.30);
}}
@media (prefers-color-scheme: dark){{
  :root:not([data-theme="light"]){{
    --ground:#121316; --surface:#1A1C20; --sunk:#0D0E10;
    --ink:#E9E9EC; --muted:#979BA3; --rule:#2A2D33;
    --accent:#A99AC9; --ok:#63B189; --warn:#D3A05C;
    --shadow:0 1px 2px rgba(0,0,0,.5), 0 14px 34px -22px rgba(0,0,0,.8);
  }}
}}
:root[data-theme="dark"]{{
  --ground:#121316; --surface:#1A1C20; --sunk:#0D0E10;
  --ink:#E9E9EC; --muted:#979BA3; --rule:#2A2D33;
  --accent:#A99AC9; --ok:#63B189; --warn:#D3A05C;
  --shadow:0 1px 2px rgba(0,0,0,.5), 0 14px 34px -22px rgba(0,0,0,.8);
}}
*{{box-sizing:border-box}}
body{{margin:0;padding:26px 20px 60px;background:var(--ground);color:var(--ink);
  font:15px/1.5 "IBM Plex Sans",ui-sans-serif,system-ui,sans-serif}}
.card{{max-width:1120px;margin:0 auto;background:var(--surface);
  border:1px solid var(--rule);border-radius:4px;box-shadow:var(--shadow);overflow:hidden}}

.hdr{{padding:22px 24px 18px;border-bottom:1px solid var(--rule);
  display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap}}
.eyebrow{{font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--muted);margin:0 0 7px}}
h1{{font:500 25px/1.24 Newsreader,Georgia,serif;margin:0;text-wrap:balance;max-width:30ch}}
.hdr .ct{{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:11.5px;
  color:var(--muted);margin-top:7px}}
.ask{{margin-left:auto;text-align:right}}
.ask .amt{{font:500 30px/1 "IBM Plex Mono",ui-monospace,monospace;
  font-variant-numeric:tabular-nums;color:var(--accent)}}
.ask .sub{{font-size:12px;color:var(--muted);margin-top:5px}}

.grid{{display:grid;grid-template-columns:1fr;gap:0}}
@media(min-width:900px){{.grid{{grid-template-columns:minmax(0,1.05fr) minmax(0,1fr)}}}}
.col{{padding:22px 24px;min-width:0}}
@media(min-width:900px){{.col+.col{{border-left:1px solid var(--rule)}}}}

h2{{font:600 11px/1 "IBM Plex Sans",sans-serif;letter-spacing:.13em;
  text-transform:uppercase;color:var(--muted);margin:0 0 13px}}
.col+.col h2:not(:first-child),.wide h2:not(:first-child){{margin-top:26px}}

.shot{{display:none;text-decoration:none;color:inherit}}
.shot[data-i="0"]{{display:block}}
.shotimg{{display:block;width:100%;aspect-ratio:4/3;background:var(--sunk);
  background-size:contain;background-repeat:no-repeat;background-position:center;
  border:1px solid var(--rule);border-radius:3px;cursor:zoom-in}}
.shotcap{{display:block;margin-top:8px;font-size:11.5px;color:var(--muted);
  font-family:"IBM Plex Mono",ui-monospace,monospace}}
.strip{{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}}
.pick{{position:absolute;width:1px;height:1px;opacity:0;margin:0}}
.strip label{{position:relative;width:70px;height:70px;border:1px solid var(--rule);
  border-radius:3px;overflow:hidden;cursor:pointer;background:var(--sunk);display:block}}
.chipimg{{position:absolute;inset:0;background-size:contain;
  background-repeat:no-repeat;background-position:center}}
.chipn{{position:absolute;left:0;bottom:0;padding:1px 5px;font-size:10px;
  font-family:"IBM Plex Mono",ui-monospace,monospace;background:var(--surface);
  color:var(--muted);border-top-right-radius:3px}}
.pick:focus-visible+label{{outline:2px solid var(--accent);outline-offset:2px}}
.hint{{font-size:12.5px;color:var(--muted);margin:11px 0 0;max-width:52ch}}

dl{{margin:0;display:grid;gap:11px}}
.fact{{display:grid;grid-template-columns:130px 1fr;gap:12px;align-items:baseline}}
.fact dt{{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}}
.fact dd{{margin:0}}
.mono{{font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}}
.pill{{display:inline-block;padding:2px 8px;border:1px solid currentColor;
  border-radius:2px;font-size:11px;letter-spacing:.05em;text-transform:uppercase}}
.pill.ok{{color:var(--ok)}} .pill.warn{{color:var(--warn)}} .pill.acc{{color:var(--accent)}}

table{{border-collapse:collapse;width:100%;font-size:13.5px}}
th,td{{text-align:left;padding:6px 10px 6px 0;border-bottom:1px solid var(--rule);
  vertical-align:top}}
th{{font-weight:500;color:var(--muted);width:44%}}
tr.lead th,tr.lead td{{color:var(--accent)}}
td.n{{font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums;
  white-space:nowrap;width:88px}}
td.note{{color:var(--muted);font-size:12.5px}}
.scroll{{overflow-x:auto}}

.wide{{padding:22px 24px;border-top:1px solid var(--rule)}}
.disc{{font-size:14px;line-height:1.6;background:var(--sunk);border-radius:3px;
  padding:14px 16px;margin:0}}
.desc{{max-width:66ch}}
.desc h3{{font:500 16px/1.3 Newsreader,Georgia,serif;margin:22px 0 8px}}
.desc p{{margin:0 0 11px}} .desc ul{{margin:0 0 11px;padding-left:20px}}
.desc li{{margin:0 0 4px}}
ul.flags{{margin:0;padding-left:18px;font-size:13.5px}}
ul.flags li{{margin:0 0 5px}} ul.flags .clear{{color:var(--ok);list-style:none;margin-left:-18px}}

pre{{font-family:"IBM Plex Mono",ui-monospace,monospace;font-size:12.5px;
  background:var(--sunk);border:1px solid var(--rule);border-radius:3px;
  padding:11px 13px;margin:0;overflow-x:auto;white-space:pre-wrap;word-break:break-all}}
.cmd{{display:none}}
.cmd[data-i="0"]{{display:block}}
.cmdlab{{font-size:12px;color:var(--muted);margin:0 0 7px}}
.pub{{border-color:var(--accent)}}

.big{{display:none;position:fixed;inset:0;background:rgba(10,11,13,.94);
  z-index:20;padding:30px}}
.big:target{{display:block}}
.bigimg{{width:100%;height:100%;background-size:contain;background-repeat:no-repeat;
  background-position:center}}
.shut{{position:absolute;top:16px;right:22px;color:#fff;font-size:13px;
  text-decoration:none;border:1px solid rgba(255,255,255,.45);
  border-radius:2px;padding:5px 12px}}
a:focus-visible,label:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
@media (prefers-reduced-motion:reduce){{*{{transition:none!important;animation:none!important}}}}
{sel}
</style>

<div class="card" id="cover">
  <div class="hdr">
    <div>
      <p class="eyebrow">Review gate · nothing is live until you say so</p>
      <h1>{html.escape(title)}</h1>
      <div class="ct">{len(title)}/80 characters · sku {html.escape(str(sku))} · {len(frames)} photos</div>
    </div>
    <div class="ask">
      <div class="amt">${html.escape(price)}</div>
      <div class="sub">Best Offer {html.escape(bo_line)}</div>
    </div>
  </div>

  <div class="grid">
    <div class="col">
      <h2>Cover shot — what buyers see in search</h2>
      {shots}
      <div class="strip">{chips}</div>
      <p class="hint">Frame 1 is eBay's gallery image. Pick another and the command
        below changes to match — the page can't reach the CLI, so run it yourself.</p>
    </div>

    <div class="col">
      <h2>The offer</h2>
      <dl>
        {_fact("Price", f'<span class="mono">${html.escape(price)}</span> delivered — free shipping')}
        {_fact("Best Offer", html.escape(bo_line))}
        {_fact("Condition", f'<span class="pill acc">{html.escape(str(fm.get("condition") or ""))}</span>')}
        {_fact("Quantity", html.escape(str(fm.get("quantity") or "")))}
        {_fact("Category", html.escape(str(fm.get("category_path") or "") or "eBay will suggest"))}
        {_fact("Ships", f'{html.escape(str(ship.get("primary_service") or ""))} · {html.escape(weight)} · {html.escape(dims)} packed')}
      </dl>

      {'<h2>Where the price came from</h2><div class="scroll"><table>' + tier_rows + '</table></div>' if tier_rows else ''}

      <h2>Item specifics</h2>
      <div class="scroll"><table>{specs_rows}</table></div>
    </div>
  </div>

  <div class="wide">
    <h2>Condition disclosure — what the buyer is told</h2>
    <p class="disc">{html.escape(str(fm.get("condition_description") or ""))}</p>
  </div>

  <div class="wide">
    <h2>Description</h2>
    <div class="desc">{_body_html(draft.body)}</div>
  </div>

  <div class="wide">
    <h2>Preflight</h2>
    <ul class="flags">{pre_html}</ul>
    <h2>Flagged for a human</h2>
    <ul class="flags">{warn_html}</ul>
  </div>

  <div class="wide">
    <h2>To change the cover shot</h2>
    {cmds}
    <h2>To publish — only after you approve</h2>
    <p class="cmdlab">This puts the listing live at ${html.escape(price)}. There is no undo
      that gets the listing's original start time back.</p>
    <pre class="pub">python lib/list_edit.py --list {html.escape(shoot_posix)} --confirm</pre>
  </div>
</div>
{bigs}
"""
    out.write_text(page, encoding="utf-8")
    return out


def _page_name(title: str) -> str:
    """A short name for the tab and the gallery — not the 80-char eBay title."""
    words = [w for w in re.split(r"\s+", title) if w]
    return " ".join(words[:5]) if words else "Listing review"


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="Render the REVIEW card as a page.")
    ap.add_argument("shoot_dir")
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args(argv)

    shoot = Path(a.shoot_dir)
    if not (shoot / "draft.md").exists():
        ap.error(f"no draft.md in {shoot} — run DRAFT first")
    p = build(shoot, Path(a.out) if a.out else None)
    print(f"{p}  ({p.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
