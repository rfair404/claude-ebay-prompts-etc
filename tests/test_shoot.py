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
from test_pager import CamObs, Camera, FakeTg7, StillObs, camera  # noqa: E402,F401  (camera: the fixture)


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

    lamp = (d / "01-brass-desk-lamp" / "shoot_log.md").read_text(encoding="utf-8")
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
    assert "(tg7, focus didn't lock)" in (inv / "s1" / "01" / "shoot_log.md").read_text(encoding="utf-8")


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


def test_a_tg7_take_that_didnt_fire_is_an_error_and_no_frame(inv, capsys, monkeypatch):
    # 2026-10-22: the TG-7 answered take=ok, the card didn't change, and the
    # previous photo was saved again as a new frame. The real tg7cam, its Wi-Fi faked.
    T = SF.tg7cam()
    card = "VER_100\r\n/DCIM/100OMSYS,ZZ220001.JPG,9,0,23893,40372\r\n"
    took = "<response><take>ok</take><affocus>ok</affocus></response>"
    monkeypatch.setattr(T, "cgi", lambda name, **kw: card if name == "get_imglist"
                        else took if kw.get("com") == "starttake" else "")
    monkeypatch.setattr(T, "get_file", lambda *a, **kw: pytest.fail("downloaded the old photo"))
    monkeypatch.setattr(T, "CARD_WAIT", 0)
    run(capsys, "start", "s1")
    run(capsys, "item")
    rc, out, err = run(capsys, "capture", "tg7")
    assert rc == 1 and "TG-7: the TG-7 didn't take a new photo; the last one on the card is unchanged" in err
    item = inv / "s1" / "01"
    log = item / "shoot_log.md"
    assert S.frames(item) == [] and "capture" not in (log.read_text(encoding="utf-8") if log.exists() else "")
    assert "frames: 0/10" in run(capsys, "status")[1]


@pytest.fixture
def on_air_still(inv, tmp_path, monkeypatch):
    """show_flow.still() on item over the fakes, its OBS copies in tmp_path."""
    monkeypatch.setattr(SF, "STILL_DIR", tmp_path / "still")
    monkeypatch.setattr(SF, "LIVE_STILLS", tmp_path / "stills")
    monkeypatch.setattr(SF.time, "sleep", lambda s: None)
    c = StillObs()
    c.tg7 = FakeTg7(c)
    monkeypatch.setattr(SF, "tg7cam", lambda: c.tg7)
    return c


def test_a_still_on_air_lands_in_the_open_shoot_item_and_its_log(inv, capsys, on_air_still, tmp_path):
    run(capsys, "start", "s1")
    run(capsys, "item", "lamp")
    r = SF.still(on_air_still, note="maker mark")
    item = inv / "s1" / "01-lamp"
    frame = Path(r["file"])
    assert frame.parent == item and r["item"] == "01-lamp" and on_air_still.scene == "TG-7 Still"
    assert r["look"] == str(item / ".shoot" / frame.name)  # the agent's small copy, as capture makes
    assert Path(r["look"]).exists()
    assert f"capture **{frame.name}** (tg7) — maker mark" in (item / "shoot_log.md").read_text(encoding="utf-8")
    assert not (tmp_path / "stills").exists()
    rc, out, _ = run(capsys, "status")
    assert "frames: 1/10" in out


def test_a_still_with_no_item_open_goes_to_the_live_stills_folder(inv, capsys, on_air_still, tmp_path):
    run(capsys, "start", "s1")  # a shoot, but nothing on the podium yet
    r = SF.still(on_air_still)
    assert Path(r["file"]).parent.parent == tmp_path / "stills" and r["item"] is None
    assert S.open_shoot() is None


def test_a_still_that_misses_focus_in_a_shoot_is_logged_and_kept_off_air(inv, capsys, on_air_still):
    run(capsys, "start", "s1")
    run(capsys, "item")
    on_air_still.tg7.focus = "ng"
    with pytest.raises(obsctl.ObsError, match="focus didn't lock"):
        SF.still(on_air_still)
    assert on_air_still.switches == []
    assert "(tg7, focus didn't lock)" in (inv / "s1" / "01" / "shoot_log.md").read_text(encoding="utf-8")


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
    c.active["Stream"] = True  # streaming: viewers would see the freeze
    r = SF.still_d5(c, tmp_path / "item")
    assert r["files"] == d5.saved
    assert d5.log[:3] == ["stop", "--auto-detect", "capture"] and d5.running
    assert c.switches == ["Up Next", "eBay Main (camera 1)"]


