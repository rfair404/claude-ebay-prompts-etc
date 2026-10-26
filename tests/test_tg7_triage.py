"""tests/test_tg7_triage.py — a TG-7 camera import is triaged by the
inventory-manager, and tools/tg7_triage.py (the run's one shell command) moves
frames only within inventory/, never overwrites, and logs every frame.

The import test runs the real tools/tg7_import.sh against a fake pull script,
a fake claude CLI and a fake notify-send (no camera, no Claude)."""
import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import tg7_triage as T  # noqa: E402


@pytest.fixture
def lot(tmp_path):
    lot = tmp_path / "inventory" / "tg7-20261024-1412"
    lot.mkdir(parents=True)
    for n in range(1, 6):
        (lot / f"P10100{n:02d}.JPG").write_bytes(b"jpg%d" % n)
    return lot


def run(*args):
    return T.main([str(a) for a in args])


def rows(lot):
    with (lot / "triage.csv").open(newline="") as fh:
        return list(csv.DictReader(fh))


def test_move_puts_frames_in_the_item_folder_and_logs_them(lot):
    dest = lot.parent / "bin-07" / "03-tin-car"
    run("move", lot, dest, "P1010002.JPG", "P1010003.JPG", "--kind", "item",
        "--note", "bin-07/catalog.csv#03")
    assert sorted(p.name for p in dest.iterdir()) == ["P1010002.JPG", "P1010003.JPG"]
    assert not (lot / "P1010002.JPG").exists()
    assert rows(lot) == [
        {"frame": "P1010002.JPG", "kind": "item", "dest": "inventory/bin-07/03-tin-car",
         "note": "bin-07/catalog.csv#03"},
        {"frame": "P1010003.JPG", "kind": "item", "dest": "inventory/bin-07/03-tin-car",
         "note": "bin-07/catalog.csv#03"},
    ]


def test_move_never_overwrites_and_moves_nothing_when_one_frame_is_taken(lot):
    dest = lot.parent / "bin-07" / "03-tin-car"
    dest.mkdir(parents=True)
    (dest / "P1010003.JPG").write_bytes(b"first shoot")
    with pytest.raises(SystemExit, match="already in"):
        run("move", lot, dest, "P1010002.JPG", "P1010003.JPG", "--kind", "item")
    assert (dest / "P1010003.JPG").read_bytes() == b"first shoot"
    assert (lot / "P1010002.JPG").exists() and (lot / "P1010003.JPG").exists()
    assert not (lot / "triage.csv").exists()


@pytest.mark.parametrize("dest", ["../outside", "tg7-20261024-1412", "_live/x", "."])
def test_move_stays_inside_inventory_item_folders(lot, dest):
    target = lot.parent / dest  # outside, the lot itself, a private folder, inventory/
    with pytest.raises(SystemExit):
        run("move", lot, target, "P1010001.JPG", "--kind", "item")
    assert (lot / "P1010001.JPG").exists()


def test_frames_must_be_plain_names_in_the_lot(lot, tmp_path):
    (tmp_path / "elsewhere.JPG").write_bytes(b"x")
    for bad in ("../../elsewhere.JPG", "P1019999.JPG"):
        with pytest.raises(SystemExit, match="no frame"):
            run("keep", lot, bad, "--kind", "other")


def test_lot_must_sit_directly_under_inventory(tmp_path):
    (tmp_path / "loose").mkdir()
    with pytest.raises(SystemExit, match="not a lot folder"):
        run("summary", tmp_path / "loose")


def test_summary_prefers_the_agents_line_and_counts_unsorted_frames(lot):
    run("keep", lot, "P1010001.JPG", "--kind", "overview")
    run("move", lot, lot / "01-vase", "P1010002.JPG", "--kind", "item")
    assert T.summary(lot) == "2 frame(s) sorted: 1 overview, 1 item · 3 frame(s) unsorted"
    (lot / "TRIAGE.txt").write_text("\n1 item new, 1 identified\n01 vase ...\n")
    assert T.summary(lot) == "1 item new, 1 identified · 3 frame(s) unsorted"
    run("keep", lot, "P1010003.JPG", "P1010004.JPG", "P1010005.JPG", "--kind", "other")
    assert T.summary(lot) == "1 item new, 1 identified"


def test_summary_with_nothing_done(lot):
    assert T.summary(lot) == "nothing sorted · 5 frame(s) unsorted"


# --- tools/tg7_import.sh, end to end with fakes ---------------------------

