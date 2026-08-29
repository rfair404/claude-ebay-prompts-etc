"""The session observer: parsing, the six friction signals, stage attribution.

Builds a synthetic transcript in a temp dir — no dependency on the real
~/.claude sessions, so the suite is deterministic and runs anywhere.

No pytest fixtures — runs under tests/run_all.py too.
"""
import csv
import json
import os
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.session_observer import (  # noqa: E402
    _classify, _clean_prompt, _command_bucket, _match_open_issue,
    _model_rates, _open_idea_issues, _prep_item_dir, _ts, _tool_signature,
    _turn_context, _turn_cost_usd, aggregate_friction, cost_per_listing,
    economics_aggregate, economics_report, file_proposals, format_idea_issue,
    main, parse_economics, parse_session, propose_fixes, report, transcripts_dir,
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


def test_ts_normalizes_a_naive_timestamp_to_utc():
    # Copilot review on PR #47: a transcript timestamp missing its offset
    # parsed as naive, and comparing/subtracting it against an aware one
    # raises TypeError — same fix as _date() in tools/sales_report.py.
    aware = _ts({"timestamp": "2026-08-27T10:00:00Z"})
    naive = _ts({"timestamp": "2026-08-27T10:00:00"})
    assert aware.tzinfo is not None and naive.tzinfo is not None
    assert (aware - naive).total_seconds() == 0.0


def test_wall_time_does_not_raise_on_a_mixed_naive_and_aware_transcript():
    d = Path(tempfile.mkdtemp())
    p = d / "s.jsonl"
    records = [
        {"type": "user", "timestamp": "2026-08-27T10:00:00",
         "message": {"role": "user", "content": "hi"}},
        {"type": "assistant", "timestamp": "2026-08-27T10:00:05.000Z",
         "message": {"role": "assistant", "model": "claude-opus-5", "content": [],
                     "usage": {"input_tokens": 1, "output_tokens": 1}}},
    ]
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    s = parse_session(p)   # would previously raise TypeError comparing naive/aware
    assert s["wall_seconds"] == 5.0


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


def test_the_observer_does_not_count_its_own_output_as_friction():
    p = _write([
        _user("show me the friction report", 0),
        _assistant(1, [("Bash", {"command": "python -m lib.cli observe --report"})]),
        _result("t0", "interrupt 2\n  [Request interrupted by user]\n  tool_error 72"),
        _assistant(2, [("Bash", {"command": "cat notes.txt"})]),
        _result("t0", "[Request interrupted by user]"),
    ])
    kinds = [f["kind"] for f in parse_session(p)["friction"]]
    assert kinds.count("interrupt") == 1        # the real one only


def test_self_guard_survives_a_long_command_prefix():
    # The signature truncates at 160 chars; a cd + long temp path can push
    # "observe" past the cut, which is how two false interrupts got through.
    prefix = 'cd "C:/Users/x/' + "deep/" * 40 + '" && '
    p = _write([
        _user("full history please", 0),
        _assistant(1, [("Bash", {"command": prefix + "python -m lib.cli observe --all --report"})]),
        _result("t0", "interrupt 13\n  [Request interrupted by user]"),
    ])
    assert not [f for f in parse_session(p)["friction"] if f["kind"] == "interrupt"]


def test_ts_returns_none_for_a_non_string_timestamp():
    # Copilot review on PR #47: _ts() called .replace() on whatever sat in the
    # timestamp field, so a malformed line carrying a number/null/object raised
    # AttributeError — "never raises on bad lines" has to cover the wrong TYPE
    # as well as the wrong format.
    for bad in (12345, None, {"t": 1}, ["2026-08-27T10:00:00Z"], True):
        assert _ts({"timestamp": bad}) is None
    assert _ts({}) is None
    assert _ts({"timestamp": ""}) is None
    assert _ts({"timestamp": "not a date"}) is None


def test_a_transcript_with_non_string_timestamps_still_parses():
    d = Path(tempfile.mkdtemp())
    p = d / "s.jsonl"
    recs = [_user("do the thing", 0), _assistant(1)]
    recs[0]["timestamp"] = 1756300000            # epoch int, not a string
    p.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    s = parse_session(p)                          # must not raise
    assert s["asks"] == 1


# ---------------------------------------------------------------------------
# economics (#61) — context/turn, tool-call batching, Read payload, gate wait
# ---------------------------------------------------------------------------
def _at(iso: str) -> str:
    return iso


def _tool_use_msg(ts, tools, usage=None):
    content = [{"type": "tool_use", "id": tid, "name": name, "input": inp}
               for tid, name, inp in tools]
    return {"type": "assistant", "timestamp": _at(ts),
            "message": {"role": "assistant", "model": "claude-opus-5",
                        "content": content,
                        "usage": usage or {"input_tokens": 1, "output_tokens": 10,
                                            "cache_read_input_tokens": 100,
                                            "cache_creation_input_tokens": 0}}}


def _tool_result_msg(ts, tool_use_id, body, is_error=False):
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": body}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "timestamp": _at(ts),
            "message": {"role": "user", "content": [block]}}


