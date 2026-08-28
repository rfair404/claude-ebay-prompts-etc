#!/usr/bin/env python3
"""SESSION OBSERVER — read our own transcripts and find the friction (#36, V4_PLAN Phase 5).

Every session between us is already recorded: Claude Code writes one JSONL per
session under `~/.claude/projects/<slug>/`, with timestamps, token usage, every
tool call and every tool result. Nothing reads them. So the same friction keeps
happening — a tool that errors the same way every batch, a question I ask that
you have already answered three sessions running, a prompt that takes twenty
round-trips because the first answer missed.

This is stage 1 of the observer: the parser and the report. It measures where
the time and the tokens went, and it counts six friction signals:

  tool_error   a tool call came back is_error — the loop paid for a retry
  denied       a permission prompt was declined — I asked for the wrong thing
  interrupt    you stopped a turn in flight — I was going the wrong way
  redo         your next message opens with a correction ("no", "actually")
  repeat       the same Bash command or file read 3+ times in one session
  long_loop    one ask that needed LOOP_TURNS+ assistant turns to answer

Stage 2 (auto-filing an `Idea:` issue, deduped against the open ones) is NOT
here on purpose. A counter is evidence; a filed issue is a claim. The counts
want a few weeks of eyeballing before anything writes to the tracker.

  session_observer.py [--days 7] [--limit 20] [--all] [--report] [--json out.json]

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
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def transcripts_dir(repo: Path) -> Path:
    """Claude Code slugifies the cwd: every ':', '\\' and '/' becomes '-'."""
    slug = re.sub(r"[:\\/]", "-", str(repo))
    return Path.home() / ".claude" / "projects" / slug


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


def report(sessions: list[dict], top: int = 8) -> str:
    """The human-readable friction report — what to read before filing anything."""
    out = []
    kinds = Counter()
    samples = defaultdict(list)
    stages = defaultdict(lambda: {"asks": 0, "turns": 0, "seconds": 0.0, "out_tokens": 0})
    tok = Counter()
    for s in sessions:
        for f in s["friction"]:
            kinds[f["kind"]] += 1
            if len(samples[f["kind"]]) < top:
                samples[f["kind"]].append((s["session"][:8], f))
        for name, v in s["by_stage"].items():
            for k in ("asks", "turns", "seconds", "out_tokens"):
                stages[name][k] += v[k]
        tok.update(s["tokens"])

    asks = sum(s["asks"] for s in sessions)
    turns = sum(s["turns"] for s in sessions)
    active = sum(s["active_seconds"] for s in sessions)
    out.append(f"SESSIONS {len(sessions)}  asks {asks}  turns {turns}  "
               f"active {_hms(active)}  out {_k(tok['output_tokens'])} tok "
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
    hot = sorted((h for s in sessions for h in s["hot_spots"]),
                 key=lambda h: -h["turns"])[:top]
    for h in hot:
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


def parse_economics(path: Path) -> dict:
    """One session's #61-style run economics: context/turn, tool-call
    batching, Read payload by extension, backgrounding, and gate wall-clock
    by header. A separate pass from parse_session's friction walk on
    purpose — it answers "what did this session cost", not "what went
    wrong", and bolting a second question onto the six-signal walk risks
    the signals that walk already locks down."""
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
                    if name == "Read":
                        body = b.get("content")
                        sz = len(json.dumps(body)) if body is not None else 0
                        ext = Path(str(inp.get("file_path") or "")).suffix.lower() or "(none)"
                        read_bytes[ext] += sz
                        continue
                    secs = (when - st).total_seconds()
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
    }


def economics_report(paths: list[Path]) -> str:
    """Aggregate `parse_economics` across sessions into the #61 headline
    table — the standing version of `.scratch/analyze{,2,3,4}.py`."""
    all_ctx: list[int] = []
    all_tools: list[int] = []
    same_tool_adjacent = total_turns = 0
    read_bytes: Counter = Counter()
    bash_total = bg_bash = bash_over30 = 0
    bash_over30_secs = 0.0
    ask_count: Counter = Counter()
    ask_wait: Counter = Counter()

    for p in paths:
        e = parse_economics(p)
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

    out = [f"=== ECONOMICS — {len(paths)} session(s), {total_turns} tool-calling turn(s)"]
    if all_ctx:
        s = sorted(all_ctx)
        out.append(f"  context/turn: mean {_k(int(sum(s) / len(s)))}  "
                   f"median {_k(s[len(s) // 2])}  p90 {_k(s[int(len(s) * .9)])}  "
                   f"max {_k(s[-1])}")
    if all_tools:
        out.append(f"  tool calls/turn: mean {sum(all_tools) / len(all_tools):.2f}  "
                   f"same-tool-adjacent {same_tool_adjacent / max(total_turns, 1) * 100:.0f}% of turns")
    img_bytes = sum(v for k, v in read_bytes.items() if k in IMAGE_EXT)
    tot_bytes = sum(read_bytes.values())
    if tot_bytes:
        out.append(f"  Read payload: {img_bytes / 1e6:.1f} MB images of "
                   f"{tot_bytes / 1e6:.1f} MB total ({img_bytes / tot_bytes * 100:.0f}%)")
    if bash_total:
        out.append(f"  Bash/PowerShell: {bash_total} calls, "
                   f"{bg_bash / bash_total * 100:.1f}% backgrounded, "
                   f"{bash_over30} over 30s totalling {bash_over30_secs / 3600:.1f}h")
    if ask_wait:
        out.append("  AskUserQuestion wait by gate:")
        for h, secs in sorted(ask_wait.items(), key=lambda kv: -kv[1])[:8]:
            out.append(f"    {ask_count.get(h, 0):>3}  {secs / 3600:>6.1f}h  {h}")
    return "\n".join(out)


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
    ap.add_argument("--json", dest="json_path", default="session_friction.json",
                    help="detail file (default session_friction.json)")
    args = ap.parse_args(argv)

    root = Path(args.dir) if args.dir else transcripts_dir(REPO)
    if not root.is_dir():
        print(f"no transcripts at {root}")
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
        print(economics_report(files))
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