def test_a_still_that_fails_says_why_and_leaves_the_feed_running(d5, tmp_path):
    d5.saved = []
    c = CamObs(["old", "new"])
    with pytest.raises(obsctl.ObsError, match=r"Out of Focus \*\*\* \(autofocus can't lock"):
        SF.still_d5(c, tmp_path / "item")
    assert d5.running and c.scene == "eBay Main (camera 1)"


# --- a shoot inside a lot folder (inventory/ES/MON1020-M) ---------------------------
sys.path.insert(0, str(ROOT / "lib"))
import dir_context  # noqa: E402
import source_report  # noqa: E402

LOT_CONTEXT = "kind: event\nspend: 800\n\nAn estate in one house.\n"


@pytest.fixture
def lot(inv):
    """The lot as the producer left it: a context.txt and three items moved
    in by hand from another shoot, none of them in a shoot.json."""
    d = inv / "ES" / "MON1020-M"
    for item in ("02-playboy-trump-cover", "03-cedar-bible-box", "04-10kt-zrw-reversible-necklace"):
        (d / item).mkdir(parents=True)
    (d / "context.txt").write_text(LOT_CONTEXT)
    return d


def test_start_takes_a_path_into_a_lot_and_keeps_its_folder_case(inv, lot, capsys):
    rc, out, _ = run(capsys, "start", "es/mon1020-m")  # typed in any case: the lot's own names win
    assert rc == 0 and "started: ES/MON1020-M" in out
    assert (inv / ".current_shoot").read_text().strip() == "ES/MON1020-M"
    st = json.loads((lot / "shoot.json").read_text())
    assert st["shoot"] == "ES/MON1020-M" and st["items"] == [] and st["current"] is None
    assert [p.name for p in inv.iterdir() if p.is_dir()] == ["ES"]  # no lowercase twin made
    assert S.shoot_dir() == lot  # CURRENT resolves to the lot
    rc, out, _ = run(capsys, "start", "ES/MON1020-M")
    assert "resumed: ES/MON1020-M" in out


def test_new_segments_are_slugged_under_existing_ones(inv, lot, capsys):
    run(capsys, "start", "ES/Second Day!")
    assert (inv / "ES" / "second-day" / "shoot.json").is_file()


@pytest.mark.parametrize("bad", ["/tmp/x", "../outside", "ES/../../x", "C:/x", "C:\\x", "/"])
def test_a_path_out_of_inventory_is_refused(inv, capsys, bad):
    rc, _, err = run(capsys, "start", bad)
    assert rc == 1 and "inside inventory/" in err
    assert not (inv / ".current_shoot").exists()


def test_items_number_on_after_the_lots_existing_folders(inv, lot, capsys):
    run(capsys, "start", "ES/MON1020-M")
    rc, out, _ = run(capsys, "item", "brass lamp")
    assert rc == 0 and "open: 05-brass-lamp" in out
    run(capsys, "item")
    assert (lot / "06").is_dir()
    (lot / "09-moved-in-later").mkdir()  # another hand move mid-shoot
    run(capsys, "item", "vase")
    assert (lot / "10-vase").is_dir()
    assert sorted(p.name for p in lot.iterdir() if p.is_dir()) == [
        "02-playboy-trump-cover", "03-cedar-bible-box", "04-10kt-zrw-reversible-necklace",
        "05-brass-lamp", "06", "09-moved-in-later", "10-vase"]


def test_a_lot_item_inherits_the_lots_context_and_bucket(inv, lot, capsys, on_air_still):
    run(capsys, "start", "ES/MON1020-M")
    run(capsys, "item", "lamp")
    item = lot / "05-lamp"
    ctx = dir_context.load(item, root=inv)
    assert ctx.keys["kind"] == "event" and ctx.keys["spend"] == "800"
    assert source_report.bucket_for(item, root=inv) == lot.resolve()
    assert source_report.bucket_label(lot, root=inv) == "ES/MON1020-M"
    assert source_report.load_context(source_report.bucket_for(item, root=inv))["spend"] == 800
    # and a TG-7 still on air lands there, through the same resolver
    r = SF.still(on_air_still)
    assert Path(r["file"]).parent == item and r["item"] == "05-lamp"
