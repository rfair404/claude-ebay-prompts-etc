#!/usr/bin/env python3
"""SESSION OBSERVER — read our own transcripts and find the friction (#36, V4_PLAN Phase 5).

Every session between us is already recorded: Claude Code writes one JSONL per
session under `~/.claude/projects/<slug>/`, with timestamps, token usage, every
tool call and every tool result. Nothing reads them. So the same friction keeps
happening — a tool that errors the same way every batch, a question I ask that
you have already answered three sessions running, a prompt that takes twenty
round-trips because the first answer missed.

Stage 1 is the parser and the report. It measures where the time and the
tokens went, and it counts six friction signals:

  tool_error   a tool call came back is_error — the loop paid for a retry
  denied       a permission prompt was declined — I asked for the wrong thing
  interrupt    you stopped a turn in flight — I was going the wrong way
  redo         your next message opens with a correction ("no", "actually")
  repeat       the same Bash command or file read 3+ times in one session
  long_loop    one ask that needed LOOP_TURNS+ assistant turns to answer

Stage 2 turns those signals (plus the #61 economics numbers) into ranked,
concrete proposed fixes (`propose_fixes`) and can file them as a house
`Idea:` issue — or a comment on a matching OPEN one, so ten review windows
with the same finding produce one issue with ten data points, not ten
issues (`file_proposals`). "A counter is evidence; a filed issue is a
claim" still holds: `--propose` alone only ever prints what it found and
what it WOULD file — nothing reaches GitHub unless `--file` is also
passed, explicitly, by a human, every time.

  session_observer.py [--days 7] [--limit 20] [--all] [--report]
                       [--economics] [--propose [--file]] [--json out.json]

Three honesty notes. The signals are heuristics over text: "redo" reads the
first words of your message, so a cheerful "no, that's perfect" counts as a
correction it isn't. `is_error` counts an expected probe (a grep that finds
nothing, a --check that reports work to do) the same as a real failure, so the
tool_error count is an upper bound — read the samples, not the number. And
active time is wall time with gaps over IDLE_GAP dropped, which is a guess at
attention, not a timesheet: a five-minute think while the screen sits idle
looks the same as walking away.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent

IDLE_GAP = 300         # s — a longer gap is you away from the desk, not work
LOOP_TURNS = 25        # assistant turns on one ask before it counts as a long loop
REPEAT_HITS = 3        # identical command/read this many times = a repeat
MAX_LINE = 500_000     # chars — longer lines are attachments/base64, skipped

# The first words of a message that mean "that was wrong, do it again".
REDO = re.compile(
    r"^\W*(no+\b|nope\b|nah\b|actually\b|wrong\b|that'?s not\b|thats not\b|"
    r"not (?:what|quite|right)\b|don'?t\b|stop\b|undo\b|revert\b|instead\b|"
    r"i said\b|again\b|still (?:not|wrong|broken)\b)", re.I)
DENIED = re.compile(r"user doesn'?t want|rejected|denied permission|"
                    r"user has denied|not allowed by permission", re.I)
INTERRUPT = re.compile(r"\[Request interrupted", re.I)
# A pasted photo arrives as a size placeholder ahead of the actual words; it is
# not what you asked, and left in it becomes the label on every PRICE hot spot.
IMAGE_LINE = re.compile(r"^\s*\[Image:[^\]]*\]\s*$", re.M)
# The observer prints its own samples, so a session where it ran contains the
# marker text it looks for. Results from its own invocations are not evidence.
SELF = re.compile(r"session_observer|cli observe|ebz observe", re.I)

# Stage attribution. Each stage owns prompt words and tool-input fragments; the
# stage with the most hits over one ask wins. Deliberately coarse — this says
# "that was a PRICE ask", not which rule inside PRICE was slow.
STAGES = {
    "PREP":     ("prep", "photo", "crop", "orient", "rotate", "unskew", "hero",
                 "contact sheet", "photo_prep", "prep_run", "prep_card"),
    "IDENTIFY": ("identify", "what is this", "maker", "marble", "hallmark",
                 "identify.txt", "marble_triage", "marble_decide"),
    "PRICE":    ("price", "comp", "apify", "ceiling", "median", "sold for",
                 "price_stats", "comps_board", "price.txt"),
    "DRAFT":    ("draft", "title", "description", "voice", "write the copy",
                 "draft.md", "voice_check"),
    "REVIEW":   ("review", "approve", "review_card", "needs_review"),
    "LIST":     ("publish", "list it", "sync", "relist", "offer", "list_edit",
                 "--publish", "--list", "--update", "--sync"),
    "OPS":      ("ledger", "reconcile", "audit", "sales", "pick list", "ship",
                 "postage", "promote", "sales_report", "live_audit"),
    "DEV":      ("git ", "gh ", "commit", "branch", "test", "refactor", "bug",
                 "pytest", "tests/", "lib/", "tools/"),
}


# $/1M tokens (input, output) — first-party API rates. Matched by longest
# known model-id prefix so a future dated variant of a listed model still
# resolves. Cache write/read aren't priced per model in the pricing table;
# they scale off the input rate at the standard Anthropic multipliers
# (~1.25x to write, ~0.1x to read) rather than a second table to keep in
# sync. An unrecognized model (a future release, a proxied name) falls back
# to DEFAULT_PRICING rather than costing $0 — "approximately right" beats
# "silently free" for a report whose whole point is catching overspend.
MODEL_PRICING = {
    "claude-opus-5": (5.00, 25.00),
    "claude-mythos-5": (10.00, 50.00),
    "claude-mythos-preview": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
}
DEFAULT_PRICING = (3.00, 15.00)   # unrecognized model: mid-tier fallback rate
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10


def _model_rates(model) -> tuple:
    if isinstance(model, str):
        for prefix in sorted(MODEL_PRICING, key=len, reverse=True):
            if model.startswith(prefix):
                return MODEL_PRICING[prefix]
    return DEFAULT_PRICING


def _turn_cost_usd(usage: dict, model) -> float:
    """One assistant turn's $ cost from its own usage block — #71 §7's
    '$ per published listing' needs a dollar figure and the observer only
    ever tracked tokens before this."""
    in_rate, out_rate = _model_rates(model)
    input_tok = usage.get("input_tokens") or 0
    output_tok = usage.get("output_tokens") or 0
    cache_w = usage.get("cache_creation_input_tokens") or 0
    cache_r = usage.get("cache_read_input_tokens") or 0
    return (input_tok * in_rate
            + output_tok * out_rate
            + cache_w * in_rate * CACHE_WRITE_MULT
            + cache_r * in_rate * CACHE_READ_MULT) / 1_000_000


def _slug(text: str) -> str:
    """Claude Code slugifies a path: every ':', '\\', '/' and '.' becomes '-'.

    The '.' matters. A worktree cwd is `<repo>/.claude/worktrees/<name>`, and
    `.claude` slugifies to `-claude`, giving `...ebaybiz--claude-worktrees-...`
    with a doubled dash. Leaving '.' out of the class resolves to a directory
    that does not exist — invisible in the main checkout, which has no dot in
    its path, and wrong in every worktree, which is where CLAUDE.md requires
    any session that changes git state to work.
    """
    return re.sub(r"[:\\/.]", "-", text)


def transcripts_dir(repo: Path) -> Path:
    return Path.home() / ".claude" / "projects" / _slug(str(repo))


def _near_miss_hint(missing: Path, limit: int = 5) -> list[str]:
    """Lines naming real project dirs that match this repo, or [] if none.

    Any future slug quirk — a space, an underscore, an upstream rule change —
    fails the same silent way this one did, so the check is on the shape of
    the answer (nothing resolved, yet siblings match) rather than on '.'.
    """
    stem = _slug(REPO.name)
    try:
        near = sorted(p.name for p in missing.parent.iterdir()
                      if p.is_dir() and stem in p.name)
    except OSError:
        return []
    if not near:
        return []
    out = [f"  {len(near)} directory(ies) there do match {stem!r} — the slug is "
           f"probably wrong, not the history. Try --dir with one of:"]
    out += [f"    {missing.parent / n}" for n in near[:limit]]
    if len(near) > limit:
        out.append(f"    ... and {len(near) - limit} more")
    return out


def _ts(rec: dict):
    t = rec.get("timestamp")
    if not isinstance(t, str) or not t:
        # A malformed line can carry a number, a null or a nested object here.
        # Anything but a string is unparseable, and "never raises on bad lines"
        # has to hold for the wrong TYPE as well as the wrong format.
        return None
    try:
        parsed = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A transcript timestamp without an offset would otherwise come back
    # naive, and mixing naive/aware datetimes raises on any later comparison
    # or subtraction (wall/active time) — exactly what "never raises on bad
    # lines" is supposed to prevent. Same normalization as _date() in
    # tools/sales_report.py: absent offset means UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _text_of(content) -> str:
    """Flatten a message content field to searchable text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                out.append(b.get("text") or "")
        return "\n".join(out)
    return ""


