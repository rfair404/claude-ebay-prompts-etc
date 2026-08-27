"""One-line tool verdicts — the v4 terse-output convention (V4_PLAN Phase 2).

A tool's default stdout is a VERDICT, not a narration:

      FLAG <name>  <reason>            (one line per flagged item, first)
    <label>: OK <ok>/<total>[, <k> flagged] -> <detail>
      next: <hint>                     (only when there is a next step)

The detail file — usually JSON the tool already writes, e.g. PREP's
`.prep/prep.json` manifest — carries everything else and is read only when
something is flagged. A flagged row is an EXCEPTION the operator (or the
model driving the run) should look at; routine per-item success lines are
noise and never print.

ASCII only ("->", "FLAG"): these lines must survive any Windows console
codepage without the tool caring.
"""
from __future__ import annotations

from typing import Iterable, Tuple


def emit(label: str, total: int,
         flagged: Iterable[Tuple[str, str]] = (),
         detail=None, next_hint: str = "") -> str:
    """Print flagged rows then the one-line verdict; return the verdict line.

    `flagged` is (name, reason) pairs. `detail` is the path of the file
    holding the full record (printed verbatim; pass a Path or str).
    """
    rows = list(flagged)
    for name, reason in rows:
        print(f"  FLAG {name}  {reason}")
    line = f"{label}: OK {total - len(rows)}/{total}"
    if rows:
        line += f", {len(rows)} flagged"
    if detail is not None:
        line += f" -> {detail}"
    print(line)
    if next_hint:
        print(f"  next: {next_hint}")
    return line