def _write_economics(records) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "0000abcd-0000-0000-0000-000000000000.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return p


def test_turn_context_sums_the_three_token_fields():
    assert _turn_context({"input_tokens": 1, "cache_creation_input_tokens": 5,
                          "cache_read_input_tokens": 100}) == 106
    assert _turn_context({}) == 0


def test_parse_economics_records_context_and_tool_count_per_turn():
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z",
                      [("t1", "Bash", {"command": "ls"}),
                       ("t2", "Bash", {"command": "pwd"})],
                      usage={"input_tokens": 1, "cache_creation_input_tokens": 5,
                             "cache_read_input_tokens": 100, "output_tokens": 10}),
        _tool_result_msg("2026-08-27T10:00:01Z", "t1", "a"),
        _tool_result_msg("2026-08-27T10:00:01Z", "t2", "b"),
    ])
    e = parse_economics(p)
    assert e["context_per_turn"] == [106]
    assert e["tools_per_turn"] == [2]


def test_parse_economics_flags_consecutive_same_tool_calls():
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z", [("t1", "Bash", {"command": "ls"})]),
        _tool_result_msg("2026-08-27T10:00:01Z", "t1", "a"),
        _tool_use_msg("2026-08-27T10:00:02Z", [("t2", "Bash", {"command": "pwd"})]),
        _tool_result_msg("2026-08-27T10:00:03Z", "t2", "b"),
        _tool_use_msg("2026-08-27T10:00:04Z", [("t3", "Read", {"file_path": "x.py"})]),
        _tool_result_msg("2026-08-27T10:00:05Z", "t3", "c"),
    ])
    e = parse_economics(p)
    # Bash -> Bash is adjacent (1); Bash -> Read is not.
    assert e["same_tool_adjacent"] == 1


def test_parse_economics_sizes_read_payload_by_extension():
    body = [{"type": "text", "text": "x" * 1000}]
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z",
                      [("t1", "Read", {"file_path": "inventory/x/photo.jpg"})]),
        _tool_result_msg("2026-08-27T10:00:01Z", "t1", body),
    ])
    e = parse_economics(p)
    assert e["read_calls"] == {".jpg": 1}
    assert e["read_bytes"][".jpg"] == len(json.dumps(body))


def test_parse_economics_flags_bash_over_30_seconds():
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z",
                      [("t1", "Bash", {"command": "python long_thing.py"})]),
        _tool_result_msg("2026-08-27T10:00:45Z", "t1", "done"),
    ])
    e = parse_economics(p)
    assert e["bash_over30"] == 1
    assert e["bash_over30_secs"] == 45.0
    assert e["bash_total"] == 1
    assert e["bg_bash"] == 0


def test_parse_economics_backgrounded_bash_is_counted():
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z",
                      [("t1", "Bash", {"command": "long", "run_in_background": True})]),
        _tool_result_msg("2026-08-27T10:00:01Z", "t1", "started"),
    ])
    e = parse_economics(p)
    assert e["bg_bash"] == 1


