"""Background guard — the #121 acceptance set.

Every "should deny" case below is a real command shape from the session
transcripts the observer read; the `cd … && python …` form is how essentially
every repo call is actually issued, which is exactly why a naive startswith
guard would have caught none of them.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

from bg_guard import SLOW_PREFIXES, offending_prefix, segments, verdict  # noqa: E402


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
