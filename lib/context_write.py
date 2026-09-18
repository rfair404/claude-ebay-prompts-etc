#!/usr/bin/env python3
"""context_write — the writer for context.txt's kind:/spend:/spend_unit:/
acquired: keys (#118).

    python -m lib.cli context <bucket-dir> --spend 575 --kind event
    python -m lib.cli context <bucket-dir> --spend 8 --spend-unit item
    python -m lib.cli context <bucket-dir> --acquired 2026-07 --dry-run

WHY THIS EXISTS

lib/source_report.py's parser (`parse_context()`) has looked for these keys
since #56 — but nothing ever WROTE them: no template, no writer, no
validation step ever existed (#118). A tolerant parser that fell back to
reading the existing prose ("Spend $575") would work directly against
source_report.py's own documented reasoning — recovering a number by
pattern-matching English is exactly what that module exists to stop doing.
So the fix is this writer, plus the validation check wired into
source_report.py's own report output (the "N bucket(s) need `ebz context
--spend=...`" line) that tells you which buckets still need it run.

WHAT IT DOES NOT DO

It never reads a bucket's prose back out loud — it does not print
context.txt's existing free-text to the terminal or a log (that prose can
carry PII: named individuals, family relationships, home addresses of an
estate sale). It only ever echoes the KEYS you asked it to set, never the
surrounding file content. It also does not walk up the tree hunting for an
existing bucket the way `bucket_for()` does — point it at the exact
directory that should own (or already owns) context.txt; a sub-lot that
should get its own basis needs its own context.txt, and this tool will
happily create one there.

IDEMPOTENT: running the same flags twice makes no further change to the
file on the second run (and says so in its output), so it is safe to script
against every real bucket without worrying about clobbering something on a
re-run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))       # sibling lib/ modules
from source_report import _KEY_RE, _KINDS, _SPEND_UNITS, _to_float  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

# Canonical write order when a key has no existing line to update in place.
_ORDER = ("kind", "spend", "spend_unit", "acquired")


def _fmt_num(v: float) -> str:
    """Canonical numeric text for a context.txt value: no `$`, no thousands
    commas, no trailing `.0` on a whole number (575.0 -> "575", 8.5 -> "8.5")."""
    s = f"{v:g}"
    return "0" if s == "-0" else s


def upsert_context_text(text: str, updates: dict) -> str:
    """Return `text` with each `key: value` pair in `updates` set in place.

    Mirrors `parse_context()`'s own "first occurrence wins" reading: an
    existing `key:` line — the first one — is rewritten with the new value;
    every other line (prose, blank lines, an unrecognised key, a second
    occurrence of a key `parse_context()` would ignore anyway) passes
    through completely unchanged, byte for byte. A key with no existing
    line in the file is inserted at the very top, ahead of any prose, in
    `_ORDER`, followed by one blank line before whatever content the file
    already had (if it had any) — matching the "keys, then a blank line,
    then the prose" shape already used by the real files that have keys.
    """
    lines = (text or "").splitlines()
    remaining = dict(updates)
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        m = _KEY_RE.match(line)
        key = m.group(1).lower() if m else None
        if key in remaining and key not in seen:
            out.append(f"{key}: {remaining[key]}")
            seen.add(key)
        else:
            out.append(line)
    new_keys = [k for k in _ORDER if k in remaining and k not in seen]
    if new_keys:
        prefix = [f"{k}: {remaining[k]}" for k in new_keys]
        if out and any(ln.strip() for ln in out):
            prefix.append("")
        out = prefix + out
    result = "\n".join(out)
    if result and not result.endswith("\n"):
        result += "\n"
    return result


def write_context(bucket_dir: Path, *, kind: Optional[str] = None,
                  spend: Optional[float] = None, spend_unit: Optional[str] = None,
                  acquired: Optional[str] = None, dry_run: bool = False
                  ) -> tuple[Path, bool, dict]:
    """Set the given keys on `bucket_dir`/context.txt.

    Only keyword args actually passed (not None) are touched — the rest of
    the file, key or prose, is left exactly as it was. Returns
    `(context_path, changed, keys_written)`; `keys_written` holds only the
    `key: value` strings actually applied — safe to print, never the
    surrounding prose. With `dry_run=True` nothing is written; `changed`
    still reports whether writing WOULD have changed the file.
    """
    bucket_dir = Path(bucket_dir)
    f = bucket_dir / "context.txt"
    old = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""

    updates: dict = {}
    if kind is not None:
        updates["kind"] = kind
    if spend is not None:
        updates["spend"] = _fmt_num(spend)
    if spend_unit is not None:
        updates["spend_unit"] = spend_unit
    if acquired is not None:
        updates["acquired"] = acquired
    if not updates:
        raise ValueError("no keys given to write")

    new = upsert_context_text(old, updates)
    changed = new != old
    if changed and not dry_run:
        f.write_text(new, encoding="utf-8")
    return f, changed, updates


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="context", description="Write kind:/spend:/spend_unit:/acquired: "
        "keys into a bucket's context.txt (#118) — preserves any existing prose.")
    ap.add_argument("bucket_dir", metavar="BUCKET_DIR",
                    help="the directory that owns (or should own) context.txt — "
                         "point it at the bucket itself, not an item folder inside it.")
    ap.add_argument("--kind", choices=sorted(_KINDS),
                    help="event (a single acquisition — has a real ROI) or "
                         "channel (an ongoing habit, e.g. FREE/THRIFT — no ROI).")
    ap.add_argument("--spend", type=str,
                    help="cost basis, e.g. 575 or 12.50 or $1,200 — what it MEANS "
                         "is set by --spend-unit (default lot if not given).")
    ap.add_argument("--spend-unit", dest="spend_unit", choices=sorted(_SPEND_UNITS),
                    help="what --spend means: lot (the whole acquisition, "
                         "default), item (a per-item rate), or pair (a "
                         "per-pair rate) — resolved by source_report.py "
                         "against the bucket's own sold+live+pending count.")
    ap.add_argument("--acquired", help="free-text acquisition date/marker, e.g. 2026-07.")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would change without writing anything.")
    args = ap.parse_args(argv)

    bdir = Path(args.bucket_dir)
    if not bdir.is_dir():
        ap.error(f"not a directory: {bdir}")

    spend_val = None
    if args.spend is not None:
        spend_val = _to_float(args.spend)
        if spend_val is None:
            ap.error(f"--spend: not a number: {args.spend!r}")

    if not any(v is not None for v in (args.kind, spend_val, args.spend_unit, args.acquired)):
        ap.error("nothing to set — pass at least one of "
                 "--kind / --spend / --spend-unit / --acquired")

    f, changed, keys = write_context(
        bdir, kind=args.kind, spend=spend_val, spend_unit=args.spend_unit,
        acquired=args.acquired, dry_run=args.dry_run)

    try:
        shown = f.resolve().relative_to(REPO)
    except ValueError:
        shown = f
    for k, v in keys.items():
        print(f"  {k}: {v}")
    if changed:
        print(f"[{'would write' if args.dry_run else 'OK'}] {shown}")
    else:
        print(f"[no change] {shown} — already set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
