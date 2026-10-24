#!/usr/bin/env python3
"""GREEN: set green up as the shipping station, from the laptop over SSH.

    python tools/green_setup.py --dry-run          # read green, print the plan and its root script
    python tools/green_setup.py                    # apply: a sudo prompt here, then one on green
    python tools/green_setup.py --autologin yes    # also log green's desktop in at boot
    python tools/green_setup.py --laptop usb       # cable back on the laptop: print by USB again

Green is a thin client plus a print server. The heavy work, the eBay API
keys and refresh tokens, the R2 keys, config.yaml and the ledgers stay on
the laptop. Green holds only a browser login (Seller Hub in Firefox). Every
connection opens from the laptop (`ssh green`, the key
tools/green_test.sh uses), so green holds no way back in.

On green, as root (sudo asks for green's password once), printer first:
  - the Brother QL-1100 driver, the same ql1100pdrv 2.1.4 .deb the laptop
    runs (checked by SHA-256), and the i386 runtime it needs;
  - a QL-1100 queue on green's USB with the laptop's 4x6 setting, shared;
  - CUPS shared to the local subnet only (cupsctl --share-printers
    --no-remote-any --no-remote-admin) and ufw opening 631/tcp and SSH to
    green's own /24 only. ufw is not switched on over other listeners
    unless --close-others says so;
  - last, as it is the slow download: a desktop only if green has none,
    XFCE with LightDM (LXQt under 2 GB of RAM), plus Firefox. A desktop
    that is already there is kept. Auto-login only with --autologin yes
    (LightDM); --autologin no removes it.
On green, as the user: a "Ship" launcher for the /ship/box page on green's
own 127.0.0.1:8770 (the web app stays localhost-only, #311).
On the laptop, first (sudo asks once): the QL-1100 queue keeps its name,
so tools/print_label.sh is unchanged, and forwards raw to green's queue,
which runs the Brother driver. Jobs wait and retry while green is down.
It is refused while the printer is still on the laptop's own USB.

Re-running is safe: each step checks before it changes anything. Nothing
is applied while inventory/_live/ON_AIR exists; --dry-run always works.
RUN.md, "Green: the shipping station", has the why. Stdlib only.
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
PAGE = "103x164"  # 4x6 shipping labels, as on the laptop (lpoptions -p QL-1100)
CUPS_PORT = 631
SHIP_URL = "http://127.0.0.1:8770/ship/box"  # webapp/server.py DEFAULT_PORT
GREEN_URI = f"ipp://{HOST}.lan:{CUPS_PORT}/printers/{QUEUE}"
REMOTE_DIR = ".cache/pops-green"
DRIVER_DEB = Path.home() / "Downloads" / "ql1100pdrv-2.1.4-0.i386.deb"
DRIVER_VERSION = "2.1.4"
DRIVER_SHA256 = "4de864edec5c3d5bbcbc47c77d3fc90bae3b19b72a409d04f178e89437cfe2f3"
PPD = "/opt/brother/PTouch/ql1100/cupswrapper/brother_ql1100_printer_en.ppd"
ON_AIR = REPO / "inventory" / "_live" / "ON_AIR"
EXIT_NO_PRINTER, EXIT_FIREWALL_OFF = 3, 4

# The Brother driver is a Perl filter around i386 binaries, fed PostScript by
# CUPS (pdftops from cups-filters, gs and pdf2ps from ghostscript).
BASE_PKGS = ["cups", "cups-filters", "ghostscript", "perl", "libc6:i386", "ufw"]
DESKTOPS = {  # dpkg package -> name; the first one installed is "green's desktop"
    "xfce4": "XFCE", "lxqt-core": "LXQt", "gnome-shell": "GNOME", "plasma-desktop": "KDE Plasma",
    "cinnamon": "Cinnamon", "mate-desktop-environment-core": "MATE", "lxde-core": "LXDE",
}
NEW_DESKTOP = {"xfce": ["xfce4", "xfce4-terminal", "lightdm", "xorg"],
               "lxqt": ["lxqt-core", "lightdm", "xorg"]}
LXQT_BELOW_MB = 2048
# Rough budget in MB for desktop + Firefox + a test run at once. An estimate
# for the dry run, not a measurement; only the 900 MB test peak is measured
# (RUN.md, 2026-10-24).
BUDGET = {"system, CUPS, serve": 550, "Firefox (Seller Hub, R2 sheet, ship page)": 1200,
          "green_test.sh peak": 900}
DESKTOP_MB = {"XFCE": 450, "LXQt": 350, "LXDE": 300, "MATE": 500, "Cinnamon": 700,
              "GNOME": 1000, "KDE Plasma": 900}

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
"""


