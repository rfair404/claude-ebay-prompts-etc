"""tests/test_green_setup.py: tools/green_setup.py, green as the shipping
station. The desktop choice and RAM budget from canned facts, the firewall
that opens only to green's /24, the QL-1100 queue and driver steps, the
laptop queue switch, the on-air refusal and the dry run. No network and no
SSH: a fake SSH plays green and a fake `run` plays the laptop's CUPS."""
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import green_setup as gs  # noqa: E402

OS_DEBIAN = 'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nID=debian\n'
OS_UBUNTU = 'PRETTY_NAME="Ubuntu 24.04 LTS"\nID=ubuntu\nID_LIKE=debian\n'


def facts(**kw):
    f = {"os": OS_DEBIAN, "user": "russ", "arch": "amd64", "pkgs": "cups 2.4 ii \nperl 5 ii \n",
         "mem": "7900", "swap": "2048", "cpu": "4 x Intel", "gpu": "VGA compatible controller: Intel HD 530",
         "dm": "", "dm_active": "inactive", "sessions": "", "firefox": "", "usb": "", "queue": "",
         "listen": "0.0.0.0:22 [::]:22 127.0.0.1:631", "ufw": "", "tailscale": "",
         "route": "default via 10.0.5.1 dev eno1 proto dhcp",
         "addr": "1: lo    inet 127.0.0.1/8 scope host lo\n2: eno1    inet 10.0.5.20/24 brd 10.0.5.255 scope global eno1",
         "client": "10.0.5.30"}
    f.update(kw)
    return f


def script_for(**kw):
    plan = gs.make_plan(facts(**{k: v for k, v in kw.items() if k not in ("autologin", "close_others")}),
                        kw.get("autologin"), close_others=kw.get("close_others", False))
    return plan, gs.root_script(plan, gs.root_steps(plan))


def test_no_desktop_gets_xfce_and_firefox_esr_with_a_reason_from_the_facts():
    plan, script = script_for()
    assert plan["desktop"]["name"] == "XFCE"
    assert "7900 MB RAM" in plan["desktop"]["why"] and "Intel HD 530" in plan["desktop"]["why"]
    assert "xfce4 xfce4-terminal lightdm xorg firefox-esr" in script


def test_small_ram_gets_lxqt_and_ubuntu_gets_firefox():
    plan, _ = script_for(mem="1900", os=OS_UBUNTU)
    assert plan["desktop"]["name"] == "LXQt" and plan["firefox"] == "firefox"


def test_an_existing_desktop_is_kept():
    plan, script = script_for(pkgs="gnome-shell 46 ii \nlightdm 1 ii \n", dm="gdm3", firefox="firefox-esr")
    assert plan["desktop"]["install"] == [] and plan["desktop"]["name"] == "GNOME"
    assert "xfce4" not in script


def test_ram_budget_says_whether_a_test_run_fits_beside_firefox():
    assert script_for(mem="7900")[0]["verdict"] == "fits"
    assert "does not fit" in script_for(mem="2500")[0]["verdict"]


def test_firewall_opens_ssh_and_cups_to_the_lan_only():
    plan, script = script_for()
    assert plan["lan"] == "10.0.5.0/24"
    assert "ufw allow from 10.0.5.0/24 to any port 631 proto tcp" in script
    assert "ufw allow from 10.0.5.0/24 to any port 22 proto tcp" in script
    assert "ufw --force enable" in script
    assert "--share-printers --no-remote-any --no-remote-admin" in script
    for wide in ("ufw allow 631", "--remote-any ", "0.0.0.0/0", "Anywhere"):
        assert wide not in script


def test_ufw_is_not_switched_on_over_other_listeners_unless_asked():
    _, script = script_for(listen="0.0.0.0:22 0.0.0.0:8080 127.0.0.1:631")
    assert "ufw --force enable" not in script and "8080" in script and "FIREWALL_OFF=1" in script
    _, script = script_for(listen="0.0.0.0:22 0.0.0.0:8080", close_others=True)
    assert "ufw --force enable" in script


