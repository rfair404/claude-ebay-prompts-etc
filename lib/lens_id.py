"""
lens_id — Google Lens reverse-image "second opinion" for IDENTIFY.

IDENTIFY makes its own visual call from the photos. This module gives it an
independent cross-check: it hosts the single best photo at a temporary public
URL, runs an Apify Google Lens actor on it, and returns a STRUCTURED opinion —
the visual-match titles plus a maker tally — that IDENTIFY weighs against its
own read. The split mirrors price_stats.py: this module owns the deterministic
half (host → search → tally convergence); the prompt owns the judgement (how
much to trust it).

Why a tally, not a single answer: Google Lens returns a noisy mix (for one
unmarked music box it surfaced Homco, Jamestown, Precious Moments, Lladro,
Armani…). Adopting any one of those would repeat the "confidently wrong"
failure. So this module counts how many DISTINCT visual matches mention each
known maker and reports the convergent signal — including the common, honest
verdict "no single maker (widely-copied / likely unmarked)".

Privacy + cost (READ THIS before wiring it to run on every shoot):
  - The photo is briefly uploaded to a public temp host (tmpfiles.org, which
    auto-expires within ~1h) and sent to Google via Apify. For product photos
    that is acceptable, but it IS an external send — gate its use (see
    prompts/identify.md: run only on unmarked, potentially-collectible items).
  - ~$0.02-0.05 per call on the Apify account. Not free. Don't run it on a $5
    generic or a lot of obvious commodities.

Network: needs direct egress to tmpfiles.org + api.apify.com. In a sandbox
whose proxy blocks api.apify.com, run this with egress enabled (the same way
lib/apify_ebay.py's CLI path needs network). The Lens actor can alternatively
be driven via the Apify MCP connector, but the image-HOSTING step always needs
direct egress.

Standard library only (urllib, json, re).

CLI:
    python lib/lens_id.py <image.jpg>
    python lib/lens_id.py <image.jpg> --json
    python lib/lens_id.py <image.jpg> --question "What maker is this figurine?"
"""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import get_apify_token, get_lens_actor  # noqa: E402

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Makers commonly seen on ceramic / figurine / music-box collectibles. Matched
# as lowercase substrings against Lens match titles. Extend freely — this is the
# vocabulary the convergence tally is built from, not an exhaustive registry.
KNOWN_MAKERS = [
    "homco", "home interiors", "lefton", "napcoware", "napco", "josef originals",
    "josef", "lladro", "hummel", "goebel", "schmid", "sankyo", "brinn", "otagiri",
    "precious moments", "enesco", "giuseppe armani", "armani", "capodimonte",
    "jasco", "jamestown", "royal copenhagen", "bing & grondahl", "roman inc",
    "fontanini", "cybis", "boehm", "florence ceramics", "ucagco", "arnart",
    "holt howard", "fitz and floyd", "fitz & floyd", "lenox", "department 56",
    "san francisco music box", "willitts", "chadwick", "melody in motion", "anri",
    "beswick", "royal doulton", "wales", "kreiss", "norcrest", "relco", "shafford",
]

# Country-of-origin hints (NOT makers — reported separately as provenance signal).
_ORIGIN_HINTS = ["occupied japan", "made in japan", "japan", "taiwan", "korea",
                 "west germany", "germany", "italy", "china", "hong kong"]

# Marketplace/source noise to ignore when harvesting "titles".
_SOURCE_NOISE = re.compile(r"^(ebay|etsy|mercari|poshmark|amazon|pinterest|reddit|"
                           r"facebook|whatnot|1stdibs|chairish|ruby lane|bonanza)",
                           re.IGNORECASE)


# ---------------------------------------------------------------------------
# 1) Host the image at a temporary public URL
# ---------------------------------------------------------------------------

