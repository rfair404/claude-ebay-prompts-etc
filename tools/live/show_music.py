#!/usr/bin/env python3
"""SHOW MUSIC — background music under the live show, with fades.

The music is HoliznaCC0's lo-fi, dedicated to the public domain (CC0), so it
is safe on eBay Live, whose policy only allows music you have the rights to
(docs/live-streaming-setup.md, "Background music"). It plays from one local
file, never an internet stream: every track in it has a license on record.

    python tools/live/show_music.py fetch     # download the CC0 packs (once per machine)
    python tools/live/show_music.py build     # shuffle + level them into one looping file
    python tools/live/show_music.py setup     # OBS: the Music source in every page, ducked under the mic
    python tools/live/show_music.py on|off|toggle [--json]   # fade in / fade out
    python tools/live/show_music.py status [--json]

All of it lives in MUSIC_DIR (~/Music/show-bgm, or $SHOW_MUSIC_DIR), outside
the repo: src/ holds the downloaded zips and their tracks, show-mix.ogg the
built file, tracklist.json its running order and LICENSE.md the record of
where every track came from and under what license.

`on` unmutes the Music source and fades it up to its fader level over FADE_S;
`off` fades it down, mutes it and pauses it, so the next `on` picks up mid
track. The fader level in OBS *is* the setting (like the stand-in's
visibility in show_flow): turn it in OBS and the next fade lands there.
`setup` starts it at LEVEL_DB. While Russell talks, a sidechain compressor on
the music (keyed by the mic) pulls it down a further ~8 dB.

webapp/pager.py serves the same on/off as POST /api/pager/music and the state
under `music` in GET /api/pager, with a button on the pager page.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: the web app imports this module there too
    fcntl = None

sys.path.insert(0, str(Path(__file__).resolve().parent))
import obsctl  # noqa: E402
import show_flow  # noqa: E402

MUSIC_DIR = Path(os.environ.get("SHOW_MUSIC_DIR") or Path.home() / "Music" / "show-bgm")
MIX = "show-mix.ogg"
TRACKLIST = "tracklist.json"
MUSIC = "Show Music"            # the OBS input
DUCK = "Duck under mic"         # its sidechain compressor
LEVEL_DB = -20.0                # fader: the mix is -16 LUFS, so about -36 LUFS on air
FLOOR_DB = -45.0                # a fade starts / ends here, then hard mute
FADE_S = 2.0
FADE_STEPS = 40
XFADE_S = 3                     # crossfade between tracks in the mix
LOUDNESS = "I=-16:TP=-1.5:LRA=11"
# Ducking: keyed by the mic after its own chain. Speech peaks around -10 dBFS
# (docs/live-streaming-setup.md), so ~16 dB over threshold at ratio 2 is ~8 dB down.
DUCK_SETTINGS = {"ratio": 2.0, "threshold": -26.0, "attack_time": 10, "release_time": 600,
                 "output_gain": 0.0, "sidechain_source": show_flow.MIC}

# The packs `fetch` downloads. Each one's page states the license; LICENSE.md quotes it.
PACKS = {
    "chill_beats.zip": {
        "title": "Chill Beats Collection", "artist": "HoliznaCC0",
        "page": "https://opengameart.org/content/chill-beats-collection",
        "url": "https://opengameart.org/sites/default/files/chill_beats.zip"},
    "happy_lo-fi_lofi_collection.zip": {
        "title": "Happy Lo-Fi (Lofi Collection)", "artist": "HoliznaCC0",
        "page": "https://opengameart.org/content/happy-lo-fi-lofi-collection",
        "url": "https://opengameart.org/sites/default/files/happy_lo-fi_lofi_collection.zip"},
}
LICENSE_NOTE = (
    "License: CC0 1.0 (public domain dedication). The artist's statement, on each pack's\n"
    "OpenGameArt page: \"This music is public domain Cc0, so use it how you want!\" and on\n"
    "https://holiznacc0.bandcamp.com: \"I am dedicating my archive of music to the creative\n"
    "commons via CC0 ... you are not required to credit me either!\"\n")

LOCK = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "show_music.lock"
PLAYING = "OBS_MEDIA_STATE_PLAYING"
ACTION = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_"


# --- the file ----------------------------------------------------------------
def fetch(music_dir: Path = MUSIC_DIR) -> list[Path]:
    src = music_dir / "src"
    src.mkdir(parents=True, exist_ok=True)
    got = []
    for name, pack in PACKS.items():
        dest = src / name
        if not dest.exists():
            print(f"downloading {pack['url']}")
            tmp = dest.with_suffix(".part")
            urllib.request.urlretrieve(pack["url"], tmp)
            tmp.rename(dest)
        subprocess.run(["unzip", "-oq", str(dest), "-d", str(src / dest.stem)], check=True)
        got.append(dest)
    return got


def tracks(music_dir: Path = MUSIC_DIR) -> list[tuple[Path, dict]]:
    """Every track under src/, with the pack it came from. The packs ship each
    track as both .mp3 and .ogg; take the mp3."""
    out = []
    for name, pack in PACKS.items():
        out += [(p, pack) for p in sorted((music_dir / "src" / Path(name).stem).rglob("*.mp3"))]
    return out


def title_of(p: Path) -> str:
    stem = p.name.split(".mp3")[0]                # "03 HoliznaCC0 - Blue Skies.mp3.mp3"
    return stem.split(" - ", 1)[-1]


def duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True)
    return float(r.stdout)


def plan(items: list[tuple[Path, float]], xfade: float = XFADE_S) -> list[dict]:
    """Running order with each track's start in the mix (tracks overlap by xfade)."""
    out, t = [], 0.0
    for p, d in items:
        out.append({"title": title_of(p), "file": p.name, "start_s": round(t, 2), "dur_s": round(d, 2)})
        t += d - xfade
    return out


