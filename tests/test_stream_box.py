"""tests/test_stream_box.py — tools/live/stream_box/ (provision.py on the
laptop, box_apply.py on the box): GPU detection from canned lspci output,
the encoder choice, the OBS config it writes, the box's setup steps, the
dry run, and the rule that the laptop gets exactly one file written. No
network and no SSH: a fake SSH plays the box."""
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools" / "live" / "stream_box"))
sys.path.insert(0, str(ROOT / "tools" / "live"))

import box_apply  # noqa: E402
import obsctl  # noqa: E402
import provision  # noqa: E402
import show_flow  # noqa: E402

TEMPLATES = ROOT / "tools" / "live" / "obs"

LSPCI_NVIDIA_ON_INTEL = """\
00:00.0 Host bridge [0600]: Intel Corporation 8th Gen Core Processor Host Bridge/DRAM Registers [8086:3ec2] (rev 07)
00:02.0 VGA compatible controller [0300]: Intel Corporation CoffeeLake-S GT2 [UHD Graphics 630] [8086:3e92]
01:00.0 VGA compatible controller [0300]: NVIDIA Corporation GP107 [GeForce GTX 1050 Ti] [10de:1c82] (rev a1)
01:00.1 Audio device [0403]: NVIDIA Corporation GP107GL High Definition Audio Controller [10de:0fb9] (rev a1)
"""
LSPCI_AMD = """\
00:01.0 PCI bridge [0604]: Advanced Micro Devices, Inc. [AMD] Starship/Matisse GPP Bridge [1022:1483]
0a:00.0 VGA compatible controller [0300]: Advanced Micro Devices, Inc. [AMD/ATI] Ellesmere [Radeon RX 470/480/570/570X/580/580X/590] [1002:67df] (rev e7)
"""
LSPCI_INTEL = "00:02.0 VGA compatible controller [0300]: Intel Corporation HD Graphics 530 [8086:1912] (rev 06)\n"
LSPCI_BMC_ONLY = "03:00.0 VGA compatible controller [0300]: ASPEED Technology, Inc. ASPEED Graphics Family [1a03:2000] (rev 41)\n"
LSPCI_3D = "0000:02:00.0 3D controller [0302]: NVIDIA Corporation TU117M [10de:1f9d] (rev a1)\n"

FACTS_OUT = """\
##os
PRETTY_NAME="Ubuntu 24.04.3 LTS"
ID=ubuntu
ID_LIKE=debian
##lspci
{lspci}
##pci
0000:00:02.0 0x030000 0x8086
##drm
renderD128 0000:00:02.0
renderD129 0000:01:00.0
##route
default via 192.168.1.1 dev enp3s0 proto dhcp src 192.168.1.50 metric 100
##addr
1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
2: enp3s0    inet 192.168.1.50/24 metric 100 brd 192.168.1.255 scope global dynamic enp3s0\\       valid_lft 86000sec
##client
{client}
##wifi
##obs
{obs}
##boot
{boot}
"""


def facts_text(lspci=LSPCI_NVIDIA_ON_INTEL, client="192.168.1.137", obs="stopped", boot="boot-1"):
    return FACTS_OUT.format(lspci=lspci.strip(), client=client, obs=obs, boot=boot)


# --- GPU detection ------------------------------------------------------------------
def test_discrete_nvidia_wins_over_the_boards_intel_graphics():
    g = provision.detect_gpu(LSPCI_NVIDIA_ON_INTEL)
    assert (g.vendor, g.slot) == ("nvidia", "0000:01:00.0")
    assert g.name == "NVIDIA Corporation GP107 [GeForce GTX 1050 Ti]"  # brackets in the name survive


def test_amd_and_intel_and_3d_controllers_are_found():
    assert provision.detect_gpu(LSPCI_AMD).vendor == "amd"
    assert provision.detect_gpu(LSPCI_INTEL).vendor == "intel"
    g = provision.detect_gpu(LSPCI_3D)
    assert (g.vendor, g.slot) == ("nvidia", "0000:02:00.0")


def test_a_server_bmc_chip_is_not_a_gpu_to_encode_on():
    assert provision.detect_gpu(LSPCI_BMC_ONLY) is None
    assert provision.detect_gpu("") is None


def test_sysfs_fallback_when_lspci_is_missing():
    g = provision.detect_gpu_sysfs("0000:00:02.0 0x030000 0x8086\n0000:01:00.0 0x030000 0x10de\n"
                                   "0000:00:1f.3 0x040300 0x8086\n")
    assert (g.vendor, g.slot) == ("nvidia", "0000:01:00.0")