def test_parse_economics_tracks_ask_wait_by_gate_header():
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z",
                      [("t1", "AskUserQuestion",
                        {"questions": [{"header": "Colour", "question": "which look?"}]})]),
        _tool_result_msg("2026-08-27T11:00:00Z", "t1", "punch"),
    ])
    e = parse_economics(p)
    assert e["ask_count"] == {"Colour": 1}
    assert e["ask_wait_secs"]["Colour"] == 3600.0


def test_economics_report_aggregates_across_sessions_and_names_the_gate():
    p1 = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z", [("t1", "Bash", {"command": "ls"})]),
        _tool_result_msg("2026-08-27T10:00:01Z", "t1", "a"),
    ])
    p2 = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z",
                      [("t1", "AskUserQuestion",
                        {"questions": [{"header": "Publish", "question": "go?"}]})]),
        _tool_result_msg("2026-08-27T10:30:00Z", "t1", "yes"),
    ])
    out = economics_report([p1, p2])
    assert "2 session(s)" in out
    assert "context/turn:" in out
    assert "tool calls/turn:" in out
    assert "Publish" in out


def test_economics_report_on_no_sessions_does_not_crash():
    out = economics_report([])
    assert "0 session(s)" in out


# ---------------------------------------------------------------------------
# propose_fixes (#36 stage 2a) — threshold-gated, ranked, evidence-cited fixes
# ---------------------------------------------------------------------------
def _friction_agg(**overrides):
    base = {"n_sessions": 10, "asks": 10, "turns": 50, "active_seconds": 1000,
            "kinds": Counter(), "samples": {}, "stages": {}, "tokens": Counter(),
            "hot_spots": []}
    base.update(overrides)
    return base


def _econ_agg(**overrides):
    base = {"n_sessions": 10, "total_turns": 100, "context_per_turn": [],
            "tools_per_turn": [], "same_tool_adjacent": 0, "read_bytes": {},
            "bash_total": 0, "bg_bash": 0, "bash_over30": 0, "bash_over30_secs": 0.0,
            "ask_count": {}, "ask_wait_secs": {}}
    base.update(overrides)
    return base


def test_propose_fixes_empty_below_every_threshold():
    assert propose_fixes(_friction_agg(), _econ_agg()) == []


def test_propose_fixes_flags_image_dominated_read_payload():
    econ = _econ_agg(read_bytes={".jpg": 6_000_000, ".txt": 1_000_000})
    props = propose_fixes(_friction_agg(), econ)
    assert any(p["key"] == "raw_frame_reads" for p in props)


def test_propose_fixes_ignores_small_read_totals():
    # Same 86% image share, but under the 1MB floor — too little data to act on.
    econ = _econ_agg(read_bytes={".jpg": 600_000, ".txt": 100_000})
    props = propose_fixes(_friction_agg(), econ)
    assert not any(p["key"] == "raw_frame_reads" for p in props)


def test_propose_fixes_flags_low_batching_with_high_adjacency():
    econ = _econ_agg(total_turns=100, tools_per_turn=[1] * 100, same_tool_adjacent=40)
    props = propose_fixes(_friction_agg(), econ)
    assert any(p["key"] == "tool_batching" for p in props)


def test_propose_fixes_flags_unbackgrounded_long_bash_and_reports_its_hours():
    econ = _econ_agg(bash_total=50, bash_over30=10, bg_bash=1, bash_over30_secs=3600 * 2)
    props = propose_fixes(_friction_agg(), econ)
    hit = next(p for p in props if p["key"] == "background_long_calls")
    assert hit["impact_hours"] == 2.0


def test_propose_fixes_ignores_well_backgrounded_long_bash():
    econ = _econ_agg(bash_total=50, bash_over30=10, bg_bash=45, bash_over30_secs=3600 * 2)
    props = propose_fixes(_friction_agg(), econ)
    assert not any(p["key"] == "background_long_calls" for p in props)


