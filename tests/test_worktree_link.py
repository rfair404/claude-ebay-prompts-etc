"""worktree_link — a worktree's inventory/ and reports/ point at main's.

reports/ is the one place for sensitive report output, and both offsite copies
(R2, Google Drive) take reports/**. A worktree session's report only reaches
them if the worktree's reports/ is a link to main's.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from worktree_link import LINKED, link_worktree  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A main checkout with inventory/ and reports/, plus one linked worktree."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    shutil.copy(ROOT / ".gitignore", main / ".gitignore")
    _git(main, "add", ".gitignore")
    _git(main, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    for name in LINKED:
        (main / name).mkdir()
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt))
    return main, wt


def test_links_inventory_and_reports(repo):
    main, wt = repo
    link_worktree(wt)
    for name in ("inventory", "reports"):
        assert os.path.realpath(wt / name) == os.path.realpath(main / name)
    (wt / "reports" / "marketing").mkdir()
    (wt / "reports" / "marketing" / "review.md").write_text("x")
    assert (main / "reports" / "marketing" / "review.md").read_text() == "x"


def test_links_are_gitignored(repo):
    """#197: a link that isn't ignored gets committed and breaks CI."""
    _, wt = repo
    link_worktree(wt)
    out = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"],
                         capture_output=True, text=True, check=True).stdout
    assert out == ""


def test_check_changes_nothing(repo):
    _, wt = repo
    notes = link_worktree(wt, check=True)
    assert all("would link" in n for n in notes) and len(notes) == len(LINKED)
    assert not any(os.path.lexists(wt / name) for name in LINKED)


def test_real_directory_is_left_alone(repo, capsys):
    main, wt = repo
    (wt / "reports").mkdir()
    (wt / "reports" / "local.json").write_text("{}")
    link_worktree(wt)
    assert not os.path.islink(wt / "reports")
    assert (wt / "reports" / "local.json").exists()
    assert "left alone" in capsys.readouterr().err
    assert os.path.realpath(wt / "inventory") == os.path.realpath(main / "inventory")


def test_main_checkout_is_untouched(repo):
    main, _ = repo
    assert link_worktree(main) == []
    assert not os.path.islink(main / "reports")