def test_render_node_is_the_chosen_gpus_own():
    drm = "renderD128 0000:00:02.0\nrenderD129 0000:0a:00.0\n"
    assert provision.render_node_for("0000:0a:00.0", drm) == "/dev/dri/renderD129"
    assert provision.render_node_for("0000:05:00.0", drm) is None


# --- the LAN and the firewall's lockout check ---------------------------------------------
ROUTE = "default via 192.168.1.1 dev enp3s0 proto dhcp src 192.168.1.50 metric 100"
ADDR = "2: enp3s0    inet 192.168.1.50/24 brd 192.168.1.255 scope global dynamic enp3s0"


def test_lan_is_the_network_on_the_default_route():
    assert provision.lan_cidr(ROUTE, ADDR) == "192.168.1.0/24"


@pytest.mark.parametrize("addr", ["2: enp3s0    inet 8.8.8.50/24 scope global enp3s0",
                                  "2: enp3s0    inet 10.1.2.3/8 scope global enp3s0"])
def test_lan_refuses_public_or_too_wide_networks(addr):
    with pytest.raises(provision.ProvisionError):
        provision.lan_cidr(ROUTE, addr)


def test_lan_needs_a_default_route():
    with pytest.raises(provision.ProvisionError, match="default route"):
        provision.lan_cidr("", ADDR)


def test_plan_refuses_a_lan_that_would_lock_the_laptop_out():
    facts = provision.parse_facts(facts_text(client="10.0.0.5"))
    with pytest.raises(provision.ProvisionError, match="lock it out"):
        provision.make_plan(facts, "russ")


def test_plan_from_the_boxs_facts():
    plan = provision.make_plan(provision.parse_facts(facts_text()), "russ")
    assert plan["vendor"] == "nvidia" and plan["lan"] == "192.168.1.0/24"
    assert plan["client"] == "192.168.1.137" and plan["os"] == "Ubuntu 24.04.3 LTS"
    assert plan["render_node"] == "/dev/dri/renderD129" and plan["wifi"] == []


def test_plan_refuses_an_odd_user_name():
    with pytest.raises(provision.ProvisionError):
        provision.make_plan({}, "russ; rm -rf /", lan="192.168.1.0/24", gpu="none")


# --- encoder choice ----------------------------------------------------------------------
def test_nvidia_with_nvenc_puts_both_destinations_on_the_gpu():
    enc = box_apply.choose_encoders("nvidia", {"nvenc": True})
    assert enc["ebay_encoder"] == "obs_nvenc_h264_tex"
    assert enc["ebay_settings"]["bf"] == 0  # WHIP can't carry B-frames
    assert enc["ebay_settings"]["bitrate"] == 3000 and enc["ebay_settings"]["keyint_sec"] == 1
    assert enc["twitch"]["FFVEncoder"] == "h264_nvenc"


def test_vaapi_puts_ebay_on_the_gpu_and_twitch_on_x264():
    enc = box_apply.choose_encoders("amd", {"vaapi": True}, "/dev/dri/renderD129")
    assert enc["ebay_encoder"] == "ffmpeg_vaapi_tex"
    assert enc["ebay_settings"]["vaapi_device"] == "/dev/dri/renderD129"
    assert enc["twitch"]["FFVEncoder"] == "libx264"  # Custom Output can't use VAAPI
    assert enc["twitch"]["FFRescaleRes"] == "720x1280"


@pytest.mark.parametrize("vendor,probe", [("nvidia", {"nvenc": False}), ("intel", {"vaapi": False}),
                                          ("none", {}), ("nvidia", {"vaapi": True})])
def test_no_proven_gpu_encoder_falls_back_to_x264(vendor, probe):
    enc = box_apply.choose_encoders(vendor, probe)
    assert enc["ebay_encoder"] == "obs_x264" and enc["twitch"]["FFVEncoder"] == "libx264"
    assert "bframes=0" in enc["ebay_settings"]["x264opts"]


