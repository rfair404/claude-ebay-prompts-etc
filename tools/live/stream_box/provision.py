#!/usr/bin/env python3
"""STREAM BOX: set up the second machine that runs OBS and the cameras, from the laptop over SSH.

    python tools/live/stream_box/provision.py --find streambox --copy-keys
    python tools/live/stream_box/provision.py 192.168.1.50 --copy-keys
    python tools/live/stream_box/provision.py 192.168.1.50 --dry-run
    python tools/live/stream_box/provision.py --dry-run --gpu nvidia --lan 192.168.1.0/24

The box is optional. This script changes the box and nothing on the
laptop: not its OBS, its scene collection, its Flatpak or its
v4l2loopback. On the laptop it only reads (the OBS templates in
tools/live/obs/, and with --copy-keys the laptop's Ebay profile) and
writes one file, the box's obs-websocket password (0600, outside the repo,
default PASSWORD_FILE). With no box configured, every show tool still runs
OBS on the laptop at 127.0.0.1:4455, exactly as before.

What it does on the box (docs/stream-box.md has the why):
  - reads the box (lspci, network, whether OBS runs) with no sudo;
  - GPU driver chosen from lspci: NVIDIA's proprietary one (ubuntu-drivers)
    or Mesa VAAPI for Intel/AMD; then a 1 s test encode decides the encoder;
  - Flatpak OBS, gphoto2, ffmpeg, v4l2loopback with /dev/video10 (D5100)
    and /dev/video11 (TG-7) at boot, PipeWire, X + openbox + auto-login so
    OBS has a screen;
  - firewall: SSH and obs-websocket only from the box's own /24;
  - OBS config (box_apply.py, run on the box): Ebay profile with the GPU
    encoder for eBay and Twitch, the eBay Live scene collection,
    obs-websocket on with a password generated on the box;
  - reboots once if a new driver needs it, waits, and finishes;
  - copies the password back into PASSWORD_FILE and checks OBS answers.

Re-running is safe: every step checks before it changes anything, and the
whole run refuses while OBS is running on the box (--stop-obs stops it
first, only if nothing is live). It asks for the box's sudo password once
per pass (the one set in the installer), usually twice: before and after
the reboot.

Try the box from the laptop without changing anything else:
    OBS_WS_HOST=<box> OBS_CONFIG_DIR=~/.config/pops-stream-box/obs-config \\
        python tools/live/obsctl.py status

Stdlib only.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import io
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
TEMPLATES = REPO / "tools" / "live" / "obs"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import box_apply  # noqa: E402
import obsctl  # noqa: E402

REMOTE_DIR = ".cache/pops-stream-box"  # under the box user's home
WS_PORT = 4455
TG7_CAMERA, TG7_RTP_PORT = "192.168.0.10", 40000  # tools/live/tg7cam CAM, DEFAULT_PORT
EXIT_REBOOT, EXIT_OBS_RUNNING, EXIT_UNSUPPORTED = 100, 3, 2
# v4l2loopback v0.15.4 ('v0.15.4^{}' on 2026-10-22); its dkms.conf says 0.15.4
V4L2LOOPBACK_COMMIT = "0f9ee86760b7f2bea174b7e3e7a1d38845da0ab4"
MAX_PASSES = 3
REBOOT_WAIT = 420  # seconds for the box to come back after a reboot
VENDORS = {"10de": "nvidia", "1002": "amd", "8086": "intel"}
PREFERENCE = ("nvidia", "amd", "intel")  # a discrete card wins over the board's own
SAFE_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SAFE_NODE = re.compile(r"^/dev/dri/renderD\d+$")


class ProvisionError(RuntimeError):
    pass


def password_file() -> Path:
    """Where the box's obs-websocket password lands on the laptop. Laid out
    like an OBS config dir, so OBS_CONFIG_DIR=<that>/obs-config lets
    obsctl.py find it today with no code change."""
    return (Path.home() / ".config/pops-stream-box/obs-config/plugin_config"
            / "obs-websocket/config.json")


def laptop_obs_dir() -> Path:
    return Path(os.environ.get("OBS_CONFIG_DIR") or obsctl.DEFAULT_CONFIG_DIR)


# --- reading the box (pure) ------------------------------------------------------
@dataclass
class Gpu:
    vendor: str  # nvidia | amd | intel
    name: str
    slot: str  # 0000:01:00.0, as sysfs names it


LSPCI_DISPLAY = re.compile(
    r"^(?P<slot>\S+) (?P<cls>.+?) \[(?P<code>03[0-9a-f]{2})\]: (?P<name>.+) "
    r"\[(?P<vendor>[0-9a-f]{4}):(?P<device>[0-9a-f]{4})\]", re.I)


def _full_slot(slot: str) -> str:
    return slot if slot.count(":") == 2 else f"0000:{slot}"


def detect_gpu(lspci: str) -> Gpu | None:
    """The GPU to encode on, from `lspci -nn`: display-class devices (03xx)
    from NVIDIA, AMD or Intel, preferring a discrete card over the board's
    own Intel graphics. None if there's no such device (a server BMC's
    ASPEED or Matrox chip can't encode)."""
    found = []
    for line in lspci.splitlines():
        m = LSPCI_DISPLAY.match(line.strip())
        if m and m["vendor"].lower() in VENDORS:
            found.append(Gpu(VENDORS[m["vendor"].lower()], m["name"].strip(), _full_slot(m["slot"])))
    found.sort(key=lambda g: PREFERENCE.index(g.vendor))
    return found[0] if found else None


