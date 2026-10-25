"""tools/ci_test.sh: dispatch the suite in GitHub Actions and wait for it.

The script is the only way the full suite runs since 2026-10-24, so the ways
it can lie matter more than the happy path:

- reporting a green CI run for code CI never saw (uncommitted or unpushed
  work), which is why the refusals come first here;
- watching somebody else's run (an older dispatch, or a parallel one on
  another commit) and reporting *its* result;
- exiting 0 when the run failed.

`git`, `gh` and `date +%s` are stubbed on PATH. Each stub logs its argv, so the
tests assert on what the script asked GitHub to do, and the run list the gh
stub returns is the boundary the run-matching logic is tested against.
"""
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "ci_test.sh"
REAL_DATE = shutil.which("date")

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not (shutil.which("bash") and shutil.which("awk") and REAL_DATE),
    reason="a Linux operator tool: needs bash, awk and GNU date")

SEP = "\x1f"
OURS = "a" * 40           # the pushed commit
OTHER = "b" * 40          # another commit on the same branch
NOW = int(datetime(2026, 10, 24, 20, 0, tzinfo=timezone.utc).timestamp())


def iso(offset_s: int) -> str:
    return datetime.fromtimestamp(NOW + offset_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(run_id: int, sha: str, offset_s: int) -> str:
    return f"{run_id}\t{sha}\t{iso(offset_s)}\thttps://github.invalid/runs/{run_id}"


GIT = f"""#!/bin/bash
printf '%s{SEP}' git "$@" >> "$STUB_DIR/calls"; echo >> "$STUB_DIR/calls"
case "$*" in
  "rev-parse --abbrev-ref HEAD") echo "$GIT_BRANCH" ;;
  "status --porcelain --untracked-files=no") cat "$STUB_DIR/dirty" 2>/dev/null ;;
  "ls-files"*) cat "$STUB_DIR/untracked" 2>/dev/null || true ;;
  "fetch"*) exit "${{GIT_FETCH_RC:-0}}" ;;
  "rev-parse FETCH_HEAD") echo "$REMOTE_SHA" ;;
  "rev-parse -q --verify"*) [[ -n ${{LOCAL_SHA:-}} ]] || exit 1; echo "$LOCAL_SHA" ;;
  *) echo "git stub: unexpected: $*" >&2; exit 99 ;;
esac
"""

GH = f"""#!/bin/bash
printf '%s{SEP}' gh "$@" >> "$STUB_DIR/calls"; echo >> "$STUB_DIR/calls"
case "$1 $2" in
  "run list") if [[ -e $STUB_DIR/dispatched ]]; then cat "$STUB_DIR/after"; else cat "$STUB_DIR/before"; fi ;;
  "workflow run") [[ ${{GH_DISPATCH_RC:-0}} == 0 ]] || exit "$GH_DISPATCH_RC"; touch "$STUB_DIR/dispatched" ;;
  "run watch") exit "${{GH_WATCH_RC:-0}}" ;;
  "run view") for i in $(seq 1 200); do echo "failed log line $i"; done ;;
  *) echo "gh stub: unexpected: $*" >&2; exit 99 ;;
esac
"""

# `date +%s` is the dispatch clock; formatting an epoch stays real GNU date.
DATE = f"""#!/bin/bash
[[ $* == +%s ]] && {{ echo "$FAKE_NOW"; exit; }}
exec {REAL_DATE} "$@"
"""


@pytest.fixture
def ci(tmp_path):
    stub = tmp_path / "stub"
    stub.mkdir()
    for name, body in {"git": GIT, "gh": GH, "date": DATE}.items():
        (stub / name).write_text(body)
        (stub / name).chmod(0o755)
    (stub / "before").write_text("")
    (stub / "after").write_text("")

    def run(*args, runs_before=(), runs_after=(), dirty="", **env):
        (stub / "before").write_text("".join(r + "\n" for r in runs_before))
        (stub / "after").write_text("".join(r + "\n" for r in runs_after))
        (stub / "dirty").write_text(dirty)
        full = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
                "STUB_DIR": str(stub), "FAKE_NOW": str(NOW),
                "GIT_BRANCH": "feature-x", "REMOTE_SHA": OURS, "LOCAL_SHA": OURS,
                "CI_TEST_POLL_S": "0", "CI_TEST_POLL_N": "3", **env}
        proc = subprocess.run(["bash", str(SCRIPT), *args], env=full, cwd=tmp_path,
                              capture_output=True, text=True, timeout=60)
        log = (stub / "calls").read_text() if (stub / "calls").exists() else ""
        proc.calls = [line.rstrip(SEP).split(SEP) for line in log.splitlines()]
        proc.gh = [c[1:] for c in proc.calls if c[0] == "gh"]
        return proc

    return run