def ffmpeg_args(files: list[Path], total_s: float, out: Path, xfade: float = XFADE_S) -> list[str]:
    args = ["ffmpeg", "-y", "-v", "error"]
    for f in files:
        args += ["-i", str(f)]
    chain = [f"[{i}:a]loudnorm={LOUDNESS},aresample=48000[t{i}]" for i in range(len(files))]
    last = "t0"
    for i in range(1, len(files)):
        chain.append(f"[{last}][t{i}]acrossfade=d={xfade}:c1=tri:c2=tri[x{i}]")
        last = f"x{i}"
    # soft edges, so the loop point (end -> start) doesn't click
    chain.append(f"[{last}]afade=t=in:d=2,afade=t=out:st={max(total_s - 3, 0):.2f}:d=3[out]")
    return args + ["-filter_complex", ";".join(chain), "-map", "[out]",
                   "-c:a", "libopus", "-b:a", "160k", "-ar", "48000", str(out)]


def build(music_dir: Path = MUSIC_DIR, seed: int | None = None) -> dict:
    found = tracks(music_dir)
    if not found:
        raise SystemExit(f"no tracks under {music_dir / 'src'}; run `show_music.py fetch` first")
    random.Random(seed).shuffle(found)
    timed = [(p, duration(p)) for p, _ in found]
    order = plan(timed)
    total = order[-1]["start_s"] + order[-1]["dur_s"]
    out = music_dir / MIX
    tmp = out.with_name(out.stem + ".part.ogg")
    subprocess.run(ffmpeg_args([p for p, _ in timed], total, tmp), check=True)
    tmp.replace(out)
    (music_dir / TRACKLIST).write_text(json.dumps({"total_s": round(total, 2), "tracks": order}, indent=1))
    write_license(music_dir, found)
    return {"file": str(out), "tracks": len(order), "total_s": round(total, 2)}


def write_license(music_dir: Path, found: list[tuple[Path, dict]]) -> None:
    lines = ["# Show music: sources and licenses", "",
             f"Built {time.strftime('%Y-%m-%d')} by tools/live/show_music.py into {MIX}.", ""]
    for name, pack in PACKS.items():
        names = sorted(title_of(p) for p, pk in found if pk is pack)
        if not names:
            continue
        lines += [f"## {pack['title']} by {pack['artist']}", "",
                  f"Source: {pack['page']}", f"Download: {pack['url']}", "", LICENSE_NOTE,
                  "Tracks:", *[f"- {n}" for n in names], ""]
    (music_dir / "LICENSE.md").write_text("\n".join(lines))


# --- OBS -----------------------------------------------------------------------
@contextmanager
def exclusive(wait: float = 10):
    """One fade at a time, across the shell and the web app."""
    with open(LOCK, "w") as f:
        deadline = time.monotonic() + wait
        while fcntl:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise show_flow.Busy("another music fade is still running")
                time.sleep(0.05)
        yield


def setup(c, music_dir: Path = MUSIC_DIR) -> None:
    mix = music_dir / MIX
    if not mix.exists():
        raise obsctl.ObsError(f"{mix} doesn't exist; run `show_music.py build` first")
    settings = {"is_local_file": True, "local_file": str(mix), "looping": True,
                "restart_on_activate": False, "close_when_inactive": False}
    inputs = {i["inputName"] for i in c.call("GetInputList")["inputs"]}
    scenes = [*show_flow.PAGES.values(), show_flow.HOLD]
    if MUSIC in inputs:
        c.call("SetInputSettings", {"inputName": MUSIC, "inputSettings": settings})
    else:
        # created muted, so setting it up never puts music on air
        c.call("CreateInput", {"sceneName": scenes[0], "inputName": MUSIC,
                               "inputKind": "ffmpeg_source", "inputSettings": settings})
        c.call("SetInputMute", {"inputName": MUSIC, "inputMuted": True})
        c.call("SetInputVolume", {"inputName": MUSIC, "inputVolumeDb": LEVEL_DB})
    if c.call("GetInputMute", {"inputName": MUSIC})["inputMuted"]:
        # a media source starts playing as soon as it loads; off means paused
        c.call("TriggerMediaInputAction", {"inputName": MUSIC, "mediaAction": ACTION + "PAUSE"})
    # the same input in every page, so a page switch never restarts or cuts it
    have = {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}
    for scene in scenes:
        if scene in have:
            show_flow.item(c, scene, MUSIC)
    filters = {f["filterName"] for f in c.call("GetSourceFilterList", {"sourceName": MUSIC})["filters"]}
    if DUCK in filters:
        c.call("SetSourceFilterSettings", {"sourceName": MUSIC, "filterName": DUCK,
                                           "filterSettings": DUCK_SETTINGS})
    else:
        c.call("CreateSourceFilter", {"sourceName": MUSIC, "filterName": DUCK,
                                      "filterKind": "compressor_filter", "filterSettings": DUCK_SETTINGS})


