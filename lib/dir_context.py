#!/usr/bin/env python3
"""Directory-level estate context — one source summary every item inherits.

WHY: items don't arrive one at a time, they arrive as an ESTATE. Everything
under inventory/FR/ came out of one house: same address, same decades, same
air. That shared fact used to live only in the user's head, so every stage
re-derived (or failed to derive) the same background for every item in the
tree — and the worst case was never the blank, it was a stage filling the
blank OPTIMISTICALLY because a book happened to look clean.

A parent directory gets a context.txt; every item under it inherits it.

FORMAT — prose is the payload, `key: value` lines are the part tools read
without guessing. Keys are only recognized in the leading block, before the
first blank line, and only as a single lowercase word + colon + value; a prose
line that happens to end in a colon is prose, not a key.

    source: Frankie (grandmother), estate, Greensboro NC house
    environment: smoker — indoors, occasional. no pets.
    storage: attic and closets, unclimatized; decades in place.

    She smoked most of her life and sometimes inside, so nothing from this
    house can be sold as coming from a smoke-free home.

CASCADE: a stage working on inventory/FR/books/TEJ collects every context.txt
from the inventory root down to the item dir and merges them NEAREST-WINS, so
a sub-estate ("these were in the damp basement, not the attic") refines its
parent without restating it. Prose accumulates outermost-first — the general
story then the correction — because none of it is redundant.

WHAT IT DOES NOT DO (the clamps — same rails the specializations carry):
  * Context is BACKGROUND, never a claim upgrade. It can FORBID an assertion
    or SUPPLY a disclosure. It cannot make a marble German or a book a first
    edition. "From a house that accumulated 1955-2010" is a prior, not a date.
  * Negative constraints are HARD. A blocked claim is blocked, not "flagged" —
    DRAFT refuses to emit it, at any confidence.
  * No PII in buyer copy. The file may name the person for our own reference;
    listing copy says "an estate" unless explicitly opted in.
  * Absent file = today's behavior. No context.txt in the chain changes nothing.
"""
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INVENTORY = REPO / "inventory"

CONTEXT_NAME = "context.txt"

# A key line: one lowercase word, colon, then something. Anchored and narrow on
# purpose — "Items that I've purchased from my neighbors:" is prose and must
# not be swallowed as a key with an empty value.
_KEY_RE = re.compile(r"^([a-z][a-z_]{1,19}):[ \t]+(\S.*)$")

# Claims the estate can forbid. Each entry: (trigger regex over the merged
# context text, the phrases DRAFT may not emit, why). The trigger reads BOTH
# the key lines and the prose, because the user writes plainly and the fact
# lands wherever it lands.
_BLOCKS: list[tuple[str, tuple[str, ...], str]] = [
    (r"\bsmok(?:e[rd]?|ing)\b",
     ("smoke-free", "smoke free", "smokefree", "non-smoking", "nonsmoking",
      "no smoking", "odor-free", "odour-free", "odor free"),
     "someone smoked in the source home"),
    (r"\b(?:cat|dog|pet)s?\b",
     ("pet-free", "pet free", "petfree", "no pets"),
     "pets in the source home"),
    (r"\b(?:unclimatized|unclimated|attic|garage|barn|shed|basement|damp)\b",
     ("climate-controlled", "climate controlled", "climate-conditioned"),
     "stored unclimatized"),
]

# A trigger fires only if the context does not already assert the negative —
# "no pets" mentions pets but forbids nothing.
_NEGATED = {
    "someone smoked in the source home": r"\b(?:no|non[- ]?)\s*smok\w*",
    "pets in the source home": r"\bno\s+pets\b",
}


@dataclass
class ContextFile:
    """One context.txt, as written."""
    path: Path
    keys: dict[str, str]
    prose: str

    @property
    def rel(self) -> str:
        try:
            return str(self.path.relative_to(REPO)).replace("\\", "/")
        except ValueError:
            return str(self.path)


@dataclass
class Blocked:
    """A claim this estate forbids, and the receipt for why."""
    phrases: tuple[str, ...]
    why: str
    source: str


@dataclass
class MergedContext:
    """The whole chain, merged. Empty is the no-context.txt case."""
    chain: list[ContextFile] = field(default_factory=list)
    keys: dict[str, str] = field(default_factory=dict)
    prose: str = ""
    blocked: list[Blocked] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.chain)

    @property
    def sources(self) -> list[str]:
        return [c.rel for c in self.chain]

    def blocks(self, text: str) -> list[Blocked]:
        """Which forbidden claims does `text` actually make? (DRAFT/REVIEW)"""
        low = (text or "").lower()
        return [b for b in self.blocked if any(p in low for p in b.phrases)]