# --- refusals: nothing is dispatched for code CI would not see ---------------
REFUSALS = [
    ({"dirty": " M lib/list_edit.py\n"}, ["uncommitted", "push first", "lib/list_edit.py"]),
    ({"LOCAL_SHA": OTHER}, ["is not what origin has", "push first", "git push origin feature-x"]),
    ({"GIT_FETCH_RC": "1"}, ["not on origin", "git push -u origin feature-x"]),
    ({"GIT_BRANCH": "HEAD"}, ["detached head", "name the branch"]),
]


@pytest.mark.parametrize("state,says", REFUSALS,
                         ids=["dirty-tree", "unpushed-commits", "branch-not-on-origin", "detached-head"])
def test_refuses_work_ci_cannot_see_and_dispatches_nothing(ci, state, says):
    proc = ci(**state)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    err = proc.stderr.lower()
    for fragment in says:
        assert fragment.lower() in err, f"refusal does not say {fragment!r}:\n{proc.stderr}"
    assert not any(c[:2] == ["workflow", "run"] for c in proc.gh), proc.gh


def test_a_named_branch_ignores_this_checkouts_dirty_tree(ci):
    """The working tree belongs to the current branch, not the one named."""
    proc = ci("other-branch", dirty=" M lib/list_edit.py\n",
              runs_after=[row(300, OURS, 5)])
    assert proc.returncode == 0, proc.stderr
    assert ["workflow", "run", "tests.yml", "--ref", "other-branch"] in proc.gh


def test_untracked_python_is_named_as_not_in_ci(ci, tmp_path):
    (tmp_path / "stub" / "untracked").write_text("tests/test_new.py\n")
    proc = ci(runs_after=[row(300, OURS, 5)])
    assert proc.returncode == 0, proc.stderr
    assert "not in CI" in proc.stderr and "tests/test_new.py" in proc.stderr


# --- dispatch and finding our run --------------------------------------------
def test_dispatches_the_current_branch_and_watches_only_its_own_run(ci):
    """Newest first, as gh lists them. Every decoy is one the script could
    plausibly have picked: an earlier dispatch of the same commit (inside the
    clock-skew slack, so only "it was already listed" excludes it), a parallel
    dispatch of another commit, and an hour-old run of this commit."""
    old_same_sha = row(100, OURS, -30)
    proc = ci(runs_before=[old_same_sha],
              runs_after=[row(202, OURS, 5),          # ours
                          row(201, OTHER, 2),          # parallel dispatch, other commit
                          old_same_sha,
                          row(98, OURS, -3600)])       # this commit, long ago
    assert proc.returncode == 0, proc.stderr
    assert ["workflow", "run", "tests.yml", "--ref", "feature-x"] in proc.gh
    watches = [c for c in proc.gh if c[:2] == ["run", "watch"]]
    assert watches == [["run", "watch", "202", "--exit-status", "--interval", "15"]], proc.gh
    assert "PASSED" in proc.stdout and "https://github.invalid/runs/202" in proc.stdout
    # The run list is filtered to dispatches of this workflow on this branch.
    listing = next(c for c in proc.gh if c[:2] == ["run", "list"])
    for flag, value in (("--workflow", "tests.yml"), ("--branch", "feature-x"),
                        ("--event", "workflow_dispatch")):
        assert listing[listing.index(flag) + 1] == value, listing


def test_a_refused_dispatch_says_why_and_watches_nothing(ci):
    """Seen live on 2026-10-24: the laptop's gh token got HTTP 403 from the
    dispatches endpoint because it had no Actions write scope."""
    proc = ci(runs_after=[row(202, OURS, 5)], GH_DISPATCH_RC="1")
    assert proc.returncode == 2
    assert "Actions: Read and write" in proc.stderr
    assert not any(c[:2] == ["run", "watch"] for c in proc.gh)


def test_a_run_that_never_appears_fails_instead_of_watching_another(ci):
    stale = [row(100, OURS, -30)]
    proc = ci(runs_before=stale, runs_after=stale)
    assert proc.returncode == 2
    assert "no new run" in proc.stderr
    assert not any(c[:2] == ["run", "watch"] for c in proc.gh)


# --- the result ---------------------------------------------------------------
def test_a_failed_run_exits_nonzero_with_the_failed_log_tail(ci):
    proc = ci(runs_after=[row(202, OURS, 5)], GH_WATCH_RC="1")
    assert proc.returncode == 1
    assert "FAILED" in proc.stderr and "https://github.invalid/runs/202" in proc.stderr
    assert ["run", "view", "202", "--log-failed"] in proc.gh
    assert "failed log line 200" in proc.stderr          # the tail, through the end
    assert "failed log line 120\n" not in proc.stderr    # and only the tail
    assert "PASSED" not in proc.stdout
