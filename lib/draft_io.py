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


def _emit_meta_kv(indent: str, key: str, val: object) -> list[str]:
    """Render `key: value` as valid YAML line(s).

    - None/"" → `key: ""`.
    - Multi-line strings → a literal block scalar (`key: |-`) with the body
      indented, so colons/quotes/newlines are preserved safely and stay
      human-readable.
    - Single-line → a double-quoted scalar with `\\` and `"` escaped.
    """
    if val is None:
        return [f'{indent}{key}: ""\n']
    s = str(val)
    if "\n" in s:
        body = s.rstrip("\n").split("\n")
        out = [f"{indent}{key}: |-\n"]
        out += [f"{indent}  {line}\n" for line in body]
        return out
    esc = s.replace("\\", "\\\\").replace('"', '\\"')
    return [f'{indent}{key}: "{esc}"\n']


def update_meta(path: str | Path, updates: dict[str, str]) -> None:
    """Write back `meta.*` values into a draft's frontmatter, in place.

    Used by LIST/EDIT to record ebay_offer_id, ebay_inventory_sku,
    last_synced, etc. after a sync. Rewrites only the `meta:` block so the
    rest of the file (comments, ordering, the markdown body) is preserved.

    Values are serialized as valid YAML: single-line values are quoted +
    escaped; multi-line values become a literal block scalar. A key whose
    existing value spans multiple lines (e.g. a block `notes:`) is replaced
    in full — no orphaned continuation lines. Keys not already present are
    appended inside the block.
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^meta:[ \t]*(#.*)?$", ln):
            start = i
            break
    if start is None:
        raise DraftParseError(f"{path}: no top-level `meta:` block to update.")

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^[^\s#]", lines[j]):  # next column-0 (non-comment) key
            end = j
            break

    def _indent_of(s: str) -> int:
        return len(s) - len(s.lstrip(" \t"))

    remaining = dict(updates)
    head, tail = lines[: start + 1], lines[end:]
    body, new_body = lines[start + 1: end], []
    i, n = 0, len(lines[start + 1: end])
    while i < n:
        ln = body[i]
        m = re.match(r"^([ \t]+)([A-Za-z0-9_]+):", ln)
        if m and not ln.lstrip().startswith("#"):
            key_indent = m.group(1)
            key = m.group(2)
            # span = the key line + its continuation lines (more indented than
            # the key). Blank lines are kept inside the span only if a deeper-
            # indented line follows before any dedent (i.e. they're part of a
            # block scalar); otherwise the blank ends the span.
            j = i + 1
            while j < n:
                if body[j].strip() == "":
                    k = j
                    while k < n and body[k].strip() == "":
                        k += 1
                    if k < n and _indent_of(body[k]) > len(key_indent):
                        j = k          # blank(s) are inside the block value
                        continue
                    break              # trailing blank ends the span
                if _indent_of(body[j]) > len(key_indent):
                    j += 1
                else:
                    break              # next sibling key / dedent
            if key in remaining:
                new_body += _emit_meta_kv(key_indent, key, remaining.pop(key))
            else:
                new_body += body[i:j]
            i = j
        else:
            new_body.append(ln)
            i += 1

    for k, v in remaining.items():  # keys not already in the block
        new_body += _emit_meta_kv("  ", k, v)

    path.write_text("".join(head + new_body + tail), encoding="utf-8")


def set_photo_order(path: str | Path, order: list[str]) -> list[str]:
    """Rewrite a draft's `photos:` list in place, leaving the rest of the file
    untouched.

    Photo ORDER is a real listing decision, not bookkeeping: entry one is the
    eBay gallery image — the only frame most buyers ever see in search results.
    It gets its own writer for the same reason `update_meta` has one: rewriting
    the whole file through a YAML round-trip would drop the template's comments
    and reflow every block a human reads.

    Returns the order written.
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)

    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^photos:[ \t]*(#.*)?$", ln):
            start = i
            break
    if start is None:
        raise DraftParseError(f"{path}: no top-level `photos:` block to reorder.")

    end = start + 1
    while end < len(lines) and re.match(r"^[ \t]*(-|#|$)", lines[end]):
        end += 1

    block = ["photos:\n"] + [f"  - {name}\n" for name in order]
    path.write_text("".join(lines[:start] + block + lines[end:]), encoding="utf-8")
    return list(order)


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
