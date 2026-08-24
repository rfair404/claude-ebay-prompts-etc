#!/usr/bin/env python3
"""Seller STYLE STUDY — learn how a good seller lists, as technique, not text.

Point this at a public eBay seller, sample their ACTIVE listings through the
Browse API (`lib/ebay_browse.py`), and measure *how they list*: title slot
order, keyword budget, casing/separators, description voice and section order,
photo counts. The output is a **style guide** module under `styleguides/` that
DRAFT (titles + voice) and PREP (photo conventions) can load — off by default,
toggled on per listing or per batch.

Study-and-emulate, NOT copy. The guide carries measured statistics and
technique rules only. No title string, description sentence, or photo from the
studied seller is ever reused, paraphrased line-by-line, or emitted into one of
our listings. The raw sample is kept beside the guide purely so every claim in
the guide is traceable and re-derivable when the seller's style drifts — it is
a research artifact, never listing input.

  sample <seller>   Pull the seller's active listings (Browse) + per-item
                    detail (description, photo count, aspects) into a raw
                    study JSON.
  study  <seller>   Measure a raw study JSON into a stats block, write the
                    human-readable study artifact, and (with --guide) scaffold
                    the styleguides/<slug>.md module with the numbers filled in.

Honesty: Browse returns ACTIVE listings — asking prices and current style, not
realized sales. A style guide says how they *present*, never that it *works*.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
STUDY_DIR = ROOT / "styleguides" / "_studies"
GUIDE_DIR = ROOT / "styleguides"

# Browse rejects a seller filter on its own and takes ONE category per call, so
# "the whole store" is a union of single-category calls over the top-level
# categories a general estate/antiques seller actually lists in.
DEFAULT_CATEGORIES = [
    ("1", "Collectibles"),
    ("20081", "Antiques"),
    ("281", "Jewelry & Watches"),
    ("870", "Pottery & Glass"),
    ("220", "Toys & Hobbies"),
    ("11450", "Clothing, Shoes & Accessories"),
    ("11700", "Home & Garden"),
    ("267", "Books & Magazines"),
    ("550", "Art"),
    ("293", "Consumer Electronics"),
    ("14339", "Crafts"),
    ("625", "Cameras & Photo"),
    ("64482", "Sports Mem, Cards & Fan Shop"),
    ("11233", "Music"),
]

ERA_WORDS = {
    "vtg", "vintage", "antique", "retro", "mcm", "midcentury", "mid-century",
    "deco", "nouveau", "victorian", "edwardian", "georgian", "estate",
    "vtge", "old", "primitive", "atomic", "boho",
}
CONDITION_WORDS = {
    "mint", "nos", "nib", "mib", "new", "unused", "excellent", "exc", "euc",
    "nm", "vg", "good", "used", "worn", "damaged", "as-is", "repair", "chip",
    "crack", "restored", "clean",
}
MATERIAL_WORDS = {
    "brass", "copper", "bronze", "silver", "sterling", "gold", "pewter",
    "glass", "crystal", "porcelain", "ceramic", "stoneware", "pottery",
    "wood", "wooden", "oak", "walnut", "leather", "linen", "cotton", "wool",
    "iron", "cast", "tin", "enamel", "bakelite", "celluloid", "marble",
    "steel", "aluminum", "plastic", "paper", "cardboard",
}
DESCRIPTOR_WORDS = ERA_WORDS | CONDITION_WORDS | {
    "rare", "unique", "beautiful", "stunning", "gorgeous", "lovely", "nice",
    "large", "small", "mini", "tiny", "huge", "heavy", "ornate", "signed",
    "marked", "original", "authentic", "genuine", "handmade", "hand",
    "unusual", "scarce", "htf", "collectible", "collectable",
}
FIRST_PERSON = re.compile(r"\b(i|i'm|i've|my|me|we|we've|our|us)\b", re.I)
YEAR = re.compile(r"\b(1[6-9]\d{2}|20[0-2]\d)\b")
DECADE = re.compile(r"\b(1[6-9]\d0s|20[0-2]0s)\b")
MEASURE = re.compile(r"\d+\s?(?:\"|''|in\b|inch|cm|mm|lb|oz|g\b)|\d+/\d+", re.I)
SEPARATORS = ["-", "–", "—", "|", "/", ",", "~", "•", ":"]

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")


# --------------------------------------------------------------------------
# pure text helpers (unit-tested without network)
# --------------------------------------------------------------------------
def strip_html(html: str) -> str:
    """eBay descriptions are nested font/div soup — render to plain text with
    paragraph breaks preserved so paragraph counts mean something."""
    if not html:
        return ""
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    s = re.sub(r"(?i)<\s*br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n\n", s)
    s = re.sub(r"(?i)<\s*li[^>]*>", "\n• ", s)
    s = _TAG.sub(" ", s)
    for ent, ch in (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'),
                    ("&#39;", "'"), ("&lt;", "<"), ("&gt;", ">")):
        s = s.replace(ent, ch)
    s = _WS.sub(" ", s)
    s = re.sub(r"\n\s*\n\s*(\n\s*)+", "\n\n", s)
    return s.strip()


def tokens(title: str) -> list[str]:
    return [t for t in re.split(r"\s+", title.strip()) if t]


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def _stat(vals: list[float]) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": round(statistics.fmean(vals), 1),
        "median": round(statistics.median(vals), 1),
        "min": round(min(vals), 1),
        "max": round(max(vals), 1),
    }


def _slot(words: list[str], vocab: set[str]) -> int | None:
    """Index of the first token belonging to `vocab` — the slot the seller
    gives that idea in the 80-char title."""
    for i, w in enumerate(words):
        if re.sub(r"[^a-z\-']", "", w.lower()) in vocab:
            return i
    return None


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------
def title_stats(records: list[dict]) -> dict:
    titled = [r for r in records if r.get("title")]
    titles = [r["title"] for r in titled]
    n = len(titles)
    lens: list[float] = []
    lead: Counter = Counter()
    allcaps_share: list[float] = []
    slots: dict[str, list[int]] = {"era": [], "material": [], "condition": []}
    seps: Counter = Counter()
    has_year = has_era = has_measure = has_brand = 0
    desc_budget: list[float] = []
    word_freq: Counter = Counter()
    for rec, t in zip(titled, titles):
        words = tokens(t)
        lens.append(len(t))
        if words:
            lead[re.sub(r"[^A-Za-z0-9']", "", words[0]).lower() or words[0]] += 1
            allcaps_share.append(_pct(sum(1 for w in words if len(w) > 2 and w.isupper()), len(words)))
        low = [re.sub(r"[^a-z\-']", "", w.lower()) for w in words]
        word_freq.update(w for w in low if w and len(w) > 2)
        for name, vocab in (("era", ERA_WORDS), ("material", MATERIAL_WORDS),
                            ("condition", CONDITION_WORDS)):
            i = _slot(words, vocab)
            if i is not None:
                slots[name].append(i)
        for s in SEPARATORS:
            if s in t:
                seps[s] += 1
        has_year += bool(YEAR.search(t) or DECADE.search(t))
        has_era += any(w in ERA_WORDS for w in low)
        has_measure += bool(MEASURE.search(t))
        brand = (rec.get("brand") or "").strip().lower()
        if brand and brand not in {"unbranded", "does not apply", "none"} and brand.split()[0] in low:
            has_brand += 1
        desc_budget.append(sum(1 for w in low if w in DESCRIPTOR_WORDS))
    return {
        "n": n,
        "length": _stat(lens),
        "pct_at_or_near_80": _pct(sum(1 for x in lens if x >= 75), n),
        "pct_with_year_or_decade": _pct(has_year, n),
        "pct_with_era_word": _pct(has_era, n),
        "pct_with_measurement": _pct(has_measure, n),
        "pct_with_brand_in_title": _pct(has_brand, n),
        "descriptor_budget": _stat(desc_budget),
        "allcaps_token_pct": _stat(allcaps_share),
        "leading_tokens": lead.most_common(12),
        "separators": {k: _pct(v, n) for k, v in seps.most_common()},
        "mean_slot": {k: (round(statistics.fmean(v), 1) if v else None) for k, v in slots.items()},
        "slot_coverage_pct": {k: _pct(len(v), n) for k, v in slots.items()},
        "common_tokens": word_freq.most_common(25),
    }


# A labelled section header inside a description body: "CONDITION:", "Size:",
# "MAKER'S MARK:". The *set and order* of these labels is the seller's body
# skeleton — the most portable thing a style guide can carry.
SECTION_LABEL = re.compile(r"(?m)^\s*[*•\-]?\s*([A-Za-z][A-Za-z'&/ ]{2,24}?)\s*:")


def section_skeleton(bodies: list[str]) -> dict:
    """Which labelled sections a body uses, how often, and in what order."""
    freq: Counter = Counter()
    positions: dict[str, list[int]] = {}
    for b in bodies:
        labels = [m.group(1).strip().upper() for m in SECTION_LABEL.finditer(b)]
        seen_here = []
        for lab in labels:
            if lab in seen_here:
                continue
            seen_here.append(lab)
        for i, lab in enumerate(seen_here):
            freq[lab] += 1
            positions.setdefault(lab, []).append(i)
    n = len(bodies)
    rows = [
        {"label": lab, "pct": _pct(c, n),
         "mean_order": round(statistics.fmean(positions[lab]), 1)}
        for lab, c in freq.most_common(12)
    ]
    rows.sort(key=lambda r: r["mean_order"])
    return {"n": n, "sections": rows}


def body_stats(records: list[dict]) -> dict:
    bodies = [strip_html(r.get("description") or "") for r in records]
    bodies = [b for b in bodies if b]
    n = len(bodies)
    words: list[float] = []
    sents: list[float] = []
    paras: list[float] = []
    sent_len: list[float] = []
    fp = cond_first = bullets = allcaps = 0
    for b in bodies:
        letters = [c for c in b if c.isalpha()]
        allcaps += bool(letters) and sum(1 for c in letters if c.isupper()) / len(letters) > 0.8
        para = [p for p in b.split("\n\n") if p.strip()]
        ss = [s for s in re.split(r"(?<=[.!?])\s+", b) if s.strip()]
        words.append(len(b.split()))
        sents.append(len(ss))
        paras.append(len(para))
        sent_len.append(statistics.fmean([len(s.split()) for s in ss]) if ss else 0)
        fp += bool(FIRST_PERSON.search(b))
        bullets += "•" in b
        head = b[: max(200, len(b) // 3)].lower()
        if any(k in head for k in ("condition", "wear", "as-is", "flaw", "crack", "chip")):
            cond_first += 1
    return {
        "n": n,
        "words": _stat(words),
        "sentences": _stat(sents),
        "paragraphs": _stat(paras),
        "words_per_sentence": _stat(sent_len),
        "pct_first_person": _pct(fp, n),
        "pct_bulleted": _pct(bullets, n),
        "pct_all_caps_body": _pct(allcaps, n),
        "skeleton": section_skeleton(bodies),
        "pct_condition_in_first_third": _pct(cond_first, n),
    }


def photo_stats(records: list[dict]) -> dict:
    counts = [r["image_count"] for r in records if r.get("image_count")]
    return {
        "n": len(counts),
        "photos": _stat(counts),
        "mode": Counter(counts).most_common(5),
        "pct_12_or_more": _pct(sum(1 for c in counts if c >= 12), len(counts)),
        "pct_24": _pct(sum(1 for c in counts if c >= 24), len(counts)),
    }


# --------------------------------------------------------------------------
# photography LOOK — measured off the public gallery thumbnails
# --------------------------------------------------------------------------
def image_look(urls: list[str], limit: int = 40, verbose: bool = True) -> dict:
    """Measure the *look* of a seller's hero frames: background lightness and
    neutrality, how uniform the backdrop is (seamless vs styled surface),
    warm/cool cast, contrast, and roughly how much of the frame the subject
    fills. Thumbnails are fetched, measured, and discarded — nothing is stored.
    """
    try:
        from PIL import Image, ImageStat  # noqa: PLC0415
    except ImportError:
        return {"n": 0, "error": "Pillow not installed"}
    import io  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    bg_L, bg_sat, bg_uniform, warm, contrast, fill = [], [], [], [], [], []
    got = 0
    for u in urls[:limit]:
        try:
            with urllib.request.urlopen(u, timeout=20) as fh:
                im = Image.open(io.BytesIO(fh.read())).convert("RGB")
        except Exception as exc:
            if verbose:
                print(f"    image {u[:60]}: {exc}", file=sys.stderr)
            continue
        im = im.resize((160, 160))
        px = im.load()
        w = h = 160
        band = 12
        border = [px[x, y] for y in range(h) for x in range(w)
                  if x < band or x >= w - band or y < band or y >= h - band]
        bl = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in border]
        bg_L.append(statistics.fmean(bl))
        bg_sat.append(statistics.fmean([max(c) - min(c) for c in border]))
        bg_uniform.append(statistics.pstdev(bl))
        warm.append(statistics.fmean([r - b for r, g, b in border]))
        st = ImageStat.Stat(im.convert("L"))
        contrast.append(st.stddev[0])
        ref = statistics.fmean(bl)
        inner = [px[x, y] for y in range(band, h - band) for x in range(band, w - band)]
        il = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in inner]
        fill.append(_pct(sum(1 for v in il if abs(v - ref) > 28), len(il)))
        got += 1
    return {
        "n": got,
        "background_lightness_0_255": _stat(bg_L),
        "background_saturation": _stat(bg_sat),
        "background_uniformity_stdev": _stat(bg_uniform),
        "warm_cast_r_minus_b": _stat(warm),
        "frame_contrast_stdev": _stat(contrast),
        "subject_fill_pct": _stat(fill),
    }


def analyze(study: dict, images: int = 0) -> dict:
    recs = study.get("listings") or []
    cats = Counter((r.get("probe_category")
                    or (r.get("category_path") or "").strip("|").split("|")[0]
                    or "?") for r in recs)
    priced = [r["askingPrice"] for r in recs if r.get("askingPrice")]
    return {
        "seller": study.get("seller"),
        "sampled": len(recs),
        "with_detail": sum(1 for r in recs if r.get("description")),
        "categories": cats.most_common(),
        "asking_price": _stat(priced),
        "titles": title_stats(recs),
        "body": body_stats(recs),
        "photos": photo_stats(recs),
        "look": (image_look([r["thumbnail"] for r in recs if r.get("thumbnail")], limit=images)
                 if images else {"n": 0}),
    }


# --------------------------------------------------------------------------
# acquisition (Browse API only — see the module docstring)
# --------------------------------------------------------------------------
def sample_seller(seller: str, categories=None, per_category: int = 200,
                  detail_limit: int = 60, verbose: bool = True) -> dict:
    import ebay_browse
    import ebay_client as ec

    cats = categories or [c for c, _ in DEFAULT_CATEGORIES]
    names = dict(DEFAULT_CATEGORIES)
    seen: dict[str, dict] = {}
    per_cat: dict[str, dict] = {}
    for cid in cats:
        try:
            total, recs = ebay_browse.seller_active(seller, category_ids=cid, sample=per_category)
        except Exception as exc:  # a dead category shouldn't kill the sweep
            if verbose:
                print(f"  {cid:>6} {names.get(cid, '')}: ERROR {exc}", file=sys.stderr)
            continue
        per_cat[cid] = {"name": names.get(cid), "total": total, "sampled": len(recs)}
        if verbose and total:
            print(f"  {cid:>6} {names.get(cid, '?')}: {total} active, sampled {len(recs)}",
                  file=sys.stderr)
        for r in recs:
            if r["itemId"] not in seen:
                r["probe_category"] = names.get(cid, cid)
                seen[r["itemId"]] = r

    listings = list(seen.values())
    # Stratify the (expensive) detail fetch round-robin across categories, so a
    # store dominated by one department doesn't make the body/photo stats a
    # study of that department alone.
    by_cat: dict[str, list[dict]] = {}
    for r in listings:
        by_cat.setdefault(r.get("probe_category") or "?", []).append(r)
    order: list[dict] = []
    while any(by_cat.values()):
        for bucket in by_cat.values():
            if bucket:
                order.append(bucket.pop(0))
    detail_n = min(detail_limit, len(order))
    if verbose:
        print(f"  fetching detail for {detail_n}/{len(listings)} listings...", file=sys.stderr)
    for r in order[:detail_n]:
        try:
            d = ec.api_get(f"/buy/browse/v1/item/{r['itemId']}")
        except Exception as exc:
            if verbose:
                print(f"    detail {r['itemId']}: {exc}", file=sys.stderr)
            continue
        r["description"] = d.get("description")
        r["conditionDescription"] = d.get("conditionDescription")
        r["shortDescription"] = d.get("shortDescription")
        r["image_count"] = 1 + len(d.get("additionalImages") or [])
        r["brand"] = d.get("brand")
        r["category_path"] = d.get("categoryPath")
        r["item_created"] = d.get("itemCreationDate")
        r["aspects"] = {a.get("name"): a.get("value") for a in (d.get("localizedAspects") or [])}
    return {
        "seller": seller,
        "source": "eBay Browse API (active listings) - asking prices, not sales",
        "categories_probed": per_cat,
        "detail_fetched": detail_n,
        "listings": listings,
    }


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------
def _tbl(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"| {a} | {b} |" for a, b in rows)


def render_study(stats: dict) -> str:
    t, b, p = stats["titles"], stats["body"], stats["photos"]
    ap = stats["asking_price"]
    lead = ", ".join(f"`{w}` x{c}" for w, c in t["leading_tokens"][:8]) or "—"
    seps = ", ".join(f"`{k}` {v}%" for k, v in t["separators"].items()) or "—"
    toks = ", ".join(f"{w} x{c}" for w, c in t["common_tokens"][:20]) or "—"
    cats = ", ".join(f"{c} ({n})" for c, n in stats["categories"][:10]) or "—"
    lk = stats.get("look") or {"n": 0}
    if lk.get("n"):
        look_rows = "| Measure | Value |\n|---|---|\n" + _tbl([
            ("Background lightness (0–255)", f"mean {lk['background_lightness_0_255'].get('mean')} · range {lk['background_lightness_0_255'].get('min')}–{lk['background_lightness_0_255'].get('max')}"),
            ("Background saturation (0 = neutral grey)", f"mean {lk['background_saturation'].get('mean')}"),
            ("Background uniformity (stdev, low = seamless)", f"mean {lk['background_uniformity_stdev'].get('mean')}"),
            ("Warm cast (R−B, >0 = warm)", f"mean {lk['warm_cast_r_minus_b'].get('mean')}"),
            ("Frame contrast (stdev)", f"mean {lk['frame_contrast_stdev'].get('mean')}"),
            ("Subject fill (% of centre unlike the backdrop)", f"mean {lk['subject_fill_pct'].get('mean')} · median {lk['subject_fill_pct'].get('median')}"),
        ])
    else:
        look_rows = "_Not measured — re-run `study` with `--images N`._"
    return f"""# Style study — {stats['seller']}

