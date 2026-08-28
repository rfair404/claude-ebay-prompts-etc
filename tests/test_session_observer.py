"""The session observer: parsing, the six friction signals, stage attribution.

Builds a synthetic transcript in a temp dir — no dependency on the real
~/.claude sessions, so the suite is deterministic and runs anywhere.

No pytest fixtures — runs under tests/run_all.py too.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.session_observer import (  # noqa: E402
    _classify, _clean_prompt, _tool_signature, parse_session, report,
    transcripts_dir,
)

T0 = "2026-08-27T10:00:0{}.000Z"


def _user(text, sec=0):
    return {"type": "user", "timestamp": T0.format(sec),
            "message": {"role": "user", "content": text}}


def _assistant(sec=0, tools=(), out=100):
    content = [{"type": "tool_use", "id": f"t{i}", "name": n, "input": inp}
               for i, (n, inp) in enumerate(tools)]
    return {"type": "assistant", "timestamp": T0.format(sec),
            "message": {"role": "assistant", "model": "claude-opus-5",
                        "content": content,
                        "usage": {"input_tokens": 1, "output_tokens": out,
                                  "cache_read_input_tokens": 10,
                                  "cache_creation_input_tokens": 5}}}


def _result(tool_use_id, body, is_error=False):
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": body}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "timestamp": T0.format(1),
            "message": {"role": "user", "content": [block]}}


def _write(records) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "0000abcd-0000-0000-0000-000000000000.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


def test_transcripts_dir_slugifies_the_repo_path():
    d = transcripts_dir(Path("C:/x/y"))
    assert d.name == "C--x-y"
    assert d.parent.name == "projects"


def test_clean_prompt_drops_image_placeholders():
    txt = "[Image: original 4000x3000, displayed at 2000x1500.]\nprice this lot"
    assert _clean_prompt(txt) == "price this lot"
    assert _clean_prompt("[Image: original 10x10]") == "[image only]"


def test_tool_signature_prefers_the_command_and_is_whitespace_stable():
    a = _tool_signature("Bash", {"command": "git   status"})
    b = _tool_signature("Bash", {"command": "git status"})
    assert a == b == "Bash:git status"
    assert _tool_signature("Read", {"file_path": "/x/draft.md"}) == "Read:/x/draft.md"
    assert _tool_signature("Weird", {}) == "Weird"


def test_classify_picks_the_stage_and_falls_back_to_other():
    assert _classify("run the comps and price it against the ceiling") == "PRICE"
    assert _classify("crop the photos, prep the shoot") == "PREP"
    assert _classify("hello there") == "OTHER"


def test_counts_asks_turns_tools_and_tokens():
    p = _write([
        _user("price this lot against comps", 0),
        _assistant(1, [("Bash", {"command": "echo hi"})], out=200),
        _result("t0", "hi"),
        _assistant(2, [], out=50),
    ])
    s = parse_session(p)
    assert s["asks"] == 1 and s["turns"] == 2 and s["tools"] == 1
    assert s["tokens"]["output_tokens"] == 250
    assert s["by_stage"]["PRICE"]["asks"] == 1
    assert s["hot_spots"][0]["stage"] == "PRICE"


def test_tool_error_and_denied_are_separate_signals():
    p = _write([
        _user("do a thing", 0),
        _assistant(1, [("Bash", {"command": "boom"})]),
        _result("t0", "Exit code 2: boom not found", is_error=True),
        _assistant(2, [("Bash", {"command": "again"})]),
        _result("t0", "The user doesn't want to take this action", is_error=True),
    ])
    kinds = [f["kind"] for f in parse_session(p)["friction"]]
    assert kinds.count("tool_error") == 1
    assert kinds.count("denied") == 1


def test_redo_fires_on_a_correction_but_not_on_the_first_prompt():
    p = _write([
        _user("no, this first one must not count", 0),
        _assistant(1),
        _user("actually, do it the other way", 2),
        _assistant(3),
        _user("thanks, that works", 4),
        _assistant(5),
    ])
    f = [x for x in parse_session(p)["friction"] if x["kind"] == "redo"]
    assert len(f) == 1 and "actually" in f[0]["sample"]


def test_repeat_fires_at_three_identical_calls():
    recs = [_user("look at things", 0)]
    for _ in range(3):
        recs += [_assistant(1, [("Bash", {"command": "cat draft.md"})])]
    s = parse_session(_write(recs))
    rep = [f for f in s["friction"] if f["kind"] == "repeat"]
    assert len(rep) == 1 and rep[0]["detail"] == "3x"


def test_long_loop_fires_at_the_turn_threshold():
    from tools.session_observer import LOOP_TURNS
    recs = [_user("a big ask", 0)] + [_assistant(1) for _ in range(LOOP_TURNS)]
    s = parse_session(_write(recs))
    assert any(f["kind"] == "long_loop" for f in s["friction"])
    assert s["hot_spots"][0]["friction"] == ["long_loop"]


def test_interrupt_and_sidechain_and_system_reminder_handling():
    side = _assistant(1)
    side["isSidechain"] = True
    p = _write([
        _user("start", 0),
        side,                                   # subagent traffic: not counted
        _user("<system-reminder>noise</system-reminder>", 1),   # not an ask
        _user("[Request interrupted by user]", 2),
        _assistant(3),
    ])
    s = parse_session(p)
    assert s["asks"] == 1
    assert s["turns"] == 1                      # the sidechain turn is excluded
    assert any(f["kind"] == "interrupt" for f in s["friction"])


def test_bad_lines_and_oversized_lines_never_raise():
    d = Path(tempfile.mkdtemp())
    p = d / "s.jsonl"
    p.write_text("\n".join([
        "{not json at all",
        json.dumps(_user("ok", 0)),
        json.dumps({"type": "user", "message": {"content": "x" * 600_000}}),
        json.dumps(_assistant(1)),
    ]), encoding="utf-8")
    s = parse_session(p)
    assert s["asks"] == 1 and s["skipped_lines"] == 1


def test_report_renders_every_section():
    p = _write([
        _user("price this", 0),
        _assistant(1, [("Bash", {"command": "x"})]),
        _result("t0", "nope", is_error=True),
    ])
    body = report([parse_session(p)])
    for header in ("SESSIONS", "WHERE THE WORK WENT", "FRICTION", "LONGEST ASKS"):
        assert header in body
    assert "tool_error" in body