def _clean_prompt(text: str) -> str:
    """Drop image placeholders so an ask is labelled by its words, not its pixels."""
    stripped = IMAGE_LINE.sub("", text).strip()
    return stripped or "[image only]"


def _tool_signature(name: str, inp: dict) -> str:
    """A short, comparable identity for a tool call — what a repeat repeats."""
    if not isinstance(inp, dict):
        return name
    for key in ("command", "file_path", "pattern", "path", "url", "query"):
        v = inp.get(key)
        if isinstance(v, str) and v.strip():
            return f"{name}:{' '.join(v.split())[:160]}"
    return name


_CD_PREFIX = re.compile(r'^cd\s+(?:"[^"]*"|\'[^\']*\'|\S+)\s*&&\s*')

# A Bash command collapsed to a stable "repo command" bucket — the shape
# #61/#71's manual command tallies used by hand. Ordered most-specific first
# so e.g. `python -m lib.cli prep ...` doesn't fall through to a bare
# `python -m` bucket before its own pattern gets a look.
_CMD_BUCKETS = [
    (re.compile(r"^python3?\s+-m\s+lib\.cli\s+(\S+)"),
     lambda m: f"python -m lib.cli {m.group(1)}"),
    (re.compile(r"^python3?\s+-m\s+(\S+)"), lambda m: f"python -m {m.group(1)}"),
    (re.compile(r"^python3?\s+((?:tools|lib)/\S+\.py)"), lambda m: f"python {m.group(1)}"),
    (re.compile(r"^pytest\b"), lambda m: "pytest"),
    (re.compile(r"^git\s+(\S+)"), lambda m: f"git {m.group(1)}"),
    (re.compile(r"^gh\s+(\S+)(?:\s+(\S+))?"),
     lambda m: f"gh {m.group(1)}" + (f" {m.group(2)}" if m.group(2) else "")),
    (re.compile(r"^ebz\s+(\S+)"), lambda m: f"ebz {m.group(1)}"),
]


def _command_bucket(command: str) -> str:
    """Collapse a Bash command to a repo-command bucket for the foreground
    blocking hours breakdown: 'python -m lib.photo_prep.prep', 'pytest',
    'git status' — a `cd ... &&` prefix or trailing flags don't change it."""
    cmd = _CD_PREFIX.sub("", (command or "").strip()).strip()
    if not cmd:
        return "(empty)"
    for pattern, fmt in _CMD_BUCKETS:
        m = pattern.match(cmd)
        if m:
            return fmt(m)
    return cmd.split()[0]


# The three shapes a PREP run reaches lib/photo_prep/prep.py's main() by:
# the module directly, through the `ebz`/`lib.cli` dispatcher, or the
# tools/ path some older docs still show. All three take the shoot dir as
# their first positional argument (lib/photo_prep/prep.py: `ap.add_argument
# ("shoot_dir")`).
PREP_INVOCATION = re.compile(
    r"(?:python3?\s+-m\s+lib\.photo_prep\.prep|python3?\s+-m\s+lib\.cli\s+prep|"
    r"python3?\s+tools/prep(?:_run)?\.py|\bebz\s+prep)\b(.*)$"
)


def _prep_item_dir(command: str):
    """The shoot/item directory argument to a `prep` invocation, or None
    when the command isn't one (#71 §7: re-runs per item for `prep`)."""
    cmd = _CD_PREFIX.sub("", (command or "").strip())
    m = PREP_INVOCATION.search(cmd)
    if not m:
        return None
    for tok in m.group(1).split():
        if not tok.startswith("-"):
            return tok
    return None


def _classify(blob: str) -> str:
    blob = blob.lower()
    scores = {s: sum(blob.count(w) for w in words) for s, words in STAGES.items()}
    best = max(scores, key=lambda s: scores[s])
    return best if scores[best] else "OTHER"


class Ask:
    """One human prompt and everything the loop did to answer it."""

    def __init__(self, prompt: str, started):
        self.prompt = prompt
        self.started = started
        self.ended = started
        self.turns = 0
        self.tools = 0
        self.out_tokens = 0
        self.blob = [prompt]
        self.friction = []

    @property
    def seconds(self) -> float:
        if not (self.started and self.ended):
            return 0.0
        return max(0.0, (self.ended - self.started).total_seconds())

    @property
    def stage(self) -> str:
        return _classify("\n".join(self.blob[:400]))