_Measured artifact. Generated by `lib/seller_style.py` from the eBay Browse API
(ACTIVE listings — asking prices and current presentation, not realized sales).
This file exists so every claim in the style guide is traceable and
re-derivable when the seller's style drifts. It is research input, never
listing input._

- **Listings sampled:** {stats['sampled']} ({stats['with_detail']} with full detail)
- **Categories:** {cats}
- **Asking price:** median ${ap.get('median')} · range ${ap.get('min')}–${ap.get('max')}

## Titles (n={t['n']})

| Measure | Value |
|---|---|
{_tbl([
    ("Length (chars)", f"mean {t['length'].get('mean')} · median {t['length'].get('median')} · max {t['length'].get('max')}"),
    ("Pushed to the 80-char cap (>=75)", f"{t['pct_at_or_near_80']}%"),
    ("Carries a year or decade", f"{t['pct_with_year_or_decade']}%"),
    ("Carries an era word", f"{t['pct_with_era_word']}%"),
    ("Carries a measurement", f"{t['pct_with_measurement']}%"),
    ("Carries the brand aspect", f"{t['pct_with_brand_in_title']}%"),
    ("Descriptor budget (adjectives/title)", f"mean {t['descriptor_budget'].get('mean')} · max {t['descriptor_budget'].get('max')}"),
    ("ALL-CAPS tokens per title", f"mean {t['allcaps_token_pct'].get('mean')}%"),
    ("Mean slot — era word", str(t['mean_slot'].get('era'))),
    ("Mean slot — material", str(t['mean_slot'].get('material'))),
    ("Mean slot — condition", str(t['mean_slot'].get('condition'))),
    ("Slot coverage", ", ".join(f"{k} {v}%" for k, v in t['slot_coverage_pct'].items())),
])}

