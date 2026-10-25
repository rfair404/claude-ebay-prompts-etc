"""tests/test_ship_station.py: tools/ship_station.sh, the laptop's side of
the shipping station on green. A fake `ssh` on PATH runs each "remote"
command locally with HOME set to a temp folder, so push, pull and setup run
for real against a fake green: the queue lands by rename, boxes.csv comes
back byte for byte as boxes-green.csv, a redeploy keeps green's data, and a
green that is down never changes a laptop file. No network."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "ship_station.sh"
pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="green and the laptop are Linux"),
    pytest.mark.skipif(not (shutil.which("bash") and shutil.which("git")), reason="needs bash and git"),
]

FAKE_SSH = r"""#!/bin/bash
echo "$*" >> "$FAKE_LOG"
while [[ ${1:-} == -* ]]; do [[ $1 == -o ]] && shift; shift; done
shift   # the host
[ -n "${FAKE_DOWN:-}" ] && { echo "ssh: connect to host green port 22: Connection refused" >&2; exit 255; }
HOME=$FAKE_HOME exec bash -c "$*"
"""


@pytest.fixture
def env(tmp_path):
    bin_, green, main = tmp_path / "bin", tmp_path / "green", tmp_path / "main"
    for d in (bin_, green, main):
        d.mkdir()
    (bin_ / "ssh").write_text(FAKE_SSH)
    # Stubs that log, so nothing reaches the laptop's own systemd or logind.
    (bin_ / "systemctl").write_text('#!/bin/bash\necho "systemctl $*" >> "$FAKE_TOOLS"\n')
    (bin_ / "loginctl").write_text('#!/bin/bash\necho "loginctl $*" >> "$FAKE_TOOLS"\n'
                                   '[ "$1" = show-user ] && echo "${FAKE_LINGER:-no}"; exit 0\n')
    for f in ("ssh", "systemctl", "loginctl"):
        (bin_ / f).chmod(0o755)
    venv = green / "pops-ship-venv" / "bin"  # a venv that is already there: no pip, no network
    venv.mkdir(parents=True)
    for f in ("python", "pip"):
        (venv / f).write_text("#!/bin/bash\nexit 0\n")
        (venv / f).chmod(0o755)
    git = ["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", str(main)], check=True)
    (main / "lib").mkdir()
    (main / "lib" / "cli.py").write_text("print('serve')\n")
    (main / "config.yaml").write_text("secret: never-on-green\n")  # untracked, like the real one
    subprocess.run(git + ["add", "lib/cli.py"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "x"], check=True)
    e = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", FAKE_HOME=str(green),
             FAKE_LOG=str(tmp_path / "ssh.log"), FAKE_TOOLS=str(tmp_path / "tools.log"),
             SHIP_STATION_MAIN=str(main), SHIP_STATION_REF="HEAD")
    return {"env": e, "green": green, "main": main, "log": tmp_path / "ssh.log",
            "tools": tmp_path / "tools.log"}


def run(env, *args, **extra):
    return subprocess.run(["bash", str(SCRIPT), *args], env=dict(env["env"], **extra),
                          capture_output=True, text=True, timeout=60)


def queue(env, text='{"version": 1, "boxes": []}'):
    q = env["main"] / "reports" / "shipping" / "ship_queue.json"
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text(text)
    return q


def test_setup_deploys_tracked_files_only_with_a_data_link_and_a_user_unit(env):
    r = run(env, "setup")
    assert r.returncode == 0, r.stderr
    app = env["green"] / "pops-ship"
    assert (app / "lib" / "cli.py").exists() and not (app / "config.yaml").exists()
    assert (app / "reports").is_symlink()
    assert (app / "reports").resolve() == (env["green"] / "pops-ship-data" / "reports").resolve()
    assert (app / ".deployed").read_text().strip()
    unit = (env["green"] / ".config" / "systemd" / "user" / "pops-serve.service").read_text()
    assert "ExecStart=%h/pops-ship-venv/bin/python -m lib.cli serve" in unit and "Restart=on-failure" in unit
    assert all("AddressFamily=inet" in line for line in env["log"].read_text().splitlines())


def test_a_redeploy_keeps_greens_boxes(env):
    assert run(env, "setup").returncode == 0
    boxes = env["green"] / "pops-ship-data" / "reports" / "shipping" / "boxes.csv"
    boxes.write_bytes(b"recorded_at,store\r\n2026-10-24,default\r\n")
    assert run(env, "setup").returncode == 0
    assert boxes.read_bytes().endswith(b"default\r\n")
    assert (env["green"] / "pops-ship" / "reports" / "shipping" / "boxes.csv").exists()
    assert not (env["green"] / "pops-ship.old").exists() and not (env["green"] / "pops-ship.new").exists()


def test_setup_refuses_a_folder_it_did_not_make(env):
    mine = env["green"] / "pops-ship" / "keep.txt"
    mine.parent.mkdir()
    mine.write_text("russell's")
    r = run(env, "setup")
    assert r.returncode == 4 and mine.read_text() == "russell's"


def test_setup_waits_on_air_and_the_dry_run_touches_nothing(env):
    flag = env["main"] / "inventory" / "_live" / "ON_AIR"
    flag.parent.mkdir(parents=True)
    flag.touch()
    r = run(env, "setup")
    assert r.returncode == 1 and "on air" in r.stderr and not env["log"].exists()
    for cmd in ("setup", "push", "pull", "status"):
        assert run(env, "--dry-run", cmd).returncode == 0
    assert not env["log"].exists() and not (env["green"] / "pops-ship").exists()


def test_push_lands_the_queue_by_rename_and_needs_setup_first(env):
    q = queue(env)
    assert run(env, "push").returncode == 3  # green not set up yet
    assert run(env, "setup").returncode == 0
    q.write_text('{"version": 1, "boxes": [1]}')
    assert run(env, "push").returncode == 0
    landed = env["green"] / "pops-ship" / "reports" / "shipping" / "ship_queue.json"
    assert landed.read_text() == q.read_text()
    assert not landed.with_name("ship_queue.json.tmp").exists()


def test_push_with_no_queue_is_a_no_op_and_a_down_green_fails_fast(env):
    r = run(env, "push")
    assert r.returncode == 0 and not env["log"].exists()
    queue(env)
    r = run(env, "push", FAKE_DOWN="1")
    assert r.returncode != 0 and "carries on" in r.stderr


def test_pull_brings_boxes_back_as_boxes_green_byte_for_byte(env):
    assert run(env, "setup").returncode == 0
    rows = b"recorded_at,store,order_ids\r\n2026-10-24T15:00,default,01-1\r\n"
    (env["green"] / "pops-ship-data" / "reports" / "shipping" / "boxes.csv").write_bytes(rows)
    assert run(env, "pull").returncode == 0
    back = env["main"] / "reports" / "shipping" / "boxes-green.csv"
    assert back.read_bytes() == rows
    assert not (env["main"] / "reports" / "shipping" / "boxes.csv").exists()


def test_pull_never_replaces_the_laptops_copy_with_nothing(env):
    back = env["main"] / "reports" / "shipping" / "boxes-green.csv"
    back.parent.mkdir(parents=True)
    back.write_bytes(b"kept\r\n")
    assert run(env, "pull").returncode == 0           # green has no boxes.csv
    assert run(env, "pull", FAKE_DOWN="1").returncode == 1
    assert back.read_bytes() == b"kept\r\n" and not back.with_name("boxes-green.csv.tmp").exists()


def test_setup_turns_linger_on_before_enabling_the_unit(env):
    assert run(env, "setup").returncode == 0
    calls = env["tools"].read_text().splitlines()
    linger = calls.index(next(c for c in calls if c.startswith("loginctl enable-linger")))
    enable = calls.index(next(c for c in calls if c.startswith("systemctl --user enable")))
    assert linger < enable


def test_setup_skips_linger_when_already_on(env):
    assert run(env, "setup", FAKE_LINGER="yes").returncode == 0
    assert not any(c.startswith("loginctl enable-linger") for c in env["tools"].read_text().splitlines())


def test_status_prints_linger(env):
    assert run(env, "setup").returncode == 0
    r = run(env, "status", FAKE_LINGER="yes")
    assert "linger:  yes" in r.stdout, r.stdout + r.stderr