def test_tailscale_ssh_is_kept_when_green_has_it():
    assert "on tailscale0 to any port 22" in script_for(tailscale="tailscale0")[1]
    assert "tailscale0" not in script_for()[1]


def test_the_laptop_outside_greens_lan_is_refused():
    with pytest.raises(gs.ProvisionError, match="lock it out"):
        gs.make_plan(facts(client="10.9.9.9"))


def test_a_public_lan_is_refused():
    with pytest.raises(gs.ProvisionError, match="private LAN"):
        gs.make_plan(facts(), lan="8.8.8.0/24")


def test_unsupported_os_or_arch_is_refused():
    with pytest.raises(gs.ProvisionError, match="Debian"):
        gs.make_plan(facts(os='PRETTY_NAME="Fedora"\nID=fedora\n'))
    with pytest.raises(gs.ProvisionError, match="i386"):
        gs.make_plan(facts(arch="arm64"))


def test_queue_step_uses_the_brother_ppd_the_label_size_and_sharing():
    _, script = script_for()
    assert f"lpadmin -p QL-1100 -E -v \"$URI\" -P {gs.PPD} -o PageSize=103x164 -o printer-is-shared=true" in script
    assert "NOPRINTER=1" in script and gs.DRIVER_SHA256 in script and "sha256sum -c" in script


def test_autologin_only_when_asked_and_only_on_lightdm():
    assert "autologin" not in script_for()[1]
    assert "autologin-user=%s" in script_for(autologin="yes")[1]
    assert "rm -f /etc/lightdm/lightdm.conf.d/50-pops-autologin.conf" in script_for(autologin="no")[1]
    with pytest.raises(gs.ProvisionError, match="LightDM"):
        gs.make_plan(facts(pkgs="gnome-shell 46 ii \n", dm="gdm3"), autologin="yes")


@pytest.mark.skipif(sys.platform == "win32", reason="green and the laptop are Linux")
@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_generated_scripts_are_valid_bash():
    plan, script = script_for(autologin="yes", tailscale="tailscale0")
    for s in (script, gs.user_script(plan), gs.LAPTOP["green"], gs.LAPTOP["usb"]):
        assert subprocess.run(["bash", "-n"], input=s, text=True).returncode == 0


def test_ship_launcher_opens_the_local_ship_page():
    plan, _ = script_for()
    assert "Exec=firefox-esr http://127.0.0.1:8770/ship/box" in gs.user_script(plan)


def test_laptop_green_queue_is_raw_forwarding_and_keeps_its_name():
    s = gs.LAPTOP["green"]
    assert "lpadmin -p QL-1100 -E -v ipp://green.lan:631/printers/QL-1100 -m raw" in s
    assert "printer-error-policy=retry-job" in s and "printer-is-shared=false" in s


class FakeRun:
    def __init__(self, uri, model, green_ok=True, on_laptop_usb=False, log=None):
        self.uri, self.model, self.green_ok, self.usb = uri, model, green_ok, on_laptop_usb
        self.calls = log if log is not None else []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out, rc = "", 0
        if cmd[:2] == ["lpstat", "-v"]:
            out = f"device for QL-1100: {self.uri}\n"
        elif cmd[0] == "lpoptions":
            out = f"printer-make-and-model='{self.model}' printer-state=3"
        elif cmd[:2] == ["lpstat", "-h"]:
            out, rc = ("printer QL-1100 is idle.\n", 0) if self.green_ok else ("", 1)
        elif cmd[0] == "lsusb":
            out = "Bus 001 Device 009: ID 04f9:20a7 Brother Industries, Ltd QL-1100\n" if self.usb else ""
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")


def sudo_calls(calls):
    return [c for c in calls if isinstance(c, list) and c[0] == "sudo"]


def test_laptop_switch_is_skipped_when_already_done():
    run = FakeRun(gs.GREEN_URI, "Local Raw Printer")
    assert gs.point_laptop("green", say=lambda *_: None, run=run) == 0
    assert not sudo_calls(run.calls)