def test_twitch_output_stays_in_step_with_show_flow():
    shared = ["RecType", "FFOutputToFile", "FFFormat", "FFFormatMimeType", "FFExtension",
              "FFAEncoder", "FFAEncoderId", "FFVEncoderId", "FFVBitrate", "FFVGOPSize",
              "FFABitrate", "FFAudioMixes"]
    for twitch in (box_apply.TWITCH_NVENC, box_apply.TWITCH_X264):
        assert {k: twitch[k] for k in shared} == {k: show_flow.TWITCH_OUT[k] for k in shared}
        assert "FFURL" not in twitch  # the Twitch key is never in a template


# --- OBS config files --------------------------------------------------------------------
def test_merge_ini_sets_keys_and_keeps_everything_else():
    text = "﻿[General]\nName=Ebay\n\n[AdvOut]\nEncoder=obs_x264\nFFURL=rtmp://secret\n\n[Video]\nBaseCX=1080\n"
    out = box_apply.merge_ini(text, {"AdvOut": {"Encoder": "obs_nvenc_h264_tex", "RecType": "FFmpeg"},
                                     "New": {"A": "1"}})
    assert out.startswith("﻿[General]\nName=Ebay\n")
    ini = box_apply.parse_ini(out)
    assert ini["AdvOut"] == {"Encoder": "obs_nvenc_h264_tex", "FFURL": "rtmp://secret", "RecType": "FFmpeg"}
    assert ini["Video"] == {"BaseCX": "1080"} and ini["New"] == {"A": "1"}
    assert box_apply.merge_ini(out, {"AdvOut": {"RecType": "FFmpeg"}}) == out  # idempotent


def test_profile_keeps_the_boxs_stream_key_and_sets_the_encoder():
    template = (TEMPLATES / "profiles/Ebay/basic.ini").read_text()
    existing = "[AdvOut]\nFFURL=rtmp://live.twitch.tv/app/KEY\nEncoder=obs_x264\n[Video]\nFPSCommon=60\n"
    enc = box_apply.choose_encoders("nvidia", {"nvenc": True})
    ini = box_apply.parse_ini(box_apply.profile_text(template, existing, enc, "/home/russ"))
    assert ini["AdvOut"]["FFURL"] == "rtmp://live.twitch.tv/app/KEY"
    assert ini["AdvOut"]["Encoder"] == "obs_nvenc_h264_tex"
    assert ini["AdvOut"]["FFVEncoder"] == "h264_nvenc" and ini["AdvOut"]["RecType"] == "FFmpeg"
    assert ini["Video"]["FPSCommon"] == "60"            # the box's own setting wins
    assert ini["Video"]["BaseCX"] == "1080"             # template keys fill the gaps
    assert ini["AdvOut"]["RecFilePath"] == "/home/russ"  # ~ expanded like the laptop's sed


def test_stream_encoder_json_layers_the_gpu_keys_on_the_template():
    template = json.loads((TEMPLATES / "profiles/Ebay/streamEncoder.json").read_text())
    out = box_apply.stream_encoder_json(template, box_apply.choose_encoders("nvidia", {"nvenc": True}))
    assert out["bf"] == 0 and out["preset2"] == "p5" and out["rate_control"] == "CBR"


def test_websocket_password_is_made_once_and_then_kept():
    cfg, made = box_apply.websocket_config(None, 4455, make_password=lambda: "generated")
    assert made and cfg["server_password"] == "generated"
    assert cfg["server_enabled"] and cfg["auth_required"] and not cfg["first_load"]
    again, made2 = box_apply.websocket_config({**cfg, "server_enabled": False}, 4455,
                                              make_password=lambda: "other")
    assert not made2 and again["server_password"] == "generated" and again["server_enabled"]


def test_autostart_block_is_replaced_not_stacked():
    first = box_apply.autostart_text("setxkbmap us &\n", "/home/russ/.cache/pops-stream-box")
    assert first.startswith("setxkbmap us &\n") and first.count(box_apply.MARK_BEGIN) == 1
    assert box_apply.autostart_text(first, "/home/russ/.cache/pops-stream-box") == first
    assert '[ -e "/home/russ/.cache/pops-stream-box/ready" ] && flatpak run' in first


def _extract_bundle(tmp_path, plan):
    data = provision.bundle(plan, "#!/bin/bash\n")
    out = tmp_path / "bundle"
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        if hasattr(tarfile, "data_filter"):
            tar.extractall(out, filter="data")
        else:
            tar.extractall(out)
    return out