def facts_script() -> str:
    pkgs = BASE_PKGS + list(DESKTOPS) + ["lightdm", "firefox", "firefox-esr", "ql1100pdrv:i386"]
    return _sub(FACTS, {"@PKGS@": " ".join(pkgs), "@QUEUE@": QUEUE})


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


def make_plan(facts: dict[str, str], autologin: str | None = None, lan: str | None = None,
              close_others: bool = False) -> dict:
    """Everything green's steps need, from its facts and the flags (pure)."""
    user = facts.get("user", "")
    if not SAFE_USER.match(user):
        raise ProvisionError(f"unusual user name on green: {user!r}")
    os_txt = facts.get("os", "")
    osv = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', os_txt, re.M))
    family = {osv.get("ID", "")} | set(osv.get("ID_LIKE", "").split())
    if "debian" not in family and "ubuntu" not in family:
        raise ProvisionError(f"green runs {osv.get('PRETTY_NAME') or 'an unknown OS'}; "
                             "this script knows Debian and Ubuntu (apt, dpkg)")
    if facts.get("arch") not in ("amd64", "i386"):
        raise ProvisionError(f"green is {facts.get('arch') or 'an unknown arch'}; Brother's "
                             "QL-1100 driver is i386 and runs on amd64 or i386 only")
    lan = check_private_lan(lan) if lan else lan_cidr(facts.get("route", ""), facts.get("addr", ""))
    client = facts.get("client") or None
    if client:
        client = str(ipaddress.ip_address(client))
    check_lan(lan, client)

    have = installed(facts.get("pkgs", ""))
    mem = int(facts.get("mem") or 0)
    dm = facts.get("dm", "").strip()
    existing = next((name for pkg, name in DESKTOPS.items() if pkg in have), None)
    if existing:
        desktop = {"name": existing, "install": [], "why": f"keep {existing}, already on green"}
        if not dm:
            desktop["install"] = ["lightdm"]
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

    budget = dict(BUDGET)
    budget[f"desktop ({desktop['name']})"] = DESKTOP_MB.get(desktop["name"], 800)
    need = sum(budget.values())
    verdict = ("fits" if mem >= need + 512 else
               "tight: don't run green_test.sh while shipping" if mem >= need else
               "does not fit: a test run beside Firefox will swap")
    others = listeners(facts.get("listen", ""))
    return {
        "user": user, "os": osv.get("PRETTY_NAME"), "lan": lan, "client": client,
        "mem": mem, "swap": int(facts.get("swap") or 0), "cpu": facts.get("cpu"),
        "gpu": facts.get("gpu", ""), "dm": dm or ("lightdm" if desktop["install"] else ""),
        "dm_now": dm, "dm_active": facts.get("dm_active", ""), "sessions": facts.get("sessions", ""),
        "desktop": desktop, "firefox": firefox, "autologin": autologin,
        "base_missing": [p for p in BASE_PKGS if p not in have],
        "driver": have.get("ql1100pdrv:i386"), "usb": facts.get("usb", ""),
        "queue": facts.get("queue", ""), "ufw": facts.get("ufw", "").strip().lower() == "yes",
        "others": others, "close_others": close_others,
        "tailscale": bool(facts.get("tailscale")), "budget": budget, "need": need,
        "verdict": verdict,
    }


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
    d = plan["desktop"]
    steps = [
        Step("Packages: CUPS, Ghostscript, the driver's i386 runtime, ufw",
             r"""dpkg --print-foreign-architectures | grep -qx i386 || dpkg --add-architecture i386
NEED=$(missing @BASE@)
if [ -n "$NEED" ]; then apt-get update -q; apt-get install -y -q --no-install-recommends $NEED
else echo "already installed"; fi"""),
        Step(f"Brother QL-1100 driver {DRIVER_VERSION} (the laptop's .deb, SHA-256 checked)",
             r"""if dpkg-query -W -f='${Version} ${db:Status-Abbrev}' ql1100pdrv:i386 2>/dev/null | grep -q '^@DRVVER@ ii'; then
  echo "driver @DRVVER@ already installed"
else
  [ -s "$B/ql1100pdrv.deb" ] || { echo "the driver .deb didn't arrive"; exit 1; }
  echo "@SHA@  $B/ql1100pdrv.deb" | sha256sum -c --quiet
  dpkg -i "$B/ql1100pdrv.deb"
fi"""),
        Step(f"Queue {QUEUE}: green's USB, {PAGE} labels, shared",
             r"""URI=$(lpinfo --include-schemes usb -v 2>/dev/null | awk '$2 ~ /^usb:\/\/Brother\/QL-1100/ {print $2; exit}')
if [ -z "$URI" ]; then
  echo "The QL-1100 isn't on green's USB (cable in, power on?). Queue not set; everything else was."
  NOPRINTER=1
else
  lpadmin -p @QUEUE@ -E -v "$URI" -P @PPD@ -o PageSize=@PAGE@ -o printer-is-shared=true
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
        # The desktop comes last: it is the slow download, and labels shouldn't wait for it.
        Step(f"Desktop: {d['name']} " + ("(already there)" if not d["install"] else "and Firefox, as needed"),
             r"""NEED=$(missing @DESKTOP@)
if [ -n "$NEED" ]; then apt-get update -q; apt-get install -y -q $NEED; else echo "already installed"; fi
[ "$(systemctl get-default)" = graphical.target ] || systemctl set-default graphical.target
systemctl is-active --quiet display-manager || systemctl start display-manager || true"""),
    ]
    if plan["autologin"] == "yes":
        steps.append(Step(f"Auto-login: {plan['user']} on LightDM", r"""mkdir -p /etc/lightdm/lightdm.conf.d
printf '[Seat:*]\nautologin-user=%s\nautologin-user-timeout=0\n' "$U" > /etc/lightdm/lightdm.conf.d/50-pops-autologin.conf
echo "next boot logs $U in" """))
    elif plan["autologin"] == "no":
        steps.append(Step("Auto-login: off", "rm -f /etc/lightdm/lightdm.conf.d/50-pops-autologin.conf"))
    subs = {"@BASE@": " ".join(BASE_PKGS), "@DESKTOP@": " ".join(d["install"]),
            "@DRVVER@": DRIVER_VERSION, "@SHA@": DRIVER_SHA256, "@QUEUE@": QUEUE, "@PPD@": PPD,
            "@PAGE@": PAGE}
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


def user_script(plan: dict) -> str:
    """Runs on green as the user, no sudo: the Ship launcher (menu and desktop)."""
    ff = plan["firefox"]
    return f"""set -eu
A="$HOME/.local/share/applications"; mkdir -p "$A"
cat > "$A/pops-ship.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=Ship
Comment=Pop's Games: box size and weight for each order
Exec={ff} {SHIP_URL}
Icon={ff}
Terminal=false
Categories=Office;
EOF
D=$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop"); mkdir -p "$D"
install -m 755 "$A/pops-ship.desktop" "$D/pops-ship.desktop"
echo "Ship launcher: $A/pops-ship.desktop and $D/"
"""


def bundle(script: str, deb: Path | None) -> bytes:
    files = {"root.sh": script.encode()}
    if deb:
        files["ql1100pdrv.deb"] = deb.read_bytes()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# --- the laptop's queue ------------------------------------------------------------
LAPTOP = {
    "green": f"""set -euo pipefail
lpadmin -p {QUEUE} -E -v {GREEN_URI} -m raw -o printer-is-shared=false -o printer-error-policy=retry-job
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


def green_answers(run=subprocess.run) -> bool:
    r = run(["lpstat", "-h", f"{HOST}.lan:{CUPS_PORT}", "-p", QUEUE], capture_output=True, text=True)
    return r.returncode == 0 and f"printer {QUEUE}" in r.stdout


def laptop_has_printer(run=subprocess.run) -> bool:
    r = run(["lsusb"], capture_output=True, text=True)
    return bool(re.search(r"\b04f9:|brother", r.stdout or "", re.I))


def point_laptop(mode: str, say=print, run=subprocess.run) -> int:
    """Point the laptop's QL-1100 queue at green (or back at its USB). Going
    to green doesn't wait for green: with the cable on green the laptop's
    queue points at nothing, and a forward that retries is no worse."""
    uri, model = laptop_queue(run)
    if mode == "green":
        if uri == GREEN_URI and "raw" in model.lower():
            say(f"laptop: {QUEUE} already forwards to green")
            return 0
        if laptop_has_printer(run):
            raise ProvisionError(f"the {QUEUE} is still on this laptop's USB; move the cable to green, "
                                 "or run with --laptop skip. The laptop's queue was left as it was")
    elif uri.startswith("usb://") and "brother" in model.lower():
        say(f"laptop: {QUEUE} already prints by USB")
        return 0
    say(f"laptop: {QUEUE} -> {'green' if mode == 'green' else 'USB'} (sudo asks for the laptop password)")
    return run(["sudo", "bash", "-c", LAPTOP[mode]]).returncode


# --- the dry run ---------------------------------------------------------------------
def describe(plan: dict, steps: list[Step], laptop: str) -> str:
    d = plan["desktop"]
    lines = [
        f"Green OS:    {plan['os']}; {plan['cpu']}; {plan['mem']} MB RAM, {plan['swap']} MB swap",
        f"GPU:         {plan['gpu'] or 'none listed'}",
        f"Display:     manager {plan['dm_now'] or 'none yet'} ({plan['dm_active'] or '?'}), "
        f"sessions: {plan['sessions'] or 'none'}",
        f"Desktop:     {d['why']}" + (f"; installs {' '.join(d['install'])}" if d["install"] else ""),
        f"Auto-login:  {({'yes': 'on', 'no': 'off'}).get(plan['autologin'] or '', 'left as it is')}",
        f"RAM budget:  {plan['need']} MB estimated at once ("
        + ", ".join(f"{k} {v}" for k, v in plan["budget"].items()) + f"): {plan['verdict']}",
        f"Printer:     USB {plan['usb'] or 'not on green yet'}; driver {plan['driver'] or 'not installed'}; "
        f"queue {plan['queue'] or 'none'}",
        f"Firewall:    ufw {'on' if plan['ufw'] else 'off'}; SSH and :{CUPS_PORT} from {plan['lan']} only"
        + (f" (laptop {plan['client']} is inside)" if plan.get("client") else "")
        + (f"; other listeners {plan['others']}" if plan["others"] else ""),
        "",
        "On green, as root (one sudo prompt):",
    ]
    for i, s in enumerate(steps, 1):
        lines.append(f"  {i:2}. {s.title}")
        lines += [f"        {l}" for l in s.script.splitlines()]
    lines += ["", "On green, as the user:", f"      Ship launcher -> {plan['firefox']} {SHIP_URL}"]
    head = ["On the laptop, first (one sudo prompt):"]
    if laptop == "skip":
        head.append(f"      {QUEUE} queue left as it is")
    else:
        head += [f"      sudo: {l}" for l in LAPTOP[laptop].splitlines()[1:]]
    i = lines.index("On green, as root (one sudo prompt):")
    return "\n".join(lines[:i] + head + [""] + lines[i:])


def gather(ssh) -> dict[str, str]:
    r = ssh.run("bash -s", input=facts_script().encode())
    if r.returncode != 0 and not r.stdout:
        raise ProvisionError(f"can't read green: {r.stderr.decode(errors='replace').strip()}")
    return parse_facts(r.stdout.decode(errors="replace"))


def setup(a, ssh, say=print, run=subprocess.run) -> int:
    if not a.dry_run and ON_AIR.exists():
        raise ProvisionError("a show is on air (inventory/_live/ON_AIR); nothing was changed")
    if a.laptop == "usb":  # the cable is back on the laptop; green is not touched
        if a.dry_run:
            say("On the laptop, as root:\n" + LAPTOP["usb"] + "\n\n(dry run: nothing changed)")
            return 0
        return point_laptop("usb", say, run)
    plan = make_plan(gather(ssh), a.autologin, a.lan, a.close_others)
    steps = root_steps(plan)
    if a.dry_run:
        say(describe(plan, steps, a.laptop))
        say("\n(dry run: nothing changed)")
        return 0
    deb = None
    if plan["driver"] != DRIVER_VERSION:
        if not DRIVER_DEB.is_file():
            raise ProvisionError(f"green needs the driver and {DRIVER_DEB} isn't here")
        deb = DRIVER_DEB
    # The laptop first: with the cable on green its queue points at nothing,
    # so labels start working the moment green's queue is up.
    lrc = 0 if a.laptop == "skip" else point_laptop("green", say, run)
    remote = f"~/{REMOTE_DIR}/bundle"
    r = ssh.run(f"rm -rf {remote} && mkdir -p {remote} && chmod 700 ~/{REMOTE_DIR} && tar -xf - -C {remote}",
                input=bundle(root_script(plan, steps), deb))
    if r.returncode != 0:
        raise ProvisionError(f"couldn't copy the setup to green: {r.stderr.decode(errors='replace').strip()}")
    say("green: running the setup as root (sudo asks for green's password)")
    rc = ssh.run(f"sudo bash {remote}/root.sh", tty=True).returncode
    if rc not in (0, EXIT_NO_PRINTER, EXIT_FIREWALL_OFF):
        raise ProvisionError(f"green's setup stopped (exit {rc}); re-run once the cause is fixed")
    u = ssh.run("bash -s", input=user_script(plan).encode())
    say(u.stdout.decode(errors="replace").strip() or u.stderr.decode(errors="replace").strip())
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
    p.add_argument("--autologin", choices=["yes", "no"], help="log green's desktop in at boot (LightDM)")
    p.add_argument("--laptop", choices=["green", "usb", "skip"], default="green",
                   help="where the laptop's QL-1100 queue prints (default: green)")
    p.add_argument("--lan", help="the LAN to allow, e.g. 192.168.1.0/24 (default: green's own)")
    p.add_argument("--close-others", action="store_true",
                   help="switch ufw on even though green listens on other ports")
    p.add_argument("--dry-run", action="store_true", help="read green and print every step; change nothing")
    return p


class GreenSsh(Ssh):
    """provision.Ssh over IPv4 only. green.lan resolves to its IPv6 ULA first,
    and ufw here opens SSH to the IPv4 /24 only, so a v6 connection would be
    dropped; the LAN check also reads the laptop's address as IPv4."""

    def __init__(self, target: str):
        super().__init__(target)
        self.base += ["-o", "AddressFamily=inet"]


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    try:
        return setup(a, GreenSsh(a.host))
    except ProvisionError as e:
        print(f"green: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
