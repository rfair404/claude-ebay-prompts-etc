#!/usr/bin/env python3
"""GREEN: set green up as the label print server, from the laptop over SSH.

    python tools/green_setup.py --dry-run          # read green, print the plan and its root script
    python tools/green_setup.py                    # apply: a sudo prompt here, then one on green
    python tools/green_setup.py --close-others     # also switch ufw on over green's other listeners
    python tools/green_setup.py --laptop usb       # cable back on the laptop: print by USB again
    python tools/green_setup.py --remove-desktop   # purge the desktop an earlier run installed
    python tools/green_setup.py --desktop          # opt in: desktop and Firefox

Green is a print server and nothing else (Russell, 2026-10-24): no desktop,
no browser, no tests, no shipping screen. The box size and weight form is
on the pick sheets. The eBay keys, the R2 keys, config.yaml and
the ledgers stay on the laptop, and green holds no login. Every connection
opens from the laptop (`ssh green`, key ~/.ssh/green), so green holds no
way back in.

The driver stays on the laptop. The laptop's QL-1100 queue keeps its name
and the Brother PPD, so print dialogs list 103x164 and the other label
sizes and tools/print_label.sh is unchanged. It runs the Brother filter and
forwards the finished job to green's raw queue, which passes it to the USB
port. Jobs wait and retry while green is down. The repoint is refused while
the printer is still on the laptop's own USB.

On the laptop, first (sudo asks once): the QL-1100 queue -> green.
On green, as root (sudo asks for green's password once):
  - CUPS and ufw;
  - a raw QL-1100 queue on green's USB, shared;
  - CUPS shared to the local subnet only (cupsctl --share-printers
    --no-remote-any --no-remote-admin) and ufw opening 631/tcp and SSH to
    green's own /24 only. ufw is not switched on over other listeners
    unless --close-others says so;
  - only with --desktop (kept "for now"), last, as it is the slow
    download: a desktop only if green has none, XFCE with LightDM (LXQt
    under 2 GB of RAM), plus Firefox. Auto-login only with --autologin yes.
--remove-desktop undoes a --desktop run, and removes the "Ship" launcher an
older --desktop run wrote. It reads green's apt history for
the desktop installs this script ran, purges those packages and what only
they pulled in (autoremove), removes the auto-login file and boots green to
the console (multi-user.target). Anything apt would remove that the script
didn't install is kept, or the cleanup stops before it changes anything.

Re-running is safe: each step checks before it changes anything. Nothing
is applied while inventory/_live/ON_AIR exists; --dry-run always works.
RUN.md, "Green: the print server", has the why. Stdlib only.
"""
from __future__ import annotations

import argparse
import io
import ipaddress
import re
import shlex
import subprocess
import sys
import tarfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE / "live" / "stream_box"))
sys.path.insert(0, str(HERE / "live"))
from provision import (ProvisionError, SAFE_USER, Ssh, Step, _sub, check_lan,  # noqa: E402
                       check_private_lan, lan_cidr, parse_facts)

HOST = "green"
QUEUE = "QL-1100"
PAGE = "103x164"  # 4x6 shipping labels, a PageSize in the Brother PPD
CUPS_PORT = 631
GREEN_URI = f"ipp://{HOST}.lan:{CUPS_PORT}/printers/{QUEUE}"
REMOTE_DIR = ".cache/pops-green"
PPD = "/opt/brother/PTouch/ql1100/cupswrapper/brother_ql1100_printer_en.ppd"  # the laptop's driver
ON_AIR = REPO / "inventory" / "_live" / "ON_AIR"
EXIT_NO_PRINTER, EXIT_FIREWALL_OFF = 3, 4

BASE_PKGS = ["cups", "ufw"]  # green's queue is raw: the driver runs on the laptop
DESKTOPS = {  # dpkg package -> name; the first one installed is "green's desktop"
    "xfce4": "XFCE", "lxqt-core": "LXQt", "gnome-shell": "GNOME", "plasma-desktop": "KDE Plasma",
    "cinnamon": "Cinnamon", "mate-desktop-environment-core": "MATE", "lxde-core": "LXDE",
}
NEW_DESKTOP = {"xfce": ["xfce4", "xfce4-terminal", "lightdm", "xorg"],
               "lxqt": ["lxqt-core", "lightdm", "xorg"]}
LXQT_BELOW_MB = 2048
# Every package a --desktop run names on its apt-get line, Debian or Ubuntu
# (lightdm-gtk-greeter was named on the ship-station branch, #334). The
# cleanup purges the ones green's apt history says this script installed.
DESKTOP_PKGS = ["xfce4", "xfce4-terminal", "lxqt-core", "lightdm", "lightdm-gtk-greeter", "xorg",
                "firefox", "firefox-esr"]