PULL = """#!/bin/bash
for f in P1010001.JPG P1010002.JPG; do echo x >"$TG7_PHOTOS/$f"; echo "  $f"; done
echo "2 new photo(s) -> $TG7_PHOTOS"
"""
# The fake claude records its arguments, then sorts like the agent would.
CLAUDE = """#!/bin/bash
printf '%s\\n' "$@" >"$FAKE_DIR/claude.args"
lot=$(ls -d inventory/tg7-* | head -1)
python3 tools/tg7_triage.py keep "$lot" P1010001.JPG --kind overview >/dev/null
python3 tools/tg7_triage.py move "$lot" "$lot/01-vase" P1010002.JPG --kind item >/dev/null
[ -n "$FAKE_NO_SUMMARY" ] || echo "overview: 1 item catalogued, 1 identified" >"$lot/TRIAGE.txt"
"""
NOTIFY = """#!/bin/bash
printf '%s|' "$@" >>"$FAKE_DIR/notify.calls"; echo >>"$FAKE_DIR/notify.calls"
"""


@pytest.fixture
def importer(tmp_path):
    if os.name == "nt" or not shutil.which("bash"):
        pytest.skip("needs bash")
    main, bin_, photos = tmp_path / "main", tmp_path / "bin", tmp_path / "photos"
    for d in (main / ".claude" / "skills" / "r", main / "tools", main / "inventory", bin_, photos):
        d.mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "tg7_triage.py", main / "tools" / "tg7_triage.py")
    for path, body in {main / ".claude" / "skills" / "r" / "tg7_pull.sh": PULL,
                       bin_ / "claude": CLAUDE, bin_ / "notify-send": NOTIFY}.items():
        path.write_text(body)
        path.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}",
           "FAKE_DIR": str(tmp_path), "TG7_MAIN": str(main), "TG7_PHOTOS": str(photos),
           "CLAUDE_BIN": str(bin_ / "claude")}
    return main, env


def _import(env):
    return subprocess.run(["bash", str(ROOT / "tools" / "tg7_import.sh")], env=env,
                          capture_output=True, text=True, timeout=60)


def test_import_hands_the_lot_to_the_inventory_manager(importer, tmp_path):
    main, env = importer
    assert _import(env).returncode == 0
    args = (tmp_path / "claude.args").read_text().splitlines()
    assert args[:2] == ["--agent", "inventory-manager"]
    lot = next((main / "inventory").glob("tg7-*"))
    assert f"inventory/{lot.name}" in args[args.index("-p") + 1]
    # the only shell command allowed is the triage tool, never bare Bash
    allowed = args[args.index("--allowedTools") + 1:]
    assert "Bash" not in allowed
    assert [a for a in allowed if a.startswith("Bash")] == [
        "Bash(python tools/tg7_triage.py:*)", "Bash(python3 tools/tg7_triage.py:*)"]

    assert (lot / "01-vase" / "P1010002.JPG").exists()
    calls = (tmp_path / "notify.calls").read_text().splitlines()
    assert calls[0].startswith("-a|TG-7|TG-7: 2 new photo(s)|")
    assert calls[-1] == "-a|TG-7|TG-7: sorted|overview: 1 item catalogued, 1 identified|"
    assert "triage exit 0: overview: 1 item catalogued" in (lot / "import.log").read_text()


def test_import_says_so_when_triage_leaves_no_summary(importer, tmp_path):
    main, env = importer
    _import({**env, "FAKE_NO_SUMMARY": "1"})
    last = (tmp_path / "notify.calls").read_text().splitlines()[-1]
    assert last.startswith("-a|TG-7|TG-7: sorting failed|2 frame(s) sorted: 1 overview, 1 item")


def test_import_without_a_hint_passes_no_model(importer, tmp_path):
    main, env = importer
    _import(env)
    args = (tmp_path / "claude.args").read_text().splitlines()
    assert "--model" not in args
    assert "first-pass" not in args[args.index("-p") + 1]


def test_a_hint_file_steers_one_import_then_is_used_up(importer, tmp_path):
    main, env = importer
    hints = main / "inventory" / "_consign"
    hints.mkdir()
    (hints / "tg7_next.txt").write_text("client: KBD\nmodel: haiku\n")
    assert _import(env).returncode == 0
    args = (tmp_path / "claude.args").read_text().splitlines()
    assert args[args.index("--model") + 1] == "haiku"
    prompt = args[args.index("-p") + 1]
    assert "first-pass triage only" in prompt and "client KBD" in prompt
    lot = next((main / "inventory").glob("tg7-*"))
    assert not (hints / "tg7_next.txt").exists()
    assert (hints / f"tg7_next.used-{lot.name}.txt").exists()
    assert "hint used: client=KBD model=haiku" in (lot / "import.log").read_text()


def test_a_hint_value_that_is_not_a_plain_code_is_ignored(importer, tmp_path):
    main, env = importer
    hints = main / "inventory" / "_consign"
    hints.mkdir()
    (hints / "tg7_next.txt").write_text("client: not a code\nmodel: haiku; rm -rf /\n")
    _import(env)
    args = (tmp_path / "claude.args").read_text().splitlines()
    assert "--model" not in args
    assert "client" not in args[args.index("-p") + 1].split("first-pass")[1]