def render_node_for(slot: str, drm: str) -> str | None:
    """/dev/dri/renderD* of the GPU in `slot`, from lines "renderD128
    0000:01:00.0". With two GPUs, VAAPI must open the right one."""
    for line in drm.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == slot:
            return f"/dev/dri/{parts[0]}"
    return None


def lan_cidr(route: str, addr: str) -> str:
    """The box's LAN as a CIDR: the network of the address on the interface
    that holds the default route. Refuses anything that isn't a private
    network of /16 or narrower, so the firewall never opens to the internet."""
    m = re.search(r"\bdev (\S+)", route)
    if not m:
        raise ProvisionError("the box has no default route; is its network cable in?")
    dev = m.group(1)
    for line in addr.splitlines():
        fields = line.split()
        if len(fields) > 3 and fields[1] == dev and fields[2] == "inet":
            return check_private_lan(ipaddress.ip_interface(fields[3]).network)
    raise ProvisionError(f"no IPv4 address on {dev}")


def check_private_lan(net) -> str:
    """The firewall opens SSH and obs-websocket to this network, so it must
    be a private LAN of /16 or narrower: never the internet, whether it was
    read from the box or typed as --lan."""
    try:
        net = ipaddress.ip_network(net)
    except ValueError as e:
        raise ProvisionError(f"--lan: {e}") from e
    if not net.is_private or net.prefixlen < 16:
        raise ProvisionError(f"{net} isn't a private LAN of /16 or narrower; the firewall only "
                             "opens to one (e.g. --lan 192.168.1.0/24)")
    return str(net)


def check_lan(lan: str, client: str | None) -> None:
    """The firewall only lets the LAN reach SSH. If the laptop's own address
    isn't in it, enabling it would lock the laptop out: refuse instead."""
    if client is None:
        return
    if ipaddress.ip_address(client) not in ipaddress.ip_network(lan):
        raise ProvisionError(f"the laptop ({client}) isn't in the box's LAN {lan}; the firewall "
                             "would lock it out. Pass --lan with a network that holds both.")


def parse_facts(text: str) -> dict[str, str]:
    """The facts script's output, split on its "##name" lines."""
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        if line.startswith("##"):
            cur = out.setdefault(line[2:].strip(), [])
        elif cur is not None:
            cur.append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


FACTS = r"""
echo '##os'; cat /etc/os-release
echo '##lspci'; lspci -nn 2>/dev/null || true
echo '##drm'; for d in /sys/class/drm/renderD*; do [ -e "$d/device" ] && echo "$(basename "$d") $(basename "$(readlink -f "$d/device")")"; done; true
echo '##route'; ip -o -4 route show default
echo '##addr'; ip -o -4 addr show
echo '##client'; echo "${SSH_CLIENT%% *}"
echo '##wifi'; for w in /sys/class/net/*/wireless; do [ -e "$w" ] && basename "$(dirname "$w")"; done; true
echo '##obs'; pgrep -x obs >/dev/null && echo running || echo stopped
echo '##boot'; cat /proc/sys/kernel/random/boot_id
"""


def make_plan(facts: dict[str, str], user: str, port: int = WS_PORT, lan: str | None = None,
              gpu: str | None = None) -> dict:
    """Everything the box's steps need, from its facts and the overrides."""
    if not SAFE_USER.match(user):
        raise ProvisionError(f"unusual box user name {user!r}")
    if gpu:
        g = Gpu(gpu, f"{gpu} (from --gpu)", "") if gpu != "none" else None
    else:
        g = detect_gpu(facts.get("lspci", ""))
    lan = check_private_lan(lan) if lan else lan_cidr(facts.get("route", ""), facts.get("addr", ""))
    client = facts.get("client") or None
    if client:
        client = str(ipaddress.ip_address(client))
    check_lan(lan, client)
    node = render_node_for(g.slot, facts.get("drm", "")) if g and g.slot else None
    if node and not SAFE_NODE.match(node):
        node = None
    os_name = re.search(r'^PRETTY_NAME="?([^"\n]*)', facts.get("os", ""), re.M)
    return {"user": user, "port": int(port), "lan": lan, "client": client,
            "vendor": g.vendor if g else "none", "gpu_name": g.name if g else None,
            "render_node": node or (box_apply.DEFAULT_RENDER_NODE if g and g.vendor != "nvidia" else None),
            "wifi": facts.get("wifi", "").split(), "os": os_name.group(1) if os_name else None}