def test_laptop_switch_is_refused_while_the_printer_is_still_on_the_laptops_usb():
    run = FakeRun("usb://Brother/QL-1100?serial=X", "Brother QL-1100 CUPS", on_laptop_usb=True)
    with pytest.raises(gs.ProvisionError, match="still on this laptop's USB"):
        gs.point_laptop("green", say=lambda *_: None, run=run)
    assert not sudo_calls(run.calls)


def test_laptop_switch_does_not_wait_for_green_and_runs_one_sudo():
    run = FakeRun("usb://Brother/QL-1100?serial=X", "Brother QL-1100 CUPS", green_ok=False)
    gs.point_laptop("green", say=lambda *_: None, run=run)
    assert [c[:3] for c in sudo_calls(run.calls)] == [["sudo", "bash", "-c"]]


class FakeSsh:
    def __init__(self, f, log=None, root_rc=0):
        self.text = "".join(f"##{k}\n{v}\n" for k, v in f.items())
        self.calls = log if log is not None else []
        self.root_rc = root_rc

    def run(self, command, input=None, tty=False):
        self.calls.append(command)
        rc = self.root_rc if command.startswith("sudo ") else 0
        return SimpleNamespace(returncode=rc, stdout=self.text.encode(), stderr=b"")


def args(**kw):
    a = dict(dry_run=True, laptop="green", autologin=None, lan=None, close_others=False)
    a.update(kw)
    return SimpleNamespace(**a)


def test_dry_run_reads_green_once_and_changes_nothing():
    ssh, out = FakeSsh(facts()), []
    run = FakeRun("", "")
    assert gs.setup(args(), ssh, say=out.append, run=run) == 0
    assert ssh.calls == ["bash -s"] and run.calls == []
    text = "\n".join(out)
    assert "RAM budget:" in text and "dry run: nothing changed" in text and "ipp://green.lan" in text
    assert text.index("On the laptop, first") < text.index("On green, as root")


def test_root_script_sets_the_printer_up_before_the_desktop():
    _, script = script_for(autologin="yes")
    assert script.index("lpadmin -p QL-1100") < script.index("ufw --force enable") < script.index("xfce4")
    assert script.index("xfce4") < script.index("autologin-user")


def test_apply_points_the_laptop_at_green_before_greens_root_step(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "ON_AIR", tmp_path / "ON_AIR")
    deb = tmp_path / "ql1100pdrv.deb"
    deb.write_bytes(b"x")
    monkeypatch.setattr(gs, "DRIVER_DEB", deb)
    log = []
    ssh = FakeSsh(facts(), log=log)
    run = FakeRun("usb://Brother/QL-1100?serial=X", "Brother QL-1100 CUPS", log=log)
    assert gs.setup(args(dry_run=False), ssh, say=lambda *_: None, run=run) == 0
    laptop = next(i for i, c in enumerate(log) if isinstance(c, list) and c[0] == "sudo")
    green = next(i for i, c in enumerate(log) if isinstance(c, str) and c.startswith("sudo bash"))
    assert laptop < green


def test_nothing_is_applied_on_air(tmp_path, monkeypatch):
    flag = tmp_path / "ON_AIR"
    flag.touch()
    monkeypatch.setattr(gs, "ON_AIR", flag)
    ssh = FakeSsh(facts())
    with pytest.raises(gs.ProvisionError, match="on air"):
        gs.setup(args(dry_run=False), ssh, say=lambda *_: None, run=FakeRun("", ""))
    assert ssh.calls == []
    assert gs.setup(args(), ssh, say=lambda *_: None, run=FakeRun("", "")) == 0  # dry run still works


def test_ssh_to_green_is_ipv4_only():
    # green.lan resolves to its IPv6 ULA first; ufw opens SSH to the IPv4 /24 only.
    s = gs.GreenSsh("green")
    assert s.base[-2:] == ["-o", "AddressFamily=inet"]
    assert "AddressFamily=inet" in (ROOT / "tools" / "green_test.sh").read_text()


def test_a_rerun_finishes_an_interrupted_apt_first():
    _, script = script_for()
    assert script.index("dpkg --configure -a") < script.index("apt-get install")