AUTOLOGIN_CONF = "/etc/lightdm/lightdm.conf.d/50-pops-autologin.conf"
LAUNCHER = "pops-ship.desktop"

FACTS = r"""
echo '##os'; cat /etc/os-release
echo '##user'; id -un
echo '##arch'; dpkg --print-architecture 2>/dev/null || true
echo '##pkgs'; dpkg-query -W -f='${binary:Package} ${Version} ${db:Status-Abbrev}\n' @PKGS@ 2>/dev/null || true
echo '##mem'; awk '/^MemTotal/ {print int($2/1024)}' /proc/meminfo
echo '##swap'; awk '/^SwapTotal/ {print int($2/1024)}' /proc/meminfo
echo '##cpu'; echo "$(nproc) x$(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2-)"
echo '##gpu'; lspci 2>/dev/null | grep -E 'VGA|3D|Display' | cut -d' ' -f2- || true
echo '##dm'; basename "$(cat /etc/X11/default-display-manager 2>/dev/null)" 2>/dev/null || true
echo '##dm_active'; systemctl is-active display-manager 2>/dev/null || true
echo '##target'; systemctl get-default 2>/dev/null || true
echo '##sessions'; for s in $(loginctl list-sessions --no-legend 2>/dev/null | awk '{print $1}'); do loginctl show-session "$s" -p Type --value 2>/dev/null; done | sort | uniq -c | xargs || true
echo '##firefox'; for f in firefox-esr firefox; do command -v "$f" >/dev/null && echo "$f"; done; true
echo '##usb'; lsusb 2>/dev/null | grep -i -E ' 04f9:| brother' | cut -d' ' -f6- || true
echo '##queue'; lpstat -v @QUEUE@ 2>/dev/null | awk '{print $NF}' || true
echo '##listen'; ss -Hltn 2>/dev/null | awk '{print $4}' | sort -u || true
echo '##ufw'; grep -s '^ENABLED=' /etc/ufw/ufw.conf | cut -d= -f2 || true
echo '##tailscale'; [ -e /sys/class/net/tailscale0 ] && echo tailscale0 || true
echo '##route'; ip -o -4 route show default
echo '##addr'; ip -o -4 addr show
echo '##client'; echo "${SSH_CLIENT%% *}"
echo '##autologin'; [ -e @AUTOLOGIN@ ] && echo yes || true
echo '##apthist_ok'; [ -r /var/log/apt/history.log ] && echo yes || true
echo '##apthist'; zcat -f /var/log/apt/history.log* 2>/dev/null | grep -E '^(Start-Date|Commandline|Install):' || true
"""


def facts_script() -> str:
    pkgs = BASE_PKGS + list(DESKTOPS) + [p for p in DESKTOP_PKGS if p not in DESKTOPS]
    return _sub(FACTS, {"@PKGS@": " ".join(pkgs), "@QUEUE@": QUEUE, "@AUTOLOGIN@": AUTOLOGIN_CONF})


def installed(pkgs: str) -> dict[str, str]:
    """dpkg-query lines -> {package: version} for the ones fully installed."""
    out = {}
    for line in pkgs.splitlines():
        f = line.split()
        if len(f) >= 3 and f[2].startswith("ii"):
            out[f[0]] = f[1]
    return out


def listeners(listen: str) -> list[int]:
    """Ports green listens on beyond loopback, other than SSH and CUPS."""
    ports = set()
    for addr in listen.split():
        host, _, port = addr.rpartition(":")
        host = host.strip("[]").split("%")[0]
        try:
            if host not in ("*", "") and ipaddress.ip_address(host).is_loopback:
                continue
        except ValueError:
            pass
        if port.isdigit() and int(port) not in (22, CUPS_PORT):
            ports.add(int(port))
    return sorted(ports)


def _green(facts: dict[str, str]) -> tuple[str, dict, set]:
    """green's user, os-release and OS family; refuses what this script doesn't know."""
    user = facts.get("user", "")
    if not SAFE_USER.match(user):
        raise ProvisionError(f"unusual user name on green: {user!r}")
    osv = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', facts.get("os", ""), re.M))
    family = {osv.get("ID", "")} | set(osv.get("ID_LIKE", "").split())
    if "debian" not in family and "ubuntu" not in family:
        raise ProvisionError(f"green runs {osv.get('PRETTY_NAME') or 'an unknown OS'}; "
                             "this script knows Debian and Ubuntu (apt, dpkg)")
    return user, osv, family


