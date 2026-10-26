"""tests/test_green_setup.py: tools/green_setup.py, green as the label print
server. Print-only by default, the opt-in --desktop and its --remove-desktop
cleanup (apt history decides what goes), the firewall that opens only to
green's /24, green's raw QL-1100 queue, the laptop queue that keeps the
Brother PPD, the on-air refusal and the dry run. No network and no SSH: a
fake SSH plays green and a fake `run` plays the laptop's CUPS."""
import io
import shutil
import subprocess
import sys
import tarfile
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


FLAGS = ("autologin", "close_others", "desktop")


def script_for(**kw):
    plan = gs.make_plan(facts(**{k: v for k, v in kw.items() if k not in FLAGS}),
                        kw.get("autologin"), close_others=kw.get("close_others", False),
                        desktop=kw.get("desktop", False))
    return plan, gs.root_script(plan, gs.root_steps(plan))


def test_default_is_print_only_no_desktop_no_firefox_no_launcher():
    plan, script = script_for()
    assert plan["desktop"] is None and plan["firefox"] is None
    for desk in ("xfce4", "lightdm", "firefox", "graphical.target", "autologin", "python3-venv"):
        assert desk not in script
    assert gs.BASE_PKGS == ["cups", "ufw"]
    text = gs.describe(plan, gs.root_steps(plan), "green")
    assert "print server only" in text and "Ship launcher" not in text and "RAM budget" not in text


def test_desktop_opt_in_gets_xfce_and_firefox_esr_with_a_reason_from_the_facts():
    plan, script = script_for(desktop=True)
    assert plan["desktop"]["name"] == "XFCE"
    assert "7900 MB RAM" in plan["desktop"]["why"] and "Intel HD 530" in plan["desktop"]["why"]
    assert "xfce4 xfce4-terminal lightdm xorg firefox-esr" in script


def test_small_ram_gets_lxqt_and_ubuntu_gets_firefox():
    plan, _ = script_for(mem="1900", os=OS_UBUNTU, desktop=True)
    assert plan["desktop"]["name"] == "LXQt" and plan["firefox"] == "firefox"


def test_an_existing_desktop_is_kept():
    plan, script = script_for(pkgs="gnome-shell 46 ii \nlightdm 1 ii \n", dm="gdm3", firefox="firefox-esr",
                              desktop=True)
    assert plan["desktop"]["install"] == [] and plan["desktop"]["name"] == "GNOME"
    assert "xfce4" not in script


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


def test_unsupported_os_is_refused():
    with pytest.raises(gs.ProvisionError, match="Debian"):
        gs.make_plan(facts(os='PRETTY_NAME="Fedora"\nID=fedora\n'))


def test_greens_queue_is_raw_and_shared_and_green_gets_no_driver():
    _, script = script_for()
    assert 'lpadmin -p QL-1100 -E -v "$URI" -m raw -o printer-is-shared=true' in script
    assert "NOPRINTER=1" in script
    for driver in ("ql1100pdrv", "i386", gs.PPD, "PageSize"):
        assert driver not in script


def test_autologin_only_with_desktop_when_asked_and_only_on_lightdm():
    assert "autologin" not in script_for(desktop=True)[1]
    assert "autologin" not in script_for(autologin="yes")[1]  # no --desktop: no desktop steps at all
    assert "autologin-user=%s" in script_for(autologin="yes", desktop=True)[1]
    assert f"rm -f {gs.AUTOLOGIN_CONF}" in script_for(autologin="no", desktop=True)[1]
    with pytest.raises(gs.ProvisionError, match="LightDM"):
        gs.make_plan(facts(pkgs="gnome-shell 46 ii \n", dm="gdm3"), autologin="yes", desktop=True)


def test_flags_autologin_needs_desktop_and_remove_desktop_stands_alone():
    assert gs.parse_args([]).desktop is False
    assert gs.parse_args(["--desktop", "--autologin", "yes"]).autologin == "yes"
    for bad in (["--autologin", "yes"], ["--remove-desktop", "--desktop"],
                ["--remove-desktop", "--autologin", "no"]):
        with pytest.raises(SystemExit):
            gs.parse_args(bad)