- **Leading token:** {lead}
- **Separators:** {seps}
- **Most common tokens:** {toks}

## Description body (n={b['n']})

| Measure | Value |
|---|---|
{_tbl([
    ("Words", f"mean {b['words'].get('mean')} · median {b['words'].get('median')} · range {b['words'].get('min')}–{b['words'].get('max')}"),
    ("Sentences", f"mean {b['sentences'].get('mean')}"),
    ("Words per sentence", f"mean {b['words_per_sentence'].get('mean')}"),
    ("Paragraphs", f"mean {b['paragraphs'].get('mean')}"),
    ("First person voice", f"{b['pct_first_person']}%"),
    ("Bulleted", f"{b['pct_bulleted']}%"),
    ("Body set in ALL CAPS", f"{b['pct_all_caps_body']}%"),
    ("Condition raised in the first third", f"{b['pct_condition_in_first_third']}%"),
])}

### Body skeleton (labelled sections, in the order they appear)

| Section label | Used in | Mean order |
|---|---|---|
{_tbl([(r['label'], f"{r['pct']}% | {r['mean_order']}") for r in b['skeleton']['sections']]) or '| — | — |'}

## Photos (n={p['n']})

| Measure | Value |
|---|---|
{_tbl([
    ("Photos per listing", f"mean {p['photos'].get('mean')} · median {p['photos'].get('median')} · range {p['photos'].get('min')}–{p['photos'].get('max')}"),
    ("Most common counts", ", ".join(f"{c} (x{k})" for c, k in p['mode'])),
    ("12 or more", f"{p['pct_12_or_more']}%"),
    ("24 (the cap)", f"{p['pct_24']}%"),
])}

