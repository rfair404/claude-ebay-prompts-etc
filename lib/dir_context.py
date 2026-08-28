"""GH #46 — directory-level `context.txt`: one estate summary every item
under it inherits.

Items don't arrive one at a time — they arrive as an estate. A parent
directory may carry a `context.txt`, a short plain-language summary of the
source the items under it came from (household, era, storage conditions,
what it cost). This module is the CONSUMER side: walk up from an item
directory to the inventory root, parse every `context.txt` on the way, and
merge them nearest-wins into one struct a stage can read.

`inventory/` is gitignored (real content is operator-local and often names
a person), so the fixtures under `tests/fixtures/dir_context/inventory/`
stand in for the real tree; the parsing rules below were written against
the three shapes already on disk in the real repo (per the issue): empty,
prose-only, and `key:` lines + prose.

File shape (all three already exist for real in `inventory/`):

    source: [private], estate, Greensboro NC house
    acquired: 2026-06
    environment: smoker -- indoors, occasional. no pets.
    storage: attic and closets, unclimatized; decades in place.
    era: household accumulated ~1955-2010
    cost: FREE

    Someone in the house smoked indoors, so nothing from this house can be
    sold as coming from a smoke-free home.

Only a fixed set of recognized `key:` names are read as structured fields
(KNOWN_KEYS below); a line shaped like `Word: rest` that isn't one of them
is not an error and is not treated specially -- it just becomes part of the
prose, same as any other line a human wrote. This is deliberately dumb: it
avoids the parser eating a prose sentence that happens to contain a colon
("Note: bought at auction.") by guessing it's structured data.

`cost:` is kept a plain string, never coerced to a number -- "FREE" and
"spent $650" are both real values seen in the tree, and this module has no
opinion on margin math.

PII guardrail (from the issue, enforced here, not left to callers):
`source:` may name a person for local reference only. Nothing in this
module ever includes `source` in `.public_summary`, `str(ctx)`, or
`repr(ctx)` -- the three places a caller reaching for "something to print
or hand to buyer-facing copy" would naturally grab. A caller that
genuinely needs the raw value (e.g. an operator-local debug print) reads
`.source` directly, or runs this file's CLI with the explicit `--source`
flag -- both are opt-in, not the default view. This module does not scrub
free text for names; the guardrail is specifically about the `source:`
field, and prose is passed through as the operator wrote it.

API:
    load_context(item_dir, root=None) -> DirContext
    forbidden_claims(ctx) -> list[ForbiddenClaim]

CLI:
    python lib/dir_context.py <item-dir>              # public view
    python lib/dir_context.py <item-dir> --source      # + local-only source
    python lib/dir_context.py <item-dir> --json
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

CONTEXT_FILENAME = "context.txt"

# The structured fields this module knows how to read. Order matters only
# for display. `kind` has no defined meaning in the issue's spec but is
# reserved for a future "what kind of source is this" tag.
KNOWN_KEYS = ("source", "acquired", "environment", "storage", "era", "cost", "kind")

_KEY_LINE_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:\s*(.*)$")


@dataclass(frozen=True)
class ContextFile:
    """One parsed `context.txt`: the dir it lives in, its recognized keys,
    and its free-text prose (with the key-line header stripped)."""
    dir: Path
    keys: dict
    prose: str

    @property
    def path(self) -> Path:
        return self.dir / CONTEXT_FILENAME

    @property
    def is_empty(self) -> bool:
        return not self.keys and not self.prose


def parse_context_text(text: str) -> tuple:
    """Split raw `context.txt` text into (known keys dict, prose str).

    Header rule: from the top of the file, a line is part of the header
    only while it is blank or matches `<KNOWN key>: value` (case-
    insensitive). The first line that is neither ends the header -- that
    line and everything after it, verbatim, is the prose. This means an
    unrecognized `key:`-shaped line is never an error and is never
    silently dropped: it just falls into the prose like any other line.

    Tolerates: an empty file (-> ({}, "")), a prose-only file (no line
    matches a known key -> header is empty, whole file is prose), and a
    keys+prose file (the real shape in the tree today).
    """
    lines = text.splitlines()
    keys = {}
    header_end = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            header_end = i + 1
            continue
        m = _KEY_LINE_RE.match(stripped)
        if m and m.group(1).lower() in KNOWN_KEYS:
            keys[m.group(1).lower()] = m.group(2).strip()
            header_end = i + 1
            continue
        break  # first non-blank, non-known-key line: header is over
    prose = "\n".join(lines[header_end:]).strip()
    return keys, prose


def parse_context_file(path: Path) -> ContextFile:
    """Parse one `context.txt`. Missing file -> empty ContextFile."""
    d = path.parent if path.name == CONTEXT_FILENAME else path
    p = d / CONTEXT_FILENAME
    if not p.is_file():
        return ContextFile(dir=d, keys={}, prose="")
    text = p.read_text(encoding="utf-8")
    keys, prose = parse_context_text(text)
    return ContextFile(dir=d, keys=keys, prose=prose)


def find_inventory_root(start: Path) -> Optional[Path]:
    """Walk up from `start` looking for an ancestor directory literally
    named `inventory` (the pipeline's content-store convention, see
    `prompts/_shared.md` "Output-file persistence"). Returns None if the
    path isn't under one -- callers then fall back to `start` alone so this
    never walks an unrelated tree all the way to filesystem root."""
    for d in (start, *start.parents):
        if d.name == "inventory":
            return d
    return None


@dataclass
class DirContext:
    """The merged, nearest-wins result of every `context.txt` from the
    inventory root down to one item directory.

    `files_read` lists every ancestor dir that actually had a (possibly
    empty) `context.txt`, root-to-leaf, so a caller (REVIEW's card) can
    show which files applied. `prose_chain` is the same idea restricted to
    files with non-empty prose, as (dir, prose) pairs -- callers that want
    provenance use this instead of `.merged_prose`.
    """
    source: Optional[str] = None
    acquired: Optional[str] = None
    environment: Optional[str] = None
    storage: Optional[str] = None
    era: Optional[str] = None
    cost: Optional[str] = None
    kind: Optional[str] = None
    files_read: list = field(default_factory=list)
    prose_chain: list = field(default_factory=list)
    field_sources: dict = field(default_factory=dict)  # key -> dir that set it

    @property
    def has_context(self) -> bool:
        """True if anything at all was found in the chain (even one
        empty/placeholder context.txt counts, since its presence is still
        informative to REVIEW's "which files applied" line)."""
        return bool(self.files_read)

    @property
    def merged_prose(self) -> str:
        """All prose in the chain, root-to-leaf (general context first,
        a sub-estate's refinement last), joined for reading as one block."""
        return "\n\n".join(prose for _, prose in self.prose_chain)

    @property
    def public_summary(self) -> dict:
        """Every known field EXCEPT `source` -- the PII guardrail's "get
        everything but the private name" accessor. Empty/unset fields are
        omitted. Safe to hand to buyer-facing copy generation or the
        review card as-is."""
        return {k: getattr(self, k) for k in KNOWN_KEYS
                if k != "source" and getattr(self, k)}

    def __str__(self) -> str:
        """Display string for chat/logs. Never includes `source` -- use
        `.source` directly for the local-only debug case."""
        bits = [f"{k}: {v}" for k, v in self.public_summary.items()]
        if self.merged_prose:
            bits.append(self.merged_prose)
        return "\n".join(bits) if bits else "(no context.txt in chain)"

    def __repr__(self) -> str:
        fields = ", ".join(f"{k}={v!r}" for k, v in self.public_summary.items())
        return (f"DirContext({fields}, "
                f"files={len(self.files_read)}, source=<redacted>)")


def load_context(item_dir, root=None) -> DirContext:
    """Walk from `root` (or the nearest `inventory/` ancestor, or
    `item_dir` itself if neither is found) down to `item_dir`, parsing
    every `context.txt` on the way, and merge them nearest-wins.

    Absent chain, or every file in it empty (six placeholders exist on
    disk today), returns a DirContext with nothing set -- "today's
    behavior", per the issue's guardrail.
    """
    item_dir = Path(item_dir).resolve()
    if root is None:
        root = find_inventory_root(item_dir) or item_dir
    else:
        root = Path(root).resolve()

    under_root = root in (item_dir, *item_dir.parents)
    if under_root:
        chain_dirs = []
        cur = item_dir
        while True:
            chain_dirs.append(cur)
            if cur == root or cur.parent == cur:  # cur.parent == cur: fs root guard
                break
            cur = cur.parent
        chain_dirs.reverse()
    else:
        # item_dir isn't under root at all -- just look at item_dir itself.
        chain_dirs = [item_dir]

    files = [parse_context_file(d) for d in chain_dirs]
    files_present = [cf for cf in files if cf.path.is_file()]

    ctx = DirContext()
    ctx.files_read = [cf.dir for cf in files_present]
    ctx.prose_chain = [(cf.dir, cf.prose) for cf in files_present if cf.prose]

    # Nearest-wins: walk leaf -> root, first non-empty value for each key sticks.
    for cf in reversed(files_present):
        for k in KNOWN_KEYS:
            if getattr(ctx, k) is None:
                v = cf.keys.get(k)
                if v:
                    setattr(ctx, k, v)
                    ctx.field_sources[k] = cf.dir
    return ctx


# --------------------------------------------------------------------- #
# Forbidden claims: DRAFT-side block list
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class ForbiddenClaim:
    """One claim-phrase DRAFT must never write, and why."""
    phrase: str
    reason: str
    source_key: str
    source_dir: Path


def _mentions(text: str, word_re: str, negated_prefixes=("no", "not", "without")) -> bool:
    """True if `word_re` occurs in `text` without one of `negated_prefixes`
    immediately before it (e.g. "no pets" does not count as mentioning
    pets being present). Deliberately simple/pattern-based -- a linter,
    not NLP; it only looks at the word or two right before the match."""
    for m in re.finditer(word_re, text, re.IGNORECASE):
        prefix = text[max(0, m.start() - 16):m.start()].lower().split()
        if prefix and prefix[-1] in negated_prefixes:
            continue
        return True
    return False


# (context field, presence-pattern, negation exempt?, forbidden phrases, reason)
_RULES = (
    ("environment", r"smoker|smoking", False,
     ("smoke-free", "smoke free", "odor-free", "odor free"),
     "environment records a smoker in the home"),
    ("environment", r"pets?|cats?|dogs?", True,
     ("pet-free", "pet free"),
     "environment records pets present"),
    ("storage", r"unclimat(?:ized|ised)|non[-\s]?climate|not\s+climate[-\s]?controlled", False,
     ("climate-controlled", "climate controlled"),
     "storage records no climate control"),
)


def forbidden_claims(ctx: DirContext) -> list:
    """Claim-phrases DRAFT must not write, derived from `environment:` and
    `storage:`. Context can only forbid or supply a disclosure -- never
    upgrade a claim -- so this is intentionally one-directional: it never
    manufactures a positive claim, only blocks ones the estate
    contradicts. Simple substring/regex matching, not NLP; tune the rule
    table above rather than special-casing callers.
    """
    out = []
    for field_name, pattern, honor_negation, phrases, reason in _RULES:
        text = getattr(ctx, field_name) or ""
        if not text:
            continue
        negated = ("no", "not", "without") if honor_negation else ()
        if _mentions(text, pattern, negated_prefixes=negated):
            src_dir = ctx.field_sources.get(field_name, Path("."))
            for phrase in phrases:
                out.append(ForbiddenClaim(
                    phrase=phrase, reason=reason,
                    source_key=field_name, source_dir=src_dir))
    return out


def forbidden_phrases(ctx: DirContext) -> list:
    """Just the phrase strings from `forbidden_claims` -- the common case
    for a quick "is this phrase allowed" check."""
    return [c.phrase for c in forbidden_claims(ctx)]


# --------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------- #

def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    show_source = "--source" in args
    as_json = "--json" in args
    targets = [a for a in args if not a.startswith("--")]
    if not targets:
        print(__doc__.strip().splitlines()[0])
        print("usage: dir_context.py <item-dir> [--source] [--json]")
        return 2

    ctx = load_context(targets[0])
    claims = forbidden_claims(ctx)

    if as_json:
        payload = dict(ctx.public_summary)
        if show_source:
            payload["source"] = ctx.source
        payload["files_read"] = [str(d) for d in ctx.files_read]
        payload["forbidden_phrases"] = [c.phrase for c in claims]
        print(json.dumps(payload, indent=2))
        return 0

    print(f"context: {len(ctx.files_read)} file(s) in chain")
    for d in ctx.files_read:
        print(f"  {d / CONTEXT_FILENAME}")
    print(str(ctx))
    if show_source and ctx.source:
        print(f"source (local only, never surface this): {ctx.source}")
    if claims:
        print("forbidden claims:")
        for c in claims:
            print(f"  '{c.phrase}' -- {c.reason} ({c.source_key})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