def parse(path: Path) -> ContextFile:
    """One file: leading key block, then prose. Both optional."""
    raw = path.read_text(encoding="utf-8", errors="ignore")
    keys: dict[str, str] = {}
    lines = raw.splitlines()
    cut = 0
    for cut, line in enumerate(lines):
        if not line.strip():
            break
        m = _KEY_RE.match(line)
        if not m:
            break                              # first non-key line ends the block
        keys[m.group(1)] = m.group(2).strip()
    else:
        cut = len(lines)
    prose = "\n".join(lines[cut:]).strip() if keys else raw.strip()
    return ContextFile(path=path, keys=keys, prose=prose)


def chain_for(item_dir: Path, root: Path = INVENTORY) -> list[ContextFile]:
    """Every context.txt from `root` down to `item_dir`, outermost first."""
    item_dir = Path(item_dir).resolve()
    root = Path(root).resolve()
    try:
        item_dir.relative_to(root)
    except ValueError:
        return []
    parts: list[Path] = []
    cur = item_dir
    while True:
        parts.append(cur)
        if cur == root:
            break
        cur = cur.parent
    out = []
    for d in reversed(parts):
        f = d / CONTEXT_NAME
        if f.is_file():
            out.append(parse(f))
    return out


def _derive_blocks(chain: list[ContextFile]) -> list[Blocked]:
    out: list[Blocked] = []
    seen: set[str] = set()
    for cf in chain:
        text = "\n".join([*(f"{k}: {v}" for k, v in cf.keys.items()),
                          cf.prose]).lower()
        for trigger, phrases, why in _BLOCKS:
            if why in seen:
                continue
            neg = _NEGATED.get(why)
            probe = re.sub(neg, " ", text) if neg else text
            if not re.search(trigger, probe):
                continue
            seen.add(why)
            out.append(Blocked(phrases=phrases, why=why, source=cf.rel))
    return out


def load(item_dir: Path, root: Path = INVENTORY) -> MergedContext:
    """The merged estate context for one item dir. Keys nearest-wins."""
    chain = chain_for(item_dir, root)
    if not chain:
        return MergedContext()
    keys: dict[str, str] = {}
    for cf in chain:                            # outermost first, nearest wins
        keys.update(cf.keys)
    prose = "\n\n".join(f"[{cf.rel}]\n{cf.prose}" for cf in chain if cf.prose)
    return MergedContext(chain=chain, keys=keys, prose=prose,
                         blocked=_derive_blocks(chain))


def brief(item_dir: Path, root: Path = INVENTORY) -> str:
    """Plain-text block a stage can paste straight into its working notes."""
    ctx = load(item_dir, root)
    if not ctx:
        return ""
    out = ["ESTATE CONTEXT (background only — never a claim upgrade)",
           f"  applied: {', '.join(ctx.sources)}"]
    for k, v in ctx.keys.items():
        out.append(f"  {k}: {v}")
    if ctx.blocked:
        out.append("  MUST NOT CLAIM:")
        for b in ctx.blocked:
            out.append(f"    - {' / '.join(b.phrases[:2])} — {b.why} ({b.source})")
    if ctx.prose:
        out += ["", ctx.prose]
    return "\n".join(out)


def sweep(root: Path = INVENTORY) -> list[tuple[str, str, Blocked]]:
    """Every drafted item asserting a claim its estate contradicts."""
    hits: list[tuple[str, str, Blocked]] = []
    for dr in sorted(root.rglob("draft.md")):
        ctx = load(dr.parent, root)
        if not ctx.blocked:
            continue
        text = dr.read_text(encoding="utf-8", errors="ignore")
        low = text.lower()
        for b in ctx.blocks(text):
            phrase = next(p for p in b.phrases if p in low)
            line = text.splitlines()[low[:low.index(phrase)].count("\n")].strip()
            hits.append((str(dr.parent.relative_to(REPO)).replace("\\", "/"),
                         line[:120], b))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("item_dir", nargs="?", help="item folder to resolve context for")
    ap.add_argument("--sweep", action="store_true",
                    help="scan every draft for a claim its estate forbids")
    ap.add_argument("--root", default=str(INVENTORY))
    a = ap.parse_args()
    root = Path(a.root)

    if a.sweep:
        hits = sweep(root)
        for d, line, b in hits:
            print(f"{d}\n    claims: {line}\n    forbidden: {b.why} ({b.source})")
        print(f"\n{len(hits)} contradicted claim(s)")
        return 1 if hits else 0

    if not a.item_dir:
        ap.error("give an item_dir, or --sweep")
    print(brief(Path(a.item_dir), root)
          or "(no context.txt in the chain — no change to behavior)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
