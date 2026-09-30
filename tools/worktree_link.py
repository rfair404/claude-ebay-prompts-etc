"""Link a worktree's inventory/ to the main checkout's inventory/.

Pipeline data (identify.txt, price.txt, draft.md, review_card.*, .prep/, ...)
lives only in the main checkout's gitignored inventory/. A worktree has none,
and the desktop app's worktree-write-guard refuses any Write/Edit to a path in
the base checkout -- so a pipeline run inside a worktree could neither read
nor write its items (GH #103, "worktree blindness").

This creates <worktree>/inventory as a directory junction (Windows) or symlink
pointing at <main>/inventory. Every tool that resolves REPO / "inventory" then
sees the real store, and Write/Edit to <worktree>/inventory/... land in main.
The guard compares paths lexically, so the carve-out is exactly inventory/:
<main>/lib/... and every other base-checkout path stay blocked.

Runs as a SessionStart / CwdChanged hook (.claude/settings.json). Idempotent,
silent outside a worktree, and never blocks: problems go to stderr, exit 0.

    python tools/worktree_link.py            # link the current worktree
    python tools/worktree_link.py --check    # report only, change nothing
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LINKED = ("inventory",)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def worktree_roots(cwd: Path) -> tuple[Path, Path] | None:
    """(worktree top, main checkout) or None when cwd isn't a linked worktree."""
    try:
        top, common = _git(cwd, "rev-parse", "--path-format=absolute",
                           "--show-toplevel", "--git-common-dir").splitlines()
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return None
    top, main = Path(top).resolve(), Path(common).resolve().parent
    return None if top == main else (top, main)


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _make_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        import _winapi  # junctions need no admin / developer mode, unlike symlinks
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def link_worktree(cwd: Path, check: bool = False) -> list[str]:
    roots = worktree_roots(cwd)
    if roots is None:
        return []
    top, main = roots
    notes = []
    for name in LINKED:
        link, target = top / name, main / name
        if not target.is_dir():
            continue
        if os.path.lexists(link):
            if _same(link, target):
                notes.append(f"{name}/ -> {target} (linked)")
            else:
                print(f"worktree_link: {link} exists and is not a link to {target}; "
                      f"left alone -- pipeline writes there stay in this worktree",
                      file=sys.stderr)
            continue
        if check:
            notes.append(f"{name}/ not linked (would link -> {target})")
            continue
        try:
            _make_link(link, target)
            notes.append(f"{name}/ -> {target} (linked now)")
        except OSError as e:
            print(f"worktree_link: could not link {link} -> {target}: {e}", file=sys.stderr)
    return notes


def main() -> int:
    cwd = Path.cwd()
    if not sys.stdin.isatty():
        try:
            given = Path(json.loads(sys.stdin.read() or "{}").get("cwd") or cwd)
            cwd = given if given.is_dir() else cwd  # e.g. a /c/... MSYS spelling
        except (json.JSONDecodeError, AttributeError):
            pass
    notes = link_worktree(cwd, check="--check" in sys.argv)
    if notes:
        print("Worktree: " + "; ".join(notes) + ". Pipeline data read/written under "
              "this worktree's inventory/ is the main checkout's store.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