def make_plan(facts: dict[str, str], autologin: str | None = None, lan: str | None = None,
              close_others: bool = False, desktop: bool = False) -> dict:
    """Everything green's steps need, from its facts and the flags (pure)."""
    user, osv, family = _green(facts)
    lan = check_private_lan(lan) if lan else lan_cidr(facts.get("route", ""), facts.get("addr", ""))
    client = facts.get("client") or None
    if client:
        client = str(ipaddress.ip_address(client))
    check_lan(lan, client)
    have = installed(facts.get("pkgs", ""))
    plan = {
        "user": user, "os": osv.get("PRETTY_NAME"), "lan": lan, "client": client,
        "mem": int(facts.get("mem") or 0), "swap": int(facts.get("swap") or 0), "cpu": facts.get("cpu"),
        "gpu": facts.get("gpu", ""), "dm_now": facts.get("dm", "").strip(),
        "dm_active": facts.get("dm_active", ""), "sessions": facts.get("sessions", ""),
        "base_missing": [p for p in BASE_PKGS if p not in have], "usb": facts.get("usb", ""),
        "queue": facts.get("queue", ""), "ufw": facts.get("ufw", "").strip().lower() == "yes",
        "others": listeners(facts.get("listen", "")), "close_others": close_others,
        "tailscale": bool(facts.get("tailscale")),
        "desktop": None, "firefox": None, "autologin": None,
    }
    if desktop:
        plan.update(_desktop_plan(facts, have, osv, family, plan["mem"], plan["dm_now"], autologin))
    return plan


def _desktop_plan(facts, have, osv, family, mem, dm, autologin) -> dict:
    """--desktop only: which desktop, which Firefox, auto-login."""
    existing = next((name for pkg, name in DESKTOPS.items() if pkg in have), None)
    if existing:
        desktop = {"name": existing, "install": [] if dm else ["lightdm"],
                   "why": f"keep {existing}, already on green"}
    else:
        kind = "lxqt" if mem and mem < LXQT_BELOW_MB else "xfce"
        name = "LXQt" if kind == "lxqt" else "XFCE"
        gpu = facts.get("gpu", "").splitlines()[0] if facts.get("gpu") else "no GPU listed"
        desktop = {"name": name, "install": NEW_DESKTOP[kind],
                   "why": f"{name}: {mem or '?'} MB RAM, {gpu}, {osv.get('PRETTY_NAME', '?')}; "
                          + ("lightest desktop that still runs Firefox well" if kind == "lxqt" else
                             "light, needs no GPU compositing, Debian's default lightweight desktop")}
    ubuntu = "ubuntu" in family
    firefox = (facts.get("firefox", "").split() or [None])[0] or ("firefox" if ubuntu else "firefox-esr")
    if firefox not in have and not facts.get("firefox"):
        desktop["install"] = desktop["install"] + [firefox]
    if autologin == "yes" and (dm or "lightdm") != "lightdm":
        raise ProvisionError(f"green's display manager is {dm}; --autologin only knows LightDM")
    return {"desktop": desktop, "firefox": firefox, "autologin": autologin}


# --- green's steps (pure) ----------------------------------------------------------
HEADER = r"""#!/bin/bash
# Generated by tools/green_setup.py. Runs on green as root.
set -euo pipefail
U=@USER@
H=$(getent passwd "$U" | cut -d: -f6)
B="$H/@REMOTE@/bundle"
NOPRINTER=0; FIREWALL_OFF=0
export DEBIAN_FRONTEND=noninteractive
dpkg --configure -a   # finish any install an earlier, interrupted run left half done
step() { printf '\n== %s\n' "$*"; }
missing() { for p in "$@"; do dpkg-query -W -f='${db:Status-Abbrev}' "$p" 2>/dev/null | grep -q '^ii' || printf '%s ' "$p"; done; }
"""

FOOTER = r"""
echo
[ "$NOPRINTER" = 0 ] || exit @EXIT_NO_PRINTER@
[ "$FIREWALL_OFF" = 0 ] || exit @EXIT_FIREWALL_OFF@
echo "green: done"
"""