def test_propose_fixes_flags_gate_wait_over_threshold_and_names_the_gate():
    econ = _econ_agg(ask_wait_secs={"Colour": 3600 * 5}, ask_count={"Colour": 4})
    props = propose_fixes(_friction_agg(), econ)
    hit = next(p for p in props if p["key"] == "gate_wait::Colour")
    assert hit["impact_hours"] == 5.0
    assert "Colour" in hit["title"]


def test_propose_fixes_ignores_short_gate_wait():
    econ = _econ_agg(ask_wait_secs={"Sync": 1800}, ask_count={"Sync": 1})
    props = propose_fixes(_friction_agg(), econ)
    assert not any(p["key"].startswith("gate_wait") for p in props)


def test_propose_fixes_flags_repeat_tool_error_and_long_loop_signals():
    friction = _friction_agg(
        n_sessions=10,
        kinds=Counter({"repeat": 3, "tool_error": 5, "long_loop": 1}),
        samples={"tool_error": [("abc12345", {"detail": "Bash"})] * 5},
    )
    props = propose_fixes(friction, _econ_agg())
    keys = {p["key"] for p in props}
    assert {"repeat_calls", "tool_error_rate", "long_loops"} <= keys
    err = next(p for p in props if p["key"] == "tool_error_rate")
    assert "Bash" in err["evidence"]


def test_propose_fixes_ranks_by_impact_hours_descending():
    econ = _econ_agg(bash_total=50, bash_over30=5, bg_bash=1, bash_over30_secs=3600,
                     ask_wait_secs={"Colour": 3600 * 10}, ask_count={"Colour": 2})
    props = propose_fixes(_friction_agg(), econ)
    hours = [p["impact_hours"] for p in props]
    assert hours == sorted(hours, reverse=True)
    assert props[0]["key"] == "gate_wait::Colour"


# ---------------------------------------------------------------------------
# format_idea_issue (#36 stage 2b) — the house `Idea:` issue shape
# ---------------------------------------------------------------------------
def test_format_idea_issue_on_empty_proposals():
    assert format_idea_issue([], window="7d") == ("", "")


def test_format_idea_issue_builds_title_and_sections():
    props = [{"key": "k1", "title": "Thing is slow", "evidence": "measured X",
              "proposed_fix": "do Y", "impact_hours": 3.0}]
    title, body = format_idea_issue(props, window="7d")
    assert title == "Idea: Thing is slow (7d session review)"
    assert "## Findings" in body and "## Suggested edits" in body
    assert "measured X" in body and "do Y" in body
    assert "3.0h" in body


# ---------------------------------------------------------------------------
# dedup against open issues, and the actual filing (subprocess always mocked
# — these tests must never touch the real `gh` / GitHub)
# ---------------------------------------------------------------------------
def test_match_open_issue_finds_a_keyword_match():
    open_issues = [{"number": 61, "title": "Idea: background long calls take too long"}]
    assert _match_open_issue("background_long_calls", open_issues)["number"] == 61


def test_match_open_issue_returns_none_without_a_match():
    open_issues = [{"number": 61, "title": "Idea: something unrelated"}]
    assert _match_open_issue("background_long_calls", open_issues) is None


def test_match_open_issue_strips_the_gate_header_suffix():
    open_issues = [{"number": 5, "title": "Idea: gate wait is high"}]
    assert _match_open_issue("gate_wait::Colour", open_issues)["number"] == 5