def test_apply_writes_the_boxs_obs_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plan = provision.make_plan({}, "russ", 4455, "192.168.1.0/24", "nvidia")
    b = _extract_bundle(tmp_path, plan)
    (b / "probe.json").write_text('{"nvenc": true, "vaapi": false}')
    r = box_apply.apply(b, home)
    obs = box_apply.obs_dir(home)
    prof = obs / "basic/profiles/Ebay"
    assert box_apply.parse_ini((prof / "basic.ini").read_text())["AdvOut"]["Encoder"] == "obs_nvenc_h264_tex"
    assert json.loads((prof / "streamEncoder.json").read_text())["bf"] == 0
    assert (obs / "basic/scenes/eBay_Live.json").read_bytes() == (TEMPLATES / "scenes/eBay_Live.json").read_bytes()
    ws_file = obs / "plugin_config/obs-websocket/config.json"
    ws = json.loads(ws_file.read_text())
    assert ws["server_password"] and ws_file.stat().st_mode & 0o777 == 0o600
    assert box_apply.parse_ini((obs / "user.ini").read_text())["General"]["FirstRun"] == "true"
    result = (home / box_apply.STATE_DIR / "result.json").read_text()
    assert ws["server_password"] not in result and r["websocket_password"] == "generated"

    # a re-run keeps the password and the box's scene collection
    (obs / "basic/scenes/eBay_Live.json").write_text('{"name": "eBay Live", "edited": true}')
    r2 = box_apply.apply(b, home)
    assert json.loads(ws_file.read_text())["server_password"] == ws["server_password"]
    assert "edited" in (obs / "basic/scenes/eBay_Live.json").read_text()
    assert r2["websocket_password"] == "kept" and r2["scene_collection"].startswith("kept")


def test_keys_land_in_the_profile_and_survive_apply(tmp_path):
    home = tmp_path / "home"
    service = {"type": "whip_custom", "settings": {"server": "https://whip.example/SECRET", "bearer_token": ""}}
    assert box_apply.keys({"service": service, "ffurl": "rtmp://twitch/KEY"}, home) == \
        ["eBay WHIP service", "Twitch URL"]
    prof = box_apply.obs_dir(home) / "basic/profiles/Ebay"
    assert (prof / "service.json").stat().st_mode & 0o777 == 0o600
    plan = provision.make_plan({}, "russ", 4455, "192.168.1.0/24", "amd")
    box_apply.apply(_extract_bundle(tmp_path, plan), home)
    ini = box_apply.parse_ini((prof / "basic.ini").read_text())
    assert ini["AdvOut"]["FFURL"] == "rtmp://twitch/KEY" and ini["AdvOut"]["Encoder"] == "obs_x264"


# --- the box's setup steps -----------------------------------------------------------------
def _script(vendor, **kw):
    plan = provision.make_plan({}, "russ", 4455, "192.168.1.0/24", vendor)
    return provision.root_script(plan, provision.root_steps(plan, **kw))


def test_steps_follow_the_gpu():
    nv, intel, none = _script("nvidia"), _script("intel"), _script("none")
    assert "ubuntu-drivers install" in nv and "h264_nvenc" in nv and "h264_vaapi" not in nv
    assert "VAAPI.Intel" in intel and "-vaapi_device /dev/dri/renderD128" in intel
    assert "ubuntu-drivers" not in intel
    assert "h264_nvenc" not in none and "h264_vaapi" not in none


def test_firewall_opens_ssh_and_websocket_to_the_lan_only():
    s = _script("amd")
    assert "ufw allow from 192.168.1.0/24 to any port 22 proto tcp" in s
    assert "ufw allow from 192.168.1.0/24 to any port 4455 proto tcp" in s
    assert "ufw default deny incoming" in s
    assert "allow 4455" not in s and "from any" not in s


def test_steps_have_no_placeholders_left_and_honour_flags():
    s = _script("nvidia", reboot_ok=False, reset_scenes=True)
    assert not re.search(r"@[A-Z_0-9]+@", s)
    assert "[ 0 = 1 ]" in s and "--reset-scenes" in s
    assert "/dev/video10" in s and "video_nr=10,11" in s


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
@pytest.mark.parametrize("vendor", ["nvidia", "intel", "amd", "none"])
def test_generated_script_is_valid_bash(vendor):
    r = subprocess.run(["bash", "-n"], input=_script(vendor), text=True, capture_output=True)
    assert r.returncode == 0, r.stderr