def root_steps(plan: dict) -> list[Step]:
    steps = [
        Step("Packages: CUPS and ufw",
             r"""NEED=$(missing @BASE@)
if [ -n "$NEED" ]; then apt-get update -q; apt-get install -y -q --no-install-recommends $NEED
else echo "already installed"; fi"""),
        Step(f"Queue {QUEUE}: green's USB, raw (the laptop runs the Brother driver), shared",
             r"""URI=$(lpinfo --include-schemes usb -v 2>/dev/null | awk '$2 ~ /^usb:\/\/Brother\/QL-1100/ {print $2; exit}')
if [ -z "$URI" ]; then
  echo "The QL-1100 isn't on green's USB (cable in, power on?). Queue not set; everything else was."
  NOPRINTER=1
else
  # CUPS 2.4 warns that raw queues are deprecated; they still work, and the
  # laptop's queue already holds the PPD that print dialogs read.
  lpadmin -p @QUEUE@ -E -v "$URI" -m raw -o printer-is-shared=true
  lpstat -p @QUEUE@
fi"""),
        Step("CUPS: share printers to the local subnet only, no remote admin",
             r"""if cupsctl | grep -qx _share_printers=1 && cupsctl | grep -qx _remote_any=0 && cupsctl | grep -qx _remote_admin=0; then
  echo "already shared to the local subnet only"
else
  cupsctl --share-printers --no-remote-any --no-remote-admin
fi
cupsctl | grep -E '^_(share_printers|remote_any|remote_admin)='"""),
        Step(f"Firewall: SSH and CUPS :{CUPS_PORT} from {plan['lan']} only", _firewall(plan)),
    ]
    d = plan["desktop"]
    if d:  # --desktop only, last: it is the slow download, and labels shouldn't wait for it
        steps.append(Step(f"Desktop: {d['name']} " + ("(already there)" if not d["install"] else "and Firefox, as needed"),
                          r"""NEED=$(missing @DESKTOP@)
if [ -n "$NEED" ]; then apt-get update -q; apt-get install -y -q $NEED; else echo "already installed"; fi
[ "$(systemctl get-default)" = graphical.target ] || systemctl set-default graphical.target
systemctl is-active --quiet display-manager || systemctl start display-manager || true"""))
        if plan["autologin"] == "yes":
            steps.append(Step(f"Auto-login: {plan['user']} on LightDM", r"""mkdir -p /etc/lightdm/lightdm.conf.d
printf '[Seat:*]\nautologin-user=%s\nautologin-user-timeout=0\n' "$U" > @AUTOLOGIN@
echo "next boot logs $U in" """))
        elif plan["autologin"] == "no":
            steps.append(Step("Auto-login: off", "rm -f @AUTOLOGIN@"))
    subs = {"@BASE@": " ".join(BASE_PKGS), "@DESKTOP@": " ".join(d["install"]) if d else "",
            "@QUEUE@": QUEUE, "@AUTOLOGIN@": AUTOLOGIN_CONF}
    return [Step(s.title, _sub(s.script, subs)) for s in steps]


def _firewall(plan: dict) -> str:
    lines = [f"ufw allow from {plan['lan']} to any port 22 proto tcp comment 'green: SSH from the LAN' >/dev/null",
             f"ufw allow from {plan['lan']} to any port {CUPS_PORT} proto tcp "
             "comment 'green: CUPS (QL-1100) from the LAN' >/dev/null"]
    if plan["tailscale"]:  # keep the SSH reach green already has
        lines.append("ufw allow in on tailscale0 to any port 22 proto tcp "
                     "comment 'green: SSH over Tailscale, as before' >/dev/null")
    blocked = plan["others"] and not plan["close_others"]
    lines += ["if ufw status | grep -q '^Status: active'; then",
              "  echo 'ufw already on; the LAN rules are in'"]
    if blocked:
        ports = " ".join(map(str, plan["others"]))
        lines += ["else",
                  f"  echo 'ufw left off: green also listens on {ports}, which it would close.'",
                  "  echo 'Check those, then re-run with --close-others.'",
                  "  FIREWALL_OFF=1"]
    else:
        lines += ["else",
                  "  ufw default deny incoming >/dev/null",
                  "  ufw default allow outgoing >/dev/null",
                  "  ufw --force enable >/dev/null"]
    lines += ["fi", "ufw status"]
    return "\n".join(lines)


def root_script(plan: dict, steps: list[Step]) -> str:
    head = _sub(HEADER, {"@USER@": plan["user"], "@REMOTE@": REMOTE_DIR})
    body = "\n".join(f"\nstep {shlex.quote(s.title)}\n{s.script}" for s in steps)
    return head + body + _sub(FOOTER, {"@EXIT_NO_PRINTER@": str(EXIT_NO_PRINTER),
                                       "@EXIT_FIREWALL_OFF@": str(EXIT_FIREWALL_OFF)})


def bundle(script: str, extra: dict[str, bytes] | None = None) -> bytes:
    files = {"root.sh": script.encode(), **(extra or {})}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# --- --remove-desktop (pure) -------------------------------------------------------