## Photography look (n={lk['n']})

_Measured off the public gallery thumbnails — fetched, measured, discarded.
Reads the backdrop and framing, not the artistry: a human glance at the
storefront grid is still the check on these numbers._

{look_rows}
"""


def render_guide(stats: dict, slug: str) -> str:
    t, b, p = stats["titles"], stats["body"], stats["photos"]
    lead = ", ".join(f"`{w}`" for w, _ in t["leading_tokens"][:5]) or "—"
    seps = ", ".join(f"`{k}` ({v}%)" for k, v in list(t["separators"].items())[:4]) or "—"
    voice = "shop/personal voice" if b["pct_first_person"] >= 50 else "neutral catalog voice"
    lk = stats.get("look") or {"n": 0}
    if lk.get("n"):
        L = lk["background_lightness_0_255"].get("mean")
        sat = lk["background_saturation"].get("mean")
        uni = lk["background_uniformity_stdev"].get("mean")
        fill = lk["subject_fill_pct"].get("mean")
        cast = lk["warm_cast_r_minus_b"].get("mean")
        tone = "bright white" if L >= 200 else "light neutral" if L >= 150 else "mid-tone" if L >= 90 else "dark"
        even = "seamless/even" if uni <= 20 else "a surface with visible texture" if uni <= 45 else "a styled/varied set"
        backdrop = (f"{tone} ({L}/255), {'near-neutral' if sat <= 20 else 'colour-tinted'} "
                    f"(sat {sat}), {even} (uniformity {uni}). Shoot to that, consistently.")
        fill_note = ("tight crop, the item owns the frame" if fill and fill >= 55
                     else "moderate crop, some breathing room" if fill and fill >= 35
                     else "loose crop with generous margin")
        contrast = lk["frame_contrast_stdev"].get("mean")
        cast = f"{'warm' if cast and cast > 6 else 'cool' if cast and cast < -6 else 'neutral'} (R−B {cast})"
    else:
        backdrop = fill_note = "_not measured — re-run `study --images N`_"
        fill = cast = contrast = "—"
    skeleton = " -> ".join(f"{r['label']} ({r['pct']}%)"
                           for r in b["skeleton"]["sections"]) or "no labelled sections"
    return f"""# {stats['seller']} — seller style guide