def test_open_idea_issues_returns_empty_when_gh_fails(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("gh not found")
    monkeypatch.setattr("tools.session_observer.subprocess.run", boom)
    assert _open_idea_issues() == []


def test_file_proposals_on_no_proposals_does_nothing():
    assert file_proposals([], window="7d")["action"] == "none"


def test_file_proposals_dry_run_never_calls_subprocess(monkeypatch):
    monkeypatch.setattr("tools.session_observer._open_idea_issues", lambda: [])

    def boom(*a, **kw):
        raise AssertionError("must not call gh in dry-run mode")
    monkeypatch.setattr("tools.session_observer.subprocess.run", boom)

    props = [{"key": "k1", "title": "Thing", "evidence": "e", "proposed_fix": "f",
              "impact_hours": 1.0}]
    result = file_proposals(props, window="7d", dry_run=True)
    assert result["action"] == "create"
    assert "dry_run_body" in result


def test_file_proposals_live_creates_issue_when_no_match(monkeypatch):
    monkeypatch.setattr("tools.session_observer._open_idea_issues", lambda: [])
    calls = []

    class _R:
        stdout = "https://github.com/x/y/issues/99\n"

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _R()
    monkeypatch.setattr("tools.session_observer.subprocess.run", fake_run)

    props = [{"key": "k1", "title": "Thing", "evidence": "e", "proposed_fix": "f",
              "impact_hours": 1.0}]
    result = file_proposals(props, window="7d", dry_run=False)
    assert result["action"] == "create"
    assert result["url"] == "https://github.com/x/y/issues/99"
    assert calls[0][:3] == ["gh", "issue", "create"]


def test_file_proposals_live_comments_on_matching_open_issue(monkeypatch):
    monkeypatch.setattr("tools.session_observer._open_idea_issues",
                        lambda: [{"number": 42, "title": "Idea: background long calls"}])
    calls = []

    class _R:
        stdout = ""

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return _R()
    monkeypatch.setattr("tools.session_observer.subprocess.run", fake_run)

    props = [{"key": "background_long_calls", "title": "Long shell calls", "evidence": "e",
              "proposed_fix": "f", "impact_hours": 1.0}]
    result = file_proposals(props, window="7d", dry_run=False)
    assert result["action"] == "comment"
    assert result["target"] == 42
    assert calls[0][:3] == ["gh", "issue", "comment"]
    assert calls[0][3] == "42"


def test_aggregate_friction_matches_report_totals():
    p = _write([
        _user("price this lot against comps", 0),
        _assistant(1, [("Bash", {"command": "echo hi"})], out=200),
        _result("t0", "hi"),
    ])
    s = parse_session(p)
    agg = aggregate_friction([s])
    assert agg["n_sessions"] == 1
    assert agg["asks"] == s["asks"]
    assert agg["tokens"]["output_tokens"] == s["tokens"]["output_tokens"]


def test_economics_aggregate_matches_report_shape():
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z", [("t1", "Bash", {"command": "ls"})]),
        _tool_result_msg("2026-08-27T10:00:01Z", "t1", "a"),
    ])
    agg = economics_aggregate([p])
    assert agg["n_sessions"] == 1
    assert agg["bash_total"] == 1


# ---------------------------------------------------------------------------
# #71 §7 — $/published listing, foreground blocking hours, prep re-runs
# ---------------------------------------------------------------------------

def test_model_rates_known_model_and_unknown_fallback():
    assert _model_rates("claude-opus-5") == (5.00, 25.00)
    assert _model_rates("claude-sonnet-5") == (2.00, 10.00)
    assert _model_rates("claude-haiku-4-5") == (1.00, 5.00)
    assert _model_rates("some-future-model") != (5.00, 25.00)   # falls back, not silently free
    assert _model_rates(None)[0] > 0


def test_turn_cost_usd_prices_input_output_and_cache_tiers():
    assert _turn_cost_usd({"input_tokens": 1_000_000}, "claude-sonnet-5") == 2.0
    assert _turn_cost_usd({"output_tokens": 1_000_000}, "claude-sonnet-5") == 10.0
    # cache write ~1.25x input rate, cache read ~0.1x input rate
    assert _turn_cost_usd({"cache_creation_input_tokens": 1_000_000},
                          "claude-opus-5") == 6.25
    assert _turn_cost_usd({"cache_read_input_tokens": 1_000_000},
                          "claude-opus-5") == 0.5
    assert _turn_cost_usd({}, "claude-opus-5") == 0.0


def test_command_bucket_normalizes_common_repo_commands():
    assert _command_bucket(
        "python -m lib.photo_prep.prep inventory/x --approve-auto"
    ) == "python -m lib.photo_prep.prep"
    assert _command_bucket("pytest tests/ -q") == "pytest"
    assert _command_bucket('cd "/some/deep/path" && git status') == "git status"
    assert _command_bucket("gh pr create --title x") == "gh pr create"
    assert _command_bucket("ebz prep inventory/x --auto") == "ebz prep"
    assert _command_bucket("") == "(empty)"