# --- the box's root steps (pure) -------------------------------------------------
@dataclass
class Step:
    title: str
    script: str


HEADER = r"""#!/bin/bash
# Generated by tools/live/stream_box/provision.py. Runs on the stream box as root.
set -euo pipefail
U=@USER@
H=$(getent passwd "$U" | cut -d: -f6)
B="$H/@REMOTE@/bundle"
REBOOT=0
export DEBIAN_FRONTEND=noninteractive
as_user() { runuser -u "$U" -- env HOME="$H" "$@"; }
step() { printf '\n== %s\n' "$*"; }
if pgrep -x obs >/dev/null; then
  echo "OBS is running on this box, so nothing was changed. Quit it and re-run."; exit @EXIT_OBS@
fi
. /etc/os-release
ID_LIKE=${ID_LIKE:-}   # Debian has none, and set -u would stop on it
"""

APT = "apt-get install -y -q --no-install-recommends"

DRIVER = {
    "nvidia": rf"""case " $ID $ID_LIKE " in *" ubuntu "*) ;; *)
  echo "NVIDIA driver install is automated on Ubuntu only (this is $PRETTY_NAME)"; exit @EXIT_UNSUP@;; esac
{APT} ubuntu-drivers-common
if nvidia-smi -L >/dev/null 2>&1; then
  echo "NVIDIA driver loaded: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader)"
else
  BOOT=$(cat /proc/sys/kernel/random/boot_id); MARK=/var/lib/pops-stream-box/nvidia-installed
  if [ -f "$MARK" ] && [ "$(cat "$MARK")" != "$BOOT" ]; then
    echo "WARNING: the NVIDIA driver is installed but didn't load after a reboot (dmesg | grep -i nvidia)."
    echo "Carrying on: the test encode will fail and OBS will encode on the CPU."
  else
    if ! dpkg-query -W -f='${{Status}}\n' 'nvidia-driver-*' 2>/dev/null | grep -q 'install ok installed'; then
      ubuntu-drivers install
    fi
    mkdir -p /var/lib/pops-stream-box; echo "$BOOT" > "$MARK"
    echo "NVIDIA driver installed; it loads after a reboot"; REBOOT=1
  fi
fi""",
    "intel": rf"""{APT} vainfo i965-va-driver intel-media-va-driver-non-free \
  || {APT} vainfo i965-va-driver intel-media-va-driver""",
    "amd": rf"""{APT} vainfo mesa-va-drivers""",
    "none": r"""echo "no NVIDIA/AMD/Intel GPU found: OBS will encode on the CPU (x264)" """,
}

FLATPAK_EXTRA = {
    "nvidia": r"""if [ "$REBOOT" = 0 ]; then
  as_user flatpak update --user -y --noninteractive   # brings the GL.nvidia extension matching the driver
fi""",
    "intel": r"""BR=$(as_user flatpak info --user -m com.obsproject.Studio | sed -n 's#^runtime=.*/##p')
as_user flatpak install --user -y --noninteractive --or-update flathub "org.freedesktop.Platform.VAAPI.Intel//$BR" """,
}

T = "-hide_banner -loglevel error -f lavfi -i testsrc2=size=1280x720:rate=30 -frames:v 30"
PROBE = {
    "nvidia": rf"""  if ffmpeg {T} -c:v h264_nvenc -bf 0 -f null -; then NVENC=true; fi""",
    "vaapi": r"""  if ffmpeg -hide_banner -loglevel error -vaapi_device @RENDER@ -f lavfi \
      -i testsrc2=size=1280x720:rate=30 -frames:v 30 -vf format=nv12,hwupload -c:v h264_vaapi -bf 0 -f null -; then
    VAAPI=true
  fi""",
}