def _native(name: str, arch: str) -> str:
    """apt history writes xfce4:amd64; apt-get -s writes xfce4. Keep foreign arches."""
    pkg, _, a = name.partition(":")
    return pkg if a in ("", arch, "all") else name


def is_desktop_install(cmd: list[str]) -> bool:
    """The --desktop step's own apt line: `apt-get install -y -q <desktop packages>`.
    The base step adds --no-install-recommends, so it never matches."""
    return cmd[:4] == ["apt-get", "install", "-y", "-q"] and len(cmd) > 4 and \
        all(p in DESKTOP_PKGS for p in cmd[4:])


def script_installs(hist: str, arch: str) -> tuple[set[str], int]:
    """Every package green's apt history says a --desktop run installed (named
    or pulled in), and how many such runs there were."""
    ours, runs, cmd = set(), 0, None
    for line in hist.splitlines():
        key, _, val = line.partition(": ")
        if key == "Start-Date":
            cmd = None
        elif key == "Commandline":
            cmd = val.split()
        elif key == "Install" and cmd and is_desktop_install(cmd):
            runs += 1
            ours |= {_native(n, arch) for n in re.findall(r"(?:^|, )([^\s,(]+) \(", val)}
    return ours, runs


def simulated_removals(text: str) -> list[str]:
    """`apt-get -s purge --autoremove` output -> the packages it would remove."""
    return list(dict.fromkeys(re.findall(r"^(?:Purg|Remv) (\S+)", text, re.M)))


def purge_targets(facts: dict[str, str]) -> list[str]:
    """The DESKTOP_PKGS still installed that a --desktop run installed."""
    _green(facts)
    if facts.get("apthist_ok") != "yes":
        raise ProvisionError("can't read green's apt history (/var/log/apt/history.log), so the cleanup "
                             "can't tell what this script installed; nothing was changed")
    ours, _ = script_installs(facts.get("apthist", ""), facts.get("arch") or "amd64")
    have = installed(facts.get("pkgs", ""))
    return [p for p in DESKTOP_PKGS if p in ours and p in have]


def removal_plan(facts: dict[str, str], simulation: str) -> dict:
    """What --remove-desktop does, from green's facts and apt's dry run of the purge."""
    user, osv, _ = _green(facts)
    ours, runs = script_installs(facts.get("apthist", ""), facts.get("arch") or "amd64")
    top = purge_targets(facts)
    remove = simulated_removals(simulation) if top else []
    return {"user": user, "os": osv.get("PRETTY_NAME"), "runs": runs, "ours": sorted(ours), "top": top,
            "remove": remove, "keep": sorted(set(remove) - ours),
            "autologin": facts.get("autologin") == "yes", "target": facts.get("target", ""),
            "dm_now": facts.get("dm", "").strip(), "dm_active": facts.get("dm_active", "")}


def removal_steps(plan: dict) -> list[Step]:
    steps = []
    top = " ".join(plan["top"])
    if plan["keep"]:
        steps.append(Step("Keep what green had before: apt-mark manual, so autoremove leaves it",
                          "apt-mark manual " + " ".join(plan["keep"])))
    if plan["top"]:
        steps.append(Step("Check: apt removes only what green_setup installed",
                          f"""SIM=$(LC_ALL=C apt-get -s -q purge --autoremove {top} | awk '/^(Purg|Remv) / {{print $2}}')
BAD=$(for p in $SIM; do grep -qxF "$p" "$B/ours.txt" || printf '%s ' "$p"; done)
if [ -n "$BAD" ]; then
  echo "apt would also remove $BAD, which green_setup didn't install. Nothing was removed."
  exit 1
fi
echo "$(echo $SIM | wc -w) packages to purge, all from green_setup's desktop runs" """))
    if plan["autologin"]:
        steps.append(Step("Auto-login: off", f"rm -f {AUTOLOGIN_CONF}"))
    if plan["top"]:
        steps.append(Step("Boot to the console: multi-user.target",
                          "[ \"$(systemctl get-default)\" = multi-user.target ] || systemctl set-default multi-user.target"))
        steps.append(Step(f"Purge {top}, and what only they pulled in",
                          f"apt-get purge -y -q --autoremove {top}"))
    return steps


REMOVE_LAUNCHER = f"""set -u
rm -f "$HOME/.local/share/applications/{LAUNCHER}" "$HOME/Desktop/{LAUNCHER}"
D=$(xdg-user-dir DESKTOP 2>/dev/null) && rm -f "$D/{LAUNCHER}"
echo "Ship launcher: removed"
"""