def parse_session(path: Path) -> dict:
    """Stream one transcript into a per-session record. Never raises on bad lines."""
    asks: list[Ask] = []
    cur = None
    tool_names: dict[str, str] = {}       # tool_use_id -> tool name
    self_calls: set = set()               # tool_use_ids of the observer's own runs
    sigs = Counter()
    tokens = Counter()
    stamps = []
    skipped = 0
    friction = []
    model = None

    def note(kind, detail, sample=""):
        item = {"kind": kind, "detail": detail}
        if sample:
            item["sample"] = " ".join(sample.split())[:200]
        friction.append(item)
        if cur is not None:
            cur.friction.append(kind)

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if len(line) > MAX_LINE:      # attachment / base64 payload
                skipped += 1
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            kind = rec.get("type")
            if kind not in ("user", "assistant"):
                continue
            if rec.get("isSidechain"):    # subagent traffic, not our conversation
                continue
            when = _ts(rec)
            if when:
                stamps.append(when)
            msg = rec.get("message") or {}
            content = msg.get("content")

            if kind == "user":
                blocks = content if isinstance(content, list) else []
                results = [b for b in blocks
                           if isinstance(b, dict) and b.get("type") == "tool_result"]
                if results:
                    for b in results:
                        tid = b.get("tool_use_id")
                        name = tool_names.get(tid, "?")
                        if tid in self_calls:
                            continue          # the observer reading itself
                        body = _text_of(b.get("content")) or str(b.get("content") or "")
                        if b.get("is_error"):
                            if DENIED.search(body):
                                note("denied", name, body)
                            else:
                                note("tool_error", name, body)
                        elif INTERRUPT.search(body):
                            note("interrupt", name, body)
                    if cur is not None and when:
                        cur.ended = when
                    continue
                text = _text_of(content)
                if not text.strip() or text.lstrip().startswith("<system-reminder>"):
                    continue
                if INTERRUPT.search(text):
                    note("interrupt", "user", text)
                    continue
                cur = Ask(_clean_prompt(text), when)
                asks.append(cur)
                if REDO.match(text) and len(asks) > 1:
                    note("redo", "prompt opens with a correction", text)
                continue

            # assistant
            model = msg.get("model") or model
            usage = msg.get("usage") or {}
            for k in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens"):
                tokens[k] += usage.get(k) or 0
            if cur is not None:
                cur.turns += 1
                cur.out_tokens += usage.get("output_tokens") or 0
                if when:
                    cur.ended = when
            for b in (content if isinstance(content, list) else []):
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                name = b.get("name") or "?"
                tool_names[b.get("id")] = name
                sig = _tool_signature(name, b.get("input") or {})
                # Match the WHOLE input, not the truncated signature: a long cd
                # prefix pushes "observe" past the signature's 160-char cut.
                if SELF.search(json.dumps(b.get("input") or {})):
                    self_calls.add(b.get("id"))
                else:
                    self_calls.discard(b.get("id"))   # last write wins, as for names
                sigs[sig] += 1
                if cur is not None:
                    cur.tools += 1
                    cur.blob.append(sig)

    for ask in asks:
        if ask.turns >= LOOP_TURNS:
            friction.append({"kind": "long_loop",
                             "detail": f"{ask.turns} turns on one ask",
                             "sample": " ".join(ask.prompt.split())[:200]})
            ask.friction.append("long_loop")
    for sig, n in sigs.items():
        if n >= REPEAT_HITS and sig.split(":", 1)[0] in ("Bash", "Read", "Grep", "PowerShell"):
            friction.append({"kind": "repeat", "detail": f"{n}x", "sample": sig})

    stamps.sort()
    wall = (stamps[-1] - stamps[0]).total_seconds() if len(stamps) > 1 else 0.0
    active = sum(min((b - a).total_seconds(), IDLE_GAP)
                 for a, b in zip(stamps, stamps[1:])) if len(stamps) > 1 else 0.0
    by_stage = defaultdict(lambda: {"asks": 0, "turns": 0, "seconds": 0.0, "out_tokens": 0})
    for ask in asks:
        s = by_stage[ask.stage]
        s["asks"] += 1
        s["turns"] += ask.turns
        s["seconds"] += min(ask.seconds, IDLE_GAP * 12)
        s["out_tokens"] += ask.out_tokens

    return {
        "session": path.stem,
        "file": str(path),
        "started": stamps[0].isoformat() if stamps else None,
        "model": model,
        "wall_seconds": round(wall, 1),
        "active_seconds": round(active, 1),
        "asks": len(asks),
        "turns": sum(a.turns for a in asks),
        "tools": sum(a.tools for a in asks),
        "tokens": dict(tokens),
        "skipped_lines": skipped,
        "by_stage": {k: v for k, v in sorted(by_stage.items())},
        "friction": friction,
        "hot_spots": sorted(
            ({"stage": a.stage, "turns": a.turns, "tools": a.tools,
              "seconds": round(a.seconds, 1), "out_tokens": a.out_tokens,
              "friction": sorted(set(a.friction)),
              "prompt": " ".join(a.prompt.split())[:160]}
             for a in asks if a.turns),
            key=lambda h: -h["turns"])[:5],
    }


