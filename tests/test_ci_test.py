"""tools/ci_test.sh: push a branch's commit to ci/<branch>, watch its CI run.

The script is the only way the full suite runs since 2026-10-24, so the ways
it can lie or do damage matter more than the happy path:

- reporting a green CI run for code CI never saw (uncommitted changes), which
  is why the refusals come first here;
- force-pushing over something that is not its own throwaway ref (main, the
  branch itself, or an open PR's branch that happens to be named ci/<x>);
- watching another commit's run, or an earlier invocation's run, and
  reporting *its* result;
- exiting 0 when the run failed;
- leaving its ci/ ref behind on origin.

`git` and `gh` are stubbed on PATH. Each stub logs its argv, so the tests
assert on what the script pushed, deleted and asked GitHub for, and the run
lists the gh stub returns are the boundary the run-matching logic is tested
against.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "ci_test.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not (shutil.which("bash") and shutil.which("awk")),
    reason="a Linux operator tool: needs bash and awk")

SEP = "\x1f"
OURS = "a" * 40           # the commit under test
OTHER = "b" * 40          # another commit pushed to the same ci/ ref
DELETE = ["push", "-q", "origin", "--delete", "ci/feature-x"]


def row(run_id: int, sha: str) -> str:
    return f"{run_id}\t{sha}\thttps://github.invalid/runs/{run_id}"


GIT = f"""#!/bin/bash
printf '%s{SEP}' git "$@" >> "$STUB_DIR/calls"; echo >> "$STUB_DIR/calls"
case "$*" in
  "rev-parse --abbrev-ref HEAD") echo "$GIT_BRANCH" ;;
  "status --porcelain --untracked-files=no") cat "$STUB_DIR/dirty" ;;
  "ls-files"*) cat "$STUB_DIR/untracked" 2>/dev/null || true ;;
  "rev-parse -q --verify"*) [[ -n ${{LOCAL_SHA:-}} ]] || exit 1; echo "$LOCAL_SHA" ;;
  "push -q origin --delete"*) [[ ${{GIT_DELETE_RC:-0}} == 0 ]] || {{ echo "error: unable to delete" >&2; exit "$GIT_DELETE_RC"; }} ;;
  "push"*) [[ ${{GIT_PUSH_RC:-0}} == 0 ]] || exit "$GIT_PUSH_RC"; touch "$STUB_DIR/pushed" ;;
  *) echo "git stub: unexpected: $*" >&2; exit 99 ;;
esac
"""

# The run list changes when the script pushes: `before` until then, `runs`
# after. A script that only looked before its own push would find no new run.
GH = f"""#!/bin/bash
printf '%s{SEP}' gh "$@" >> "$STUB_DIR/calls"; echo >> "$STUB_DIR/calls"
case "$1 $2" in
  "pr list") echo "${{GH_OPEN_PR:-}}" ;;
  "run list") if [[ -e $STUB_DIR/pushed ]]; then cat "$STUB_DIR/runs"; else cat "$STUB_DIR/before"; fi ;;
  "run watch") exit "${{GH_WATCH_RC:-0}}" ;;
  "run view") for i in $(seq 1 200); do echo "failed log line $i"; done ;;
  *) echo "gh stub: unexpected: $*" >&2; exit 99 ;;