def describe_removal(plan: dict, steps: list[Step]) -> str:
    lines = [f"Green OS:    {plan['os']}; boots to {plan['target'] or '?'}; display manager "
             f"{plan['dm_now'] or 'none'} ({plan['dm_active'] or '?'})",
             f"apt history: {plan['runs']} desktop run(s) of this script, {len(plan['ours'])} packages installed"]
    if plan["top"]:
        lines.append(f"Purge:       {' '.join(plan['top'])}; {len(plan['remove'])} packages with autoremove, "
                     f"{len(plan['remove']) - len(plan['keep'])} of them from those runs")
    else:
        lines.append("Purge:       nothing; no desktop package this script installed is still on green")
    if plan["keep"]:
        lines.append(f"Kept:        {' '.join(plan['keep'])} (on green before; marked manual)")
    lines += [f"Auto-login:  {'file removed' if plan['autologin'] else 'none set'}",
              "On green, as the user:", f"      remove the Ship launcher ({LAUNCHER})"]
    if steps:
        lines.append("On green, as root (one sudo prompt):")
        for i, s in enumerate(steps, 1):
            lines.append(f"  {i:2}. {s.title}")
            lines += [f"        {l}" for l in s.script.splitlines()]
    return "\n".join(lines)


# --- the laptop's queue ------------------------------------------------------------
# The laptop keeps the Brother PPD in both modes, so print dialogs and
# print_label.sh see the same sizes. With the cable on green the filtered job
# goes to green's raw queue (an `-m everywhere` queue would need green up to
# create it, and its PageSize names wouldn't be the PPD's 103x164).
LAPTOP = {
    "green": f"""set -euo pipefail
[ -r {PPD} ] || {{ echo "the Brother PPD isn't on this laptop: {PPD}"; exit 1; }}
lpadmin -p {QUEUE} -E -v {GREEN_URI} -P {PPD} -o PageSize={PAGE} -o printer-is-shared=false -o printer-error-policy=retry-job
lpstat -v {QUEUE}""",
    "usb": f"""set -euo pipefail
URI=$(lpinfo --include-schemes usb -v 2>/dev/null | awk '$2 ~ /^usb:\\/\\/Brother\\/QL-1100/ {{print $2; exit}}')
[ -n "$URI" ] || {{ echo "The QL-1100 isn't on this laptop's USB (cable in, power on?)"; exit {EXIT_NO_PRINTER}; }}
lpadmin -p {QUEUE} -E -v "$URI" -P {PPD} -o PageSize={PAGE} -o printer-is-shared=false -o printer-error-policy=stop-printer
lpstat -v {QUEUE}""",
}


def laptop_queue(run=subprocess.run) -> tuple[str, str]:
    """(device URI, make-and-model) of the laptop's QL-1100 queue."""
    v = run(["lpstat", "-v", QUEUE], capture_output=True, text=True).stdout.split()
    o = run(["lpoptions", "-p", QUEUE], capture_output=True, text=True).stdout
    m = re.search(r"printer-make-and-model='([^']*)'", o) or re.search(r"printer-make-and-model=(\S+)", o)
    return (v[-1] if v else ""), (m.group(1) if m else "")


def laptop_sizes(run=subprocess.run) -> list[str]:
    """The PageSize choices the laptop's queue offers (`lpoptions -l`), what print dialogs list."""
    out = run(["lpoptions", "-p", QUEUE, "-l"], capture_output=True, text=True).stdout or ""
    for line in out.splitlines():
        if line.startswith("PageSize"):
            return [w.lstrip("*") for w in line.split(":", 1)[1].split()]
    return []


def green_answers(run=subprocess.run) -> bool:
    r = run(["lpstat", "-h", f"{HOST}.lan:{CUPS_PORT}", "-p", QUEUE], capture_output=True, text=True)
    return r.returncode == 0 and f"printer {QUEUE}" in r.stdout


def laptop_has_printer(run=subprocess.run) -> bool:
    r = run(["lsusb"], capture_output=True, text=True)
    return bool(re.search(r"\b04f9:|brother", r.stdout or "", re.I))


def _check_sizes(say, run) -> None:
    sizes = laptop_sizes(run)
    if PAGE not in sizes:
        raise ProvisionError(f"the laptop's {QUEUE} queue offers no {PAGE} size "
                             f"({' '.join(sizes) or 'no PageSize at all'}); print dialogs won't list the label")
    say(f"laptop: {QUEUE} offers {PAGE} and {len(sizes) - 1} other label sizes")