def _hms(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m" if sec >= 3600 else f"{sec // 60}m"


def _k(n) -> str:
    return f"{n / 1000:.0f}k" if n >= 1000 else str(n)


def aggregate_friction(sessions: list[dict], top: int = 8) -> dict:
    """The structured numbers `report()` renders — split out so
    `propose_fixes()` builds its proposals from the exact same aggregate the
    human report shows, instead of a second, driftable computation of the
    same thing."""
    kinds: Counter = Counter()
    samples = defaultdict(list)
    stages = defaultdict(lambda: {"asks": 0, "turns": 0, "seconds": 0.0, "out_tokens": 0})
    tok: Counter = Counter()
    for s in sessions:
        for f in s["friction"]:
            kinds[f["kind"]] += 1
            if len(samples[f["kind"]]) < top:
                samples[f["kind"]].append((s["session"][:8], f))
        for name, v in s["by_stage"].items():
            for k in ("asks", "turns", "seconds", "out_tokens"):
                stages[name][k] += v[k]
        tok.update(s["tokens"])
    hot = sorted((h for s in sessions for h in s["hot_spots"]),
                key=lambda h: -h["turns"])[:top]
    return {
        "n_sessions": len(sessions),
        "asks": sum(s["asks"] for s in sessions),
        "turns": sum(s["turns"] for s in sessions),
        "active_seconds": sum(s["active_seconds"] for s in sessions),
        "kinds": kinds, "samples": samples, "stages": dict(stages),
        "tokens": tok, "hot_spots": hot,
    }


def report(sessions: list[dict], top: int = 8) -> str:
    """The human-readable friction report — what to read before filing anything."""
    agg = aggregate_friction(sessions, top)
    kinds, samples, stages, tok = agg["kinds"], agg["samples"], agg["stages"], agg["tokens"]
    out = []
    out.append(f"SESSIONS {agg['n_sessions']}  asks {agg['asks']}  turns {agg['turns']}  "
               f"active {_hms(agg['active_seconds'])}  out {_k(tok['output_tokens'])} tok "
               f"(cache read {_k(tok['cache_read_input_tokens'])})")
    out.append("")
    out.append("WHERE THE WORK WENT")
    # "elapsed", not "active": the session line above sums gap-capped deltas
    # between turns, this column sums each ask's start-to-end span (capped at
    # IDLE_GAP * 12). One ask that sat open over lunch counts here and not
    # there, so giving both the same label would overstate stage time.
    out.append(f"  {'stage':<9} {'asks':>5} {'turns':>6} {'turns/ask':>10} "
               f"{'elapsed':>8} {'out tok':>9}")
    for name, v in sorted(stages.items(), key=lambda kv: -kv[1]["turns"]):
        per = v["turns"] / v["asks"] if v["asks"] else 0
        out.append(f"  {name:<9} {v['asks']:>5} {v['turns']:>6} {per:>10.1f} "
                   f"{_hms(v['seconds']):>8} {_k(v['out_tokens']):>9}")
    out.append("")
    out.append("FRICTION")
    if not kinds:
        out.append("  none of the six signals fired")
    for kind, n in kinds.most_common():
        out.append(f"  {kind:<11} {n}")
        for sid, f in samples[kind][:3]:
            out.append(f"      {sid}  {f['detail']}  {f.get('sample', '')[:110]}")
    out.append("")
    out.append("LONGEST ASKS")
    for h in agg["hot_spots"]:
        flag = (" [" + ",".join(h["friction"]) + "]") if h["friction"] else ""
        out.append(f"  {h['stage']:<9} {h['turns']:>3} turns  {_hms(h['seconds']):>5}  "
                   f"{_k(h['out_tokens']):>5}  {h['prompt'][:80]}{flag}")
    return "\n".join(out)


IMAGE_EXT = (".jpg", ".jpeg", ".png", ".heic", ".webp", ".tiff", ".gif")


def _turn_context(usage: dict) -> int:
    """The full payload one assistant API call re-sent — the number the bill
    actually scales with (#61: "bill ~= turns x context per turn")."""
    return ((usage.get("input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
            + (usage.get("cache_read_input_tokens") or 0))


def _ledger_path() -> Path:
    """Same resolution order as lib/list_edit.py's _ledger_path(): explicit
    env override first (also how tests point this at a synthetic ledger),
    else <repo>/listings_ledger.csv."""
    env = os.environ.get("EBAYBIZ_LISTINGS_LEDGER") or os.environ.get("EBAYBIZ_LISTINGS_LOG")
    return Path(env) if env else REPO / "listings_ledger.csv"


def _ledger_rows() -> list:
    path = _ledger_path()
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def _money(raw) -> float:
    try:
        return float(str(raw).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _parse_ledger_ts(raw: str):
    """Same UTC-normalization idiom as _ts() above and _date() in
    tools/sales_report.py: a ledger timestamp with no offset means UTC."""
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def cost_per_listing(total_cost_usd: float, ledger_rows: list,
                     window_start, window_end) -> dict:
    """#71 §7: total model spend against listings actually PUBLISHED in the
    same window, joined on `published_at` — the batch ratio the audit
    measured (3-day window: ~$2,990 spend / 43 published listings =
    ~$69.55/listing, spend exceeding the $1,728.84 those listings were
    worth at ask), not a per-listing session attribution — there isn't one.

    `window_start`/`window_end` are `None` for an unbounded (--all) window.
    """
    published = []
    for r in ledger_rows:
        ts = _parse_ledger_ts((r.get("published_at") or "").strip())
        if ts is None:
            continue
        if window_start is not None and ts < window_start:
            continue
        if window_end is not None and ts > window_end:
            continue
        published.append(r)
    n = len(published)
    total_list_price = sum(_money(r.get("price")) for r in published)
    return {
        "total_cost_usd": total_cost_usd,
        "n_published": n,
        "total_list_price": total_list_price,
        "cost_per_listing": (total_cost_usd / n) if n else None,
        "cost_to_list_price_ratio": (total_cost_usd / total_list_price)
                                    if total_list_price else None,
    }


def _in_window(ts, window_start, window_end) -> bool:
    """A malformed/missing timestamp can't be reliably placed in any
    window — bounded or not — so it's always excluded, never counted as
    "in an unbounded window" by default."""
    if ts is None:
        return False
    if window_start is not None and ts < window_start:
        return False
    if window_end is not None and ts > window_end:
        return False
    return True


def parse_economics(path: Path, *, window_start=None, window_end=None) -> dict:
    """One session's #61-style run economics: context/turn, tool-call
    batching, Read payload by extension, backgrounding, and gate wall-clock
    by header. A separate pass from parse_session's friction walk on
    purpose — it answers "what did this session cost", not "what went
    wrong", and bolting a second question onto the six-signal walk risks
    the signals that walk already locks down.

    `window_start`/`window_end` (both None by default = unbounded, matching
    prior behavior) bound `cost_usd` and the `fg_seconds_by_*` dicts to
    turns whose OWN timestamp falls in the window — file-mtime selection
    alone isn't enough for a long-lived/resumed session (#71 §5: sessions
    spanning hundreds of hours), which would otherwise have turns from
    outside the requested window counted toward $/published-listing and
    foreground-hours totals for that window. The structural counters
    (context/turn, tool-call shape, read payload, bash/backgrounding
    counts) stay file-level — they describe session shape, not a
    window-scoped cost, and #71 didn't flag them as skewed."""
    context_per_turn: list[int] = []
    tools_per_turn: list[int] = []
    same_tool_adjacent = 0
    last_tool = None
    read_bytes: Counter = Counter()
    read_calls: Counter = Counter()
    bash_total = bg_bash = bash_over30 = 0
    bash_over30_secs = 0.0
    ask_count: Counter = Counter()
    ask_wait: Counter = Counter()
    pending: dict = {}   # tool_use_id -> (ts, name, input)
    cost_usd = 0.0
    model = None
    fg_seconds_by_tool: Counter = Counter()
    fg_seconds_by_command: Counter = Counter()
    prep_calls: Counter = Counter()      # item/shoot dir -> `prep` invocations

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if len(line) > MAX_LINE:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("isSidechain"):
                continue
            kind = rec.get("type")
            when = _ts(rec)

            if kind == "assistant":
                msg = rec.get("message") or {}
                model = msg.get("model") or model
                if _in_window(when, window_start, window_end):
                    cost_usd += _turn_cost_usd(msg.get("usage") or {}, model)
                blocks = [b for b in (msg.get("content") or [])
                          if isinstance(b, dict) and b.get("type") == "tool_use"]
                if blocks:
                    context_per_turn.append(_turn_context(msg.get("usage") or {}))
                    tools_per_turn.append(len(blocks))
                for b in blocks:
                    name = b.get("name") or "?"
                    inp = b.get("input") or {}
                    pending[b.get("id")] = (when, name, inp)
                    if last_tool == name:
                        same_tool_adjacent += 1
                    last_tool = name
                    if name == "Read":
                        ext = Path(str(inp.get("file_path") or "")).suffix.lower() or "(none)"
                        read_calls[ext] += 1
                    elif name in ("Bash", "PowerShell"):
                        bash_total += 1
                        if inp.get("run_in_background"):
                            bg_bash += 1
                        item = _prep_item_dir(str(inp.get("command") or ""))
                        if item:
                            prep_calls[item] += 1
                    elif name == "AskUserQuestion":
                        for q in (inp.get("questions") or []):
                            ask_count[(q.get("header") or "?")[:24]] += 1

            elif kind == "user":
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict) or b.get("type") != "tool_result":
                        continue
                    st, name, inp = pending.pop(b.get("tool_use_id"), (None, None, {}))
                    if not (st and when and name):
                        continue
                    secs = (when - st).total_seconds()
                    # Foreground blocking hours by tool + repo command (#71
                    # §7): every tool call blocks the turn UNLESS it's a
                    # Bash/PowerShell call that opted into run_in_background.
                    backgrounded = (name in ("Bash", "PowerShell")
                                    and bool(inp.get("run_in_background")))
                    # Windowed on the call's OWN (start) timestamp, not its
                    # completion time — a long call starting just inside the
                    # window but finishing just after it is still that
                    # window's call, matching the docstring's contract.
                    if not backgrounded and _in_window(st, window_start, window_end):
                        fg_seconds_by_tool[name] += secs
                        if name in ("Bash", "PowerShell"):
                            fg_seconds_by_command[
                                _command_bucket(str(inp.get("command") or ""))] += secs
                    if name == "Read":
                        body = b.get("content")
                        sz = len(json.dumps(body)) if body is not None else 0
                        ext = Path(str(inp.get("file_path") or "")).suffix.lower() or "(none)"
                        read_bytes[ext] += sz
                        continue
                    if name in ("Bash", "PowerShell") and secs > 30:
                        bash_over30 += 1
                        bash_over30_secs += secs
                    elif name == "AskUserQuestion":
                        for q in (inp.get("questions") or []):
                            ask_wait[(q.get("header") or "?")[:24]] += secs
                            break

    return {
        "context_per_turn": context_per_turn, "tools_per_turn": tools_per_turn,
        "same_tool_adjacent": same_tool_adjacent,
        "read_calls": dict(read_calls), "read_bytes": dict(read_bytes),
        "bash_total": bash_total, "bg_bash": bg_bash,
        "bash_over30": bash_over30, "bash_over30_secs": bash_over30_secs,
        "ask_count": dict(ask_count), "ask_wait_secs": dict(ask_wait),
        "cost_usd": cost_usd,
        "fg_seconds_by_tool": dict(fg_seconds_by_tool),
        "fg_seconds_by_command": dict(fg_seconds_by_command),
        "prep_calls": dict(prep_calls),
    }


def economics_aggregate(paths: list[Path], *, window_start=None, window_end=None) -> dict:
    """`parse_economics` summed across sessions — the numbers both
    `economics_report()` and `propose_fixes()` read, computed once.

    `window_start`/`window_end` pass straight through to `parse_economics`
    (see its docstring) — the cost/foreground-hour totals only reflect
    turns whose own timestamp falls in the window, not every turn in every
    file that happened to be selected by mtime."""
    all_ctx: list[int] = []
    all_tools: list[int] = []
    same_tool_adjacent = total_turns = 0
    read_bytes: Counter = Counter()
    bash_total = bg_bash = bash_over30 = 0
    bash_over30_secs = 0.0
    ask_count: Counter = Counter()
    ask_wait: Counter = Counter()
    cost_usd = 0.0
    fg_seconds_by_tool: Counter = Counter()
    fg_seconds_by_command: Counter = Counter()
    prep_calls: Counter = Counter()

    for p in paths:
        e = parse_economics(p, window_start=window_start, window_end=window_end)
        all_ctx.extend(e["context_per_turn"])
        all_tools.extend(e["tools_per_turn"])
        same_tool_adjacent += e["same_tool_adjacent"]
        total_turns += len(e["tools_per_turn"])
        read_bytes.update(e["read_bytes"])
        bash_total += e["bash_total"]
        bg_bash += e["bg_bash"]
        bash_over30 += e["bash_over30"]
        bash_over30_secs += e["bash_over30_secs"]
        ask_count.update(e["ask_count"])
        ask_wait.update(e["ask_wait_secs"])
        cost_usd += e["cost_usd"]
        fg_seconds_by_tool.update(e["fg_seconds_by_tool"])
        fg_seconds_by_command.update(e["fg_seconds_by_command"])
        prep_calls.update(e["prep_calls"])

    return {
        "n_sessions": len(paths), "total_turns": total_turns,
        "context_per_turn": sorted(all_ctx), "tools_per_turn": all_tools,
        "same_tool_adjacent": same_tool_adjacent,
        "read_bytes": dict(read_bytes),
        "bash_total": bash_total, "bg_bash": bg_bash,
        "bash_over30": bash_over30, "bash_over30_secs": bash_over30_secs,
        "ask_count": dict(ask_count), "ask_wait_secs": dict(ask_wait),
        "cost_usd": cost_usd,
        "fg_seconds_by_tool": dict(fg_seconds_by_tool),
        "fg_seconds_by_command": dict(fg_seconds_by_command),
        "prep_calls": dict(prep_calls),
    }


def economics_report(paths: list[Path], *, window_start=None, window_end=None) -> str:
    """Aggregate `parse_economics` across sessions into the #61 headline
    table — the standing version of `.scratch/analyze{,2,3,4}.py` — plus
    the three #71 §7 cost lines: $/published listing (joined to
    `listings_ledger.csv` on `published_at`), foreground blocking hours by
    tool and by repo command, and `prep` re-runs per item.

    `window_start`/`window_end` bound both the cost/foreground-hour turn
    accumulation (see `parse_economics`) and the ledger join to the same
    window `paths` was already filtered to (None/None for an unbounded
    --all run — every turn and every published row counts)."""
    agg = economics_aggregate(paths, window_start=window_start, window_end=window_end)
    s = agg["context_per_turn"]
    all_tools = agg["tools_per_turn"]
    total_turns = agg["total_turns"]
    read_bytes = agg["read_bytes"]
    ask_wait = agg["ask_wait_secs"]
    ask_count = agg["ask_count"]

    out = [f"=== ECONOMICS — {agg['n_sessions']} session(s), {total_turns} tool-calling turn(s)"]
    if s:
        out.append(f"  context/turn: mean {_k(int(sum(s) / len(s)))}  "
                   f"median {_k(s[len(s) // 2])}  p90 {_k(s[int(len(s) * .9)])}  "
                   f"max {_k(s[-1])}")
    if all_tools:
        out.append(f"  tool calls/turn: mean {sum(all_tools) / len(all_tools):.2f}  "
                   f"same-tool-adjacent {agg['same_tool_adjacent'] / max(total_turns, 1) * 100:.0f}% of turns")
    img_bytes = sum(v for k, v in read_bytes.items() if k in IMAGE_EXT)
    tot_bytes = sum(read_bytes.values())
    if tot_bytes:
        out.append(f"  Read payload: {img_bytes / 1e6:.1f} MB images of "
                   f"{tot_bytes / 1e6:.1f} MB total ({img_bytes / tot_bytes * 100:.0f}%)")
    if agg["bash_total"]:
        out.append(f"  Bash/PowerShell: {agg['bash_total']} calls, "
                   f"{agg['bg_bash'] / agg['bash_total'] * 100:.1f}% backgrounded, "
                   f"{agg['bash_over30']} over 30s totalling {agg['bash_over30_secs'] / 3600:.1f}h")
    if ask_wait:
        out.append("  AskUserQuestion wait by gate:")
        for h, secs in sorted(ask_wait.items(), key=lambda kv: -kv[1])[:8]:
            out.append(f"    {ask_count.get(h, 0):>3}  {secs / 3600:>6.1f}h  {h}")

    if agg["cost_usd"]:
        out.append(f"  model spend (est.): ${agg['cost_usd']:,.2f}")
        cpl = cost_per_listing(agg["cost_usd"], _ledger_rows(), window_start, window_end)
        if cpl["n_published"]:
            ratio = (f", {cpl['cost_to_list_price_ratio']:.2f}x their ${cpl['total_list_price']:,.2f} "
                     f"list price" if cpl["cost_to_list_price_ratio"] else "")
            out.append(f"  $/published listing: ${cpl['cost_per_listing']:,.2f}  "
                       f"({cpl['n_published']} published in window{ratio})")
        else:
            out.append("  $/published listing: no listings_ledger.csv row published in this window")

    fg_tool = agg["fg_seconds_by_tool"]
    if fg_tool:
        out.append("  Foreground blocking hours by tool:")
        for name, secs in sorted(fg_tool.items(), key=lambda kv: -kv[1])[:8]:
            out.append(f"    {secs / 3600:>6.1f}h  {name}")

    fg_cmd = agg["fg_seconds_by_command"]
    if fg_cmd:
        out.append("  Foreground blocking hours by repo command:")
        for cmd, secs in sorted(fg_cmd.items(), key=lambda kv: -kv[1])[:8]:
            out.append(f"    {secs / 3600:>6.1f}h  {cmd}")

    prep_calls = agg["prep_calls"]
    if prep_calls:
        reruns = {k: v for k, v in prep_calls.items() if v > 1}
        out.append(f"  prep re-runs: {sum(prep_calls.values())} invocation(s) across "
                   f"{len(prep_calls)} item dir(s), {len(reruns)} re-run 2+ times")
        for item, n in sorted(prep_calls.items(), key=lambda kv: (-kv[1], kv[0]))[:8]:
            if n > 1:
                out.append(f"    {n:>3}x  {item}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# stage 2a — friction signals + economics -> ranked, concrete proposed fixes
# ---------------------------------------------------------------------------

# (key, min-hours-to-lead-the-ranking) — impact_hours is 0 for signals that
# don't have a direct time cost (e.g. tool_error), so they still rank behind
# anything with a measured wall-clock number attached.
def propose_fixes(friction_agg: dict, econ_agg: dict) -> list[dict]:
    """Turn the observer's raw numbers into ranked, concrete proposed fixes.

    Every rule is threshold-gated and cites the exact number that tripped
    it — a proposal is a measurement with a recommendation attached, never
    a guess. Thresholds are deliberately the same shape as the fixes #61
    and #74 actually shipped (same categories, same kind of evidence), so
    this is the automated version of the manual pass, not a new heuristic
    invented for this file."""
    proposals: list[dict] = []

    def add(key, title, evidence, fix, impact_hours=0.0):
        proposals.append({"key": key, "title": title, "evidence": evidence,
                          "proposed_fix": fix, "impact_hours": round(impact_hours, 1)})

    read_bytes = econ_agg.get("read_bytes") or {}
    tot_bytes = sum(read_bytes.values())
    img_bytes = sum(v for k, v in read_bytes.items() if k in IMAGE_EXT)
    if tot_bytes > 1_000_000 and img_bytes / tot_bytes > 0.5:
        add("raw_frame_reads",
            "Raw photo frames are being Read into the main thread",
            f"{img_bytes / 1e6:.1f} MB of {tot_bytes / 1e6:.1f} MB of Read payload "
            f"({img_bytes / tot_bytes * 100:.0f}%) is images",
            "Read the contact sheet (tools/prep_card.py / prep_sheet_html.py) or "
            "delegate frame-level looking to a worker subagent that returns text, "
            "instead of Reading raw frames into the main thread.")

    tools_per_turn = econ_agg.get("tools_per_turn") or []
    total_turns = econ_agg.get("total_turns") or 0
    if tools_per_turn and total_turns > 20:
        mean_tools = sum(tools_per_turn) / len(tools_per_turn)
        adjacent_pct = econ_agg.get("same_tool_adjacent", 0) / max(total_turns, 1) * 100
        if mean_tools < 1.3 and adjacent_pct > 30:
            add("tool_batching",
                "Independent tool calls aren't being batched into one turn",
                f"mean {mean_tools:.2f} tool call(s)/turn across {total_turns} turns; "
                f"{adjacent_pct:.0f}% of turns immediately followed a same-tool turn",
                "Batch independent tool calls into one turn instead of one call per "
                "turn — each turn re-sends the full context, so N sequential "
                "same-tool calls cost N re-sends of everything already in the window.")

    bash_total = econ_agg.get("bash_total") or 0
    bash_over30 = econ_agg.get("bash_over30") or 0
    bg_bash = econ_agg.get("bg_bash") or 0
    if bash_total > 10 and bash_over30 > 0:
        bg_pct = bg_bash / bash_total * 100
        hrs = econ_agg.get("bash_over30_secs", 0) / 3600
        if bg_pct < 20 and hrs > 0.5:
            add("background_long_calls",
                "Long shell calls are blocking the run in the foreground",
                f"{bash_over30} Bash/PowerShell call(s) over 30s totalling {hrs:.1f}h; "
                f"only {bg_pct:.0f}% of {bash_total} call(s) used run_in_background",
                "Background any runner over ~30s (prep --auto foremost) with "
                "run_in_background and collect the result when it's needed, instead "
                "of blocking the whole turn on it.",
                impact_hours=hrs)

    ask_wait = econ_agg.get("ask_wait_secs") or {}
    ask_count = econ_agg.get("ask_count") or {}
    for header, secs in sorted(ask_wait.items(), key=lambda kv: -kv[1])[:3]:
        hrs = secs / 3600
        if hrs > 2:
            add(f"gate_wait::{header}",
                f'The "{header}" gate accounts for {hrs:.1f}h of blocked wall-clock',
                f"{ask_count.get(header, 0)} ask(s) under this gate header, "
                f"{hrs:.1f}h total wait",
                f'Check whether "{header}" already has (or should get) a '
                f"confidence-gate default the pipeline can self-approve when clean, "
                f"the way PREP's orientation/crop/colour gates do — reserving the "
                f"question for genuine exceptions rather than every occurrence.",
                impact_hours=hrs)

    kinds = friction_agg.get("kinds") or Counter()
    samples = friction_agg.get("samples") or {}
    n_sessions = friction_agg.get("n_sessions") or 0
    if n_sessions:
        if kinds.get("repeat", 0) >= max(2, n_sessions // 5):
            # Same "which one, concretely" treatment tool_error_rate gives its
            # worst offender below: samples["repeat"] already carries the exact
            # signature each repeat repeats (_tool_signature() — tool name plus
            # its command/file_path/pattern/etc, per the "what a repeat repeats"
            # docstring), so surface the worst of it instead of a bare count —
            # #121 finding 5 couldn't be diagnosed further than "27 signals"
            # without this.
            worst = max(samples.get("repeat", []),
                        key=lambda sf: int(sf[1]["detail"].rstrip("x") or 0),
                        default=None)
            worst_txt = (f' (worst: {worst[1]["sample"][:80]!r} {worst[1]["detail"]})'
                        if worst else "")
            add("repeat_calls",
                "The same tool call is repeating 3+ times within a session",
                f"{kinds['repeat']} repeat-call signal(s) across {n_sessions} session(s)"
                f"{worst_txt}",
                "A tool re-run 3+ times with the same input in one session is either "
                "a silent failure being retried blind or a result that should have "
                "been cached — check the samples and fix the root cause rather than "
                "the retry.")
        if kinds.get("tool_error", 0) >= max(3, n_sessions // 3):
            by_tool = Counter(f["detail"] for _, f in samples.get("tool_error", []))
            worst = by_tool.most_common(1)
            worst_txt = f" ({worst[0][0]} most often)" if worst else ""
            add("tool_error_rate",
                "Tool calls are erroring often enough to be worth a guard",
                f"{kinds['tool_error']} tool_error signal(s) across {n_sessions} "
                f"session(s){worst_txt}",
                "Read the tool_error samples in the friction report and either fix "
                "the root cause or add a guard that catches the failure mode before "
                "it reaches the tool call.")
        if kinds.get("long_loop", 0) >= 1:
            add("long_loops",
                "At least one ask needed an unusually long back-and-forth to resolve",
                f"{kinds['long_loop']} long_loop signal(s) (>= {LOOP_TURNS} assistant "
                f"turns on one ask) across {n_sessions} session(s)",
                "Read the LONGEST ASKS section for the flagged ask(s) and see whether "
                "the back-and-forth traces to a missing tool, an ambiguous "
                "instruction, or a genuinely hard case — the fix differs by cause.")

    return sorted(proposals, key=lambda p: -p["impact_hours"])


# ---------------------------------------------------------------------------
# stage 2b — file proposals as a house `Idea:` issue, deduped against open ones
# ---------------------------------------------------------------------------

def format_idea_issue(proposals: list[dict], *, window: str) -> tuple[str, str]:
    """House `Idea:` format (see #30/#61/#74): a short title, then
    `## Findings` (one subsection per proposal, evidence + impact) and
    `## Suggested edits` (the concrete fix, one bullet per proposal)."""
    if not proposals:
        return "", ""
    top = proposals[0]
    title = f"Idea: {top['title']} ({window} session review)"
    lines = ["## Findings", "",
             f"Auto-generated by `ebz observe --propose` from the session "
             f"transcripts, window: {window}.", ""]
    for p in proposals:
        lines.append(f"### {p['title']}")
        lines.append(f"- Evidence: {p['evidence']}")
        if p["impact_hours"]:
            lines.append(f"- Estimated impact: {p['impact_hours']:.1f}h")
        lines.append("")
    lines.append("## Suggested edits")
    lines.append("")
    for p in proposals:
        lines.append(f"- **{p['title']}:** {p['proposed_fix']}")
    return title, "\n".join(lines)


def _open_idea_issues() -> list[dict]:
    """Every currently-open 'Idea:' issue — number + title, for dedup.
    Never raises: if `gh` isn't available or isn't authed, filing degrades
    to 'no match found' (files a new issue) rather than crashing — the
    same fail-open posture as the rest of this module's `gh`-free parsing."""
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "--search", "Idea in:title", "--state", "open",
             "--json", "number,title", "--limit", "50"],
            capture_output=True, text=True, timeout=30, check=True)
        return json.loads(out.stdout)
    except Exception:                                             # noqa: BLE001
        return []


def _match_open_issue(key: str, open_issues: list[dict]) -> Optional[dict]:
    """A proposal dedupes against an open issue when a keyword from its key
    appears in that issue's title. Deliberately coarse: a false negative
    (a near-duplicate issue) costs a human five seconds to close-as-dup; a
    false positive (a comment landing on the wrong issue) is worse and
    harder to notice, so the match stays conservative."""
    needle = key.split("::")[0].replace("_", " ")
    for issue in open_issues:
        if needle in issue["title"].lower():
            return issue
    return None


def file_proposals(proposals: list[dict], *, window: str, dry_run: bool = True) -> dict:
    """File one `Idea:` issue for this review window's findings — or, if
    the top-ranked finding already has an open issue, one comment there
    instead. This is the actual house pattern: #61 opened fresh, #71 later
    commented "nothing changed" on #62 rather than re-filing — ten review
    windows with the same finding become one issue with ten data points.

    `dry_run=True` (the default) never calls `gh issue create`/`comment` —
    it returns exactly what WOULD be filed. Only `dry_run=False`, which
    the CLI only reaches via an explicit `--file` flag, writes to GitHub."""
    if not proposals:
        return {"action": "none", "reason": "no proposals cleared threshold"}

    open_issues = _open_idea_issues()
    top = proposals[0]
    match = _match_open_issue(top["key"], open_issues)
    title, body = format_idea_issue(proposals, window=window)

    if match:
        comment = (f"Friction review, window {window} — {len(proposals)} finding(s), "
                  f"top: {top['title']} ({top['evidence']}).\n\n" + body)
        result = {"action": "comment", "target": match["number"], "title": match["title"]}
        if dry_run:
            result["dry_run_body"] = comment
        else:
            subprocess.run(["gh", "issue", "comment", str(match["number"]),
                           "--body", comment], check=True, timeout=30)
        return result

    result = {"action": "create", "title": title}
    if dry_run:
        result["dry_run_body"] = body
    else:
        out = subprocess.run(["gh", "issue", "create", "--title", title, "--body", body],
                            capture_output=True, text=True, check=True, timeout=30)
        result["url"] = out.stdout.strip()
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Parse Claude Code session transcripts into a friction report.")
    ap.add_argument("--dir", help="transcript directory (default: this repo's)")
    ap.add_argument("--days", type=int, default=7, help="only sessions this recent (default 7)")
    ap.add_argument("--limit", type=int, default=20, help="most recent N sessions (default 20)")
    ap.add_argument("--all", action="store_true", help="every session, no window")
    ap.add_argument("--report", action="store_true", help="print the full report to stdout")
    ap.add_argument("--economics", action="store_true",
                    help="print the #61 run-economics table (context/turn, tool-call "
                         "batching, image Read payload, gate wait) instead of the "
                         "friction report")
    ap.add_argument("--propose", action="store_true",
                    help="print the #36 ranked friction/economics -> concrete-fix "
                         "proposals, and what would be filed to GitHub for them "
                         "(dry run unless --file is also passed)")
    ap.add_argument("--file", action="store_true",
                    help="with --propose: actually create/comment on GitHub via `gh` "
                         "(default: dry run only — nothing reaches GitHub without this)")
    ap.add_argument("--json", dest="json_path", default="session_friction.json",
                    help="detail file (default session_friction.json)")
    args = ap.parse_args(argv)

    root = Path(args.dir) if args.dir else transcripts_dir(REPO)
    if not root.is_dir():
        # "no transcripts" reads as "you have not worked much lately", not "I
        # computed the wrong path" — which is how the missing '.' in the slug
        # survived. If sibling directories do match this repo, say so: a wrong
        # slug and a quiet week must not look the same.
        print(f"no transcripts at {root}")
        for line in _near_miss_hint(root):
            print(line)
        return 2
    files = sorted(root.glob("*.jsonl"), key=os.path.getmtime, reverse=True)
    if not args.all:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
        files = [f for f in files
                 if datetime.fromtimestamp(os.path.getmtime(f), timezone.utc) >= cutoff]
        files = files[:args.limit]
    if not files:
        print(f"no sessions in the last {args.days}d under {root}")
        return 0

    if args.economics:
        if args.all:
            window_start = window_end = None
        else:
            # Derived from the mtimes of the *actually included* session
            # files, not from --days alone — --limit can truncate `files`
            # to fewer/newer sessions than the --days cutoff would suggest,
            # and the ledger join must match spend against the same window
            # the spend was measured over.
            mtimes = [datetime.fromtimestamp(os.path.getmtime(f), timezone.utc)
                     for f in files]
            window_start, window_end = min(mtimes), max(mtimes)
        print(economics_report(files, window_start=window_start, window_end=window_end))
        return 0

    if args.propose:
        window = "all" if args.all else f"{args.days}d"
        friction_sessions = [s for s in (parse_session(f) for f in files) if s["asks"]]
        proposals = propose_fixes(aggregate_friction(friction_sessions),
                                  economics_aggregate(files))
        if not proposals:
            print(f"propose: no findings cleared threshold over {len(files)} "
                  f"session(s) ({window}) — nothing to file")
            return 0
        for p in proposals:
            print(f"[{p['impact_hours']:>5.1f}h] {p['title']}")
            print(f"  evidence: {p['evidence']}")
            print(f"  fix: {p['proposed_fix']}")
            print()
        result = file_proposals(proposals, window=window, dry_run=not args.file)
        if args.file:
            if result["action"] == "create":
                print(f"[FILED] {result.get('url', result['title'])}")
            elif result["action"] == "comment":
                print(f"[COMMENTED] on #{result['target']} ({result['title']})")
            else:
                print(f"[SKIPPED] {result.get('reason', '')}")
        else:
            if result["action"] == "comment":
                print(f"[DRY RUN] would comment on #{result['target']} "
                      f"({result['title']})")
            elif result["action"] == "create":
                print(f"[DRY RUN] would create: {result['title']}")
            print("  pass --file to actually write to GitHub")
        return 0

    sessions = [parse_session(f) for f in files]
    sessions = [s for s in sessions if s["asks"]]
    flagged = sum(1 for s in sessions if s["friction"])
    body = report(sessions)

    out = Path(args.json_path)
    out.write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "transcripts": str(root),
         "window": "all" if args.all else f"{args.days}d",
         "sessions": sessions,
         "report": body}, indent=2), encoding="utf-8")

    if args.report:
        print(body)
        print()
    signals = sum(len(s["friction"]) for s in sessions)
    print(f"observer: OK {len(sessions)}/{len(files)} sessions, {flagged} flagged, "
          f"{signals} signals -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
