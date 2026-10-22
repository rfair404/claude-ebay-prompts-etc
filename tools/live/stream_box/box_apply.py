#!/usr/bin/env python3
"""BOX APPLY: the stream box's own half of tools/live/stream_box/provision.py.

This file runs ON the stream box, as the box user, from the bundle that
provision.py copies to ~/.cache/pops-stream-box/bundle. It writes OBS's
config there and nowhere else; it never runs on the laptop.

    python3 box_apply.py apply --bundle DIR [--reset-scenes]
        Ebay profile (encoder for the box's GPU, both destinations),
        eBay Live scene collection (only if missing, or --reset-scenes),
        obs-websocket on with a password generated here, user.ini (no
        first-run wizard, the show profile current), the openbox
        autostart line that launches OBS, and result.json (no secrets).
    python3 box_apply.py keys
        Reads {"service": {...}, "ffurl": "..."} on stdin (provision.py
        --copy-keys sends the laptop's eBay WHIP service and Twitch URL over
        SSH) and writes them into the box's Ebay profile, 0600.

Both refuse while OBS is running: OBS rewrites its config on exit.

The websocket password is made with `secrets` and written straight into
OBS's own config file (0600). It is never printed, logged or put in argv.

Encoders (choose_encoders):
- NVIDIA with a working NVENC: eBay on OBS's NVENC (obs_nvenc_h264_tex),
  Twitch on ffmpeg's h264_nvenc in the Custom Output, full size. Both on
  the GPU.
- Intel/AMD with working VAAPI: eBay on ffmpeg_vaapi_tex. Twitch stays on
  libx264 superfast at 720x1280, because OBS's Custom Output can't use VAAPI
  (the same split as PR #246 on the laptop). The box's CPU does nothing
  else, so it has room for that.
- No working GPU encoder: x264 for both, and result.json says so.

Stdlib only, Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

OBS_APP = "com.obsproject.Studio"
PROFILE = "Ebay"
COLLECTION_FILE = "eBay_Live.json"
STATE_DIR = ".cache/pops-stream-box"  # under $HOME
AUTOSTART = ".config/openbox/autostart"  # under $HOME
MARK_BEGIN, MARK_END = "# >>> pops stream box", "# <<< pops stream box"
# OBS opens on the show's profile and collection, with no update or
# safe-mode dialog to block a box nobody sits at. No --minimize-to-tray:
# openbox has no tray, and the OBS window on the box's own monitor is useful.
OBS_ARGS = "--profile Ebay --collection 'eBay Live' --disable-shutdown-check --disable-updater"

# eBay's OBS guide: CBR 3000 kbps, 1 s keyframes. WHIP can't carry B-frames.
EBAY_BASE = {"rate_control": "CBR", "bitrate": 3000, "keyint_sec": 1}
NVENC_EBAY = {**EBAY_BASE, "preset2": "p5", "tune": "ll", "multipass": "disabled",
              "profile": "high", "bf": 0}
VAAPI_EBAY = {**EBAY_BASE, "bf": 0}  # profile/level: OBS's defaults, as on the laptop (#246)
X264_EBAY = {**EBAY_BASE, "preset": "superfast", "x264opts": "bframes=0"}
# user.ini: past the first-run wizard (a dialog nobody at the box would
# answer), on the show's profile and collection. Same keys the laptop has.
USER_INI = {"General": {"FirstRun": "true"},
            "Basic": {"Profile": PROFILE, "ProfileDir": PROFILE, "SceneCollection": "eBay Live",
                      "SceneCollectionFile": COLLECTION_FILE}}

# Twitch rides OBS's recording output as a Custom Output (FFmpeg) to an RTMP
# URL. These mirror show_flow.TWITCH_OUT (what `twitch-setup` writes on the
# laptop) minus the video encoder, which depends on the box's GPU.
# tests/test_stream_box.py checks they stay in step. FFURL (the Twitch key)
# is never written here; it comes from --copy-keys or a paste into OBS.
TWITCH_COMMON = {"RecType": "FFmpeg", "FFOutputToFile": "false", "FFFormat": "flv",
                 "FFFormatMimeType": "video/x-flv", "FFExtension": "flv",
                 "FFAEncoder": "aac", "FFAEncoderId": "86018",  # AV_CODEC_ID_AAC
                 "FFVBitrate": "3000", "FFVGOPSize": "60", "FFABitrate": "160",
                 "FFAudioMixes": "1"}
H264_ID = "27"  # AV_CODEC_ID_H264
TWITCH_NVENC = {**TWITCH_COMMON, "FFVEncoder": "h264_nvenc", "FFVEncoderId": H264_ID,
                "FFVCustom": "preset=p5", "FFRescale": "false"}
TWITCH_X264 = {**TWITCH_COMMON, "FFVEncoder": "libx264", "FFVEncoderId": H264_ID,
               "FFVCustom": "preset=superfast", "FFRescale": "true", "FFRescaleRes": "720x1280"}


class BoxError(RuntimeError):
    pass


# --- pure logic (tested) -------------------------------------------------------
def choose_encoders(vendor: str, probe: dict, render_node: str | None = None) -> dict:
    """Encoders for eBay (OBS stream output) and Twitch (Custom Output) from
    the GPU vendor and what the box's test encodes proved (probe: {"nvenc":
    bool, "vaapi": bool}). Never trusts the vendor alone: an old or low-end
    card can lack NVENC, and VAAPI can be missing its encode entrypoint."""
    if vendor == "nvidia" and probe.get("nvenc"):
        return {"kind": "nvenc", "ebay_encoder": "obs_nvenc_h264_tex", "ebay_settings": NVENC_EBAY,
                "twitch": TWITCH_NVENC, "notes": ["eBay and Twitch both encode on the NVIDIA GPU"]}
    if vendor in ("intel", "amd") and probe.get("vaapi"):
        return {"kind": "vaapi", "ebay_encoder": "ffmpeg_vaapi_tex",
                "ebay_settings": {**VAAPI_EBAY, "vaapi_device": render_node or "/dev/dri/renderD128"},
                "twitch": TWITCH_X264,
                "notes": ["eBay encodes on the GPU (VAAPI)",
                          "Twitch is libx264 superfast 720x1280 on the box's CPU: "
                          "OBS's Custom Output can't use VAAPI"]}
    why = (f"{vendor} GPU found but its test encode failed" if vendor in ("nvidia", "intel", "amd")
           else "no GPU found")
    return {"kind": "x264", "ebay_encoder": "obs_x264", "ebay_settings": X264_EBAY,
            "twitch": TWITCH_X264, "notes": [f"{why}: both destinations encode on the CPU (x264)"]}


def stream_encoder_json(template: dict, enc: dict) -> dict:
    """streamEncoder.json: the template's keys, then the chosen encoder's.
    OBS ignores keys an encoder doesn't know, so the x264 fallback keys can
    stay alongside (as in PR #246's template)."""
    return {**template, **enc["ebay_settings"]}


def profile_updates(enc: dict) -> dict[str, dict[str, str]]:
    """The basic.ini keys this script owns: the stream encoder and the Twitch
    Custom Output. Everything else (and FFURL) is left as the box has it."""
    return {"AdvOut": {"Encoder": enc["ebay_encoder"], **enc["twitch"]}}


def parse_ini(text: str) -> dict[str, dict[str, str]]:
    """OBS's ini as {section: {key: value}}, case kept (configparser lowercases)."""
    out: dict[str, dict[str, str]] = {}
    sec = None
    for line in text.lstrip("﻿").splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = out.setdefault(s[1:-1], {})
        elif sec is not None and "=" in s and not s.startswith((";", "#")):
            k, _, v = s.partition("=")
            sec[k] = v
    return out


def merge_ini(text: str, updates: dict[str, dict[str, str]]) -> str:
    """Set `updates` in an OBS ini, keeping every other line, its order and a
    leading BOM. A key missing from its section is added at the section's
    end; a missing section is added at the end of the file."""
    bom = "﻿" if text.startswith("﻿") else ""
    lines = text.lstrip("﻿").splitlines()
    pending = {s: dict(kv) for s, kv in updates.items()}
    out: list[str] = []
    sec = None

    def flush(section):
        for k, v in pending.pop(section, {}).items():
            out.append(f"{k}={v}")

    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            if sec is not None:
                while out and not out[-1].strip():  # keys go before the blank line
                    out.pop()
                flush(sec)
                out.append("")
            sec = s[1:-1]
            out.append(line)
            continue
        if sec in pending and "=" in s and not s.startswith((";", "#")):
            k = s.partition("=")[0]
            if k in pending[sec]:
                out.append(f"{k}={pending[sec].pop(k)}")
                continue
        out.append(line)
    if sec is not None:
        while out and not out[-1].strip():
            out.pop()
        flush(sec)
    for section in list(pending):
        out += ["", f"[{section}]"]
        flush(section)
    return bom + "\n".join(out) + "\n"


def profile_text(template: str, existing: str | None, enc: dict, home: str) -> str:
    """The box's basic.ini: the repo template (with ~ expanded, as the laptop
    install does with sed), then whatever the box already has (its stream
    keys, anything set in OBS), then this script's encoder keys on top."""
    base = template.replace("=~", f"={home}")
    if existing:
        base = merge_ini(base, parse_ini(existing))
    return merge_ini(base, profile_updates(enc))


def websocket_config(existing: dict | None, port: int, make_password=None) -> tuple[dict, bool]:
    """obs-websocket's config.json with the server on, auth required and no
    first-run dialog. Keeps a password the box already has, so a re-run
    doesn't break the laptop's copy. Returns (config, made a new password)."""
    cfg = dict(existing or {})
    made = not cfg.get("server_password")
    if made:
        cfg["server_password"] = (make_password or (lambda: secrets.token_urlsafe(24)))()
    cfg.update({"server_enabled": True, "auth_required": True, "first_load": False,
                "alerts_enabled": False, "server_port": int(port)})
    return cfg, made


def autostart_text(existing: str | None, state_dir: str) -> str:
    """openbox's autostart with one marked block: no screen blanking, and OBS
    once provision.py has finished (the `ready` file), so a half-provisioned
    box never starts OBS. Replaces an earlier block; keeps other lines."""
    block = [MARK_BEGIN,
             "xset s off -dpms s noblank &",
             f'[ -e "{state_dir}/ready" ] && flatpak run {OBS_APP} {OBS_ARGS} '
             f'>>"{state_dir}/obs.log" 2>&1 &',
             MARK_END]
    keep, inside = [], False
    for line in (existing or "").splitlines():
        if line.strip() == MARK_BEGIN:
            inside = True
        elif line.strip() == MARK_END:
            inside = False
        elif not inside:
            keep.append(line)
    while keep and not keep[-1].strip():
        keep.pop()
    return "\n".join(keep + ([""] if keep else []) + block) + "\n"


def keys_updates(payload: dict) -> tuple[dict | None, dict[str, dict[str, str]]]:
    """(service.json content or None, basic.ini updates) from --copy-keys'
    payload. Only the WHIP service and the Twitch URL; nothing else."""
    service = payload.get("service")
    if service is not None and not (isinstance(service, dict) and service.get("settings")):
        raise BoxError("copy-keys: the laptop's service.json has no settings")
    ffurl = payload.get("ffurl")
    return service, ({"AdvOut": {"FFURL": ffurl}} if ffurl else {})


# --- the box's files -------------------------------------------------------------
def obs_dir(home: Path) -> Path:
    return home / ".var/app" / OBS_APP / "config/obs-studio"


def write_private(path: Path, text: str) -> None:
    """Write a file only its owner can read (0600), creating parent dirs 0700."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def obs_running() -> bool:
    return subprocess.run(["pgrep", "-x", "obs"], capture_output=True).returncode == 0


def apply(bundle: Path, home: Path, reset_scenes: bool = False) -> dict:
    plan = json.loads((bundle / "plan.json").read_text())
    probe = json.loads(_read(bundle / "probe.json") or "{}")
    enc = choose_encoders(plan["vendor"], probe, plan.get("render_node"))
    base = obs_dir(home)
    prof = base / "basic/profiles" / PROFILE
    write_private(prof / "basic.ini", profile_text((bundle / "basic.ini").read_text(),
                                                   _read(prof / "basic.ini"), enc, str(home)))
    template_enc = json.loads((bundle / "streamEncoder.json").read_text())
    write_private(prof / "streamEncoder.json", json.dumps(stream_encoder_json(template_enc, enc)))

    write_private(base / "user.ini", merge_ini(_read(base / "user.ini") or "", USER_INI))

    scenes = base / "basic/scenes" / COLLECTION_FILE
    scenes_written = reset_scenes or not scenes.exists()
    if scenes_written:
        write_private(scenes, (bundle / COLLECTION_FILE).read_text())

    ws_path = base / "plugin_config/obs-websocket/config.json"
    try:
        existing = json.loads(_read(ws_path) or "null")
    except ValueError:
        existing = None
    cfg, made = websocket_config(existing, plan["port"])
    write_private(ws_path, json.dumps(cfg, indent=4))

    state = home / STATE_DIR
    auto = home / AUTOSTART
    auto.parent.mkdir(parents=True, exist_ok=True)
    auto.write_text(autostart_text(_read(auto), str(state)))

    result = {"vendor": plan["vendor"], "gpu": plan.get("gpu_name"), "probe": probe,
              "encoder": enc["kind"], "ebay_encoder": enc["ebay_encoder"],
              "twitch_encoder": enc["twitch"]["FFVEncoder"], "notes": enc["notes"],
              "scene_collection": "installed" if scenes_written else "kept (already on the box)",
              "websocket_password": "generated" if made else "kept", "port": plan["port"]}
    write_private(state / "result.json", json.dumps(result, indent=2))
    return result


def keys(payload: dict, home: Path) -> list[str]:
    service, updates = keys_updates(payload)
    prof = obs_dir(home) / "basic/profiles" / PROFILE
    done = []
    if service is not None:
        write_private(prof / "service.json", json.dumps(service, indent=4))
        done.append("eBay WHIP service")
    if updates:
        write_private(prof / "basic.ini", merge_ini(_read(prof / "basic.ini") or "", updates))
        done.append("Twitch URL")
    return done


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("apply", help="write the box's OBS config from the bundle")
    s.add_argument("--bundle", required=True, type=Path)
    s.add_argument("--reset-scenes", action="store_true",
                   help="overwrite the box's eBay Live scene collection with the repo template")
    sub.add_parser("keys", help="stream keys from stdin (JSON) into the Ebay profile")
    a = p.parse_args(argv)
    try:
        if obs_running():
            raise BoxError("OBS is running on this box; it rewrites its config on exit, so nothing "
                           "was changed. Quit OBS and re-run.")
        home = Path.home()
        if a.cmd == "apply":
            r = apply(a.bundle, home, a.reset_scenes)
            print(f"OBS config: eBay {r['ebay_encoder']}, Twitch {r['twitch_encoder']}; scenes "
                  f"{r['scene_collection']}; websocket password {r['websocket_password']}")
            for n in r["notes"]:
                print(f"  {n}")
        else:
            done = keys(json.load(sys.stdin), home)
            print(f"copied: {', '.join(done) or 'nothing (the laptop had no keys)'}")
    except (BoxError, OSError, ValueError, KeyError) as e:
        print(f"box_apply: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
