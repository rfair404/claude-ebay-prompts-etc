"""dir_context — the directory-level context.txt chain (#46).

Items live under a source directory ("bucket") that owns a single
context.txt: a plain-language summary of where the items came from, with a
few `key: value` lines up front for the things tools have to branch on
without guessing prose.

Bucket = the nearest ancestor directory (inclusive) that holds a context.txt.
That is a property of the tree, not of how many slashes are in the path, so
a re-org changes what a bucket contains, never how one is found — the same
reason `rglob` replaced the depth-capped globs elsewhere in this repo (#50).

    from dir_context import bucket_for, context_for

    bucket = bucket_for(item_dir)          # Path | None
    ctx = context_for(item_dir)            # merged keys + prose, cascade applied

`ctx["keys"]` holds whatever `key:` lines were present, nearest-directory-
wins. The economic keys tools branch on:

    kind:     "event"   a single purchase with a real cost basis — ROI is
                        meaningful, and a missing `spend:` is a data gap.
              "channel" an ongoing source (thrifting, curb finds, gifts) —
                        ROI is not meaningful, and a missing `spend:` is
                        simply correct.
    spend:    what the bucket cost. Parse with spend_amount(), not float():
              "FREE" -> 0.0, absent -> None. Zero and "we don't know" are
              different facts and must not collapse into each other.
    acquired: a free-form date/period string, returned as-is.

Every other key is passed through as a plain string, unknown keys included —
a context.txt predates every consumer that will ever read it, so an unknown
key is not an error.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INVENTORY = REPO / "inventory"
CONTEXT_FILE = "context.txt"

# Leading contiguous `key: value` lines are the header; the first line that
# doesn't match ends it, and everything from there on — blank lines included
# — is prose. The header can only grow from the top of the file down, so a
# sentence in the prose that happens to contain a colon is never mistaken
# for a key.
_KEY_LINE = re.compile(r"^([a-z_]+):\s*(.*)$")
_FREE_RE = re.compile(r"^\s*free\s*$", re.I)
_AMOUNT_RE = re.compile(r"\$?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")


def spend_amount(raw: str | None) -> float | None:
    """Parse a `spend:` (or `cost:`) value. None means "not recorded" — never 0."""
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None
    if _FREE_RE.match(s):
        return 0.0
    m = _AMOUNT_RE.search(s)
    return float(m.group(1).replace(",", "")) if m else None


def parse_context_file(path: Path) -> dict:
    """One context.txt -> {"keys": {...}, "prose": str, "path": path}.

    Tolerates the three shapes on disk: empty, prose-only, and
    keys-then-prose.
    """
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    lines = text.splitlines()
    keys: dict[str, str] = {}
    i = 0
    while i < len(lines):
        m = _KEY_LINE.match(lines[i])
        if not m:
            break
        keys[m.group(1)] = m.group(2).strip()
        i += 1
    prose = "\n".join(lines[i:]).strip()
    return {"keys": keys, "prose": prose, "path": path}


def bucket_for(item_dir: Path, root: Path = INVENTORY) -> Path | None:
    """Nearest ancestor of item_dir (inclusive) that owns a context.txt.

    None if item_dir is not under root, or no ancestor up to root has one.
    """
    item_dir, root = item_dir.resolve(), root.resolve()
    try:
        item_dir.relative_to(root)
    except ValueError:
        return None
    d = item_dir
    while True:
        if (d / CONTEXT_FILE).exists():
            return d
        if d == root:
            return None
        d = d.parent


def chain_for(item_dir: Path, root: Path = INVENTORY) -> list[dict]:
    """Every context.txt from root down to item_dir, root-most first.

    A sub-bucket (inventory/ESTATES/FR/books/context.txt) refines its parent
    without restating it, so the merge in context_for() takes nearest-wins —
    but the chain itself stays in cascade order so a caller can see who said
    what.
    """
    item_dir, root = item_dir.resolve(), root.resolve()
    try:
        rel = item_dir.relative_to(root)
    except ValueError:
        return []
    chain = []
    d = root
    if (d / CONTEXT_FILE).exists():
        chain.append(parse_context_file(d / CONTEXT_FILE))
    for part in rel.parts:
        d = d / part
        if (d / CONTEXT_FILE).exists():
            chain.append(parse_context_file(d / CONTEXT_FILE))
    return chain


def context_for(item_dir: Path, root: Path = INVENTORY) -> dict:
    """Merged context for item_dir: nearest-wins keys, cascaded prose."""
    chain = chain_for(item_dir, root)
    keys: dict[str, str] = {}
    for c in chain:
        keys.update(c["keys"])                 # later (nearer) wins
    prose = "\n\n".join(c["prose"] for c in chain if c["prose"])
    return {"keys": keys, "prose": prose, "chain": chain}


def is_backup_dir(path: Path) -> bool:
    """True if a path segment marks it a staging/backup copy, not real stock.

    `_prepped/` and `*.prior-run-bak` copies of a shoot sit inside the tree
    during a run and must never be counted as inventory of their own.
    """
    return any(part == "_prepped" or part.endswith(".prior-run-bak")
               for part in path.parts)