def root_steps(plan: dict, reboot_ok: bool = True, reset_scenes: bool = False) -> list[Step]:
    """The box's setup as ordered steps of bash, run as root. Each one checks
    before it changes, so the whole list is safe to run again."""
    v = plan["vendor"]
    steps = [
        Step("Check the system", r"""case " $ID $ID_LIKE " in *" ubuntu "*|*" debian "*) ;; *)
  echo "unsupported OS: $PRETTY_NAME (Ubuntu or Debian)"; exit @EXIT_UNSUP@;; esac
echo "$PRETTY_NAME, kernel $(uname -r)"
if command -v mokutil >/dev/null && mokutil --sb-state 2>/dev/null | grep -qi enabled; then
  echo "WARNING: Secure Boot is on. If /dev/video10 is missing at the end, turn it off in the firmware setup."
fi"""),
        Step("Packages: Flatpak, gphoto2, ffmpeg, v4l2 tools, PipeWire, firewall",
             rf"""apt-get update -q
{APT} flatpak gphoto2 ffmpeg v4l-utils v4l2loopback-utils dkms "linux-headers-$(uname -r)" \
  pciutils ufw python3 pipewire pipewire-pulse wireplumber x11-utils x11-xserver-utils"""),
        Step(f"GPU driver ({plan['gpu_name'] or 'none found'})", DRIVER[v]),
        Step("Display: X, openbox and auto-login, so OBS has a screen; never sleep",
             rf"""{APT} xserver-xorg xinit openbox lightdm lightdm-gtk-greeter dbus-x11
mkdir -p /etc/lightdm/lightdm.conf.d
cat > /etc/lightdm/lightdm.conf.d/50-pops-stream-box.conf <<EOF
[Seat:*]
autologin-user=$U
autologin-session=openbox
EOF
if [ "$(systemctl get-default)" != graphical.target ]; then systemctl set-default graphical.target; REBOOT=1; fi
systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null"""),
        Step("Virtual cameras: v4l2loopback, /dev/video10 (D5100) and /dev/video11 (TG-7) at boot",
             rf"""echo v4l2loopback > /etc/modules-load.d/v4l2loopback.conf
cat > /etc/modprobe.d/v4l2loopback.conf <<'EOF'
options v4l2loopback devices=2 exclusive_caps=1,1 video_nr=10,11 card_label="Nikon D5100","OM TG-7"
EOF
if ! modinfo v4l2loopback >/dev/null 2>&1; then
  apt-get install -y -q v4l2loopback-dkms || {{ echo "v4l2loopback-dkms didn't build; trying upstream 0.15.4"; apt-get remove -y -q v4l2loopback-dkms || true; }}
fi
if ! modinfo v4l2loopback >/dev/null 2>&1; then
  {APT} git
  [ -d /usr/src/v4l2loopback-0.15.4 ] || git clone -q --depth 1 -b v0.15.4 https://github.com/v4l2loopback/v4l2loopback /usr/src/v4l2loopback-0.15.4
  # a tag can move: build (as root, into the kernel) only the commit pinned in provision.py
  [ "$(git -C /usr/src/v4l2loopback-0.15.4 rev-parse HEAD)" = @V4L2_COMMIT@ ] \
    || {{ echo "v4l2loopback checkout doesn't match the pinned commit; not building it"; exit 1; }}
  dkms install v4l2loopback/0.15.4
fi
if [ ! -e /dev/video10 ] || [ ! -e /dev/video11 ]; then modprobe -r v4l2loopback 2>/dev/null || true; modprobe v4l2loopback; fi
[ -e /dev/video10 ] && [ -e /dev/video11 ] || {{ echo "/dev/video10 or /dev/video11 is missing"; exit 1; }}
echo "/dev/video10 and /dev/video11 present"
"""),
        Step("User: camera and GPU access; user services run without a login",
             r"""for g in video render plugdev; do if getent group "$g" >/dev/null; then usermod -aG "$g" "$U"; fi; done
loginctl enable-linger "$U" """),
        Step("OBS Studio from Flathub, per user (as on the laptop)",
             "as_user flatpak remote-add --user --if-not-exists flathub "
             "https://dl.flathub.org/repo/flathub.flatpakrepo\n"
             "as_user flatpak install --user -y --noninteractive --or-update flathub com.obsproject.Studio"
             + ("\n" + FLATPAK_EXTRA[v] if v in FLATPAK_EXTRA else "")),
        Step(f"Firewall: SSH and obs-websocket only from {plan['lan']}; TG-7 live view in",
             r"""ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow from @LAN@ to any port 22 proto tcp comment 'stream box: SSH from the LAN' >/dev/null
ufw allow from @LAN@ to any port @PORT@ proto tcp comment 'stream box: obs-websocket from the LAN' >/dev/null
ufw allow from @TG7@ to any port @TG7PORT@ proto udp comment 'stream box: TG-7 live view' >/dev/null
ufw --force enable >/dev/null
ufw status"""),
        Step("Test-encode on the GPU (decides the encoder)",
             "NVENC=false; VAAPI=false\nif [ \"$REBOOT\" = 0 ]; then\n"
             + {"nvidia": PROBE["nvidia"], "intel": PROBE["vaapi"], "amd": PROBE["vaapi"]}.get(v, "  :")
             + "\n  echo \"test encode: nvenc=$NVENC vaapi=$VAAPI\"\nfi\n"
             'printf \'{"nvenc": %s, "vaapi": %s}\\n\' "$NVENC" "$VAAPI" > "$B/probe.json"\n'
             'chown "$U:" "$B/probe.json"'),
        Step("Reboot if the new driver or display needs it",
             r"""if [ "$REBOOT" = 1 ]; then
  rm -f "$H/@REMOTE@/ready"   # OBS stays off after this reboot, so the next pass can finish
  if [ @REBOOT_OK@ = 1 ]; then
    echo "Rebooting the box; provision.py waits for it and finishes."
    systemd-run --on-active=3 --quiet systemctl reboot
  else
    echo "Reboot the box, then run provision.py again."
  fi
  exit @EXIT_REBOOT@
fi"""),
        Step("OBS config: Ebay profile, eBay Live scenes, obs-websocket (box_apply.py)",
             'as_user python3 "$B/box_apply.py" apply --bundle "$B"'
             + (" --reset-scenes" if reset_scenes else "")),
        Step("Start OBS in the auto-login session",
             r"""as_user touch "$H/@REMOTE@/ready"
systemctl restart display-manager 2>/dev/null || systemctl restart lightdm \
  || echo "the display didn't start; is a monitor (or an HDMI dummy plug) on the GPU?" """),
    ]
    subs = {"@USER@": plan["user"], "@LAN@": plan["lan"], "@PORT@": str(plan["port"]),
            "@TG7@": TG7_CAMERA, "@TG7PORT@": str(TG7_RTP_PORT),
            "@RENDER@": plan.get("render_node") or box_apply.DEFAULT_RENDER_NODE,
            "@REBOOT_OK@": "1" if reboot_ok else "0", "@REMOTE@": REMOTE_DIR,
            "@V4L2_COMMIT@": V4L2LOOPBACK_COMMIT,
            "@EXIT_REBOOT@": str(EXIT_REBOOT), "@EXIT_UNSUP@": str(EXIT_UNSUPPORTED)}
    return [Step(s.title, _sub(s.script, subs)) for s in steps]