def test_command_bucket_strips_a_quoted_cd_prefix_containing_spaces():
    # Windows-style quoted paths with embedded spaces used to defeat the
    # `\S+`-only cd-prefix strip and leak the whole `cd "..." && ...` line
    # into its own bucket instead of collapsing to the real command.
    assert _command_bucket(
        'cd "C:/Users/A Name/repo" && git status'
    ) == "git status"
    assert _command_bucket(
        "cd '/Users/A Name/repo' && pytest tests/ -q"
    ) == "pytest"


def test_command_bucket_keeps_lib_cli_subcommands_distinct():
    # A bare `python -m <module>` bucket would collapse every
    # `python -m lib.cli <subcommand>` invocation together, hiding `prep`
    # runs dispatched through the CLI rather than called directly.
    assert _command_bucket(
        "python -m lib.cli prep inventory/x --auto"
    ) == "python -m lib.cli prep"
    assert _command_bucket(
        "python -m lib.cli sales-report --by-source"
    ) == "python -m lib.cli sales-report"


def test_prep_item_dir_extracts_across_every_invocation_form():
    assert _prep_item_dir(
        "python -m lib.photo_prep.prep inventory/sand-dollars --approve-auto"
    ) == "inventory/sand-dollars"
    assert _prep_item_dir(
        "python -m lib.cli prep inventory/sand-dollars --auto"
    ) == "inventory/sand-dollars"
    assert _prep_item_dir("ebz prep inventory/sand-dollars") == "inventory/sand-dollars"
    assert _prep_item_dir(
        'cd "/repo" && python -m lib.photo_prep.prep inventory/x --check'
    ) == "inventory/x"


def test_prep_item_dir_none_for_non_prep_commands_and_no_positional_arg():
    assert _prep_item_dir("python -m lib.cli sales-report") is None
    assert _prep_item_dir("git status") is None
    assert _prep_item_dir("python -m lib.photo_prep.prep --help") is None


def test_parse_economics_tracks_cost_usd_from_model_pricing():
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z", [("t1", "Bash", {"command": "ls"})],
                      usage={"input_tokens": 1_000_000, "output_tokens": 0,
                             "cache_creation_input_tokens": 0,
                             "cache_read_input_tokens": 0}),
        _tool_result_msg("2026-08-27T10:00:01Z", "t1", "a"),
    ])
    e = parse_economics(p)
    assert round(e["cost_usd"], 2) == 5.00       # claude-opus-5 (test helper's model)


def test_parse_economics_foreground_hours_exclude_backgrounded_bash():
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z", [
            ("t1", "Bash", {"command": "python -m lib.photo_prep.prep inventory/x --auto"}),
            ("t2", "Bash", {"command": "long_thing", "run_in_background": True}),
        ]),
        _tool_result_msg("2026-08-27T10:05:00Z", "t1", "done"),
        _tool_result_msg("2026-08-27T10:00:02Z", "t2", "started"),
    ])
    e = parse_economics(p)
    assert e["fg_seconds_by_tool"]["Bash"] == 300.0
    assert e["fg_seconds_by_command"] == {"python -m lib.photo_prep.prep": 300.0}


def test_parse_economics_foreground_hours_covers_every_tool_not_just_bash():
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z", [("t1", "Read", {"file_path": "x.py"})]),
        _tool_result_msg("2026-08-27T10:00:10Z", "t1", [{"type": "text", "text": "hi"}]),
    ])
    e = parse_economics(p)
    assert e["fg_seconds_by_tool"]["Read"] == 10.0
    assert e["fg_seconds_by_command"] == {}      # only Bash/PowerShell get a command bucket