```yaml
seller: {stats['seller']}
slug: {slug}
version: 1
status: draft            # draft | active
default: off             # style guides are OFF unless a run turns one on
sample: {stats['sampled']} active listings ({stats['with_detail']} with full detail)
source: eBay Browse API (active listings)
study: _studies/{slug}.md
```

**Study, not copy.** Everything below is technique measured from a sample —
slot order, budgets, voice, photo conventions. No title string, sentence, or
photo of theirs is reused or paraphrased into ours. **House rules win**: the
honesty bar, the no-sensationalizing-wear rule, PII redaction, and the
maker-attribution discipline in [`../prompts/_shared.md`](../prompts/_shared.md)
override anything here. A style guide changes *how we say it*, never *what we
are willing to claim*.

## Turn it on

Per run: "use the {slug} style guide". Per batch: set `style_guide: {slug}` in
the batch config. Off by default.

## DRAFT — titles

- Write to the cap: their titles run ~{t['length'].get('median')} chars median,
  {t['pct_at_or_near_80']}% pushed to >=75. A short title is a wasted title.
- **Lead token** is usually one of: {lead}.
- **Slot order** (mean position, 0 = first word): era {t['mean_slot'].get('era')},
  material {t['mean_slot'].get('material')}, condition {t['mean_slot'].get('condition')}.