def _sub(text: str, subs: dict[str, str]) -> str:
    for k, val in subs.items():
        text = text.replace(k, val)
    return text


def root_script(plan: dict, steps: list[Step]) -> str:
    head = _sub(HEADER, {"@USER@": plan["user"], "@REMOTE@": REMOTE_DIR,
                         "@EXIT_OBS@": str(EXIT_OBS_RUNNING)})
    body = "\n".join(f"\nstep {json.dumps(s.title)}\n{s.script}" for s in steps)
    return head + body + '\n\necho; echo "stream box: done"\n'


def bundle(plan: dict, script: str) -> bytes:
    """The files the box needs, as a tar built in memory (nothing is
    written on the laptop): root.sh, box_apply.py, plan.json and the OBS
    templates from tools/live/obs/."""
    files = {
        "root.sh": script.encode(),
        "box_apply.py": (HERE / "box_apply.py").read_bytes(),
        "plan.json": json.dumps(plan, indent=2).encode(),
        "basic.ini": (TEMPLATES / "profiles/Ebay/basic.ini").read_bytes(),
        "streamEncoder.json": (TEMPLATES / "profiles/Ebay/streamEncoder.json").read_bytes(),
        box_apply.COLLECTION_FILE: (TEMPLATES / "scenes" / box_apply.COLLECTION_FILE).read_bytes(),
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o700 if name.endswith(".sh") else 0o600, 0
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def describe(plan: dict, steps: list[Step], copy_keys: bool, pw_file: Path, script: bool) -> str:
    """The dry-run report: what was read, what would change, step by step."""
    expect = box_apply.choose_encoders(plan["vendor"], {"nvenc": True, "vaapi": True},
                                       plan.get("render_node"))
    lines = [
        f"Box OS:      {plan.get('os') or '(not read: offline plan)'}",
        f"GPU:         {plan['gpu_name'] or 'none found'} -> {plan['vendor']}"
        + (f" ({plan['render_node']})" if plan.get("render_node") else ""),
        f"Encoders:    eBay {expect['ebay_encoder']}, Twitch {expect['twitch']['FFVEncoder']}"
        " if the box's test encode passes, else x264",
        f"Firewall:    SSH and obs-websocket :{plan['port']} from {plan['lan']} only"
        + (f" (laptop {plan['client']} is inside)" if plan.get("client") else ""),
        f"Wi-Fi:       {', '.join(plan['wifi']) or 'none found (the TG-7 needs a Wi-Fi adapter on the box)'}",
        "",
        "On the box, as root (one sudo prompt per pass):",
    ]
    for i, s in enumerate(steps, 1):
        lines.append(f"  {i:2}. {s.title}")
        if script:
            lines += [f"        {l}" for l in s.script.splitlines()]
    lines += ["",
              "On the laptop:",
              f"  reads   {TEMPLATES.relative_to(REPO)}/ (profile and scene templates)"]
    if copy_keys:
        lines.append(f"  reads   {laptop_obs_dir() / 'basic/profiles/Ebay'} (stream keys, "
                     "sent to the box over SSH, never shown)")
    lines += [f"  writes  {pw_file} (0600): the box's obs-websocket password, and nothing else",
              "  (ssh itself records the box's host key in ~/.ssh/known_hosts on first contact)"]
    return "\n".join(lines)


# --- the laptop: reads, and the one write -----------------------------------------
def laptop_keys(obs_dir: Path) -> dict:
    """The laptop's eBay WHIP service and Twitch URL, read only. Values stay
    in memory and go to the box over SSH stdin; nothing prints them."""
    prof = obs_dir / "basic/profiles/Ebay"
    payload: dict = {}
    try:
        service = json.loads((prof / "service.json").read_text())
        if service.get("settings", {}).get("server"):
            payload["service"] = service
    except (OSError, ValueError):
        pass
    try:
        ffurl = box_apply.parse_ini((prof / "basic.ini").read_text()).get("AdvOut", {}).get("FFURL")
    except OSError:
        ffurl = None
    if ffurl:
        payload["ffurl"] = ffurl
    return payload


def check_local_target(path: Path) -> Path:
    """The password file must sit outside the repo and outside the laptop's
    own OBS config: that config is the fallback and this never touches it.
    The real Flatpak config is always guarded, also when OBS_CONFIG_DIR
    points somewhere else (e.g. at the box's folder, as docs/stream-box.md
    shows; that folder itself is where the password file belongs)."""
    p = path.expanduser().resolve()
    box_dir = password_file().parents[2].resolve()  # .../pops-stream-box/obs-config
    guarded = {REPO, obsctl.DEFAULT_CONFIG_DIR.expanduser().resolve(),
               laptop_obs_dir().expanduser().resolve()} - {box_dir}
    for g in sorted(guarded):
        if p == g or g in p.parents:
            raise ProvisionError(f"refusing to write {p}: it's inside {g}")
    return p


def write_password_file(path: Path, ws: dict) -> Path:
    """The only file this script writes on the laptop."""
    p = check_local_target(path)
    keep = {k: ws[k] for k in ("server_password", "server_port", "auth_required", "server_enabled")
            if k in ws}
    if not keep.get("server_password"):
        raise ProvisionError("the box's obs-websocket config has no password")
    box_apply.write_private(p, json.dumps(keep, indent=4))
    return p


# --- SSH --------------------------------------------------------------------------
class Ssh:
    """One ssh invocation per call. Non-interactive calls use BatchMode, so
    a missing key fails at once instead of prompting."""

    def __init__(self, target: str, key: Path | None = None):
        self.target = target
        self.base = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]
        if key:
            self.base += ["-i", str(key), "-o", "IdentitiesOnly=yes"]

    def run(self, command: str, input: bytes | None = None, tty: bool = False):
        if tty:  # the terminal stays attached: sudo asks for the box password
            return subprocess.run(self.base + ["-t", self.target, command])
        return subprocess.run(self.base + ["-o", "BatchMode=yes", self.target, command],
                              input=input, capture_output=True)


