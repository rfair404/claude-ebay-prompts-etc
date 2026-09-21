"""tests/test_webapp_jobs.py — webapp/jobs.py's Phase-2 job-type whitelist
(#31): path validation for both job types, plus prep-auto's subprocess
wiring (mocked — this file never actually runs PREP) and price-stats'
real call into lib/price_stats.py's pure function (no mock needed, no
network, no credentials).
"""
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import webapp.jobs as jobs  # noqa: E402

FIXTURE_COMP = ROOT / "tests" / "fixtures" / "price_stats_pair" / "best_match.json"


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    inv = tmp_path / "inventory"
    inv.mkdir()
    monkeypatch.setattr(jobs, "INVENTORY", inv)
    return inv


# ---------------------------------------------------------------------------
# _resolve_shoot_dir
# ---------------------------------------------------------------------------

def test_resolve_shoot_dir_accepts_a_real_shoot(inventory):
    shoot = inventory / "ge-tube-6sn7gtb"
    shoot.mkdir()
    assert jobs._resolve_shoot_dir("ge-tube-6sn7gtb") == shoot.resolve()


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "../escape"])
def test_resolve_shoot_dir_rejects_path_like_names(inventory, name):
    with pytest.raises(ValueError):
        jobs._resolve_shoot_dir(name)


def test_resolve_shoot_dir_rejects_unknown_shoot(inventory):
    with pytest.raises(ValueError, match="unknown shoot"):
        jobs._resolve_shoot_dir("never-shot-this")


def test_resolve_shoot_dir_rejects_a_file_not_a_directory(inventory):
    (inventory / "not-a-dir").write_text("x")
    with pytest.raises(ValueError):
        jobs._resolve_shoot_dir("not-a-dir")


# ---------------------------------------------------------------------------
# _resolve_under_inventory
# ---------------------------------------------------------------------------

def test_resolve_under_inventory_accepts_a_real_file(inventory):
    shoot = inventory / "shoot1"
    shoot.mkdir()
    comp = shoot / "best_match.json"
    comp.write_text("{}")
    resolved = jobs._resolve_under_inventory(str(comp))
    assert resolved == comp.resolve()


def test_resolve_under_inventory_accepts_a_relative_path(tmp_path, monkeypatch):
    # A relative path resolves against jobs.REPO, then must still land
    # inside jobs.INVENTORY — the shape a real request body sends (a path
    # relative to the repo root, as tools/dashboard.py's rows already are).
    monkeypatch.setattr(jobs, "REPO", tmp_path)
    monkeypatch.setattr(jobs, "INVENTORY", tmp_path / "inventory")
    shoot = tmp_path / "inventory" / "shoot1"
    shoot.mkdir(parents=True)
    comp = shoot / "best_match.json"
    comp.write_text("{}")
    resolved = jobs._resolve_under_inventory("inventory/shoot1/best_match.json")
    assert resolved == comp.resolve()


def test_resolve_under_inventory_rejects_a_path_outside_inventory(inventory, tmp_path):
    outside = tmp_path / "secret.json"
    outside.write_text("{}")
    with pytest.raises(ValueError, match="must be under inventory"):
        jobs._resolve_under_inventory(str(outside))


def test_resolve_under_inventory_rejects_a_missing_file(inventory):
    missing = inventory / "shoot1" / "best_match.json"
    with pytest.raises(ValueError, match="not found"):
        jobs._resolve_under_inventory(str(missing))


# ---------------------------------------------------------------------------
# prep_auto — subprocess.run mocked, never actually runs PREP
# ---------------------------------------------------------------------------

def test_prep_auto_shells_to_ebz_prep_with_auto(inventory, monkeypatch):
    shoot = inventory / "shoot1"
    shoot.mkdir()
    calls = []

    class _FakeProc:
        returncode = 0
        stdout = "OK 3/3\n"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _FakeProc()

    monkeypatch.setattr(jobs.subprocess, "run", _fake_run)
    result = jobs.prep_auto({"shoot": "shoot1"})

    assert result == {"returncode": 0, "stdout": "OK 3/3\n", "stderr": ""}
    (cmd, kwargs) = calls[0]
    assert cmd[1:4] == ["-m", "lib.cli", "prep"]
    assert cmd[4] == str(shoot.resolve())
    assert cmd[-1] == "--auto"
    assert kwargs["timeout"] == jobs.PREP_AUTO_TIMEOUT
    assert kwargs["cwd"] == jobs.REPO


def test_prep_auto_rejects_an_unknown_shoot(inventory):
    with pytest.raises(ValueError, match="unknown shoot"):
        jobs.prep_auto({"shoot": "does-not-exist"})


# ---------------------------------------------------------------------------
# price_stats — a real call into lib/price_stats.py, no mocking needed
# ---------------------------------------------------------------------------

def test_price_stats_runs_the_real_pricing_pipeline(inventory):
    shoot = inventory / "shoot1"
    shoot.mkdir()
    comp = shoot / "best_match.json"
    shutil.copy(FIXTURE_COMP, comp)

    report = jobs.price_stats({
        "best_match_json": str(comp),
        "unit_type": "pair",
    })

    assert report["n_raw"] > 0
    assert "distribution" in report
    assert "confidence" in report


def test_price_stats_rejects_a_comp_path_outside_inventory(inventory, tmp_path):
    outside = tmp_path / "best_match.json"
    shutil.copy(FIXTURE_COMP, outside)
    with pytest.raises(ValueError, match="must be under inventory"):
        jobs.price_stats({"best_match_json": str(outside)})


def test_price_stats_requires_at_least_one_source(inventory):
    with pytest.raises(ValueError):
        jobs.price_stats({})


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def test_job_handlers_whitelist_is_exactly_the_two_phase2_types():
    assert set(jobs.JOB_HANDLERS) == {"prep-auto", "price-stats"}