def point_laptop(mode: str, say=print, run=subprocess.run) -> int:
    """Point the laptop's QL-1100 queue at green (or back at its USB). Going
    to green doesn't wait for green: with the cable on green the laptop's
    queue points at nothing, and a forward that retries is no worse."""
    uri, model = laptop_queue(run)
    brother = "brother" in model.lower()
    if mode == "green":
        if uri == GREEN_URI and brother:
            say(f"laptop: {QUEUE} already forwards to green with the Brother PPD")
            _check_sizes(say, run)
            return 0
        if laptop_has_printer(run):
            raise ProvisionError(f"the {QUEUE} is still on this laptop's USB; move the cable to green, "
                                 "or run with --laptop skip. The laptop's queue was left as it was")
    elif uri.startswith("usb://") and brother:
        say(f"laptop: {QUEUE} already prints by USB")
        return 0
    say(f"laptop: {QUEUE} -> {'green' if mode == 'green' else 'USB'} (sudo asks for the laptop password)")
    rc = run(["sudo", "bash", "-c", LAPTOP[mode]]).returncode
    if rc == 0:
        _check_sizes(say, run)
    return rc


# --- the dry run ---------------------------------------------------------------------
def describe(plan: dict, steps: list[Step], laptop: str) -> str:
    d = plan["desktop"]
    lines = [f"Green OS:    {plan['os']}; {plan['cpu']}; {plan['mem']} MB RAM, {plan['swap']} MB swap"]
    if d:
        lines += [
            f"GPU:         {plan['gpu'] or 'none listed'}",
            f"Display:     manager {plan['dm_now'] or 'none yet'} ({plan['dm_active'] or '?'}), "
            f"sessions: {plan['sessions'] or 'none'}",
            f"Desktop:     {d['why']}" + (f"; installs {' '.join(d['install'])}" if d["install"] else ""),
            f"Auto-login:  {({'yes': 'on', 'no': 'off'}).get(plan['autologin'] or '', 'left as it is')}",
        ]
    else:
        lines.append(f"Role:        print server only; display manager {plan['dm_now'] or 'none'} "
                     "(--remove-desktop clears a desktop this script installed)")
    lines += [
        f"Printer:     USB {plan['usb'] or 'not on green yet'}; queue {plan['queue'] or 'none'} "
        "(raw; the laptop runs the driver)",
        f"Firewall:    ufw {'on' if plan['ufw'] else 'off'}; SSH and :{CUPS_PORT} from {plan['lan']} only"
        + (f" (laptop {plan['client']} is inside)" if plan.get("client") else "")
        + (f"; other listeners {plan['others']}" if plan["others"] else ""),
        "",
    ]
    if laptop == "skip":
        lines += ["On the laptop, first:", f"      {QUEUE} queue left as it is"]
    else:
        lines.append("On the laptop, first (one sudo prompt):")
        lines += [f"      sudo: {l}" for l in LAPTOP[laptop].splitlines()[1:]]
    lines += ["", "On green, as root (one sudo prompt):"]
    for i, s in enumerate(steps, 1):
        lines.append(f"  {i:2}. {s.title}")
        lines += [f"        {l}" for l in s.script.splitlines()]
    return "\n".join(lines)


def gather(ssh) -> dict[str, str]:
    r = ssh.run("bash -s", input=facts_script().encode())
    if r.returncode != 0 and not r.stdout:
        raise ProvisionError(f"can't read green: {r.stderr.decode(errors='replace').strip()}")
    return parse_facts(r.stdout.decode(errors="replace"))


def _send(ssh, script: str, extra: dict[str, bytes] | None = None) -> str:
    remote = f"~/{REMOTE_DIR}/bundle"
    r = ssh.run(f"rm -rf {remote} && mkdir -p {remote} && chmod 700 ~/{REMOTE_DIR} && tar -xf - -C {remote}",
                input=bundle(script, extra))
    if r.returncode != 0:
        raise ProvisionError(f"couldn't copy the setup to green: {r.stderr.decode(errors='replace').strip()}")
    return remote


def remove_desktop(a, ssh, say=print) -> int:
    facts = gather(ssh)
    top = purge_targets(facts)
    simulation = ""
    if top:  # apt's own dry run, as the user: what the purge and its autoremove would take
        r = ssh.run("LC_ALL=C apt-get -s -q purge --autoremove " + " ".join(top))
        if r.returncode != 0:
            raise ProvisionError(f"apt's dry run on green failed: {r.stderr.decode(errors='replace').strip()}")
        simulation = r.stdout.decode(errors="replace")
    plan = removal_plan(facts, simulation)
    steps = removal_steps(plan)
    if a.dry_run:
        say(describe_removal(plan, steps))
        say("\n(dry run: nothing changed)")
        return 0
    u = ssh.run("bash -s", input=REMOVE_LAUNCHER.encode())
    say(u.stdout.decode(errors="replace").strip() or u.stderr.decode(errors="replace").strip())
    if not steps:
        say("green: no desktop from this script and no auto-login file; nothing to remove")
        return 0
    script = _sub(HEADER, {"@USER@": plan["user"], "@REMOTE@": REMOTE_DIR}) + "\n".join(
        f"\nstep {shlex.quote(s.title)}\n{s.script}" for s in steps) + '\necho\necho "green: desktop removed"\n'
    remote = _send(ssh, script, {"ours.txt": ("\n".join(plan["ours"]) + "\n").encode()})
    say("green: removing the desktop as root (sudo asks for green's password)")
    rc = ssh.run(f"sudo bash {remote}/root.sh", tty=True).returncode
    if rc != 0:
        raise ProvisionError(f"green's cleanup stopped (exit {rc}); its output above says why")
    return 0