esac
"""


@pytest.fixture
def ci(tmp_path):
    stub = tmp_path / "stub"
    stub.mkdir()
    for name, body in {"git": GIT, "gh": GH}.items():
        (stub / name).write_text(body)
        (stub / name).chmod(0o755)

    def run(*args, runs=(), before=(), dirty="", **env):
        (stub / "runs").write_text("".join(r + "\n" for r in runs))
        (stub / "before").write_text("".join(r + "\n" for r in before))
        (stub / "dirty").write_text(dirty)
        full = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
                "STUB_DIR": str(stub), "GIT_BRANCH": "feature-x", "LOCAL_SHA": OURS,
                "CI_TEST_POLL_S": "0", "CI_TEST_POLL_N": "3", **env}
        proc = subprocess.run(["bash", str(SCRIPT), *args], env=full, cwd=tmp_path,
                              capture_output=True, text=True, timeout=60)
        log = (stub / "calls").read_text() if (stub / "calls").exists() else ""
        proc.calls = [line.rstrip(SEP).split(SEP) for line in log.splitlines()]
        proc.git = [c[1:] for c in proc.calls if c[0] == "git"]
        proc.gh = [c[1:] for c in proc.calls if c[0] == "gh"]
        proc.pushes = [c for c in proc.git if c[:1] == ["push"] and "--delete" not in c]
        proc.deletes = [c for c in proc.git if c[:1] == ["push"] and "--delete" in c]
        proc.watches = [c for c in proc.gh if c[:2] == ["run", "watch"]]
        return proc

    return run


# --- refusals: nothing is pushed for code CI would not see -------------------
REFUSALS = [
    ((), {"dirty": " M lib/list_edit.py\n"}, ["uncommitted", "commit first", "lib/list_edit.py"]),
    ((), {"GIT_BRANCH": "HEAD"}, ["detached head", "name the branch"]),
    ((), {"GIT_BRANCH": "main"}, ["main is tested by ci on every push"]),
    (("main",), {}, ["main is tested by ci on every push"]),
    (("no-such-branch",), {"LOCAL_SHA": ""}, ["no local branch no-such-branch"]),
]


@pytest.mark.parametrize("args,state,says", REFUSALS, ids=[
    "dirty-tree", "detached-head", "on-main", "main-named", "unknown-branch"])
def test_refuses_and_pushes_nothing(ci, args, state, says):
    proc = ci(*args, **state)
    assert proc.returncode == 2, proc.stdout + proc.stderr
    err = proc.stderr.lower()
    for fragment in says:
        assert fragment in err, f"refusal does not say {fragment!r}:\n{proc.stderr}"
    assert proc.pushes == [] and proc.deletes == [] and proc.gh == [], proc.calls


def test_refuses_to_force_push_over_an_open_prs_branch(ci):
    """A working branch literally named ci/feature-x with an open PR: testing
    feature-x must not overwrite it, and must not delete it on the way out."""
    proc = ci(runs=[row(301, OURS)], GH_OPEN_PR="412")
    assert proc.returncode == 2
    assert "ci/feature-x is an open PR's branch (#412)" in proc.stderr
    assert proc.pushes == [] and proc.deletes == [] and proc.watches == [], proc.calls
    assert ["pr", "list", "--head", "ci/feature-x", "--state", "open",
            "--json", "number", "-q", ".[].number"] in proc.gh


def test_a_failed_push_stops_before_watching(ci):
    proc = ci(runs=[row(301, OURS)], GIT_PUSH_RC="1")
    assert proc.returncode == 2
    assert "could not push feature-x to ci/feature-x" in proc.stderr
    assert proc.watches == [] and proc.deletes == []


# --- the push: only ever the throwaway ci/<branch> ref -----------------------
def test_pushes_head_to_its_ci_ref(ci):
    proc = ci(runs=[row(301, OURS)])
    assert proc.returncode == 0, proc.stderr
    assert proc.pushes == [["push", "-q", "-f", "origin", "HEAD:refs/heads/ci/feature-x"]]


def test_a_named_branch_pushes_its_own_tip_and_ignores_this_dirty_tree(ci):
    """The working tree belongs to the current branch, not the one named."""
    proc = ci("other-branch", dirty=" M lib/list_edit.py\n", runs=[row(301, OURS)])
    assert proc.returncode == 0, proc.stderr
    assert proc.pushes == [["push", "-q", "-f", "origin",
                            "refs/heads/other-branch:refs/heads/ci/other-branch"]]


def test_untracked_python_is_named_as_not_in_ci(ci, tmp_path):
    (tmp_path / "stub" / "untracked").write_text("tests/test_new.py\n")
    proc = ci(runs=[row(301, OURS)])
    assert proc.returncode == 0, proc.stderr
    assert "not in CI" in proc.stderr and "tests/test_new.py" in proc.stderr


# --- the ref is deleted on the way out, whatever the result ------------------
@pytest.mark.parametrize("watch_rc,delete_rc,exit_rc", [
    ("0", "0", 0), ("1", "0", 1), ("0", "1", 0), ("1", "1", 1),
], ids=["pass", "fail", "pass-ref-already-gone", "fail-ref-already-gone"])
def test_deletes_its_ci_ref_after_the_watch_and_keeps_the_result(ci, watch_rc, delete_rc, exit_rc):
    proc = ci(runs=[row(301, OURS)], GH_WATCH_RC=watch_rc, GIT_DELETE_RC=delete_rc)
    assert proc.returncode == exit_rc, proc.stderr
    assert proc.deletes == [DELETE]
    order = [c[1:] for c in proc.calls]
    assert order.index(DELETE) > order.index(proc.watches[0]), "deleted before the watch"
    assert "unable to delete" not in proc.stderr          # quiet when the ref is gone


# --- finding the run of this commit ------------------------------------------
def test_watches_the_new_run_of_this_commit_only(ci):
    """Newest first, as gh lists them: a later push of another commit to the
    same ci/ ref, our new run, and an earlier invocation's run of the same
    commit (listed before the push too)."""
    earlier = row(300, OURS)
    proc = ci(before=[earlier], runs=[row(302, OTHER), row(301, OURS), earlier])
    assert proc.returncode == 0, proc.stderr
    assert proc.watches == [["run", "watch", "301", "--exit-status", "--interval", "15"]], proc.gh
    assert "PASSED" in proc.stdout and "https://github.invalid/runs/301" in proc.stdout
    listing = next(c for c in proc.gh if c[:2] == ["run", "list"])
    for flag, value in (("--workflow", "tests.yml"), ("--branch", "ci/feature-x"),
                        ("--event", "push")):
        assert listing[listing.index(flag) + 1] == value, listing


@pytest.mark.parametrize("before,runs", [
    ([], [row(302, OTHER)]),
    ([row(300, OURS)], [row(302, OTHER), row(300, OURS)]),
], ids=["only-another-commit", "only-an-earlier-run-of-this-commit"])
def test_no_new_run_fails_instead_of_watching_another(ci, before, runs):
    proc = ci(before=before, runs=runs)
    assert proc.returncode == 2
    assert "no new run for aaaaaaaaaaaa" in proc.stderr
    assert proc.watches == [] and proc.deletes == [DELETE]


# --- the result ---------------------------------------------------------------
def test_a_failed_run_exits_nonzero_with_the_failed_log_tail(ci):
    proc = ci(runs=[row(301, OURS)], GH_WATCH_RC="1")
    assert proc.returncode == 1
    assert "FAILED" in proc.stderr and "https://github.invalid/runs/301" in proc.stderr
    assert ["run", "view", "301", "--log-failed"] in proc.gh
    assert "failed log line 200" in proc.stderr          # the tail, through the end
    assert "failed log line 120\n" not in proc.stderr    # and only the tail
    assert "PASSED" not in proc.stdout
