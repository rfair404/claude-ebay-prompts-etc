#!/usr/bin/env python3
"""In-hand voice linter — enforce the house rule in code, not in a grep.

THE RULE (prompts/draft.md, adopted 2026-08-26). Buyer-visible copy speaks as
a seller holding the item, never from behind the camera. Banned:

  * camera framing        "visible in the photos", "shown/pictured", "as-shown",
                          "photographed surfaces", "in the frames shown"
  * photo-limit confession "not verifiable from the photos", "not assessable
                          from photos", "measured off the photographs"
  * inspection narration  tests that were never run — "not shake-tested",
                          "odor not verified", "no ruler frame was shot"

The fix is always the same: **state the finding, never the method.**
"no chips visible" -> "No chips noted." "Knife handle seated tight in photos;
not shake-tested" -> "Knife handle sits tight."

----- Why this file exists -----

The rule lived only in prose, so enforcement was whatever regex somebody typed
that day. On the 2026-08 sweep that regex matched `shown in` and `as shown` but
not bare `shown`, and silently missed 12 LIVE listings — "in the frames shown",
"on the shown surfaces", "any soiling not shown", "between shown spreads". They
were found days later by a second, wider pass. See GH #40.

Two things make a linter like this useful rather than ignorable:

1. **Field scoping.** Only title / condition_description / item_specifics /
   body are buyer-visible. `meta.notes` legitimately records every can't-assess
   observation, and the internal record is REQUIRED to keep doing so. Scanning
   whole files makes ~90% of hits internal noise. We parse the draft and read
   the named fields, so internal state is structurally out of reach.

2. **Exemptions.** A checker that cries wolf gets muted. These are correct and
   must never be flagged: the standing "Please see the photos" close line; PII
   redaction disclosures ("name and street masked in the photos"), which
   another house rule REQUIRES; sealed-item limits phrased physically ("cannot
   be assessed through the sealed bag"); grade-setting "Untested; sold as-is";
   the item's own printed content ("every player pictured and named", picture
   *frames* on art listings); and ordinary in-hand uses of the same words
   ("shows wear", "visible under magnification", "staples visible in gutter").

CLI:
    python lib/voice_check.py <draft.md|shoot-dir>     # check one
    python lib/voice_check.py --audit inventory/       # sweep a tree
    python lib/voice_check.py --audit inventory/ --warnings   # include nits

Exit code is 1 if any BLOCKing finding was made, else 0.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from draft_io import Draft, parse_draft  # noqa: E402

BLOCK = "block"
WARN = "warn"

# Everything after this marker in a body is an internal working note.
_END_BUYER = re.compile(r"END BUYER DESCRIPTION", re.I)

# --- The banned set -----------------------------------------------------
# (pattern, severity, human fix). Ordered roughly by how often each fired
# across the 131-draft sweep. `shown` MUST be matched bare — that gap is the
# whole reason this file exists.
_RULES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"visible\s+in\s+(the\s+)?(photo|frame|picture|listing|any\s+photo)\w*", re.I),
     BLOCK, 'camera framing — drop it: "no chips visible in the photos" -> "No chips noted."'),
    (re.compile(r"\bnot\s+(shown|pictured|photographed)\b", re.I),
     BLOCK, "photo-coverage confession — delete it, or state the physical limit "
            '("the foot rim sits beneath the label")'),
    (re.compile(r"\b(shown|pictured|photographed)\s+"
                r"(in|on|fully|honestly|close|exactly|as|through|base-on|un-)", re.I),
     BLOCK, 'camera framing — delete the clause; the finding itself stays'),
    # Both word orders. "surfaces shown" AND "the shown surfaces" — missing the
    # adjectival form is exactly the bug this linter exists to prevent, and the
    # first draft of this file had it. Caught by tests/test_voice_check.py.
    (re.compile(r"\b(pages?|frames?|spreads?|surfaces?|areas?|views?|faces?|sections?|"
                r"lot|car|pieces?)\s+(shown|photographed|pictured)\b", re.I),
     BLOCK, 'drop the camera scope: "no tears noted on the surfaces shown" -> "no tears noted"'),
    (re.compile(r"\b(shown|photographed|pictured)\s+"
                r"(pages?|frames?|spreads?|surfaces?|areas?|views?|faces?|sections?|pieces?)\b", re.I),
     BLOCK, 'drop the camera scope: "no tears noted on the shown surfaces" -> "no tears noted"'),
    # `frames` PLURAL only. "seated in the frame" (a tie bar) and "no glass in
    # the frame" (a painting) are physical objects, and flagging them is how a
    # linter loses its audience.
    (re.compile(r"\bin\s+the\s+(frames|photos?|photographs?|pictures?)\b", re.I),
     BLOCK, "camera framing — delete"),
    (re.compile(r"\bas[-\s](shown|pictured|photographed)\b", re.I),
     BLOCK, 'delete, or "Sold as-is." where it genuinely sets the grade'),
    (re.compile(r"\bunshown\b|\bunphotographed\b", re.I),
     BLOCK, 'replace with an in-hand limit: "Not collated page by page."'),
    # NB: `photo(graph)?s?` — an earlier `photograph?s?` silently failed to
    # match the commonest form of all, "from photos".
    (re.compile(r"\b(from|off)\s+(the\s+)?photo(graph)?s?\b", re.I),
     BLOCK, 'photo-limit confession — state the estimate plainly ("approximately 3/8 inch")'),
    # Trailing form: "the top face is photographed", "all five undersides are
    # photographed". "photographed" is unambiguously about the camera, so it
    # blocks; "is shown" can legitimately describe what an item DEPICTS
    # ("Elina is shown with butterfly wings"), so it only warns.
    (re.compile(r"\b(is|are|was|were)\s+photographed\b", re.I),
     BLOCK, "camera narration — delete; the finding stands on its own"),
    (re.compile(r"\b(is|are|was|were)\s+(shown|pictured)\b(?!\s+(with|wearing|in\s+a))", re.I),
     WARN, "if this describes the listing photos, delete it; if it describes what "
           "the item depicts, it is fine"),
    (re.compile(r"\bnot\s+(verifiable|identifiable|assessable)\b", re.I),
     BLOCK, 'use "Not identified:" / "not individually verified" instead'),
    (re.compile(r"assessable\s+from\b", re.I),
     BLOCK, "photo-limit confession — delete or rephrase as a physical limit"),
    (re.compile(r"\bany\s+(photo|frame)\b", re.I),
     BLOCK, 'camera framing — "no hairlines visible in any photo" -> "no hairlines noted"'),
    (re.compile(r"no\s+scale\s+(photograph|reference)|ruler\s+frame\s+was\s+shot", re.I),
     BLOCK, "camera narration — just give the approximate measurement"),
    (re.compile(r"at\s+full\s+resolution", re.I),
     BLOCK, 'narrates inspecting the photo — "the underside was examined."'),
    (re.compile(r"\bshake.?test\w*", re.I),
     BLOCK, 'test-not-run narration — "Knife handle sits tight."'),
    (re.compile(r"\bodou?rs?\b", re.I),
     BLOCK, "odor was never testable from photos — delete the clause entirely "
            "(it belongs in meta.notes / NEEDS_REVIEW)"),
    # Only the NOT-done form. "I checked both backs under raking light" is an
    # inspection that actually happened, described in-hand — exactly the voice
    # the rule wants, and an earlier bare `raking.light` match flagged it.
    (re.compile(r"\b(not|no|never)\b[^.]{0,25}"
                r"\b(raking[-\s]?light|ring[-\s]?test\w*|ring/?tap|ring/?backlight|backlight\s+test)\b", re.I),
     BLOCK, "test-not-run narration — delete; keep only the hedge it supports"),
    (re.compile(r"\b(raking[-\s]?light|ring[-\s]?tap|ring[-\s]?test\w*)\b[^.]{0,40}"
                r"\b(recommended|not\s+(performed|run|done|possible))\b", re.I),
     BLOCK, "test-not-run narration — delete the recommendation, state the finding"),
    # --- softer normalizations: true statements, wrong idiom ---
    (re.compile(r"\bno\s+[a-z][a-z ,/–-]{2,40}?\s+visible\b", re.I),
     WARN, 'prefer "noted" over "visible": "no monogram visible" -> "no monogram noted"'),
    (re.compile(r"\(no\s+caliper\)|not\s+measured\s+with\s+calipers", re.I),
     WARN, 'drop the tool narration — "approximately" already carries the estimate'),
]

# --- The exemption set --------------------------------------------------
# Checked against a window around each match. Every one of these is live in
# production copy and is CORRECT; flagging them is how a linter gets ignored.
_EXEMPT = re.compile(r"""
      please\s+(see|review|read|use|study|confirm|ask)      # standing close line
    | you\s+can\s+see[^.]{0,40}(photo|picture)              # same family: a pointer
    | (see|shown\s+in)\s+(photo|picture)\s*\d               # bare pointer "(photo 4)"
    | (blocked\s+out|covered|redacted|masked|obscured|left\s+visible)
      [^.]{0,70}(privacy|photos)                            # PII disclosure (required)
    | privacy[^.]{0,50}(photos|listing)
    # Sealed-item limits are PHYSICAL, and survive. But the exemption must be
    # about an assessment, not merely near the word "sealed" — a blanket
    # \bsealed\b let "Photographed through the packaging" pass because the
    # preceding sentence happened to say "sealed poly bag".
    | (cannot|can\s?not|can't|could\s+not|not)\s+(be\s+)?
      (assessed|assessable|verified|inspected|judged|determined|ruled\s+out)
      [^.]{0,60}(sealed|through\s+the\s+(bag|plastic|packaging|sleeve|wrap))
    | (assessed|assessable|judged|seen|verified)\s+(while\s+sealed|through\s+the\s+
      (bag|plastic|packaging|sleeve|wrap))
    | while\s+(the\s+\w+\s+)?(is\s+)?sealed
    | not\s+(opened|unwrapped|removed\s+from)
    | untested;\s*sold\s+as-is                              # grade-setting
    | pictured\s+and\s+named | merchandise\s+pictured | styles\s+pictured
    | photography\s+by | photographic\s+(cover|plate) | \(photographs?\)
    | camera'?s\s+own | own\s+EXIF                          # the item IS a camera
    | under\s+magnification | in\s+gutter | when\s+worn     # in-hand observations
    | at\s+raking\s+angles
""", re.I | re.X)

_WINDOW = 80


@dataclass
class Finding:
    field: str
    severity: str
    phrase: str
    fix: str
    context: str

    def __str__(self) -> str:
        tag = "BLOCK" if self.severity == BLOCK else "warn "
        return f"[{tag}] {self.field}: {self.phrase!r} — {self.fix}\n          …{self.context}…"


def _buyer_visible(draft: Draft) -> list[tuple[str, str]]:
    """The four buyer-visible surfaces, and nothing else.

    Reading named fields (rather than scanning lines) is what keeps
    `meta.notes` — which is SUPPOSED to record every can't-assess — out of
    scope structurally instead of by regex luck.
    """
    out: list[tuple[str, str]] = []
    title = draft.get("title")
    if title:
        out.append(("title", str(title)))
    cond = draft.get("condition_description")
    if cond:
        out.append(("condition_description", str(cond)))

    specifics = draft.frontmatter.get("item_specifics")
    if isinstance(specifics, dict):
        def walk(node, prefix):
            for k, v in node.items():
                if isinstance(v, dict):
                    walk(v, f"{prefix}.{k}")
                elif v:
                    out.append((f"{prefix}.{k}", str(v)))
        walk(specifics, "item_specifics")

    body = draft.body or ""
    cut = _END_BUYER.search(body)
    if cut:
        body = body[:cut.start()]
    if body.strip():
        out.append(("body", body))
    return out


def scan_text(field: str, text: str) -> list[Finding]:
    found: list[Finding] = []
    for pat, sev, fix in _RULES:
        for m in pat.finditer(text):
            a = max(0, m.start() - _WINDOW)
            b = min(len(text), m.end() + _WINDOW)
            window = text[a:b].replace("\n", " ")
            if _EXEMPT.search(window):
                continue
            found.append(Finding(field=field, severity=sev, phrase=m.group(0),
                                 fix=fix, context=" ".join(window.split())))
    return found


def check_voice_detailed(draft: Draft) -> list[Finding]:
    """Every finding, blocking and soft, across all buyer-visible fields."""
    out: list[Finding] = []
    for field, text in _buyer_visible(draft):
        out.extend(scan_text(field, text))
    # de-dupe: condition_description is usually mirrored into the body
    seen, uniq = set(), []
    for f in out:
        key = (f.field, f.phrase.lower(), f.context[:60])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(f)
    return uniq


def check_voice(draft: Draft) -> list[str]:
    """Blocking issues only, as strings — the shape validate_draft_for_sync wants."""
    return [str(f) for f in check_voice_detailed(draft) if f.severity == BLOCK]


def _iter_drafts(target: Path):
    if target.is_dir():
        yield from sorted(target.rglob("draft.md"))
    else:
        yield target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lint buyer-visible copy for camera-frame language.")
    ap.add_argument("target", nargs="?", help="a draft.md, or a shoot dir")
    ap.add_argument("--audit", metavar="DIR", help="walk a tree and report every draft")
    ap.add_argument("--warnings", action="store_true", help="include soft normalizations")
    args = ap.parse_args(argv)

    root = Path(args.audit) if args.audit else (Path(args.target) if args.target else None)
    if root is None:
        ap.error("give a draft.md / dir, or --audit DIR")
    if root.is_dir() and (root / "draft.md").exists() and not args.audit:
        root = root / "draft.md"

    files = dirty = blocking = 0
    for path in _iter_drafts(root):
        if ".prior-run-bak" in str(path):
            continue
        files += 1
        try:
            draft = parse_draft(path)
        except Exception as e:
            print(f"\n{path}: parse error — {e}")
            continue
        findings = check_voice_detailed(draft)
        if not args.warnings:
            findings = [f for f in findings if f.severity == BLOCK]
        if findings:
            dirty += 1
            blocking += sum(1 for f in findings if f.severity == BLOCK)
            print(f"\n{path}")
            for f in findings:
                print(f"  {f}")

    print(f"\n{files} draft(s) checked, {dirty} with findings, {blocking} blocking.")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