def test_laptop_green_queue_keeps_its_name_and_the_brother_ppd():
    # Raw had no PPD, so print dialogs listed no label sizes (found in use, 2026-10-24).
    s = gs.LAPTOP["green"]
    assert (f"lpadmin -p QL-1100 -E -v ipp://green.lan:631/printers/QL-1100 -P {gs.PPD} -o PageSize=103x164"
            in s)
    assert "printer-error-policy=retry-job" in s and "printer-is-shared=false" in s
    assert "-m raw" not in s and "everywhere" not in s


BROTHER_SIZES = "PageSize/Media Size: 17x54 29x90 62x100 102x152 *103x164 103X1\n"


class FakeRun:
    def __init__(self, uri, model, green_ok=True, on_laptop_usb=False, log=None, sizes=BROTHER_SIZES):
        self.uri, self.model, self.green_ok, self.usb = uri, model, green_ok, on_laptop_usb
        self.calls = log if log is not None else []
        self.sizes = sizes

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        out, rc = "", 0
        if cmd[:2] == ["lpstat", "-v"]:
            out = f"device for QL-1100: {self.uri}\n"
        elif cmd[0] == "lpoptions" and cmd[-1] == "-l":
            out = self.sizes
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
    run = FakeRun(gs.GREEN_URI, "Brother QL-1100 CUPS")
    assert gs.point_laptop("green", say=lambda *_: None, run=run) == 0
    assert not sudo_calls(run.calls)


def test_the_old_raw_forward_is_repointed_with_the_ppd_and_the_sizes_are_checked():
    run, out = FakeRun(gs.GREEN_URI, "Local Raw Printer"), []
    assert gs.point_laptop("green", say=out.append, run=run) == 0
    assert len(sudo_calls(run.calls)) == 1 and ["lpoptions", "-p", "QL-1100", "-l"] in run.calls
    assert any("offers 103x164" in o for o in out)


def test_a_laptop_queue_without_the_label_size_fails_loud():
    run = FakeRun(gs.GREEN_URI, "Local Raw Printer", sizes="")
    with pytest.raises(gs.ProvisionError, match="no 103x164"):
        gs.point_laptop("green", say=lambda *_: None, run=run)


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
    def __init__(self, f, log=None, root_rc=0, sim=""):
        self.text = "".join(f"##{k}\n{v}\n" for k, v in f.items())
        self.calls = log if log is not None else []
        self.root_rc, self.sim, self.inputs = root_rc, sim, []

    def run(self, command, input=None, tty=False):
        self.calls.append(command)
        self.inputs.append(input)
        rc = self.root_rc if command.startswith("sudo ") else 0
        out = self.sim if "apt-get -s" in command else self.text
        return SimpleNamespace(returncode=rc, stdout=out.encode(), stderr=b"")


def args(**kw):
    a = dict(dry_run=True, laptop="green", autologin=None, lan=None, close_others=False,
             desktop=False, remove_desktop=False)
    a.update(kw)
    return SimpleNamespace(**a)


def test_dry_run_reads_green_once_and_changes_nothing():
    ssh, out = FakeSsh(facts()), []
    run = FakeRun("", "")
    assert gs.setup(args(), ssh, say=out.append, run=run) == 0
    assert ssh.calls == ["bash -s"] and run.calls == []
    text = "\n".join(out)
    assert "dry run: nothing changed" in text and "ipp://green.lan" in text and gs.PPD in text
    assert text.index("On the laptop, first") < text.index("On green, as root")


def test_root_script_sets_the_printer_up_before_the_desktop():
    _, script = script_for(autologin="yes", desktop=True)
    assert script.index("lpadmin -p QL-1100") < script.index("ufw --force enable") < script.index("xfce4")
    assert script.index("xfce4") < script.index("autologin-user")


