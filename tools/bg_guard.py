#!/usr/bin/env python3
"""BACKGROUND GUARD — refuse a foreground shell call that should have been backgrounded (#121).

RUN.md has carried the rule since #74: a fixed list of command prefixes always
starts with `run_in_background`, "by command prefix, not judgment call". The
7-day observer read that produced #121 measured what the documented rule is
actually worth: **54 Bash calls over 30s totalling 9.8h, with 2.2% of 947 calls
backgrounded**. A rule that nobody follows is not a rule, it is a comment.

So this is the same rule with teeth. It runs as a `PreToolUse` hook on `Bash`,
reads the tool input, and denies the call when a listed prefix appears in the
command and `run_in_background` is not set. The deny message names the prefix
and says what to do, so the fix is one re-issue of the same call.

Deliberate design choices, each of which is a way this could have gone wrong:

- **Prefixes are matched per shell segment**, not against the whole string.
  Nearly every real call in this repo is `cd "<dir>" && python -m ...`, so a
  `startswith` test on the raw command would have matched almost nothing and
  the guard would have been decoration.
- **Segmenting is quote-aware.** The first version split on `&&`/`;`/`|`
  anywhere, and promptly denied the command that pipe-tested it — the operator
  it split on was inside a quoted JSON argument, leaving a fragment that
  *started with* `python -m pytest`. Any command that merely mentions one of
  these tools inside a string (a grep, a heredoc, docs about the rule) would
  have been blocked. Operators inside quotes are not separators.
- **The list here is the source of truth**, and `tests/test_bg_guard.py`
  asserts RUN.md still names every entry. The failure mode for a rule that
  lives in two places is that they drift and neither is trusted.
- **There is one escape hatch, and it leaves a trace.** Prefix the command with
  `# fg-ok: <reason>` to run it in the foreground anyway. Some of these tools
  have genuinely instant flags (`prep --status`), and a guard with no way out
  gets disabled wholesale the first time it is wrong. A stated reason in the
  command line is cheap; a silently weakened rule is not.
- **Unparseable input allows.** A guard that blocks the shell because its own
  JSON was malformed would be worse than the friction it prevents.

    echo '{"tool_name":"Bash","tool_input":{"command":"python -m pytest"}}' \
        | python tools/bg_guard.py        # -> deny, exit 0
"""
from __future__ import annotations

import json
import re
import sys

# Every prefix RUN.md's "Always start these backgrounded" list names, in its
# real invocation form. Keep in sync with RUN.md — test_bg_guard enforces it.
SLOW_PREFIXES = (
    "python -m lib.photo_prep.prep",
    "python -m lib.cli prep",
    "python -m lib.cli single-pass",
    "python -m lib.cli observe",
    "python -m pytest",
    "python tests/run_all.py",
    "python tools/ledger_reconcile.py",
    "python tools/prep_sheet_html.py",
    "python tools/review_card_html.py",
    "python lib/ebay_sold_browse.py",
    "python lib/lens_id.py",
    "python lib/list_edit.py",
    "python tools/reindex_",
)

# `cd x && python …`, `a; b`, `a | b` — a prefix can sit in any segment.
_SEPARATORS = ("&&", "||", ";", "|", "\n")
# `python3 …` and `py …` are the same runner; normalise before matching.
_RUNNER = re.compile(r"^(python3|py)\b")
_ESCAPE = re.compile(r"^\s*#\s*fg-ok\b", re.I)


def segments(command: str) -> list[str]:
    """The command split into shell segments, each stripped and normalised.

    Quote-aware: a separator inside '…' or "…" is argument text, not a break.
    Without that, `echo '… && python -m pytest'` splits into a fragment that
    starts with the prefix and the guard blocks a command that runs nothing.
    """
    out, buf, quote, i = [], [], None, 0
    text = command or ""
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(text):
                buf.append(text[i + 1])        # escaped char stays inside the quote
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        hit = next((s for s in _SEPARATORS if text.startswith(s, i)), None)
        if hit:
            out.append("".join(buf))
            buf = []
            i += len(hit)
            continue
        buf.append(ch)
        i += 1
    out.append("".join(buf))

    cleaned = []
    for raw in out:
        seg = raw.strip()
        while seg[:1] in ("(", "{"):
            seg = seg[1:].strip()
        cleaned.append(_RUNNER.sub("python", seg))
    return cleaned


def offending_prefix(command: str) -> str | None:
    """The first slow prefix this command runs, or None.

    Returns None when the `# fg-ok:` escape is present — the operator (or the
    model, on the record) has said why the foreground run is correct here.
    """
    if _ESCAPE.match(command or ""):
        return None
    for seg in segments(command):
        for prefix in SLOW_PREFIXES:
            if seg.startswith(prefix):
                return prefix
    return None


def verdict(tool_input: dict) -> str | None:
    """The deny reason for this Bash tool input, or None to allow."""
    if tool_input.get("run_in_background"):
        return None
    prefix = offending_prefix(tool_input.get("command", ""))
    if not prefix:
        return None
    return (
        f"`{prefix}` is on RUN.md's always-background list and this call has no "
        f"run_in_background. Measured in #121: 54 foreground shell calls over 30s "
        f"cost 9.8h of blocked wall-clock in one week.\n"
        f"Re-issue the SAME command with run_in_background: true, then carry on "
        f"with the next step — its output is available on demand and the result "
        f"arrives on its own when it lands. Do not poll it with sleep.\n"
        f"If this particular invocation really is instant (a --status or --check "
        f"read), prefix the command with `# fg-ok: <reason>` and it will run."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:                       # malformed input must never block work
        return 0
    if payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    reason = verdict(payload.get("tool_input") or {})
    if not reason:
        return 0
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
