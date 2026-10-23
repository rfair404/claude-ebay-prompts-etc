"""tests/test_tg7_wifi_hop.py — the /r camera photos Wi-Fi hop always puts the
laptop back on its home Wi-Fi and leaves a status file saying what happened.

Runs the real scripts against fake nmcli/curl and a fake tg7cam (nothing
touches NetworkManager or a camera). The fake nmcli records every call and
keeps the active connection in a file, the way NetworkManager would."""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOP = ROOT / ".claude" / "skills" / "r" / "tg7_wifi_hop.sh"
PULL = ROOT / ".claude" / "skills" / "r" / "tg7_pull.sh"
HOME_UUID = "11111111-1111-1111-1111-111111111111"
TG7_UUID = "22222222-2222-2222-2222-222222222222"

pytestmark = pytest.mark.skipif(
    os.name == "nt" or not all(shutil.which(c) for c in ("bash", "timeout", "flock", "setsid")),
    reason="needs bash and util-linux")

NMCLI = f"""#!/bin/bash
echo "$*" >>"$FAKE_DIR/nmcli.calls"
case "$*" in
  "-t -f UUID,TYPE,NAME con show")
    echo "{HOME_UUID}:802-11-wireless:HomeWiFi"
    echo "{TG7_UUID}:802-11-wireless:TG-7-P-BJPB37542"
    echo "33333333-3333-3333-3333-333333333333:802-3-ethernet:Wired connection 1" ;;
  "-g 802-11-wireless.ssid con show uuid {HOME_UUID}") echo HomeWiFi ;;
  "-g 802-11-wireless.ssid con show uuid {TG7_UUID}") echo TG-7-P-BJPB37542 ;;
  "-t -f SSID dev wifi list"*) echo HomeWiFi; [ -n "$FAKE_IN_RANGE" ] && echo TG-7-P-BJPB37542 ;;
  "-t -f UUID,TYPE,DEVICE,NAME con show --active")
    a=$(cat "$FAKE_DIR/active"); n=TG-7-P-BJPB37542; [ "$a" = {HOME_UUID} ] && n=HomeWiFi
    echo "$a:802-11-wireless:wlp0s20f3:$n" ;;
  "-t -f DEVICE,TYPE dev") echo wlp0s20f3:wifi ;;
  *"con up uuid {HOME_UUID}"*)
    if [ -s "$FAKE_DIR/home_fails" ]; then sed -i 1d "$FAKE_DIR/home_fails"; exit 4; fi
    echo {HOME_UUID} >"$FAKE_DIR/active" ;;
  *"con up uuid {TG7_UUID}"*) echo {TG7_UUID} >"$FAKE_DIR/active" ;;
  *) exit 2 ;;
esac
"""

# the camera answers only while the laptop is on its network
CURL = f"""#!/bin/bash
[ "$(cat "$FAKE_DIR/active")" = {TG7_UUID} ] && [ -z "$FAKE_CAM_OFF" ]
"""

TG7CAM = """import os, sys, time
to = sys.argv[sys.argv.index("--to") + 1]
mode = os.environ.get("FAKE_SYNC", "ok")
if mode == "hang":
    time.sleep(60)
for name in ("P1010001.JPG", "P1010001.ORF"):
    open(os.path.join(to, name), "w").write("x")
if mode == "fail":
    sys.exit("sync broke halfway")
print(f"2 new photo(s) -> {to}")
"""


def fake_env(tmp_path, *, in_range=True, home_fails=0, sync="ok", limit=60, cam_off=False,
             on_air=False):
    bin_, main, run = tmp_path / "bin", tmp_path / "main", tmp_path / "run"
    for d in (bin_, main / "tools" / "live", main / "inventory" / "_live", run):
        d.mkdir(parents=True)
    for name, body in {"nmcli": NMCLI, "curl": CURL, "gphoto2": "#!/bin/bash\n",
                       "udisksctl": "#!/bin/bash\n"}.items():
        (bin_ / name).write_text(body)
        (bin_ / name).chmod(0o755)
    (main / "tools" / "live" / "tg7cam").write_text(TG7CAM)
    if on_air:
        (main / "inventory" / "_live" / "ON_AIR").touch()
    (tmp_path / "active").write_text(HOME_UUID + "\n")
    (tmp_path / "home_fails").write_text("x\n" * home_fails)
    return {**os.environ, "PATH": f"{bin_}{os.pathsep}{os.environ['PATH']}",
            "FAKE_DIR": str(tmp_path), "FAKE_IN_RANGE": "1" if in_range else "",
            "FAKE_CAM_OFF": "1" if cam_off else "", "FAKE_SYNC": sync,
            "XDG_RUNTIME_DIR": str(run), "TG7_MAIN": str(main), "TG7_PHOTOS": str(tmp_path / "photos"),
            "TG7_HOP_DELAY": "0", "TG7_HOP_LIMIT": str(limit), "USER": "no-such-user-tg7-test"}


