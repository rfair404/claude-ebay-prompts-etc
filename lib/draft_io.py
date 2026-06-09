"""
draft.md I/O — parse YAML frontmatter + markdown body, and write meta back.

Shared by Function 5 (DRAFT) consumers and Function 6 (LIST/EDIT). The
draft file is `YAML frontmatter (between ^---$ lines) + markdown body`.

Parsing rule (per PLAN.md): anchor on lines that are exactly `---`, NOT
on the substring `---`. The template uses `# --- Section ---` YAML
comments internally, so a naive `split("---")` mis-splits. We split on
the first two lines that are exactly `---`.

No eBay calls here. No credentials needed. This module is import-safe and
fully unit-testable offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError as e:  # pragma: no cover
    raise ImportError("PyYAML is required. Install with: pip install pyyaml") from e


_FRONTMATTER_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n(.*)$", re.DOTALL)


class DraftParseError(RuntimeError):
    """Raised when a draft file can't be parsed as frontmatter + body."""


@dataclass
class Draft:
    """A parsed draft.md: structured frontmatter + free-text body."""
    path: Path
    frontmatter: dict[str, Any]
    body: str

    def get(self, dotpath: str, default: Any = None) -> Any:
        """Read a value by dot-path, e.g. 'shipping.weight.major_lb'."""
        node: Any = self.frontmatter
        for part in dotpath.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def parse_draft(path: str | Path) -> Draft:
    """Parse a draft.md file into a Draft (frontmatter dict + body str)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise DraftParseError(
            f"{path} does not start with a `---` YAML frontmatter block "
            f"delimited by lines that are exactly `---`."
        )
    fm_text, body = m.group(1), m.group(2)
    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as e:
        raise DraftParseError(f"{path}: frontmatter is not valid YAML: {e}") from e
    if not isinstance(fm, dict):
        raise DraftParseError(f"{path}: frontmatter did not parse to a mapping.")
    return Draft(path=path, frontmatter=fm, body=body.strip("\n"))


def update_meta(path: str | Path, updates: dict[str, str]) -> None:
    """Write back `meta.*` scalar values into a draft's frontmatter, in place.

    Used by LIST/EDIT to record ebay_offer_id, ebay_inventory_sku, and
    last_synced after a successful sync. Does a minimal, line-oriented
    rewrite of the `meta:` block so the rest of the file (comments,
    ordering, the markdown body) is preserved exactly.

    Only keys already present under `meta:` are updated; unknown keys are
    appended inside the meta block. Quotes the value.
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    # locate the `meta:` block: from the 'meta:' line to the next top-level
    # (column-0, non-comment) key.
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^meta:[ \t]*(#.*)?$", ln):
            start = i
            break
    if start is None:
        raise DraftParseError(f"{path}: no top-level `meta:` block to update.")

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^[^\s#]", lines[j]):  # next column-0 key
            end = j
            break

    remaining = dict(updates)
    for j in range(start + 1, end):
        for key, val in list(remaining.items()):
            if re.match(rf"^\s+{re.escape(key)}:", lines[j]):
                indent = lines[j][: len(lines[j]) - len(lines[j].lstrip())]
                lines[j] = f'{indent}{key}: "{val}"\n'
                del remaining[key]
                break

    # append any keys not already present, just before the block end
    if remaining:
        indent = "  "
        insert_at = end
        addition = [f'{indent}{k}: "{v}"\n' for k, v in remaining.items()]
        lines[insert_at:insert_at] = addition

    path.write_text("".join(lines), encoding="utf-8")


def resolve_photo_paths(draft: Draft) -> list[Path]:
    """Resolve the draft's `photos:` list to absolute paths in the shoot dir."""
    shoot_dir = draft.path.parent
    out: list[Path] = []
    for name in (draft.frontmatter.get("photos") or []):
        out.append((shoot_dir / str(name)).resolve())
    return out


# ---------------------------------------------------------------------------
# CLI — offline self-test against a draft file
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover
        pass

    ap = argparse.ArgumentParser(description="Parse a draft.md and print a summary.")
    ap.add_argument("draft", help="Path to a draft.md")
    args = ap.parse_args()

    d = parse_draft(args.draft)
    print(f"path:   {d.path}")
    print(f"title:  {d.get('title')!r}  [{len(str(d.get('title') or ''))}/80]")
    print(f"price:  {d.get('price')!r}   qty: {d.get('quantity')!r}")
    print(f"cond:   {d.get('condition')!r}")
    print(f"photos: {len(d.frontmatter.get('photos') or [])}")
    print(f"body:   {len(d.body)} chars")
    print(f"specifics with values: "
          f"{sum(1 for v in (d.frontmatter.get('item_specifics') or {}).values() if isinstance(v, str) and v)}")


if __name__ == "__main__":
    _cli()