# --- the laptop: dry run, one write, nothing else touched --------------------------------------
class FakeBox:
    """Plays the box over SSH. Records every command and stdin."""

    def __init__(self, root_codes=(0,), obs="stopped", client="192.168.1.137"):
        self.calls = []
        self.root_codes = list(root_codes)
        self.boot = 1
        self.obs = obs
        self.client = client

    def run(self, command, input=None, tty=False):
        self.calls.append((command, input, tty))
        out = b""
        rc = 0
        if command == "bash -s":
            out = facts_text(client=self.client, obs=self.obs, boot=f"boot-{self.boot}").encode()
        elif "root.sh" in command:
            rc = self.root_codes.pop(0)
            if rc == provision.EXIT_REBOOT:
                self.boot += 1
        elif "boot_id" in command:
            out = f"boot-{self.boot}\n".encode()
        elif "obs-websocket/config.json" in command:
            out = json.dumps({"server_password": "s3cret-box-pw", "server_port": 4455,
                              "auth_required": True, "server_enabled": True, "first_load": False}).encode()
        elif "result.json" in command:
            out = json.dumps({"ebay_encoder": "obs_nvenc_h264_tex", "twitch_encoder": "h264_nvenc",
                              "notes": ["both on the GPU"]}).encode()
        elif "box_apply.py keys" in command:
            out = b"copied: eBay WHIP service, Twitch URL"
        return subprocess.CompletedProcess(command, rc, out, b"")


def _tree(path: Path) -> dict:
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob("*")) if p.is_file()}


@pytest.fixture
def laptop(tmp_path, monkeypatch):
    """A laptop home with its own OBS config (the fallback) and stream keys."""
    home = tmp_path / "laptop"
    obs = home / ".var/app/com.obsproject.Studio/config/obs-studio"
    prof = obs / "basic/profiles/Ebay"
    prof.mkdir(parents=True)
    (prof / "service.json").write_text(json.dumps(
        {"type": "whip_custom", "settings": {"server": "https://whip.example/LAPTOP-SECRET"}}))
    (prof / "basic.ini").write_text("[AdvOut]\nFFURL=rtmp://live.twitch.tv/app/LAPTOP-KEY\n")
    (obs / "plugin_config/obs-websocket").mkdir(parents=True)
    (obs / "plugin_config/obs-websocket/config.json").write_text('{"server_password": "laptop-pw"}')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OBS_CONFIG_DIR", str(obs))
    monkeypatch.setattr(provision, "check_obs", lambda *a, **k: "32.2.2")
    return home


def _args(*extra):
    return provision.parser().parse_args(["192.168.1.50", *extra])


def test_offline_dry_run_touches_nothing(laptop, monkeypatch, capsys):
    before, templates = _tree(laptop), _tree(TEMPLATES)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail(f"ran {a}"))
    assert provision.main(["--dry-run", "--gpu", "intel", "--lan", "192.168.1.0/24"]) == 0
    out = capsys.readouterr().out
    assert "nothing changed" in out and "ffmpeg_vaapi_tex" in out and "ufw default deny incoming" in out
    assert _tree(laptop) == before and _tree(TEMPLATES) == templates


def test_dry_run_reads_the_box_and_changes_nothing(laptop, capsys):
    box = FakeBox()
    assert provision.provision(_args("--dry-run", "--copy-keys"), box, sleep=lambda s: None) == 0
    assert [c[0] for c in box.calls] == ["bash -s"]  # the read-only facts, nothing else
    out = capsys.readouterr().out
    assert "GeForce GTX 1050 Ti" in out and "LAPTOP-SECRET" not in out


def test_a_full_run_writes_only_the_password_file_on_the_laptop(laptop, capsys):
    before, templates = _tree(laptop), _tree(TEMPLATES)
    box = FakeBox()
    assert provision.provision(_args("--copy-keys"), box, sleep=lambda s: None) == 0
    after = _tree(laptop)
    added = set(after) - set(before)
    assert added == {".config/pops-stream-box/obs-config/plugin_config/obs-websocket/config.json"}
    assert {k: after[k] for k in before} == before  # the laptop's OBS config (the fallback) is untouched
    assert _tree(TEMPLATES) == templates
    pw = laptop / added.pop()
    assert pw.stat().st_mode & 0o777 == 0o600
    assert json.loads(pw.read_text())["server_password"] == "s3cret-box-pw"
    out = capsys.readouterr()
    assert "s3cret-box-pw" not in out.out + out.err
    # secrets travel on stdin, never on a command line
    commands = " ".join(c[0] for c in box.calls)
    assert "LAPTOP-SECRET" not in commands and "LAPTOP-KEY" not in commands
    keys_call = next(c for c in box.calls if "box_apply.py keys" in c[0])
    assert b"LAPTOP-KEY" in keys_call[1]
    root = next(c for c in box.calls if "root.sh" in c[0])
    assert root[2] is True  # sudo gets a terminal for the box password