def test_parse_economics_counts_prep_reruns_per_item_dir():
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z", [
            ("t1", "Bash", {"command": "python -m lib.photo_prep.prep inventory/sand-dollars --check"}),
        ]),
        _tool_result_msg("2026-08-27T10:00:01Z", "t1", "a"),
        _tool_use_msg("2026-08-27T10:01:00Z", [
            ("t2", "Bash", {"command": "python -m lib.photo_prep.prep inventory/sand-dollars --approve-auto"}),
        ]),
        _tool_result_msg("2026-08-27T10:01:01Z", "t2", "b"),
        _tool_use_msg("2026-08-27T10:02:00Z", [
            ("t3", "Bash", {"command": "python -m lib.photo_prep.prep inventory/other-item --auto"}),
        ]),
        _tool_result_msg("2026-08-27T10:02:01Z", "t3", "c"),
    ])
    e = parse_economics(p)
    assert e["prep_calls"] == {"inventory/sand-dollars": 2, "inventory/other-item": 1}


def test_economics_aggregate_sums_cost_foreground_hours_and_prep_reruns():
    def _prep_session(dir_name):
        return _write_economics([
            _tool_use_msg("2026-08-27T10:00:00Z", [
                ("t1", "Bash", {"command": f"python -m lib.photo_prep.prep {dir_name} --auto"}),
            ], usage={"input_tokens": 1000, "output_tokens": 0,
                     "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}),
            _tool_result_msg("2026-08-27T10:00:05Z", "t1", "a"),
        ])
    agg = economics_aggregate([_prep_session("inventory/a"), _prep_session("inventory/a")])
    assert agg["prep_calls"] == {"inventory/a": 2}
    assert agg["fg_seconds_by_tool"]["Bash"] == 10.0
    assert round(agg["cost_usd"], 4) == round(2 * (1000 * 5.00 / 1e6), 4)


def test_cost_per_listing_joins_ledger_rows_within_window():
    rows = [
        {"price": "100.00", "published_at": "2026-08-27T10:00:00Z"},
        {"price": "50.00", "published_at": "2026-08-27T11:00:00Z"},
        {"price": "9999.00", "published_at": "2026-08-20T00:00:00Z"},   # before window
        {"price": "10.00", "published_at": ""},                        # never published
    ]
    start = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 28, 0, 0, 0, tzinfo=timezone.utc)
    r = cost_per_listing(300.0, rows, start, end)
    assert r["n_published"] == 2
    assert r["total_list_price"] == 150.0
    assert r["cost_per_listing"] == 150.0
    assert round(r["cost_to_list_price_ratio"], 4) == round(300.0 / 150.0, 4)


def test_cost_per_listing_unbounded_window_counts_every_published_row():
    rows = [{"price": "10", "published_at": "2020-01-01T00:00:00Z"}]
    r = cost_per_listing(10.0, rows, None, None)
    assert r["n_published"] == 1
    assert r["cost_per_listing"] == 10.0


def test_cost_per_listing_zero_published_returns_none_ratios_not_a_crash():
    r = cost_per_listing(50.0, [], None, None)
    assert r["n_published"] == 0
    assert r["cost_per_listing"] is None
    assert r["cost_to_list_price_ratio"] is None


def test_cost_per_listing_zero_list_price_returns_none_ratio():
    rows = [{"price": "0", "published_at": "2026-08-27T10:00:00Z"}]
    r = cost_per_listing(50.0, rows, None, None)
    assert r["n_published"] == 1
    assert r["cost_per_listing"] == 50.0
    assert r["cost_to_list_price_ratio"] is None


def _write_ledger(rows) -> Path:
    d = Path(tempfile.mkdtemp())
    path = d / "listings_ledger.csv"
    fields = ["sku", "status", "title", "price", "offer_id", "listing_id",
              "url", "drafted_at", "synced_at", "published_at", "ended_at",
              "updated_at"]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def test_economics_report_renders_the_three_71_metrics(monkeypatch):
    ledger = _write_ledger([
        {"sku": "a1", "status": "PUBLISHED", "title": "Widget",
         "price": "40.00", "published_at": "2026-08-27T10:00:00Z"},
    ])
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(ledger))

    p = _write_economics([
        _tool_use_msg("2026-08-27T09:00:00Z", [
            ("t1", "Bash", {"command": "python -m lib.photo_prep.prep inventory/widget --check"}),
        ], usage={"input_tokens": 1_000_000, "output_tokens": 0,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}),
        _tool_result_msg("2026-08-27T09:10:00Z", "t1", "a"),
        _tool_use_msg("2026-08-27T09:20:00Z", [
            ("t2", "Bash", {"command": "python -m lib.photo_prep.prep inventory/widget --approve-auto"}),
        ], usage={"input_tokens": 0, "output_tokens": 0,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}),
        _tool_result_msg("2026-08-27T09:21:00Z", "t2", "b"),
    ])
    start = datetime(2026, 8, 27, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 28, 0, 0, 0, tzinfo=timezone.utc)
    out = economics_report([p], window_start=start, window_end=end)

    assert "$/published listing" in out
    assert "$5.00" in out or "5.00" in out            # cost_per_listing == model spend / 1 listing
    assert "Foreground blocking hours by tool" in out
    assert "Foreground blocking hours by repo command" in out
    assert "python -m lib.photo_prep.prep" in out
    assert "prep re-runs" in out
    assert "2x  inventory/widget" in out