def gather(ssh) -> dict[str, str]:
    r = ssh.run("bash -s", input=FACTS.encode())
    if r.returncode != 0:
        raise ProvisionError(f"can't read the box over SSH (exit {r.returncode}): "
                             f"{r.stderr.decode(errors='replace').strip()[-300:]}")
    return parse_facts(r.stdout.decode(errors="replace"))


def wait_for_reboot(ssh, old_boot: str, say, sleep=time.sleep, clock=time.monotonic) -> None:
    """Back once the box answers with a new boot id (not the old boot still
    shutting down)."""
    deadline = clock() + REBOOT_WAIT
    sleep(15)
    while clock() < deadline:
        r = ssh.run("cat /proc/sys/kernel/random/boot_id")
        if r.returncode == 0 and r.stdout.decode().strip() not in ("", old_boot):
            say("box is back")
            return
        sleep(5)
    raise ProvisionError(f"the box didn't come back within {REBOOT_WAIT}s of the reboot; check its "
                         "screen, then run this again")


def box_obs_live(host: str, port: int, pw_file: Path) -> bool | None:
    """Is the box's OBS streaming or recording? None if we can't ask."""
    try:
        password = json.loads(pw_file.read_text())["server_password"]
        c = obsctl.ObsClient(host, port, password, timeout=5)
    except (OSError, ValueError, KeyError, obsctl.ObsError):
        return None
    try:
        return any(c.call(f"Get{k}Status").get("outputActive") for k in ("Stream", "Record"))
    finally:
        c.close()