def test_a_reboot_pass_waits_for_a_new_boot_and_finishes(laptop, capsys):
    box = FakeBox(root_codes=(provision.EXIT_REBOOT, 0))
    assert provision.provision(_args(), box, sleep=lambda s: None) == 0
    assert sum("root.sh" in c[0] for c in box.calls) == 2
    assert "box is back" in capsys.readouterr().out


def test_no_reboot_flag_stops_with_instructions(laptop):
    box = FakeBox(root_codes=(provision.EXIT_REBOOT,))
    with pytest.raises(provision.ProvisionError, match="reboot it"):
        provision.provision(_args("--no-reboot"), box, sleep=lambda s: None)


def test_refuses_while_obs_runs_on_the_box(laptop):
    box = FakeBox(obs="running")
    with pytest.raises(provision.ProvisionError, match="--stop-obs"):
        provision.provision(_args(), box, sleep=lambda s: None)
    assert [c[0] for c in box.calls] == ["bash -s"]


def test_stop_obs_refuses_when_it_cant_tell_whether_obs_is_live(laptop):
    box = FakeBox(obs="running")
    with pytest.raises(provision.ProvisionError, match="live"):
        provision.provision(_args("--stop-obs"), box, sleep=lambda s: None)
    assert not any("pkill" in c[0] for c in box.calls)


def test_laptop_outside_the_lan_stops_before_anything_is_sent(laptop):
    box = FakeBox(client="10.9.9.9")
    with pytest.raises(provision.ProvisionError, match="lock it out"):
        provision.provision(_args(), box, sleep=lambda s: None)
    assert [c[0] for c in box.calls] == ["bash -s"]


def test_password_file_may_not_go_in_the_repo_or_the_laptops_obs(laptop):
    with pytest.raises(provision.ProvisionError):
        provision.check_local_target(ROOT / "box.json")
    with pytest.raises(provision.ProvisionError):
        provision.check_local_target(provision.laptop_obs_dir() / "plugin_config/obs-websocket/config.json")
    assert provision.check_local_target(laptop / ".config/x.json")


def test_laptop_keys_are_read_without_writing(laptop):
    before = _tree(laptop)
    keys = provision.laptop_keys(provision.laptop_obs_dir())
    assert keys["ffurl"].endswith("LAPTOP-KEY") and keys["service"]["type"] == "whip_custom"
    assert _tree(laptop) == before


def test_find_only_asks_linux_ssh_hosts():
    banners = {"192.168.1.20": "SSH-2.0-OpenSSH_9.6p1 Ubuntu-3ubuntu13.5", "192.168.1.3": None,
               "192.168.1.9": "SSH-2.0-dropbear_2022.83", "192.168.1.100": "SSH-2.0-OpenSSH_9.2p1 Debian-2"}
    assert provision.linux_candidates(banners) == ["192.168.1.20", "192.168.1.100"]


# --- the box is optional: with none configured, the show stays on the laptop ----------------


def test_with_no_box_configured_show_flow_talks_to_the_laptops_obs(monkeypatch):
    monkeypatch.delenv("OBS_WS_HOST", raising=False)
    monkeypatch.delenv("OBS_WS_PORT", raising=False)
    seen = []

    class FakeClient:
        def __init__(self, host, port, password=None, timeout=10.0):
            seen.append((host, port))

    monkeypatch.setattr(show_flow.obsctl, "ObsClient", FakeClient)
    show_flow.connect()
    assert seen == [("127.0.0.1", 4455)]


def test_with_no_box_configured_obsctl_talks_to_the_laptops_obs(monkeypatch):
    monkeypatch.delenv("OBS_WS_HOST", raising=False)
    monkeypatch.delenv("OBS_WS_PORT", raising=False)
    seen = []

    def fake_connect(host, port, timeout=10.0):
        seen.append((host, port))
        raise obsctl.ObsError("no OBS in tests")

    monkeypatch.setattr(obsctl, "_connect", fake_connect)
    assert obsctl.main(["status"]) == 1
    assert seen == [("127.0.0.1", 4455)]