def test_economics_report_without_ledger_env_falls_back_to_no_row_line(monkeypatch):
    # Point the ledger at a path that doesn't exist rather than trusting
    # whatever real listings_ledger.csv this machine happens to have.
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER",
                       str(Path(tempfile.mkdtemp()) / "missing.csv"))
    p = _write_economics([
        _tool_use_msg("2026-08-27T10:00:00Z", [("t1", "Bash", {"command": "ls"})],
                      usage={"input_tokens": 100, "output_tokens": 0,
                             "cache_creation_input_tokens": 0,
                             "cache_read_input_tokens": 0}),
        _tool_result_msg("2026-08-27T10:00:01Z", "t1", "a"),
    ])
    out = economics_report([p])
    assert "no listings_ledger.csv row published in this window" in out


def test_main_economics_derives_ledger_window_from_included_files_not_days(
        monkeypatch, capsys):
    # Copilot review on #94: --limit can truncate the --days-filtered file
    # list to fewer/newer sessions than --days alone would suggest, so the
    # ledger join window must come from the mtimes of the files actually
    # included, not from `now - --days`. Two sessions eight months apart,
    # --days 365 (wide enough both pass the date filter) but --limit 1
    # (only the newer one is actually included) — the listing published
    # only in the OLDER session's window must NOT be counted.
    tmp = Path(tempfile.mkdtemp())
    old_ts, new_ts = "2026-01-01T10:00:00Z", "2026-08-20T10:00:00Z"
    usage = {"input_tokens": 1_000_000, "output_tokens": 0,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    old_path = tmp / "old-session.jsonl"
    old_path.write_text("\n".join(json.dumps(r) for r in [
        _tool_use_msg(old_ts, [("t1", "Bash", {"command": "ls"})], usage=usage),
        _tool_result_msg(old_ts, "t1", "a"),
    ]), encoding="utf-8")
    new_path = tmp / "new-session.jsonl"
    new_path.write_text("\n".join(json.dumps(r) for r in [
        _tool_use_msg(new_ts, [("t1", "Bash", {"command": "ls"})], usage=usage),
        _tool_result_msg(new_ts, "t1", "a"),
    ]), encoding="utf-8")

    old_epoch = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    new_epoch = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    os.utime(old_path, (old_epoch, old_epoch))
    os.utime(new_path, (new_epoch, new_epoch))

    ledger = _write_ledger([
        {"sku": "old-only", "status": "PUBLISHED", "title": "Old widget",
         "price": "10.00", "published_at": old_ts},
        {"sku": "new-only", "status": "PUBLISHED", "title": "New widget",
         "price": "20.00", "published_at": new_ts},
    ])
    monkeypatch.setenv("EBAYBIZ_LISTINGS_LEDGER", str(ledger))

    rc = main(["--dir", str(tmp), "--economics", "--days", "365", "--limit", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 published in window" in out
    assert "2 published in window" not in out
    assert "$20.00 their" in out or "20.00" in out
    assert "$10.00 their" not in out