def check_obs(host: str, port: int, pw_file: Path, tries: int = 12, sleep=time.sleep) -> str | None:
    """OBS's version over the LAN with the fetched password, or None."""
    password = json.loads(pw_file.read_text())["server_password"]
    for _ in range(tries):
        try:
            c = obsctl.ObsClient(host, port, password, timeout=5)
            try:
                return c.call("GetVersion")["obsVersion"]
            finally:
                c.close()
        except obsctl.ObsError:
            sleep(5)
    return None


def provision(a, ssh, say=print, sleep=time.sleep) -> int:
    pw_file = check_local_target(password_file())
    offline = ssh is None
    for attempt in range(MAX_PASSES):
        facts = {} if offline else gather(ssh)
        if facts.get("obs") == "running":
            live = box_obs_live(a.host, WS_PORT, pw_file)
            if not a.stop_obs:
                raise ProvisionError("OBS is running on the box, and this run would change its config "
                                     "and drivers. If nothing is live, add --stop-obs.")
            if live is not False:
                raise ProvisionError("OBS on the box is live (or can't be asked); not stopping it")
            if not a.dry_run:
                r = ssh.run("pkill -TERM -x obs; for i in $(seq 40); do pgrep -x obs >/dev/null || exit 0; "
                            "sleep 0.5; done; exit 1")
                if r.returncode != 0:
                    raise ProvisionError("OBS on the box didn't stop within 20 s; nothing was changed")
        plan = make_plan(facts, a.user, lan=a.lan, gpu=a.gpu)
        steps = root_steps(plan, reboot_ok=not a.no_reboot, reset_scenes=a.reset_scenes)
        if a.dry_run:
            say(describe(plan, steps, a.copy_keys, pw_file, script=True))
            say("\n(dry run: nothing changed)")
            return 0
        remote = f"~/{REMOTE_DIR}/bundle"
        r = ssh.run(f"rm -rf {remote} && mkdir -p {remote} && chmod 700 ~/{REMOTE_DIR} "
                    f"&& tar -xf - -C {remote}", input=bundle(plan, root_script(plan, steps)))
        if r.returncode != 0:
            raise ProvisionError(f"couldn't copy the setup files to the box: {r.stderr.decode()[-300:]}")
        if a.copy_keys and attempt == 0:
            payload = laptop_keys(laptop_obs_dir())
            r = ssh.run(f"python3 {remote}/box_apply.py keys", input=json.dumps(payload).encode())
            say(r.stdout.decode().strip() if r.returncode == 0 else
                f"copy-keys failed: {r.stderr.decode().strip()[-300:]}")
        say(f"\nSetting up the box ({plan['gpu_name'] or 'no GPU found'}). "
            "It asks for the box's password (the one set in the installer).")
        rc = ssh.run(f"sudo bash {remote}/root.sh", tty=True).returncode
        if rc == 0:
            break
        if rc == EXIT_REBOOT and not a.no_reboot:
            say("\nWaiting for the box to reboot (a few minutes)...")
            wait_for_reboot(ssh, facts.get("boot", ""), say, sleep)
            continue
        if rc == EXIT_REBOOT:
            raise ProvisionError("the box needs a reboot: reboot it, then run this again")
        if rc == EXIT_OBS_RUNNING:
            raise ProvisionError("OBS started on the box meanwhile; nothing was changed")
        raise ProvisionError(f"setup on the box stopped (exit {rc}); the step above says why. "
                             "Fix it and run this again: finished steps are skipped.")
    else:
        raise ProvisionError(f"the box still wanted a reboot after {MAX_PASSES} passes")

    obs_cfg = box_apply.obs_dir(Path("~")).as_posix()
    r = ssh.run(f"cat {obs_cfg}/plugin_config/obs-websocket/config.json")
    if r.returncode != 0:
        raise ProvisionError("couldn't read the box's obs-websocket config back")
    path = write_password_file(pw_file, json.loads(r.stdout))
    say(f"\nobs-websocket password saved to {path} (0600)")
    r = ssh.run(f"cat ~/{REMOTE_DIR}/result.json")
    if r.returncode == 0:
        res = json.loads(r.stdout)
        say(f"Encoders: eBay {res['ebay_encoder']}, Twitch {res['twitch_encoder']}")
        for n in res.get("notes", []):
            say(f"  {n}")
    version = check_obs(a.host, WS_PORT, path, sleep=sleep)
    say(f"OBS {version} answers on {a.host}:{WS_PORT}" if version else
        f"OBS didn't answer on {a.host}:{WS_PORT} yet; on the box see ~/{REMOTE_DIR}/obs.log")
    say(f"\nTry it: OBS_WS_HOST={a.host} OBS_CONFIG_DIR={path.parents[2]} "
        "python tools/live/obsctl.py status")
    return 0