def host_image_tmpfiles(image_path: str | Path, *, timeout: int = 120) -> str:
    """Upload a local image to tmpfiles.org; return the direct-download URL.

    tmpfiles auto-expires the file within ~1h (no delete token is issued), which
    satisfies "public briefly, not forever". Raises on upload failure.
    """
    data = Path(image_path).read_bytes()
    boundary = "----lensidboundary7e3f"
    pre = (f"--{boundary}\r\n"
           f'Content-Disposition: form-data; name="file"; filename="lens.jpg"\r\n'
           f"Content-Type: image/jpeg\r\n\r\n").encode()
    body = pre + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        "https://tmpfiles.org/api/v1/upload", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": _UA})
    resp = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    page_url = resp["data"]["url"]                     # https://tmpfiles.org/<id>/<name>
    return page_url.replace("tmpfiles.org/", "tmpfiles.org/dl/", 1)


# ---------------------------------------------------------------------------
# 2) Run the Lens actor on the public URL
# ---------------------------------------------------------------------------

_DEFAULT_QUESTION = ("What is the brand, manufacturer, or collectible product "
                     "line of this item? Identify the maker if you can; say so "
                     "plainly if it appears to be an unbranded/generic design.")


def run_lens(image_urls, *, token: Optional[str] = None,
             actor: Optional[str] = None, question: str = _DEFAULT_QUESTION,
             timeout: int = 300) -> list[dict]:
    """Run the Apify Google Lens actor on ONE OR MORE public image URLs.

    Pass several URLs in a single run — e.g. a full-form shot (design/visual
    match) AND an underside/back mark close-up (the decisive ID, where OCR can
    read a maker name outright). `ocr` is in searchTypes so a legible mark is
    read, not just matched. Returns the actor's dataset items (tally_opinion
    parses them)."""
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    token = token or get_apify_token()
    actor = (actor or get_lens_actor()).replace("/", "~")
    endpoint = (f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
                f"?token={token}")
    payload = {
        "searchTypes": ["all", "visual-match", "exact-match", "ocr", "ai-mode"],
        "imageUrls": [{"url": u} for u in image_urls],
        "aiModeQuestions": [question],
    }
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


# ---------------------------------------------------------------------------
# 3) Parse the noisy result into a convergence tally
# ---------------------------------------------------------------------------