- **Era/date:** {t['pct_with_era_word']}% carry an era word,
  {t['pct_with_year_or_decade']}% a year or decade. Date it when we can support the date.
- **Measurement:** {t['pct_with_measurement']}% carry one — size earns a slot.
- **Brand:** {t['pct_with_brand_in_title']}% put the brand aspect in the title.
- **Descriptor budget:** ~{t['descriptor_budget'].get('mean')} adjectives, max
  {t['descriptor_budget'].get('max')}. Past that it reads as keyword soup — cut.
- **Casing:** ~{t['allcaps_token_pct'].get('mean')}% of tokens ALL-CAPS (emphasis
  on one or two words, not the whole title). **Separators:** {seps}.

## DRAFT — description voice

- Length: ~{b['words'].get('median')} words median, ~{b['paragraphs'].get('mean')}
  paragraphs, ~{b['words_per_sentence'].get('mean')} words per sentence.
- Voice: first person in {b['pct_first_person']}% of bodies — {voice}.
- Structure: {b['pct_bulleted']}% use bullets;
  {b['pct_condition_in_first_third']}% raise condition in the first third.
- **Body skeleton** — the labelled sections they use, in order:
  {skeleton}. Where a section is near-universal, treat it as required: a body
  missing it reads as a thinner listing than theirs.
