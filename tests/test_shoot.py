"""tests/test_shoot.py — tools/live/shoot.py, the interactive photo shoot, and
show_flow.still_d5, the D5100 still it takes. The shoot lives in a temp
inventory/; OBS, the feed, gphoto2 and the TG-7 are stubbed (the D5 fakes are
test_pager's), so nothing here needs a camera."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "live"))

import obsctl  # noqa: E402
import shoot as S  # noqa: E402
import show_flow as SF  # noqa: E402
from test_pager import CamObs, Camera, camera  # noqa: E402,F401  (camera: the fixture)


def jpeg(path: Path, size=(1200, 800)) -> Path:
    Image.new("RGB", size, "white").save(path)
    return path


@pytest.fixture
def inv(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "INVENTORY", tmp_path / "inventory")
    monkeypatch.setattr(S, "CURRENT", tmp_path / "inventory" / ".current_shoot")
    return tmp_path / "inventory"


def run(capsys, *argv):
    rc = S.main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_start_item_capture_done_round_trip(inv, capsys, monkeypatch):
    shots = []

    def fake_still(c, dest):
        shots.append(dest)
        return {"camera": "d5", "files": [str(jpeg(dest / f"D5_{len(shots)}.jpg"))], "still_s": 6.1}

    monkeypatch.setattr(SF, "connect", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(SF, "still_d5", fake_still)

    assert run(capsys, "start", "Oct 21 Shoot")[0] == 0
    d = inv / "oct-21-shoot"
    assert (inv / ".current_shoot").read_text().strip() == "oct-21-shoot"

    rc, out, _ = run(capsys, "item", "--note", "brass lamp, sticky switch")
    assert rc == 0 and "open: 01" in out
    rc, out, _ = run(capsys, "capture", "d5", "--note", "hero")
    assert rc == 0 and "01: 1/10 frames" in out
    look = d / "01" / ".shoot" / "D5_1.jpg"
    assert f"look: {look}" in out
    assert max(Image.open(look).size) == S.THUMB  # the small copy, not the frame

    assert run(capsys, "name", "Brass Desk Lamp")[0] == 0
    assert run(capsys, "log", "maker stamp on the base, not shot yet")[0] == 0
    rc, out, _ = run(capsys, "item", "tin-robot")  # closes the lamp on its way
    assert "done: 01-brass-desk-lamp: 1 frames  (house target 10)" in out and "open: 02-tin-robot" in out

    lamp = (d / "01-brass-desk-lamp" / "shoot_log.md").read_text()
    for line in ("**Russell:** brass lamp, sticky switch", "capture **D5_1.jpg** (d5) — hero",
                 "renamed from 01", "**Claude:** maker stamp on the base", "done: 1 frames (house target 10)"):
        assert line in lamp
    st = json.loads((d / "shoot.json").read_text())
    assert [i["dir"] for i in st["items"]] == ["01-brass-desk-lamp", "02-tin-robot"]
    assert st["items"][0]["done"] and st["current"] == "02-tin-robot"
    assert shots == [d / "01"]


def test_start_resumes_and_status_shows_the_open_item(inv, capsys):
    run(capsys, "start", "s1")
    run(capsys, "item", "vase", "--note", "chip on the rim")
    rc, out, _ = run(capsys, "start", "s1")
    assert "resumed: s1" in out and "open: 01-vase" in out
    rc, out, _ = run(capsys, "status")
    assert "frames: 0/10" in out and "chip on the rim" in out


def test_tg7_capture_flags_a_focus_miss(inv, capsys, monkeypatch):
    class CamError(Exception):
        pass

    def fake_shoot(x=50, y=50, to=None):
        return jpeg(Path(to) / "TG7_20261021_220000.jpg"), "ng"

    monkeypatch.setattr(SF, "tg7cam", lambda: SimpleNamespace(shoot=fake_shoot, CamError=CamError))
    run(capsys, "start", "s1")
    run(capsys, "item")
    rc, out, _ = run(capsys, "capture", "tg7")
    assert rc == 0 and "FOCUS DIDN'T LOCK" in out
    assert "(tg7, focus didn't lock)" in (inv / "s1" / "01" / "shoot_log.md").read_text()


def test_tg7_unreachable_is_an_error_line(inv, capsys, monkeypatch):
    class CamError(Exception):
        pass

    def off(**kw):
        raise CamError("camera not reachable at 192.168.0.10")

    monkeypatch.setattr(SF, "tg7cam", lambda: SimpleNamespace(shoot=off, CamError=CamError))
    run(capsys, "start", "s1")
    run(capsys, "item")
    rc, _, err = run(capsys, "capture", "tg7")
    assert rc == 1 and "TG-7: camera not reachable" in err


def test_shoot_flag_takes_the_name_start_was_given(inv, capsys):
    run(capsys, "start", "Oct 21 Shoot")
    assert run(capsys, "--shoot", "Oct 21 Shoot", "item", "lamp")[0] == 0
    assert (inv / "oct-21-shoot" / "01-lamp").is_dir()


def test_commands_need_a_shoot_and_an_open_item(inv, capsys):
    rc, _, err = run(capsys, "status")
    assert rc == 1 and "no shoot started" in err
    run(capsys, "start", "s1")
    rc, _, err = run(capsys, "capture", "d5")
    assert rc == 1 and "no item open" in err
    rc, out, _ = run(capsys, "done")
    assert rc == 0 and "no item open" in out


def test_frames_count_jpegs_once_and_skip_the_copies(inv, tmp_path):
    item = tmp_path / "item"
    (item / ".shoot").mkdir(parents=True)
    for name in ("D5_1.JPG", "D5_1.NEF", "TG7_2.jpg", "shoot_log.md", ".shoot/D5_1.jpg"):
        (item / name).write_bytes(b"x")
    assert [f.name for f in S.frames(item)] == ["D5_1.JPG", "TG7_2.jpg"]


# --- show_flow.still_d5 ---------------------------------------------------------
class StillCamera(Camera):
    """test_pager's Camera, plus the capture: gphoto2 reports each file it saved."""

    saved: list[str] = []

    def run(self, cmd, **kw):
        if "--capture-image-and-download" not in cmd:
            return super().run(cmd, **kw)
        assert kw["timeout"] == SF.STILL_TIMEOUT and not self.running  # live view is off for it
        self.log.append("capture")
        out = "".join(f"New file is in location /capt0000.jpg on the camera\nSaving file as {f}\n"
                      for f in self.saved)
        return SF.subprocess.CompletedProcess(cmd, 0 if self.saved else 1, stdout=out,
                                              stderr="" if self.saved else "*** Error: Out of Focus ***")


@pytest.fixture
def d5(camera, monkeypatch):
    return StillCamera(monkeypatch, camera.snap)


def test_still_d5_hides_behind_up_next_and_restarts_the_feed(d5, tmp_path):
    d5.saved = [str(tmp_path / "D5_20261021_214112.JPG"), str(tmp_path / "D5_20261021_214112.NEF")]
    c = CamObs(["old", "new"])
    r = SF.still_d5(c, tmp_path / "item")
    assert r["files"] == d5.saved
    assert d5.log[:3] == ["stop", "--auto-detect", "capture"] and d5.running
    assert c.switches == ["Up Next", "eBay Main"]


def test_a_still_that_fails_says_why_and_leaves_the_feed_running(d5, tmp_path):
    d5.saved = []
    c = CamObs(["old", "new"])
    with pytest.raises(obsctl.ObsError, match=r"Out of Focus \*\*\* \(autofocus can't lock"):
        SF.still_d5(c, tmp_path / "item")
    assert d5.running and c.scene == "eBay Main"
