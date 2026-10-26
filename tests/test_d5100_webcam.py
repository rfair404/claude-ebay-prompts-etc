"""tests/test_d5100_webcam.py — tools/d5100_webcam.sh sets the D5100's clock
before every live-view (re)start and notes a reset date beside its frame.

Runs the real script against fake gphoto2/ffmpeg on PATH (nothing touches a
camera); the fake capture ends at once, so the loop restarts until killed."""
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FEED = ROOT / "tools" / "d5100_webcam.sh"
REAL_SLEEP = shutil.which("sleep")

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("timeout") and REAL_SLEEP)
                                or os.name == "nt", reason="needs bash and coreutils")

# what gphoto2 2.5 prints for `--get-config datetime` (a DATE widget)
DATE = ("Label: Camera Date and Time\nReadonly: 0\nType: DATE\nCurrent: {epoch}\n"
        "Printable: {printable}\nHelp: Use 'now' as the current time when setting.\nEND\n")
RESET = DATE.format(epoch=1293840005, printable="Sat Jan  1 00:00:05 2011")  # 2011-01-01 UTC
SANE = DATE.format(epoch=1792600000, printable="Fri Oct 23 2026")

# `gphoto2 --auto-detect` with the TG-7 on USB too: it is listed first (2026-10-25)
DETECT = ("Model                          Port\n----------------------------------------------------------\n"
          "Mass Storage Camera            disk:/media/russ/OM SYSTEM\n"
          "Nikon DSC D5100 (PTP mode)     usb:001,036\n")
TG7_ONLY = DETECT.splitlines(keepends=True)[:3]

GPHOTO2 = f"""#!/bin/bash
echo "$*" >>"$FAKE_DIR/calls"
case "$*" in
  --auto-detect) cat "$FAKE_DIR/detect" ;;
  *--get-config*) case "$FAKE_GET" in hang) exec {REAL_SLEEP} 30;; fail) exit 1;; esac
                  cat "$FAKE_DIR/date" ;;
  *--set-config*) case "$FAKE_SET" in hang) exec {REAL_SLEEP} 30;; fail) exit 1;; esac ;;
  *) exit 1 ;;  # --capture-movie: live view drops at once
esac
"""


def run_feed(tmp_path, date="", get="ok", set_="ok", captures=1, detect=DETECT, port=None, until="--capture-movie"):
    """Run the feed until gphoto2 `until` (--capture-movie) has run `captures` times;
    returns (gphoto2 calls, stderr, the clock marker path)."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in {"gphoto2": GPHOTO2, "ffmpeg": "#!/bin/bash\nexec cat >/dev/null\n",
                       "sleep": f"#!/bin/bash\nexec {REAL_SLEEP} 0.05\n"}.items():  # the 2 s restart pause
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    (tmp_path / "date").write_text(date)
    (tmp_path / "detect").write_text("".join(detect))
    calls, err = tmp_path / "calls", tmp_path / "err"
    env = {**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}", "TZ": "UTC",
           "FAKE_DIR": str(tmp_path), "FAKE_GET": get, "FAKE_SET": set_, "CLOCK_TIMEOUT": "1",
           "SNAP": str(tmp_path / "d5100_latest.jpg"), "DEV": str(tmp_path / "video10")}
    env.pop("PORT", None)
    if port:
        env["PORT"] = port
    with open(err, "wb") as e:
        p = subprocess.Popen(["bash", str(FEED)], env=env, stdin=subprocess.DEVNULL, stdout=e, stderr=e,
                             start_new_session=True)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if calls.exists() and calls.read_text().count(until) >= captures:
                    break
                time.sleep(0.02)
        finally:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
    lines = calls.read_text().splitlines() if calls.exists() else []
    return lines, err.read_text(), tmp_path / "d5100_latest.clock"


def test_a_reset_date_is_logged_and_noted_beside_the_frame(tmp_path):
    calls, err, mark = run_feed(tmp_path, RESET)
    assert "D5100 clock reset (camera said 2011-01-01 00:00:05)" in err
    when, said = mark.read_text().rstrip("\n").split("\t")
    assert said == "2011-01-01 00:00:05" and when.startswith(time.strftime("%Y-"))


def test_a_sane_date_leaves_no_note(tmp_path):
    (tmp_path / "d5100_latest.clock").write_text("old\tnote\n")  # from an earlier reset
    calls, err, mark = run_feed(tmp_path, SANE)
    assert not mark.exists() and "clock" not in err


def test_the_clock_is_set_before_every_live_view_restart(tmp_path):
    calls, _, _ = run_feed(tmp_path, SANE, captures=3)
    assert calls[:12] == ["--auto-detect", "--port usb:001,036 --get-config datetime",
                          "--port usb:001,036 --set-config datetime=now",
                          "--port usb:001,036 --stdout --capture-movie"] * 3


def test_every_call_names_the_d5100_port_not_the_tg7_listed_first(tmp_path):
    calls, _, _ = run_feed(tmp_path, SANE)
    assert all(c.startswith("--port usb:001,036 ") for c in calls if c != "--auto-detect")


def test_port_overrides_auto_detect(tmp_path):
    calls, _, _ = run_feed(tmp_path, SANE, detect="", port="usb:002,007")
    assert "--auto-detect" not in calls and calls[-1] == "--port usb:002,007 --stdout --capture-movie"


def test_no_d5100_detected_is_logged_and_retried_without_touching_the_tg7(tmp_path):
    calls, err, _ = run_feed(tmp_path, SANE, detect=TG7_ONLY, captures=2, until="--auto-detect")
    assert calls[:2] == ["--auto-detect", "--auto-detect"]  # nothing else ran in between
    assert "no D5100 detected by gphoto2 --auto-detect" in err


@pytest.mark.parametrize("date,get,set_", [(RESET, "hang", "fail"), (RESET, "fail", "hang"),
                                           ("gphoto2: garbage\n", "ok", "ok")],
                         ids=["get hangs", "get fails", "unparseable"])
def test_a_failing_or_hanging_clock_still_starts_live_view(tmp_path, date, get, set_):
    calls, err, mark = run_feed(tmp_path, date, get, set_)
    assert calls[-1] == "--port usb:001,036 --stdout --capture-movie"
    assert not mark.exists()  # unreadable is unknown, not reset
    if set_ != "ok":
        assert "couldn't set the D5100 clock; starting live view anyway" in err