- Casing: {b['pct_all_caps_body']}% of bodies are set in ALL CAPS. Copy the
  *structure*, not the shouting — house style stays sentence case.
- Condition phrasing: match their *plainness*, never their drama. Our
  no-sensationalizing rule stands — expected age reads as one neutral clause.

## PREP — photography

- **Count:** median {p['photos'].get('median')} photos,
  {p['pct_12_or_more']}% at 12+, {p['pct_24']}% at the 24 cap. Shoot to that count.
- **Backdrop:** {backdrop}
- **Framing:** subject fills ~{fill}% of the frame ({fill_note}).
- **Colour:** {cast} cast, frame contrast {contrast}. Match their cast rather
  than pushing saturation to flatter an item.
- Anything the numbers cannot see (props, scale objects, shot order, hero choice)
  belongs here only after a human glance at the storefront grid. Leave a line
  blank rather than guessing.

## Conflicts

| Their pattern | Our rule | Resolution |
|---|---|---|
| Superlatives / drama in titles | no-sensationalizing, claim bar | our rule wins — drop the adjective |
| Attribution stated flat | `[BEST-CASE]` + verify for value-swing calls | our rule wins |
| Anything about a mailing label / PII | redaction + disclosure | our rule wins |

## Provenance

Measured {stats['sampled']} active listings via the Browse API. Numbers and
their derivation: [`_studies/{slug}.md`](_studies/{slug}.md), raw sample
`_studies/{slug}.json`. Re-run `python lib/seller_style.py sample {stats['seller']}`
then `study {stats['seller']} --guide` to refresh when their style drifts.
"""


# --------------------------------------------------------------------------
def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="pull a seller's active listings + detail into a study JSON")
    s.add_argument("seller")
    s.add_argument("--categories", default=None,
                   help="comma-separated category ids (default: the general sweep)")
    s.add_argument("--per-category", type=int, default=200)
    s.add_argument("--details", type=int, default=60,
                   help="how many listings to fetch full detail for")
    s.add_argument("--out", default=None)

    st = sub.add_parser("study", help="measure a study JSON into the study artifact + guide")
    st.add_argument("seller")
    st.add_argument("--from", dest="src", default=None)
    st.add_argument("--out", default=None)
    st.add_argument("--guide", action="store_true", help="also scaffold styleguides/<slug>.md")
    st.add_argument("--force", action="store_true", help="overwrite an existing guide")
    st.add_argument("--json", default=None, help="write the stats block as JSON too")
    st.add_argument("--images", type=int, default=0,
                    help="measure the LOOK of N gallery thumbnails (background, cast, fill)")

    a = ap.parse_args()
    slug = slugify(a.seller)
    STUDY_DIR.mkdir(parents=True, exist_ok=True)

    if a.cmd == "sample":
        study = sample_seller(a.seller,
                              categories=(a.categories.split(",") if a.categories else None),
                              per_category=a.per_category, detail_limit=a.details)
        out = Path(a.out) if a.out else STUDY_DIR / f"{slug}.json"
        out.write_text(json.dumps(study, indent=2), encoding="utf-8")
        print(f"{len(study['listings'])} listings -> {out}")
        return

    src = Path(a.src) if a.src else STUDY_DIR / f"{slug}.json"
    if not src.exists():
        sys.exit(f"no study sample at {src} — run `sample {a.seller}` first")
    stats = analyze(json.loads(src.read_text(encoding="utf-8")), images=a.images)
    out = Path(a.out) if a.out else STUDY_DIR / f"{slug}.md"
    out.write_text(render_study(stats), encoding="utf-8")
    print(f"study -> {out}")
    if a.json:
        Path(a.json).write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print(f"stats -> {a.json}")
    if a.guide:
        GUIDE_DIR.mkdir(parents=True, exist_ok=True)
        g = GUIDE_DIR / f"{slug}.md"
        if g.exists() and not a.force:
            print(f"guide {g} exists — pass --force to overwrite (hand edits will be lost)")
        else:
            g.write_text(render_guide(stats, slug), encoding="utf-8")
            print(f"guide -> {g}  (status: draft — fill the photography section by eye, "
                  f"then flip status to active and add it to styleguides/README.md)")


if __name__ == "__main__":
    main()