def setup(a, ssh, say=print, run=subprocess.run) -> int:
    if not a.dry_run and ON_AIR.exists():
        raise ProvisionError("a show is on air (inventory/_live/ON_AIR); nothing was changed")
    if a.remove_desktop:  # green only; the laptop's queue is not touched
        return remove_desktop(a, ssh, say)
    if a.laptop == "usb":  # the cable is back on the laptop; green is not touched
        if a.dry_run:
            say("On the laptop, as root:\n" + LAPTOP["usb"] + "\n\n(dry run: nothing changed)")
            return 0
        return point_laptop("usb", say, run)
    plan = make_plan(gather(ssh), a.autologin, a.lan, a.close_others, a.desktop)
    steps = root_steps(plan)
    if a.dry_run:
        say(describe(plan, steps, a.laptop))
        say("\n(dry run: nothing changed)")
        return 0
    # The laptop first: with the cable on green its queue points at nothing,
    # so labels start working the moment green's queue is up.
    lrc = 0 if a.laptop == "skip" else point_laptop("green", say, run)
    remote = _send(ssh, root_script(plan, steps))
    say("green: running the setup as root (sudo asks for green's password)")
    rc = ssh.run(f"sudo bash {remote}/root.sh", tty=True).returncode
    if rc not in (0, EXIT_NO_PRINTER, EXIT_FIREWALL_OFF):
        raise ProvisionError(f"green's setup stopped (exit {rc}); re-run once the cause is fixed")
    if rc == EXIT_NO_PRINTER:
        say("green: plug the QL-1100 into green, power it on, run this again")
        return rc
    if a.laptop != "skip":
        say(f"laptop -> green: {QUEUE} answers" if green_answers(run) else
            f"laptop -> green: green's {QUEUE} doesn't answer on :{CUPS_PORT} yet; labels wait in the laptop's queue")
    return rc or lrc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default=HOST, help="ssh alias for green (default: green, from ~/.ssh/config)")
    p.add_argument("--laptop", choices=["green", "usb", "skip"], default="green",
                   help="where the laptop's QL-1100 queue prints (default: green)")
    p.add_argument("--lan", help="the LAN to allow, e.g. 192.168.1.0/24 (default: green's own)")
    p.add_argument("--close-others", action="store_true",
                   help="switch ufw on even though green listens on other ports")
    p.add_argument("--desktop", action="store_true",
                   help="also a desktop and Firefox (off: green is print-only for now)")
    p.add_argument("--autologin", choices=["yes", "no"], help="with --desktop: log green's desktop in at boot")
    p.add_argument("--remove-desktop", action="store_true",
                   help="purge the desktop packages an earlier --desktop run installed (apt history), "
                        "remove auto-login, boot to the console")
    p.add_argument("--dry-run", action="store_true", help="read green and print every step; change nothing")
    return p


def parse_args(argv=None) -> argparse.Namespace:
    p = parser()
    a = p.parse_args(argv)
    if a.remove_desktop and (a.desktop or a.autologin):
        p.error("--remove-desktop can't be combined with --desktop or --autologin")
    if a.autologin and not a.desktop:
        p.error("--autologin needs --desktop (green is print-only; --remove-desktop clears auto-login)")
    return a


class GreenSsh(Ssh):
    """provision.Ssh over IPv4 only. green.lan resolves to its IPv6 ULA first,
    and ufw here opens SSH to the IPv4 /24 only, so a v6 connection would be
    dropped; the LAN check also reads the laptop's address as IPv4."""

    def __init__(self, target: str):
        super().__init__(target)
        self.base += ["-o", "AddressFamily=inet"]


def main(argv=None) -> int:
    a = parse_args(argv)
    try:
        return setup(a, GreenSsh(a.host))
    except ProvisionError as e:
        print(f"green: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