# --- finding the box ----------------------------------------------------------------
def laptop_lan() -> ipaddress.IPv4Network:
    r = subprocess.run(["ip", "-o", "-4", "route", "show", "default"], capture_output=True, text=True)
    a = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True)
    return ipaddress.ip_network(lan_cidr(r.stdout, a.stdout))


def ssh_banner(ip: str, timeout: float = 0.4) -> str | None:
    try:
        with socket.create_connection((ip, 22), timeout) as s:
            s.settimeout(1.0)
            return s.recv(256).decode(errors="replace").strip()
    except OSError:
        return None


def linux_candidates(banners: dict[str, str | None]) -> list[str]:
    """Hosts whose SSH banner says Ubuntu or Debian, in address order."""
    hits = [ip for ip, b in banners.items() if b and re.search(r"ubuntu|debian", b, re.I)]
    return sorted(hits, key=ipaddress.ip_address)


def find_box(name: str, user: str, key: Path | None, say=print) -> str:
    """The address of the box called `name` on the laptop's /24: hosts with
    SSH open and an Ubuntu/Debian banner, then `hostname` over SSH (key
    login, host key not recorded) on each."""
    net = laptop_lan()
    if net.num_addresses > 1024:
        raise ProvisionError(f"{net} is too big to search; give the box's address instead")
    say(f"looking for {name!r} on {net}...")
    with concurrent.futures.ThreadPoolExecutor(64) as pool:
        ips = [str(h) for h in net.hosts()]
        banners = dict(zip(ips, pool.map(ssh_banner, ips)))
    for ip in linux_candidates(banners):
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", "-o", "StrictHostKeyChecking=no",
               "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]
        if key:
            cmd += ["-i", str(key), "-o", "IdentitiesOnly=yes"]
        r = subprocess.run(cmd + [f"{user}@{ip}", "hostname"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip().split(".")[0] == name:
            say(f"found {name} at {ip}")
            return ip
    raise ProvisionError(f"no box named {name!r} answered on {net}. Is it on and past the install? "
                         "Its address is also on the installer's network screen; pass that instead.")


def default_key() -> Path | None:
    """~/.ssh/gh: the key GitHub has (github.com/<user>.keys), which the
    Ubuntu installer imports."""
    k = Path.home() / ".ssh" / "gh"
    return k if k.exists() else None


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("host", nargs="?", help="the box's address (or use --find)")
    p.add_argument("--find", metavar="NAME", help="find the box by hostname on the laptop's /24")
    p.add_argument("--user", default=os.environ.get("USER") or "russ",
                   help="the box's user (default: the same name as on the laptop)")
    p.add_argument("--ssh-key", type=Path, default=default_key(), help="default ~/.ssh/gh")
    p.add_argument("--lan", help="the LAN to allow, e.g. 192.168.1.0/24 (default: the box's own)")
    p.add_argument("--gpu", choices=["nvidia", "amd", "intel", "none"], help="skip GPU detection")
    p.add_argument("--copy-keys", action="store_true",
                   help="copy the eBay WHIP service and Twitch URL from the laptop's OBS (never shown)")
    p.add_argument("--stop-obs", action="store_true", help="stop OBS on the box first, if nothing is live")
    p.add_argument("--reset-scenes", action="store_true",
                   help="overwrite the box's scene collection with the repo template")
    p.add_argument("--no-reboot", action="store_true", help="stop and say so instead of rebooting")
    p.add_argument("--dry-run", action="store_true",
                   help="read the box (if given) and print every step; change nothing")
    return p


def main(argv=None) -> int:
    p = parser()
    a = p.parse_args(argv)
    try:
        if a.find and not a.host:
            a.host = find_box(a.find, a.user, a.ssh_key)
        if not a.host and not (a.dry_run and a.gpu and a.lan):
            p.error("give the box's address or --find NAME (an offline --dry-run needs --gpu and --lan)")
        ssh = Ssh(f"{a.user}@{a.host}", a.ssh_key) if a.host else None
        return provision(a, ssh)
    except ProvisionError as e:
        print(f"stream box: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
