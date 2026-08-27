"""In-hand voice linter — buyer copy never speaks from behind the camera.

GH #40: the rule lives in prompts/draft.md ("In-hand voice"), and until now
its enforcement was whatever regex an agent typed that day — which missed 12
live listings on the first sweep. This is the linter, wired into
`validate_draft_for_sync()` so it runs on `--validate`, `--sync` and
`--review` with no eBay call.

The pattern and exemption sets were validated empirically against 131 drafts
and the 84-listing live sweep (2026-08-26/27); tune them HERE and port the
change back to issue #40, never ad hoc in a session. Two deliberate
refinements of the issue's list: the bare `odou?r` / `shake-test` patterns
block only as TESTS-NOT-RUN NARRATION ("odor not verified", "not
shake-tested") — a genuine defect disclosure ("slight musty odor") must
never be blocked, per the honesty ground rules — and four adjective-order /
"for these photographs" forms are added because the issue's own example
listings require them.

API:  check_voice(draft) -> list[str]
      findings look like  "voice (block): body: 'no tears noted on the shown surfaces'"
      Blocks fail sync; warns are advisory.

CLI:  python lib/voice_check.py <draft.md | shoot-dir> [...]
      python lib/voice_check.py --audit inventory/        # every **/draft.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    from draft_io import Draft, parse_draft
except ImportError:                       # imported as lib.voice_check
    from lib.draft_io import Draft, parse_draft  # type: ignore

# Everything after this marker in a body is internal, never buyer-visible.
END_MARKER = "END BUYER DESCRIPTION"

_B = [  # camera-frame confessions — BLOCK
    r"visible\s+in\s+(the\s+)?(photo|frame|any\s+photo|listing)",
    r"\bnot\s+(shown|pictured|photographed)\b",
    r"\b(shown|pictured|photographed)\s+(in|on|fully|honestly|close|exactly|as|through|base-)",
    r"\b(pages|frames|spreads|surfaces|areas|views|faces|sections|lot|car|piece)\s+(shown|photographed|pictured)\b",
    r"\b(shown|pictured|photographed)\s+(surfaces?|pages?|frames?|spreads?|areas?|views?|sides?|sections?)\b",
    r"\bin\s+the\s+(frames|photos|photographs|pictures)\b",
    r"\bas[-\s]shown\b",
    r"\bas\s+pictured\b",
    r"\bunshown\b",
    r"\bunphotographed\b",
    r"for\s+(these|the)\s+photo(graph)?s\b",
    r"from\s+(the\s+)?photo",
    r"assessable\s+from",
    r"\bnot\s+(verifiable|identifiable|assessable)\b",
    r"\bcannot\s+be\s+(verified|identified|assessed)\s+from\b",
    r"\bany\s+photo\b",
    r"\bany\s+frame\b",
    r"no\s+scale\s+(photograph|reference)",
    r"at\s+full\s+resolution",
    r"ruler\s+frame\s+was\s+shot",
    r"\b(not\s+)?shake[-\s]?tested?\b|\bshake\s+test\b",
    r"\bodou?r\b[^.!?\n]{0,24}\b(not\s+)?(verified|checked|tested|assessed)",
    r"\b(ring|sound)\s+test\s+(not\s+)?(performed|run|done)",
]
BLOCK = [re.compile(p, re.IGNORECASE) for p in _B]

_W = [  # softer normalizations — WARN, never block
    (r"\bno\s+[\w\s,/-]{0,40}?\bvisible\b",
     "prefer 'no X noted' over 'no X visible'"),
    (r"\bodou?r\b",
     "odor: fine as a defect disclosure, never as a test not run"),
]
WARN = [(re.compile(p, re.IGNORECASE), why) for p, why in _W]

_E = [  # sentence-level exemptions — correct copy that must NOT be flagged
    r"please\s+(see|review|read|study)",                     # the standing close
    r"\b(masked|redacted|blocked[-\s]?out|covered)\b",       # PII disclosure
    r"(through|while|inside)\s+(the\s+)?(sealed|plastic|poly|sleeve|bag|shrink)",
    r"while\s+sealed|\bsealed\s+(bag|sleeve|packaging|poly)",
    r"\buntested\b",                                         # grade-setter
    r"photography\s+by",                                     # the item's own content
    r"pictured\s+(and\s+named|include)",
    r"\(\s*(see\s+)?photo\s*\d*\s*\)",                       # bare photo pointer
]
EXEMPT = [re.compile(p, re.IGNORECASE) for p in _E]

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+|(?:^|\s)[-•*]\s+")


def _sentences(text: str):
    for s in _SENT_SPLIT.split(text or ""):
        s = (s or "").strip()
        if s:
            yield s


def _specific_values(node, prefix="item_specifics"):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _specific_values(v, f"{prefix}.{k}")
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _specific_values(v, prefix)
    elif node not in (None, ""):
        yield prefix, str(node)


def _buyer_fields(draft: Draft):
    """The positive list of buyer-visible text. Everything else is skipped —
    meta (esp. meta.notes, which legitimately records every can't-assess),
    photos, _field_constraints, and anything after the END marker."""
    yield "title", str(draft.get("title") or "")
    yield "condition_description", str(draft.get("condition_description") or "")
    yield from _specific_values(draft.get("item_specifics") or {})
    body = draft.body or ""
    if END_MARKER in body:
        body = body.split(END_MARKER, 1)[0]
    yield "body", body


def check_voice(draft: Draft) -> list[str]:
    """Lint the draft's buyer-visible fields. Returns findings, blocks first."""
    blocks, warns, seen = [], [], set()
    for field, text in _buyer_fields(draft):
        for sent in _sentences(text):
            if any(e.search(sent) for e in EXEMPT):
                continue
            snip = sent if len(sent) <= 90 else sent[:87] + "..."
            for rx in BLOCK:
                if rx.search(sent):
                    key = (field, snip)
                    if key not in seen:
                        seen.add(key)
                        blocks.append(f"voice (block): {field}: '{snip}'")
                    break
            else:
                for rx, why in WARN:
                    if rx.search(sent):
                        key = (field, snip)
                        if key not in seen:
                            seen.add(key)
                            warns.append(f"voice (warn): {field}: '{snip}' — {why}")
                        break
    return blocks + warns


def _iter_drafts(target: Path):
    if target.is_file():
        yield target
    else:
        yield from sorted(target.rglob("draft.md"))


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    audit = "--audit" in args
    targets = [Path(a) for a in args if not a.startswith("--")]
    if not targets:
        print(__doc__.strip().splitlines()[0])
        print("usage: voice_check.py <draft.md | dir> [--audit]")
        return 2

    flagged, total = [], 0
    for t in targets:
        for dp in _iter_drafts(t):
            total += 1
            try:
                findings = check_voice(parse_draft(dp))
            except Exception as e:                       # noqa: BLE001
                flagged.append((str(dp), f"parse error: {e}"))
                continue
            n_block = sum(1 for f in findings if f.startswith("voice (block)"))
            if findings:
                flagged.append((str(dp),
                                f"{n_block} block / {len(findings) - n_block} warn"))
                if not audit:                            # single-draft: full detail
                    for f in findings:
                        print(f"  {f}")
    try:
        from verdict import emit as verdict_emit
    except ImportError:
        from lib.verdict import emit as verdict_emit     # type: ignore
    verdict_emit("voice", total, flagged)
    return 1 if any("block" in r for _, r in flagged) else 0


if __name__ == "__main__":
    sys.exit(main())