def hop(tmp_path, **kw):
    env = fake_env(tmp_path, **kw)
    subprocess.run(["bash", str(HOP)], env=env, stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=60)
    status = (tmp_path / "run" / "tg7_hop.status").read_text().splitlines()
    log = tmp_path / "nmcli.calls"
    calls = log.read_text().splitlines() if log.exists() else []
    return status, [c for c in calls if " con up " in c or " con down " in c or c.startswith("con ")]


def ups(calls):
    return [c.split("uuid ")[1].split()[0] for c in calls if "con up" in c]


def test_a_good_hop_syncs_then_goes_back_home(tmp_path):
    status, calls = hop(tmp_path)
    assert status[0] == "ok"
    assert status[1].startswith("new: 2 photo(s) -> ")
    assert status[2] == "network: back on HomeWiFi"
    assert ups(calls) == [TG7_UUID, HOME_UUID]
    assert all("ifname wlp0s20f3" in c for c in calls if "con up" in c)  # one adapter, no second link
    assert (tmp_path / "active").read_text().strip() == HOME_UUID


def test_a_failed_sync_still_reconnects_home_and_says_failed(tmp_path):
    status, calls = hop(tmp_path, sync="fail")
    assert status[0].startswith("failed: tg7cam sync exited 1")
    assert status[1].startswith("new: 2 photo(s)")  # what landed before it broke is counted
    assert status[2] == "network: back on HomeWiFi"
    assert ups(calls) == [TG7_UUID, HOME_UUID]
    assert (tmp_path / "active").read_text().strip() == HOME_UUID


def test_a_silent_camera_still_reconnects_home(tmp_path):
    # 20 probes 2 s apart would take 40 s; the time limit cuts it short instead
    status, calls = hop(tmp_path, limit=3, cam_off=True)
    assert status[0].startswith("failed: stopped by a signal")
    assert status[2] == "network: back on HomeWiFi"
    assert ups(calls) == [TG7_UUID, HOME_UUID]


def test_the_time_limit_stops_a_hung_sync_and_reconnects_home(tmp_path):
    t0 = time.monotonic()
    status, calls = hop(tmp_path, sync="hang", limit=2)
    assert time.monotonic() - t0 < 20
    assert status[0] == "failed: stopped by a signal (the 2s time limit, or killed)"
    assert status[2] == "network: back on HomeWiFi"
    assert ups(calls) == [TG7_UUID, HOME_UUID]


def test_the_home_reconnect_is_retried(tmp_path):
    status, calls = hop(tmp_path, home_fails=1)
    assert status[0] == "ok" and status[2] == "network: back on HomeWiFi"
    assert ups(calls) == [TG7_UUID, HOME_UUID, HOME_UUID]


def test_on_air_never_leaves_the_network(tmp_path):
    status, calls = hop(tmp_path, on_air=True)
    assert status[0].startswith("failed: on air")
    assert status[2] == "network: unchanged (never left it)"
    assert calls == []


def test_a_camera_out_of_range_never_leaves_the_network(tmp_path):
    status, calls = hop(tmp_path, in_range=False)
    assert status[0].startswith("failed: the TG-7's Wi-Fi (TG-7-P-BJPB37542) is not in range")
    assert calls == []


def test_pull_starts_the_hop_detached_and_wait_prints_its_status(tmp_path):
    env = fake_env(tmp_path)
    status = tmp_path / "run" / "tg7_hop.status"
    status.write_text("ok\nstale from the last hop\n")
    out = subprocess.run(["bash", str(PULL)], env=env, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0
    assert out.stdout.strip().splitlines()[-1] == f"HOP started; status at {status}"
    waited = subprocess.run(["bash", str(PULL), "--wait"], env=env, capture_output=True,
                            text=True, timeout=60)
    assert waited.stdout.splitlines()[:3] == [
        "ok", f"new: 2 photo(s) -> {tmp_path / 'photos'}", "network: back on HomeWiFi"]


def test_pull_without_the_camera_in_range_does_not_hop(tmp_path):
    env = fake_env(tmp_path, in_range=False)
    out = subprocess.run(["bash", str(PULL)], env=env, stdin=subprocess.DEVNULL,
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 2 and "NOT FOUND" in out.stdout
    assert not (tmp_path / "run" / "tg7_hop.status").exists()