def _harvest_titles(obj: Any, out: list[str], depth: int = 0) -> None:
    """Recursively collect human-readable 'title'/'name' strings from the result."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        for key in ("title", "name", "snippet"):
            v = obj.get(key)
            if isinstance(v, str) and len(v.strip()) > 3:
                out.append(v.strip())
        for v in obj.values():
            _harvest_titles(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _harvest_titles(v, out, depth + 1)


def _harvest_ai_answer(obj: Any, depth: int = 0) -> Optional[str]:
    """Find the longest AI-mode / overview text the actor returned, if any."""
    best: Optional[str] = None
    if depth > 8:
        return None
    if isinstance(obj, dict):
        for key, v in obj.items():
            kl = key.lower()
            if isinstance(v, str) and len(v) > 40 and any(
                    t in kl for t in ("answer", "ai", "overview", "summary", "response")):
                if best is None or len(v) > len(best):
                    best = v.strip()
            sub = _harvest_ai_answer(v, depth + 1)
            if sub and (best is None or len(sub) > len(best)):
                best = sub
    elif isinstance(obj, list):
        for v in obj:
            sub = _harvest_ai_answer(v, depth + 1)
            if sub and (best is None or len(sub) > len(best)):
                best = sub
    return best


def _harvest_ocr(obj: Any, out: list[str], depth: int = 0) -> None:
    """Collect OCR / extracted-text strings — i.e. what Lens READ off the image
    (a mark, a backstamp, a signature). A read maker name is near-decisive."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if isinstance(v, str) and v.strip() and (
                    "ocr" in kl or kl in ("text", "extractedtext", "fulltext",
                                          "plaintext", "words", "recognizedtext")):
                out.append(v.strip())
            _harvest_ocr(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _harvest_ocr(v, out, depth + 1)


def _clean_title(t: str) -> str:
    """Strip a leading marketplace name (e.g. 'eBayVintage Boy…' -> 'Vintage Boy…')."""
    return _SOURCE_NOISE.sub("", t).lstrip(" -|·").strip()


def tally_opinion(items: list[dict], *, makers: Optional[list[str]] = None) -> dict:
    """Turn raw Lens items into a structured, convergence-weighted opinion."""
    makers = makers or KNOWN_MAKERS
    raw: list[str] = []
    for it in items:
        _harvest_titles(it, raw)
    # de-dup titles (case-insensitive), clean source prefixes
    seen: set[str] = set()
    titles: list[str] = []
    for t in raw:
        c = _clean_title(t)
        k = c.lower()
        if c and k not in seen and not _SOURCE_NOISE.fullmatch(k or ""):
            seen.add(k)
            titles.append(c)

    # tally makers across DISTINCT titles (convergence, not a single guess)
    maker_hits: dict[str, list[str]] = {}
    for t in titles:
        tl = t.lower()
        for m in makers:
            if m in tl:
                maker_hits.setdefault(m, [])
                if len(maker_hits[m]) < 3:
                    maker_hits[m].append(t[:90])
    maker_candidates = sorted(
        ({"maker": m, "count": tl_count(titles, m), "examples": ex}
         for m, ex in maker_hits.items()),
        key=lambda x: x["count"], reverse=True)

    origin_hits = sorted({h for t in titles for h in _ORIGIN_HINTS if h in t.lower()})
    ai_answer = _harvest_ai_answer(items)

    # OCR — what Lens READ off the image(s). A maker name read from a mark/back-
    # stamp is near-decisive and outranks visual-match convergence.
    ocr_raw: list[str] = []
    for it in items:
        _harvest_ocr(it, ocr_raw)
    ocr_text = " | ".join(dict.fromkeys(s for s in ocr_raw if s))[:600]
    ocr_makers = sorted({m for m in makers if m in ocr_text.lower()})

    # The actor reports per-searchType failures as {"error": "No results found"}.
    # Surface them so an empty result is HONEST: Lens OCR routinely can't read a
    # low-contrast or embossed metal stamp (silver/pewter/buckle) — that's a Lens
    # limitation, not an absent mark. Don't let it read as "no mark exists".
    lens_errors = sorted({
        st for it in items if isinstance(it, dict)
        for st, v in it.items() if isinstance(v, dict)
        for rr in (v.get("results") or [])
        if isinstance(rr, dict) and rr.get("error")
    })

    n = len(titles)
    branded = sum(c["count"] for c in maker_candidates)
    top = maker_candidates[0] if maker_candidates else None
    # Verdict, in priority order: a read mark (OCR) > visual convergence >
    # split/no-convergence > markless. OCR wins because it's the actual mark,
    # not a look-alike.
    if ocr_makers:
        verdict = (f"MARK READ (OCR): a maker name appears in the image text "
                   f"({', '.join(m.title() for m in ocr_makers)}) — near-decisive "
                   f"if the reading is legible; confirm it on the photo")
    elif top and top["count"] >= 3 and top["count"] >= 0.4 * max(branded, 1) and (
            len(maker_candidates) == 1 or top["count"] >= 2 * maker_candidates[1]["count"]):
        verdict = (f"leans {top['maker'].title()} "
                   f"({top['count']}/{n} matches) — still confirm against a mark")
    elif n == 0 and lens_errors:
        verdict = ("Lens returned NO results (" + "/".join(lens_errors) + ") — "
                   "common for low-contrast or EMBOSSED METAL stamps (silver/"
                   "pewter/buckle/jewelry); Lens OCR can't read those. Rely on "
                   "your OWN close-read of the mark, not Lens, for metal stamps.")
    elif n == 0:
        verdict = "no usable matches (Lens returned nothing for the image(s))"
    elif branded <= max(1, n // 6):
        verdict = ("no single maker — widely-copied / likely unmarked "
                   f"(only {branded}/{n} matches name a maker)")
    else:
        names = ", ".join(c["maker"].title() for c in maker_candidates[:3])
        verdict = (f"split across makers ({names}); no convergence — "
                   f"treat as unmarked, use names as keywords only")

    return {
        "n_matches": n,
        "match_titles": titles[:25],
        "maker_candidates": maker_candidates,
        "origin_hints": origin_hits,
        "ai_answer": ai_answer,
        "ocr_text": ocr_text,
        "ocr_makers": ocr_makers,
        "lens_errors": lens_errors,
        "verdict": verdict,
    }


def tl_count(titles: list[str], maker: str) -> int:
    return sum(1 for t in titles if maker in t.lower())


# ---------------------------------------------------------------------------
# 4) Orchestrate: host -> search -> tally
# ---------------------------------------------------------------------------

def lens_opinion(images, *, token: Optional[str] = None,
                 actor: Optional[str] = None,
                 question: str = _DEFAULT_QUESTION) -> dict:
    """Full pipeline on ONE OR MORE photos. Pass the full-form shot AND, when a
    mark/stamp/label is visible, the underside/back close-up — IDENTIFY chooses
    which photos (see prompts/identify.md). Returns the opinion dict, or
    {"error": ...} on failure (never raises — IDENTIFY degrades to its own call)."""
    paths = [images] if isinstance(images, (str, Path)) else list(images)
    hosted: list[str] = []
    for p in paths:
        try:
            hosted.append(host_image_tmpfiles(p))
        except Exception as e:  # noqa: BLE001
            return {"error": f"host failed for {p}: {e}", "images": [str(x) for x in paths]}
    try:
        items = run_lens(hosted, token=token, actor=actor, question=question)
    except Exception as e:  # noqa: BLE001
        return {"error": f"lens run failed: {e}", "hosted_urls": hosted}
    report = tally_opinion(items)
    report["hosted_urls"] = hosted
    report["images"] = [str(p) for p in paths]
    return report


def render(report: dict) -> str:
    """Human-readable block IDENTIFY folds into its reasoning."""
    if report.get("error"):
        return f"Lens cross-check: UNAVAILABLE — {report['error']}"
    out = [f"Lens cross-check ({report['n_matches']} visual matches"
           f"{', images: ' + str(len(report.get('images', []))) if report.get('images') else ''}):",
           f"  Verdict: {report['verdict']}"]
    if report.get("ocr_text"):
        out.append(f"  Mark/OCR read: \"{report['ocr_text'][:140]}\""
                   + (f"  → maker: {', '.join(m.title() for m in report['ocr_makers'])}"
                      if report.get("ocr_makers") else ""))
    if report["maker_candidates"]:
        out.append("  Maker mentions across matches:")
        for c in report["maker_candidates"][:5]:
            out.append(f"    • {c['maker'].title()} ×{c['count']}  e.g. \"{(c['examples'] or [''])[0]}\"")
    if report["origin_hints"]:
        out.append(f"  Origin hints: {', '.join(report['origin_hints'])}")
    if report.get("ai_answer"):
        out.append(f"  Lens AI guess: {report['ai_answer'][:240]}")
    out.append("  Top match titles:")
    for t in report["match_titles"][:8]:
        out.append(f"    - {t}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass
    ap = argparse.ArgumentParser(description="Google Lens second opinion for IDENTIFY")
    ap.add_argument("image", nargs="+",
                    help="One or more photos: the full-form shot AND (when a mark "
                         "is visible) the underside/back/mark close-up")
    ap.add_argument("--actor", help="Override the Lens actor (default from config)")
    ap.add_argument("--question", default=_DEFAULT_QUESTION, help="AI-mode question")
    ap.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    args = ap.parse_args()
    report = lens_opinion(args.image, actor=args.actor, question=args.question)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(render(report))
    if report.get("error"):
        sys.exit(1)


if __name__ == "__main__":
    _cli()