def test_apply_points_the_laptop_at_green_before_greens_root_step(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "ON_AIR", tmp_path / "ON_AIR")
    log = []
    ssh = FakeSsh(facts(), log=log)
    run = FakeRun("usb://Brother/QL-1100?serial=X", "Brother QL-1100 CUPS", log=log)
    assert gs.setup(args(dry_run=False), ssh, say=lambda *_: None, run=run) == 0
    laptop = next(i for i, c in enumerate(log) if isinstance(c, list) and c[0] == "sudo")
    green = next(i for i, c in enumerate(log) if isinstance(c, str) and c.startswith("sudo bash"))
    assert laptop < green
    # print-only: no Ship launcher is written as the user
    assert not any(i and b"pops-ship" in i for i in ssh.inputs)


def test_nothing_is_applied_on_air(tmp_path, monkeypatch):
    flag = tmp_path / "ON_AIR"
    flag.touch()
    monkeypatch.setattr(gs, "ON_AIR", flag)
    ssh = FakeSsh(facts())
    with pytest.raises(gs.ProvisionError, match="on air"):
        gs.setup(args(dry_run=False), ssh, say=lambda *_: None, run=FakeRun("", ""))
    with pytest.raises(gs.ProvisionError, match="on air"):
        gs.setup(args(dry_run=False, remove_desktop=True), ssh, say=lambda *_: None, run=FakeRun("", ""))
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


# --- --remove-desktop ---------------------------------------------------------------
HIST = """Start-Date: 2026-10-20  10:00:00
Commandline: apt-get install -y -q --no-install-recommends cups ufw
Install: cups:amd64 (2.4.10-3), ufw:all (0.36.2-9)
Start-Date: 2026-10-24  20:10:00
Commandline: apt-get install -y -q xfce4 xfce4-terminal lightdm xorg firefox-esr
Install: xfce4:amd64 (4.20.0), xfce4-session:amd64 (4.20.1-1, automatic), lightdm:amd64 (1.32.0-6), \
lightdm-gtk-greeter:amd64 (2.0.9-1, automatic), xorg:amd64 (1:7.7+24), xfce4-terminal:amd64 (1.1.4-1), \
firefox-esr:amd64 (128.10.0esr-1), libfoo:i386 (1, automatic)
Start-Date: 2026-10-24  21:00:00
Install: unattended-thing:amd64 (1)
Start-Date: 2026-10-24  21:05:00
Commandline: apt-get install -y -q gimp
Install: gimp:amd64 (3.0)"""

INSTALLED = ("cups 2.4 ii \nufw 0.36 ii \nxfce4 4.20 ii \nlightdm 1.32 ii \nlightdm-gtk-greeter 2.0 ii \n"
             "xorg 7.7 ii \nxfce4-terminal 1.1 ii \nfirefox-esr 128 ii \n")
SIM = ("NOTE: This is only a simulation!\nPurg xfce4 [4.20.0]\nPurg xfce4-session [4.20.1-1]\n"
       "Purg lightdm [1.32.0-6]\nPurg lightdm-gtk-greeter [2.0.9-1]\nPurg libfoo:i386 [1]\n")


def rm_facts(**kw):
    f = dict(pkgs=INSTALLED, apthist=HIST, apthist_ok="yes", dm="lightdm", dm_active="active",
             target="graphical.target")
    f.update(kw)
    return facts(**f)


def test_apt_history_names_only_the_desktop_runs_installs():
    ours, runs = gs.script_installs(HIST, "amd64")
    assert runs == 1
    assert {"xfce4", "xfce4-session", "lightdm-gtk-greeter", "firefox-esr", "libfoo:i386"} <= ours
    assert not ours & {"cups", "ufw", "gimp", "unattended-thing"}  # base step, by hand, no Commandline


def test_cleanup_purges_only_the_desktop_packages_the_script_installed():
    plan = gs.removal_plan(rm_facts(), SIM)
    assert plan["top"] == ["xfce4", "xfce4-terminal", "lightdm", "lightdm-gtk-greeter", "xorg", "firefox-esr"]
    script = "\n".join(s.script for s in gs.removal_steps(plan))
    assert ("apt-get purge -y -q --autoremove xfce4 xfce4-terminal lightdm lightdm-gtk-greeter xorg firefox-esr"
            in script)
    assert "systemctl set-default multi-user.target" in script and "apt-mark manual" not in script
    for other in ("cups", "ufw", "gimp"):
        assert other not in script


