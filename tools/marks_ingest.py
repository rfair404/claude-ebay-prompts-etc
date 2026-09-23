#!/usr/bin/env python3
"""Build the jeweler's maker's-mark comparison index from a public-domain register.

WHY THIS SOURCE. Every free online mark database worth using (Lang/AJU, Global
Gemology, 925-1000) is all-rights-reserved, and two of the three actively block
automated clients. HathiTrust's 1922 copy of the Jewelers' Circular register is
public domain but its page server 403s scripts and its bulk download is gated to
member institutions. The one corpus that is BOTH legally clean and
programmatically reachable is the Internet Archive's copy of the **1896 edition**
of the same work, carrying a CC0 Public Domain Mark. That is what this ingests.
See kb/articles/makers-marks.md for the full source/licensing table.

WHAT IT IS GOOD FOR, AND WHAT IT IS NOT. The register uniquely records
UNREGISTERED marks, which by definition never appear in any trademark database.
But it is trade-SUBMITTED, not a census — absence is not evidence a mark never
existed — and its 1896 ceiling predates the 1930s-1980s houses (Trifari, Monet,
Napier, Michael Anthony, Milor, ArtCarved...) that dominate a typical estate lot.
Strong for ANTIQUE marks, materially incomplete for VINTAGE ones. Do not let a
confident hit on a 19th-century mark stand in for a mid-century one.

HOW IT WORKS. Each register page is a free layout of engraved mark cuts, each
with the firm's name/address set beneath it. So:
  1. per-page OCR + word boxes come from the IA djvu.xml (no OCR run needed)
  2. pages are kept only if they carry the register running head
  3. ink is segmented into regions; a region is a MARK if it is mostly graphic,
     TEXT if OCR words cover it
  4. each MARK is paired with the TEXT block(s) directly beneath it -> caption
  5. the mark crop is CLIP-embedded into kb/index/jewelry_marks/

CLIP puts images and text in one space, so the result answers both "find marks
that look like this crop" and "find marks described as an eagle in a lozenge".

Retrieval is a CANDIDATE FINDER, not an identification — the same rule the
marble index carries. And an attribution only pays if buyers search it: run the
market test in kb/articles/makers-marks.md before a name touches a title.

    python tools/marks_ingest.py --survey          # which leaves are register pages
    python tools/marks_ingest.py --limit 5         # small trial run
    python tools/marks_ingest.py                   # full build
    python tools/marks_ingest.py --refs            # rewrite the human-readable table

Run it from the MAIN checkout: kb/index/ is gitignored heavy data and a worktree
does not carry it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "lib"))

from vindex import VIndex, embed_images, http_get          # noqa: E402

IA_ITEM = "TradeMarksJewelryAndKindredTrades"
IA_BASE = f"https://archive.org/download/{IA_ITEM}/"
OCR_XML = "trade-marks-1896-00010678-LowRes_djvu.xml"
INDEX_NAME = "jewelry_marks"
PAGE_W = 1600                      # requested page width; IA may serve full size
MAX_MARKS_PER_PAGE = 20            # above this the page reads as prose, not cuts
SOURCE_URL = f"https://archive.org/details/{IA_ITEM}"
SOURCE_CITE = ("Jewelers' Circular Publishing, \"Trade-marks of the jewelry and "
               "kindred trades\", 1896 ed. (Internet Archive, CC0 Public Domain Mark)")

# A register page carries the running head; the advertising pages at the front
# and back of the volume do not. The head is SPLIT across the facing pages —
# verso reads "TRADE-MARKS OF THE", recto "JEWELRY AND KINDRED TRADES." — so
# matching only one half silently drops every other page (it cost us half the
# register the first time). OCR is noisy, so match loosely.
RUNNING_HEAD = re.compile(r"KINDRED\s+TRADES|TRADE[-\s]?MARKS\s+OF\s+THE", re.I)
FIRST_REGISTER_LEAF = 25       # everything before this is cover + front matter

# Section headers ("PRECIOUS AND IMITATION STONES", "TORTOISE SHELL GOODS") tell
# us the trade class a mark was filed under — a genuinely useful facet.
SECTION = re.compile(r"^[A-Z][A-Z&.,'\- ]{8,60}$")


# ---------------------------------------------------------------- OCR --------
def load_pages():
    """-> [{leaf, w, h, words:[(x0,y0,x1,y1,text)]}] from the IA djvu.xml."""
    cache = Path(REPO / "kb" / "index" / INDEX_NAME / "_djvu.xml")
    if cache.exists():
        raw = cache.read_text(encoding="utf-8", errors="replace")
    else:
        raw = http_get(IA_BASE + OCR_XML, timeout=180)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(raw, encoding="utf-8")
    pages = []
    for leaf, obj in enumerate(ET.fromstring(raw).iter("OBJECT")):
        w = h = 0
        for p in obj.findall("PARAM"):
            if p.get("name") == "PAGE":
                pass
        w = int(float(obj.get("width") or 0))
        h = int(float(obj.get("height") or 0))
        words = []
        for wd in obj.iter("WORD"):
            c = (wd.get("coords") or "").split(",")
            if len(c) < 4:
                continue
            try:
                a, b, cc, d = (int(float(v)) for v in c[:4])
            except ValueError:
                continue
            words.append((min(a, cc), min(b, d), max(a, cc), max(b, d),
                          (wd.text or "").strip()))
        pages.append({"leaf": leaf, "w": w, "h": h, "words": words})
    return pages


def is_register_page(page) -> bool:
    if page["leaf"] < FIRST_REGISTER_LEAF:
        return False
    txt = " ".join(w[4] for w in page["words"][:40])
    return bool(RUNNING_HEAD.search(txt))


# The running head contributes a KNOWN MULTISET of tokens: {TRADE-MARKS, OF,
# THE} on the verso, {JEWELRY, AND, KINDRED, TRADES} on the recto. OCR returns
# them scrambled ("TRADE-MARKS THE OF"), so phrase matching fails — subtract one
# occurrence of each token instead and keep what is left. That also preserves a
# real section called "JEWELRY MARKS", which a blanket JEWELRY strip would eat.
_HEAD_TOKENS = ["TRADEMARKS", "OF", "THE", "JEWELRY", "AND", "KINDRED", "TRADES"]

# The register's trade classes. OCR scrambles word order and welds "—Continued"
# onto the last word, so the raw header is matched to this list by token overlap
# rather than string equality — that turns "SILVERCONTINUED STERLING" into
# "STERLING SILVER" instead of leaving 37 spellings of 12 real sections.
_CANON_SECTIONS = [
    "STERLING SILVER", "SILVER PLATED WARE", "JEWELRY MARKS", "WATCH CASES",
    "AMERICAN WATCHES", "IMPORTED WATCHES", "GOLD FINGER RINGS",
    "PRECIOUS AND IMITATION STONES", "IMITATION DIAMONDS",
    "SOUVENIR SILVERWARE AND JEWELRY", "MEDALS EMBLEMS ETC",
    "PLATED CHAIN TAGS", "TORTOISE SHELL GOODS", "MATERIALS",
    # The register runs well past jewelry — the volume covers the "kindred
    # trades" too, and these classes are a real part of it.
    "JOBBERS MARKS", "OPTICAL GOODS", "AMERICAN CUT GLASS", "WATCH GLASSES",
    "FRENCH ART POTTERY", "ENGLISH ART POTTERY",
    "GERMAN AND AUSTRIAN ART POTTERY", "MISCELLANEOUS ART POTTERY",
    "ALPHABETICAL LIST", "MISCELLANEOUS LINES",
]
_CANON_SETS = [(s, set(s.split())) for s in _CANON_SECTIONS]


def page_section(page) -> str:
    """Best guess at the trade-class header printed near the top of the page.

    e.g. "PRECIOUS AND IMITATION STONES", "STERLING SILVER", "TORTOISE SHELL
    GOODS" — the trade class a mark was filed under, which is a useful facet.
    """
    top = [w for w in page["words"] if w[1] < page["h"] * 0.16]
    toks = []
    for w in sorted(top, key=lambda w: (w[1], w[0])):
        t = re.sub(r"[^A-Za-z-]", "", w[4]).upper().replace("-", "")
        t = re.sub(r"CONTINUE[DUA]*$", "", t)        # "SILVER—Continued." welds on
        if t and t != "ETC":
            toks.append(t)
    for h in _HEAD_TOKENS:
        if h in toks:
            toks.remove(h)
    got = set(toks)
    best, score = "", 0.0
    for name, want in _CANON_SETS:
        hit = len(got & want) / len(want)
        if hit > score:
            best, score = name, hit
    if score >= 0.6:
        return best
    s = re.sub(r"\s+", " ", " ".join(toks)).strip()
    return s if SECTION.match(s) else ""


# ------------------------------------------------------------ segment --------
def segment(img, words):
    """Split a page into MARK regions paired with the caption beneath each.

    Returns [{box, caption}] in reading order. `words` are in the image's pixel
    space.

    The method that works here is SUBTRACTION, not classification. An earlier
    version segmented all ink and then tried to guess which blobs were type —
    that turned every display word ("JEWELRY", "TORTOISE") into its own "mark"
    and still missed most real cuts. Since the IA djvu.xml gives exact word
    boxes, the reliable move is to erase text from the mask outright and keep
    what survives: the engraved cuts. Erasure is only used to FIND the cuts;
    every crop is taken from the untouched image, so marks with lettering
    inside them (L&N, KIPLING) lose nothing.
    """
    import cv2
    import numpy as np

    a = np.array(img.convert("L"))
    H, W = a.shape
    ink = cv2.adaptiveThreshold(a, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY_INV, 51, 15)

    # The page carries a printed border rule and section dividers. Long thin
    # runs of ink are rules, not marks — without removing them every region on
    # the page merges into one blob through the frame.
    k = max(60, W // 18)
    rules = cv2.bitwise_or(
        cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1))),
        cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k))))
    graphic = cv2.subtract(ink, cv2.dilate(rules, np.ones((5, 5), np.uint8)))

    # Erase every OCR word box. What is left is engraving.
    for (x0, y0, x1, y1, t) in words:
        if not t:
            continue
        pad = 3
        graphic[max(0, y0 - pad):min(H, y1 + pad), max(0, x0 - pad):min(W, x1 + pad)] = 0

    d = cv2.dilate(graphic, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)), iterations=2)
    n, _, stats, _ = cv2.connectedComponentsWithStats(d, 8)

    marks = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w < W * 0.04 or h < H * 0.015:                   # specks
            continue
        if w > W * 0.9 and h > H * 0.9:                     # the page frame
            continue
        # Anything hugging the paper edge is the border rule or the gutter
        # shadow, not a cut.
        if x < W * 0.04 or y < H * 0.03 or x + w > W * 0.96 or y + h > H * 0.97:
            continue
        if area < (w * h) * 0.06:                           # hollow — a stray rule
            continue
        marks.append([int(x), int(y), int(w), int(h)])

    # Caption lines: group the OCR words into rows, so a firm name, street and
    # city become three lines rather than twenty loose words. Words within a row
    # are re-sorted left-to-right — djvu order alone yields "LASSNER NORDLINGER &".
    rows = []
    for (x0, y0, x1, y1, t) in sorted(words, key=lambda w: (w[1], w[0])):
        if not t:
            continue
        for r in rows:
            if abs(r[1] - y0) < max(12, (y1 - y0) * 0.7) and x0 - r[2] < (y1 - y0) * 8:
                r[0] = min(r[0], x0); r[1] = min(r[1], y0)
                r[2] = max(r[2], x1); r[3] = max(r[3], y1)
                r[4].append((x0, t))
                break
        else:
            rows.append([x0, y0, x1, y1, [(x0, t)]])

    # Assign each caption row to the mark it belongs to — the nearest cut ABOVE
    # it that the row sits under. Doing it this direction is what fixes the
    # pairing: searching downward from each mark instead let one address block
    # attach itself to three different marks at once.
    owned = {i: [] for i in range(len(marks))}
    for r in rows:
        rx0, ry0, rx1, ry1, ws = r
        cx = (rx0 + rx1) / 2
        best, bestgap = None, None
        for i, (x, y, w, h) in enumerate(marks):
            if y + h > ry0 + (ry1 - ry0) * 0.5:          # not above this row
                continue
            if cx < x - w * 0.25 or cx > x + w * 1.25:   # not under this cut
                continue
            gap = ry0 - (y + h)
            if gap > max(h * 1.5, 220):
                continue
            if bestgap is None or gap < bestgap:
                best, bestgap = i, gap
        if best is not None:
            owned[best].append((ry0, " ".join(t for _, t in sorted(ws))))

    out = []
    for i, b in enumerate(marks):
        lines = [t for _, t in sorted(owned[i])]
        out.append({"box": b, "caption": re.sub(r"\s+", " ", " ".join(lines)).strip()})
    out.sort(key=lambda r: (r["box"][1], r["box"][0]))
    return out


def guess_maker(caption: str) -> str:
    """First caption line is the firm name; the rest is street/city."""
    if not caption:
        return ""
    stop = re.split(r"\s\d{1,4}[\- ]|\bNEW YORK\b|\bPROVIDENCE\b|\bCHICAGO\b|\bBOSTON\b",
                    caption, maxsplit=1, flags=re.I)[0]
    return stop.strip(" ,.*").strip()[:80]


# Cuts that carry lettering INSIDE them (a name engraved around an oval, say)
# leak that lettering into the caption, and OCR on engraved script is poor. So
# say plainly which captions are trustworthy instead of implying all are: a
# clean one reads like a firm — mostly capitals and spaces, few stray glyphs.
_FIRM = re.compile(r"^[A-Z][A-Z&.,'\- ]{3,}$")


def caption_conf(maker: str, caption: str) -> str:
    if not maker:
        return "none"
    junk = sum(1 for c in maker if not (c.isalnum() or c in " &.,'-"))
    if _FIRM.match(maker.strip()) and junk == 0:
        return "clean"
    if junk <= 2 and len(maker) >= 4:
        return "partial"
    return "garbled"


# --------------------------------------------------------------- build -------
def build(limit=None, survey=False):
    from PIL import Image
    import io

    idx = VIndex(INDEX_NAME)
    state = idx.load_state({"leaves_done": [], "count": 0, "sections": {}})
    if state.get("model"):
        idx.check_model(state)
    done = set(state["leaves_done"])

    pages = load_pages()
    reg = [p for p in pages if is_register_page(p)]
    print(f"{len(pages)} leaves, {len(reg)} register pages "
          f"(leaves {reg[0]['leaf']}-{reg[-1]['leaf']})" if reg else "no register pages found")
    if survey:
        for p in reg[:80]:
            print(f"  leaf {p['leaf']:>4}  words={len(p['words']):>4}  section={page_section(p)!r}")
        return

    crops = idx.dir / "crops"
    crops.mkdir(parents=True, exist_ok=True)
    todo = [p for p in reg if p["leaf"] not in done]
    if limit:
        todo = todo[:limit]

    for p in todo:
        leaf = p["leaf"]
        url = f"{IA_BASE}page/n{leaf}_w{PAGE_W}.jpg"
        try:
            img = Image.open(io.BytesIO(http_get(url, binary=True, timeout=120))).convert("RGB")
        except Exception as e:
            print(f"  leaf {leaf}: fetch failed ({e}) — skipped")
            continue
        sx = img.width / max(1, p["w"])
        sy = img.height / max(1, p["h"])
        words = [(int(a * sx), int(b * sy), int(c * sx), int(d * sy), t)
                 for (a, b, c, d, t) in p["words"]]
        section = page_section(p)

        found = segment(img, words)
        # The register opens with a prose title page that carries the running
        # head and so passes is_register_page(), but has no cuts at all — its
        # display type shatters into dozens of "marks". A real register page
        # holds well under 20 cuts, so an implausible count means prose.
        if len(found) > MAX_MARKS_PER_PAGE:
            print(f"  leaf {leaf:>4}  skipped — {len(found)} regions, reads as a text page")
            state["leaves_done"].append(leaf)
            idx.save_state(state)
            continue

        rows, imgs = [], []
        for j, r in enumerate(found):
            x, y, w, h = r["box"]
            pad = int(max(w, h) * 0.06)
            box = (max(0, x - pad), max(0, y - pad),
                   min(img.width, x + w + pad), min(img.height, y + h + pad))
            crop = img.crop(box)
            if crop.width < 40 or crop.height < 40:
                continue
            name = f"n{leaf}_{j:02d}.jpg"
            crop.save(crops / name, quality=92)
            mk = guess_maker(r["caption"])
            rows.append({
                "maker": mk,
                "caption": r["caption"],
                "caption_conf": caption_conf(mk, r["caption"]),
                "section": section,
                "leaf": leaf,
                "crop": name,
                "page_img": url,
                "source": SOURCE_CITE,
                "source_url": SOURCE_URL,
                "era": "pre-1896",
                # descriptor facets, left blank for hand-curation against the
                # 41-term AJU OUTLINE SHAPE vocabulary (kb/articles/makers-marks.md)
                "outline_shape": "",
                "symbol": "",
                "alpha": "",
                "country": "",
                "city": "",
            })
            imgs.append(crop)

        if imgs:
            emb = embed_images(imgs)
            idx.append(rows, emb)
            state["count"] += len(rows)
            state["sections"][section or "(unlabelled)"] = \
                state["sections"].get(section or "(unlabelled)", 0) + len(rows)
            idx.stamp(state, emb.shape[1])
        state["leaves_done"].append(leaf)
        idx.save_state(state)
        print(f"  leaf {leaf:>4}  marks={len(rows):>3}  total={state['count']:>5}  {section}")

    print(f"done — {state['count']} marks in kb/index/{INDEX_NAME}/")


def resection():
    """Re-canonicalise `section` on existing rows without rebuilding.

    The section facet is derived from noisy OCR of the running head, so the
    canonical list gets extended as new trade classes turn up. Rewriting meta in
    place is safe here because only a field changes — row order, and therefore
    alignment with emb.npy, is untouched.
    """
    idx = VIndex(INDEX_NAME)
    _, meta = idx.load()
    pages = {p["leaf"]: p for p in load_pages()}
    changed = 0
    counts = {}
    for m in meta:
        p = pages.get(m.get("leaf"))
        new = page_section(p) if p else m.get("section", "")
        if new != m.get("section"):
            m["section"] = new
            changed += 1
        counts[new or "(unlabelled)"] = counts.get(new or "(unlabelled)", 0) + 1
    idx.save_meta(meta)
    state = idx.load_state({})
    state["sections"] = counts
    idx.save_state(state)
    print(f"re-sectioned {changed} of {len(meta)} rows -> {len(counts)} distinct sections")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:14]:
        print(f"  {v:>4}  {k}")


def write_refs():
    idx = VIndex(INDEX_NAME)
    _, meta = idx.load()
    by = {}
    for m in meta:
        by.setdefault(m.get("section") or "(unlabelled)", []).append(m)
    out = [f"# Maker's marks — {len(meta)} cuts from the 1896 Jewelers' Circular register",
           "",
           f"Source: {SOURCE_CITE} — {SOURCE_URL}",
           "",
           "A LEAD, not a maker verdict. The register is trade-submitted, not a census,",
           "and its 1896 ceiling predates the mid-century houses common in estate lots.",
           "Run the market test in kb/articles/makers-marks.md before any name reaches a title.",
           "", "| section | marks | example makers |", "|---|---|---|"]
    for sec, rows in sorted(by.items(), key=lambda kv: -len(kv[1])):
        eg = ", ".join(dict.fromkeys(r["maker"] for r in rows if r.get("maker"))) or "—"
        out.append(f"| {sec} | {len(rows)} | {eg[:110]} |")
    p = idx.dir / "jewelry_mark_refs.md"
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote {p}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--survey", action="store_true", help="list register pages, build nothing")
    ap.add_argument("--limit", type=int, help="only process N new leaves")
    ap.add_argument("--refs", action="store_true", help="rewrite the human-readable table")
    ap.add_argument("--resection", action="store_true",
                    help="re-canonicalise the section facet in place, no rebuild")
    a = ap.parse_args()
    if a.resection:
        resection()
    elif a.refs:
        write_refs()
    else:
        build(limit=a.limit, survey=a.survey)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