def _ready(c) -> bool:
    return any(i["inputName"] == MUSIC for i in c.call("GetInputList")["inputs"])


def _level(c) -> float:
    """The fader's resting level: what a fade-in lands on."""
    db = c.call("GetInputVolume", {"inputName": MUSIC})["inputVolumeDb"]
    return db if db > FLOOR_DB else LEVEL_DB  # a fade cut off midway leaves it low


def _ramp(c, start: float, end: float, seconds: float = FADE_S) -> None:
    for i in range(1, FADE_STEPS + 1):
        time.sleep(seconds / FADE_STEPS)
        c.call("SetInputVolume", {"inputName": MUSIC, "inputVolumeDb": start + (end - start) * i / FADE_STEPS})


def now_playing(cursor_ms: int | None, music_dir: Path = MUSIC_DIR) -> str | None:
    try:
        tl = json.loads((music_dir / TRACKLIST).read_text())
    except (OSError, ValueError):
        return None
    if cursor_ms is None or not tl.get("tracks"):
        return None
    t = (cursor_ms / 1000) % tl["total_s"]
    title = tl["tracks"][0]["title"]
    for tr in tl["tracks"]:
        if tr["start_s"] <= t:
            title = tr["title"]
    return title


def state(c) -> dict:
    if not _ready(c):
        return {"ready": False, "on": False, "db": None, "track": None}
    muted = c.call("GetInputMute", {"inputName": MUSIC})["inputMuted"]
    media = c.call("GetMediaInputStatus", {"inputName": MUSIC})
    playing = media.get("mediaState") == PLAYING
    return {"ready": True, "on": playing and not muted,
            "db": round(c.call("GetInputVolume", {"inputName": MUSIC})["inputVolumeDb"], 1),
            "track": now_playing(media.get("mediaCursor")) if playing and not muted else None}


def set_on(c, on: bool) -> dict:
    """Fade the music in or out. Returns {"on", "changed"}."""
    if not _ready(c):
        raise obsctl.ObsError(f"no {MUSIC!r} source in OBS; run `show_music.py setup`")
    with exclusive():
        if state(c)["on"] == on:
            return {"on": on, "changed": False}
        level = _level(c)
        if on:
            c.call("SetInputVolume", {"inputName": MUSIC, "inputVolumeDb": -100.0})
            c.call("SetInputMute", {"inputName": MUSIC, "inputMuted": False})
            c.call("TriggerMediaInputAction", {"inputName": MUSIC, "mediaAction": ACTION + "PLAY"})
            _ramp(c, FLOOR_DB, level)
        else:
            _ramp(c, level, FLOOR_DB)
            c.call("SetInputMute", {"inputName": MUSIC, "inputMuted": True})
            c.call("TriggerMediaInputAction", {"inputName": MUSIC, "mediaAction": ACTION + "PAUSE"})
            c.call("SetInputVolume", {"inputName": MUSIC, "inputVolumeDb": level})  # the fader keeps its place
        return {"on": on, "changed": True}


# --- CLI -----------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="download the CC0 packs into MUSIC_DIR/src")
    s = sub.add_parser("build", help="shuffle + level the tracks into one looping file")
    s.add_argument("--seed", type=int, help="fixed shuffle (default: new order each build)")
    sub.add_parser("setup", help="OBS: the Music source in every page, ducked under the mic")
    for name in ("on", "off", "toggle", "status"):
        sub.add_parser(name).add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "fetch":
        fetch()
        print(f"packs in {MUSIC_DIR / 'src'}: {len(tracks())} tracks")
        return 0
    if a.cmd == "build":
        r = build(seed=a.seed)
        print(f"{r['file']}: {r['tracks']} tracks, {r['total_s'] / 60:.1f} min (loops)")
        return 0
    try:
        c = show_flow.connect()
        try:
            if a.cmd == "setup":
                setup(c)
                print(f"{MUSIC!r} in {', '.join(show_flow.PAGES.values())}; muted until `on`")
                return 0
            if a.cmd != "status":
                on = (not state(c)["on"]) if a.cmd == "toggle" else a.cmd == "on"
                set_on(c, on)
            st = state(c)
            if a.json:
                print(json.dumps(st))
            elif not st["ready"]:
                print("music: not set up (show_music.py setup)")
            else:
                print(f"music: {'ON' if st['on'] else 'off'}  fader {st['db']} dB"
                      + (f"  now: {st['track']}" if st["track"] else ""))
        finally:
            c.close()
    except obsctl.ObsError as e:
        print(f"show_music: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