def test_cleanup_keeps_what_green_had_and_checks_again_before_purging():
    plan = gs.removal_plan(rm_facts(), SIM + "Purg cups-old [1]\n")
    assert plan["keep"] == ["cups-old"]
    steps = gs.removal_steps(plan)
    titles = [s.title for s in steps]
    assert titles[0].startswith("Keep") and steps[0].script == "apt-mark manual cups-old"
    check = next(i for i, t in enumerate(titles) if t.startswith("Check"))
    purge = next(i for i, t in enumerate(titles) if t.startswith("Purge"))
    assert check < purge and 'grep -qxF "$p" "$B/ours.txt"' in steps[check].script
    assert "exit 1" in steps[check].script


def test_cleanup_removes_the_autologin_file_only_when_it_is_there():
    with_file = gs.removal_steps(gs.removal_plan(rm_facts(autologin="yes"), SIM))
    assert f"rm -f {gs.AUTOLOGIN_CONF}" in [s.script for s in with_file]
    assert not any("autologin" in s.script for s in gs.removal_steps(gs.removal_plan(rm_facts(), SIM)))


def test_cleanup_with_no_desktop_from_the_script_purges_nothing():
    plan = gs.removal_plan(rm_facts(apthist=HIST.split("Start-Date: 2026-10-24  20:10:00")[0]), "")
    assert plan["top"] == [] and gs.removal_steps(plan) == []


def test_cleanup_refuses_without_apt_history():
    with pytest.raises(gs.ProvisionError, match="apt history"):
        gs.purge_targets(rm_facts(apthist_ok=""))


def test_cleanup_dry_run_reads_and_simulates_and_changes_nothing():
    ssh, out = FakeSsh(rm_facts(), sim=SIM), []
    assert gs.setup(args(remove_desktop=True), ssh, say=out.append, run=FakeRun("", "")) == 0
    assert ssh.calls[0] == "bash -s"
    assert ssh.calls[1].startswith("LC_ALL=C apt-get -s -q purge --autoremove xfce4")
    assert len(ssh.calls) == 2
    text = "\n".join(out)
    assert "1 desktop run(s)" in text and "dry run: nothing changed" in text


def test_cleanup_apply_sends_the_install_list_and_runs_one_sudo(tmp_path, monkeypatch):
    monkeypatch.setattr(gs, "ON_AIR", tmp_path / "ON_AIR")
    ssh = FakeSsh(rm_facts(), sim=SIM)
    run = FakeRun("", "")
    assert gs.setup(args(dry_run=False, remove_desktop=True), ssh, say=lambda *_: None, run=run) == 0
    assert [c for c in ssh.calls if c.startswith("sudo ")] == [f"sudo bash ~/{gs.REMOTE_DIR}/bundle/root.sh"]
    assert run.calls == []  # the laptop's queue is not touched
    tar_bytes = next(i for c, i in zip(ssh.calls, ssh.inputs) if c.startswith("rm -rf"))
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as t:
        ours = t.extractfile("ours.txt").read().decode().split()
        root = t.extractfile("root.sh").read().decode()
    assert "xfce4-session" in ours and "cups" not in ours
    assert root.index("Check:") < root.index("apt-get purge -y")


@pytest.mark.skipif(sys.platform == "win32", reason="green and the laptop are Linux")
@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_generated_scripts_are_valid_bash():
    _, script = script_for(autologin="yes", tailscale="tailscale0", desktop=True)
    rplan = gs.removal_plan(rm_facts(autologin="yes"), SIM + "Purg cups-old [1]\n")
    rsteps = gs.removal_steps(rplan)
    for s in (script, script_for()[1], gs.LAPTOP["green"], gs.LAPTOP["usb"],
              gs.REMOVE_LAUNCHER, gs.root_script(rplan, rsteps)):
        assert subprocess.run(["bash", "-n"], input=s, text=True).returncode == 0
