"""Background guard — the #121 acceptance set.

Every "should deny" case below is a real command shape from the session
transcripts the observer read; the `cd … && python …` form is how essentially
every repo call is actually issued, which is exactly why a naive startswith
guard would have caught none of them.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

from bg_guard import (ON_AIR_STALE_H, SLOW_PREFIXES, offending_prefix, on_air,  # noqa: E402
                      on_air_job, segments, verdict)


def test_bare_slow_command_is_caught():
    assert offending_prefix("python -m pytest") == "python -m pytest"


def test_prefix_after_cd_is_caught():
    """The real invocation form. A whole-string startswith would miss this."""
    cmd = 'cd "C:/repo" && python -m lib.photo_prep.prep "shoot" --auto'
    assert offending_prefix(cmd) == "python -m lib.photo_prep.prep"


def test_prefix_in_a_later_segment_is_caught():
    cmd = 'echo start; python tests/run_all.py --fast'
    assert offending_prefix(cmd) == "python tests/run_all.py"


def test_python3_and_py_are_the_same_runner():
    assert offending_prefix("python3 -m pytest") == "python -m pytest"
    assert offending_prefix("py tools/ledger_reconcile.py") == "python tools/ledger_reconcile.py"


def test_reindex_glob_prefix():
    assert offending_prefix("python tools/reindex_full.py") == "python tools/reindex_"


def test_fast_command_is_untouched():
    assert offending_prefix('cd "C:/repo" && git status') is None
    assert offending_prefix("ls -la") is None
    assert offending_prefix("python lib/dir_context.py x --root y") is None


def test_a_similar_looking_path_is_not_a_prefix():
    """`prep_card.py` is fast and is NOT on the list; `prep_sheet_html.py` is."""
    assert offending_prefix("python tools/prep_card.py shoot") is None
    assert offending_prefix("python tools/prep_sheet_html.py shoot") == "python tools/prep_sheet_html.py"


def test_backgrounded_call_is_allowed():
    ti = {"command": "python -m pytest", "run_in_background": True}
    assert verdict(ti) is None


def test_foreground_call_is_denied_with_an_actionable_reason():
    reason = verdict({"command": "python -m pytest"})
    assert reason is not None
    assert "run_in_background" in reason
    assert "python -m pytest" in reason
    assert "sleep" in reason           # the "don't poll" half of the RUN.md rule


def test_escape_hatch_allows_with_a_stated_reason():
    cmd = '# fg-ok: --status is instant\npython -m lib.photo_prep.prep shoot --status'
    assert offending_prefix(cmd) is None
    assert verdict({"command": cmd}) is None


def test_escape_hatch_must_lead_the_command():
    """A `# fg-ok` buried mid-command is not an opt-out — it would hide the rule."""
    cmd = 'python -m pytest  # fg-ok: I promise'
    assert offending_prefix(cmd) == "python -m pytest"


def test_segments_normalises_and_splits():
    assert "python -m pytest" in segments('cd x && python3 -m pytest')


def test_separator_inside_quotes_is_not_a_split():
    """The regression that denied this guard's own pipe-test.

    `echo '{"command":"cd x && python -m pytest"}' | python tools/bg_guard.py`
    runs no slow tool — the prefix is argument text. Splitting on the quoted
    `&&` produced a fragment starting with the prefix and blocked the call.
    """
    cmd = ('echo \'{"tool_input":{"command":"cd x && python -m pytest"}}\''
           ' | python tools/bg_guard.py')
    assert offending_prefix(cmd) is None


def test_prefix_named_inside_a_grep_is_not_a_run():
    assert offending_prefix("""grep -n 'python -m pytest' RUN.md""") is None
    assert offending_prefix('rg "python lib/lens_id.py" docs/') is None


def test_real_pipe_after_a_quoted_mention_still_catches_a_real_run():
    """Quote-awareness must not make the guard blind to an actual run."""
    cmd = 'echo "about to run python -m pytest" && python -m pytest -q'
    assert offending_prefix(cmd) == "python -m pytest"


def test_run_md_names_every_prefix():
    """The guard is the source of truth; RUN.md must not drift from it."""
    run_md = (ROOT / "RUN.md").read_text(encoding="utf-8")
    missing = [p for p in SLOW_PREFIXES if p not in run_md]
    assert not missing, f"RUN.md does not name: {missing}"


def _hook(payload: dict) -> str:
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bg_guard.py")],
        input=json.dumps(payload), capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_hook_denies_over_stdin():
    out = _hook({"tool_name": "Bash", "tool_input": {"command": "python -m pytest"}})
    decision = json.loads(out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"


def test_hook_stays_silent_when_allowing():
    assert _hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}) == ""
    assert _hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}) == ""


def test_malformed_input_never_blocks():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "bg_guard.py")],
        input="not json", capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout == ""


# --- show mode: batch jobs wait while the show is on air ---------------------
VENV_PYTEST = 'cd "/srv/repo" && /srv/repo/.venv/bin/python -m pytest -q'
BATCH = [
    ("python -m pytest", "pytest"),
    (VENV_PYTEST, "pytest"),
    ("pytest tests/test_bg_guard.py", "pytest"),
    ("python tests/run_all.py --fast", "the test suite"),
    ("python tools/live/show_music.py build", "show_music.py build"),
    ("python /srv/repo/tools/live/show_music.py build", "show_music.py build"),
    ('cd "C:/repo" && python -m lib.photo_prep.prep shoot --auto', "PREP"),
    ("python tools/prep_sheet_html.py shoot", "prep_sheet_html.py"),
    ("python -m lib.cli prep shoot --auto", "a prep or listing run"),
    ("python -m lib.cli single-pass shoot", "a prep or listing run"),
    ("python -m lib.cli --store junk listing --publish x", "a prep or listing run"),
    ("python lib/list_edit.py --update inventory/x", "a listing run"),
]


@pytest.mark.parametrize("cmd,job", BATCH)
def test_on_air_denies_batch_jobs_even_backgrounded(on_air_flag, cmd, job):
    on_air_flag.write_text("{}")
    reason = verdict({"command": cmd, "run_in_background": True})
    assert reason is not None and job in reason
    assert "show is live" in reason and str(on_air_flag) in reason


def test_on_air_message_never_points_at_stopping_the_stream(on_air_flag):
    """An agent that misjudges "no show is on air" must not end a live
    broadcast: the way out is check status, then rm the flag."""
    on_air_flag.write_text("{}")
    reason = verdict({"command": "python -m pytest", "run_in_background": True})
    assert "show_flow.py status" in reason and f"rm {on_air_flag}" in reason
    assert "show_flow.py stop" not in reason


@pytest.mark.parametrize("cmd,job", BATCH)
def test_off_air_the_same_jobs_only_need_backgrounding(cmd, job):
    assert verdict({"command": cmd, "run_in_background": True}) is None


@pytest.mark.parametrize("cmd", [
    "python tools/live/show_flow.py page item",
    "python tools/live/show_music.py on",
    "python -m lib.cli status shoot",
    "python -m lib.cli serve",
    "git status",
    "grep -n 'python -m pytest' RUN.md",
])
def test_on_air_leaves_show_controls_and_reads_alone(on_air_flag, cmd):
    on_air_flag.write_text("{}")
    assert on_air_job(cmd) is None
    assert verdict({"command": cmd, "run_in_background": True}) is None


def test_a_stale_flag_no_longer_blocks(on_air_flag):
    on_air_flag.write_text("{}")
    old = on_air_flag.stat().st_mtime - ON_AIR_STALE_H * 3600 - 1
    os.utime(on_air_flag, (old, old))
    assert on_air() is None
    assert verdict({"command": "python -m pytest", "run_in_background": True}) is None
    assert on_air(now=old + 60) == on_air_flag  # the same flag, when fresh, counts


def test_on_air_escape_must_lead_and_fg_ok_does_not_open_it(on_air_flag):
    on_air_flag.write_text("{}")
    ok = "# on-air-ok: --status is instant\npython -m lib.photo_prep.prep shoot --status"
    assert verdict({"command": ok, "run_in_background": True}) is None
    assert verdict({"command": "python -m pytest  # on-air-ok: no",
                    "run_in_background": True}) is not None
    fg = "# fg-ok: instant\npython -m lib.photo_prep.prep shoot --status"
    assert "show is live" in verdict({"command": fg})


def test_venv_python_is_the_same_runner_for_the_background_rule():
    """The repo's real pytest call names the venv's python by path."""
    assert offending_prefix(VENV_PYTEST) == "python -m pytest"
    assert offending_prefix("C:/repo/.venv/Scripts/python.exe -m pytest") == "python -m pytest"
    assert offending_prefix("/usr/bin/python3.13 tools/ledger_reconcile.py") == \
        "python tools/ledger_reconcile.py"
    assert offending_prefix("./tools/fast.py --x") is None


def test_hook_denies_on_air_over_stdin(on_air_flag):
    """The real hook, finding the flag through SHOW_ON_AIR_FLAG (conftest)."""
    payload = {"tool_name": "Bash",
               "tool_input": {"command": VENV_PYTEST, "run_in_background": True}}
    assert _hook(payload) == ""
    on_air_flag.write_text("{}")
    decision = json.loads(_hook(payload))["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert str(on_air_flag) in decision["permissionDecisionReason"]


def test_show_flow_and_bg_guard_agree_on_the_flag(monkeypatch):
    sys.path.insert(0, str(ROOT / "tools" / "live"))
    import bg_guard
    import show_flow
    monkeypatch.delenv("SHOW_ON_AIR_FLAG")
    assert show_flow.on_air_flag() == bg_guard.on_air_flag() == \
        ROOT / "inventory" / "_live" / "ON_AIR"
