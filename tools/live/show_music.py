#!/usr/bin/env python3
"""SHOW MUSIC — CC0 background music under the live show, with fades.

    python tools/live/show_music.py fetch lofi|folk   # download a preset's CC0 tracks (once per machine)
    python tools/live/show_music.py build lofi|folk   # level + crossfade them into one looping file
    python tools/live/show_music.py setup             # OBS: "Show Music" in every page, ducked under the mic
    python tools/live/show_music.py use lofi|folk     # switch the preset (fades out and back in if on)
    python tools/live/show_music.py on|off [--json]   # fade in / fade out
    python tools/live/show_music.py status [--json]

Why this music, the levels and the pager button: docs/live-streaming-setup.md
section 7. The OBS fader is the level setting: a fade-in lands where it was left.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import obsctl  # noqa: E402
import show_flow  # noqa: E402

MUSIC_DIR = Path(os.environ.get("SHOW_MUSIC_DIR") or Path.home() / "Music" / "show-bgm")
PRESETS_DIR = "presets"         # <name>.ogg, <name>.tracklist.json, <name>.LICENSE.md
DEFAULT = "lofi"
MUSIC = "Show Music"            # the OBS input
DUCK = "Duck under mic"         # its sidechain compressor
LEVEL_DB = -20.0                # fader: the mix is -16 LUFS, so about -36 LUFS on air
FLOOR_DB = -45.0                # a fade starts / ends here, then hard mute
FADE_S = 2.0
FADE_STEPS = 40
XFADE_S = 3                     # crossfade between tracks in the mix
MIX_LUFS = -16.0                # every preset ends here, so a switch keeps the fader's level
# Per preset: the target each track is levelled to, and whether it's acoustic
# (trim the silent intro and tail, 2:1 compression for plucked attacks, limiter).
PRESETS = {
    "lofi": {"title": "Lo-fi beats", "loudness": "I=-16:TP=-1.5:LRA=11", "acoustic": False},
    "folk": {"title": "Folk guitar", "loudness": "I=-18:TP=-2:LRA=7", "acoustic": True},
}
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

# The folk preset: the sound-editor's final running order (2026-10-22), all CC0 1.0.
# Dropped on measurements: Spell me your love (swings ~12 LU throughout), Blood
# in the fridge (bright noise layer), and the album tracks with big swells.
# FMA is the license of record for Monplaisir (the archive.org copy of Fifty
# Seconds was uploaded by another account); the Wayback snapshots date it.
FOLK_SOURCES = {
    "bonjour": {"title": "Bonjour From Paris, Nantes And Montreal", "artist": "Monplaisir (Loyalty Freak Music)",
                "page": "https://freemusicarchive.org/music/Monplaisir/Bonjour_from_Paris_Nantes_and_Montreal",
                "snapshot": "http://web.archive.org/web/20240707084205/https://freemusicarchive.org/music/"
                            "Monplaisir/Bonjour_from_Paris_Nantes_and_Montreal",
                "download": "https://archive.org/download/MonplaisirBonjourFromParisNantesAndMontreal/",
                "dir": "folk/MonplaisirBonjourFromParisNantesAndMontreal"},
    "fifty": {"title": "Fifty Seconds Of Rain", "artist": "Monplaisir (Loyalty Freak Music)",
              "page": "https://freemusicarchive.org/music/Monplaisir/Fifty_seconds_of_rain",
              "snapshot": "http://web.archive.org/web/20240711011043/https://freemusicarchive.org/music/"
                          "Monplaisir/Fifty_seconds_of_rain",
              "download": "https://archive.org/download/Monplaisir-FiftySecondsOfRain/",
              "dir": "folk/Monplaisir-FiftySecondsOfRain",
              "note": "The album's cover photo is CC-BY: never show it on stream."},
    "leviathan": {"title": "Chasing the Leviathan", "artist": "jomusic (John Oestmann)",
                  "page": "https://opengameart.org/content/chasing-the-leviathan-adventure-world-music-album",
                  "download": "https://opengameart.org/sites/default/files/", "dir": "ambient/leviathan"},
}
_B = "Monplaisir - Bonjour from Paris, Nantes and Montreal - "
_F = "Monplaisir - Fifty seconds of rain - "
FOLK = [  # (title, source, file under src/<source dir>/, download name if different)
    ("Hélice", "bonjour", _B + "01 Hélice.mp3", None),
    ("Skysand", "fifty", _F + "04 Skysand.mp3", None),
    ("Ce flou entre nous", "bonjour", _B + "05 Ce flou entre nous.mp3", None),
    ("Improvisation n°8818", "bonjour", _B + "03 Improvisation n°8818.mp3", None),
    ("Close to you", "fifty", _F + "02 Close to you.mp3", None),
    ("Un creux loin", "bonjour", _B + "14 Un creux loin.mp3", None),
    ("Sun Cave Village", "leviathan", "03-0x2A723-Sun_Cave_Village.ogg", "03-0x2a723-sun_cave_village_1.ogg"),
    ("Waves", "fifty", _F + "06 Waves.mp3", None),
    ("Improvisation n°807713", "bonjour", _B + "08 Improvisation n°807713.mp3", None),
    ("Juillet", "bonjour", _B + "06 Juillet.mp3", None),
    ("Dormir rien de plus", "bonjour", _B + "04 Dormir rien de plus.mp3", None),
    ("Improvisation n°15978", "bonjour", _B + "11 Improvisation n°15978.mp3", None),
    ("Narcissus smells like headache", "fifty", _F + "03 Narcissus smells like headache.mp3", None),
    ("Moon Cave", "leviathan", "05-0x2A725-Moon_Cave.ogg", "05-0x2a725-moon_cave.ogg"),
    ("Se recourber", "bonjour", _B + "16 Se recourber.mp3", None),
    ("Poing Dauphin", "bonjour", _B + "12 Poing Dauphin.mp3", None),
    ("Improvisation n°5077153", "bonjour", _B + "13 Improvisation n°5077153.mp3", None),  # leads back into Hélice
]
# Tracks faded out early (seconds): Un creux loin's last fifth drops to about -22 LUFS.
FOLK_CUT = {"Un creux loin": 175}

PLAYING = "OBS_MEDIA_STATE_PLAYING"
ACTION = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_"


# --- the file ----------------------------------------------------------------
def preset_path(name: str, music_dir: Path = MUSIC_DIR, kind: str = "ogg") -> Path:
    return music_dir / PRESETS_DIR / f"{name}.{kind}"


def fetch(preset: str, music_dir: Path = MUSIC_DIR) -> int:
    """Download what `preset` needs that isn't on disk yet. Returns the track count."""
    if preset == "folk":
        for _, src, name, dl in FOLK:
            dest = music_dir / "src" / FOLK_SOURCES[src]["dir"] / name
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                url = FOLK_SOURCES[src]["download"] + urllib.parse.quote(dl or name)
                print(f"downloading {url}")
                urllib.request.urlretrieve(url, dest.with_suffix(".part"))
                dest.with_suffix(".part").rename(dest)
        return len(FOLK)
    src = music_dir / "src"
    src.mkdir(parents=True, exist_ok=True)
    for name, pack in PACKS.items():
        dest = src / name
        if not dest.exists():
            print(f"downloading {pack['url']}")
            tmp = dest.with_suffix(".part")
            urllib.request.urlretrieve(pack["url"], tmp)
            tmp.rename(dest)
        with zipfile.ZipFile(dest) as z:
            z.extractall(src / dest.stem)
    return len(tracks(music_dir))


def tracks(music_dir: Path = MUSIC_DIR) -> list[tuple[Path, dict]]:
    """Every lofi track under src/, with the pack it came from. The packs ship each
    track as both .mp3 and .ogg; take the mp3."""
    out = []
    for name, pack in PACKS.items():
        out += [(p, pack) for p in sorted((music_dir / "src" / Path(name).stem).rglob("*.mp3"))]
    return out


def title_of(p: Path) -> str:
    stem = p.name.split(".mp3")[0]                # "03 HoliznaCC0 - Blue Skies.mp3.mp3"
    return stem.split(" - ", 1)[-1]


def preset_tracks(preset: str, music_dir: Path = MUSIC_DIR) -> list[tuple[Path, str]]:
    """(file, title) in playing order: lofi shuffled, folk as the sound-editor ordered it."""
    if preset == "folk":
        return [(music_dir / "src" / FOLK_SOURCES[src]["dir"] / name, title) for title, src, name, _ in FOLK]
    found = [(p, title_of(p)) for p, _ in tracks(music_dir)]
    random.shuffle(found)
    return found


def duration(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True)
    return float(r.stdout)


def plan(items: list[tuple[str, float]], xfade: float = XFADE_S) -> list[dict]:
    """Running order with each track's start in the mix (tracks overlap by xfade)."""
    out, t = [], 0.0
    for title, d in items:
        out.append({"title": title, "start_s": round(t, 2), "dur_s": round(d, 2)})
        t += d - xfade
    return out


def track_filter(preset: str, cut_s: float | None = None) -> str:
    """One track's chain: level it, and for acoustic presets trim the silent ends
    and tame the plucked attacks. `cut_s` ends the track there with a 4 s fade."""
    cut = f"atrim=end={cut_s},afade=t=out:st={cut_s - 4}:d=4," if cut_s else ""  # on the original timeline
    level = f"loudnorm={PRESETS[preset]['loudness']},aresample=48000"
    if not PRESETS[preset]["acoustic"]:
        return cut + level
    trim = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.3"
    return (f"{cut}aresample=48000,aformat=channel_layouts=stereo,{trim},areverse,{trim},areverse,"
            f"{level},acompressor=ratio=2:threshold=-24dB:attack=20:release=250")


def mix_args(parts: list[Path], total_s: float, out: Path, acoustic: bool, xfade: float = XFADE_S) -> list[str]:
    args = ["ffmpeg", "-y", "-v", "error"]
    for f in parts:
        args += ["-i", str(f)]
    chain, last = [], "0:a"
    for i in range(1, len(parts)):
        chain.append(f"[{last}][{i}:a]acrossfade=d={xfade}:c1=tri:c2=tri[x{i}]")
        last = f"x{i}"
    # soft edges, so the loop point (end -> start) doesn't click
    tail = f"afade=t=in:d=2,afade=t=out:st={max(total_s - 3, 0):.2f}:d=3"
    chain.append(f"[{last}]{tail}" + (",alimiter=limit=0.84:level=false" if acoustic else "") + "[out]")
    return args + ["-filter_complex", ";".join(chain), "-map", "[out]",
                   "-c:a", "libopus", "-b:a", "160k", "-ar", "48000", str(out)]


def loudness(path: Path) -> float:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af", "ebur128=framelog=quiet",
                        "-f", "null", "-"], capture_output=True, text=True, check=True)
    return float(r.stderr.rsplit(" I:", 1)[1].split()[0])


def match_loudness(src: Path, out: Path, target: float = MIX_LUFS) -> None:
    """Gain the finished mix to `target` LUFS, with a limiter so the gain never clips."""
    gain = target - loudness(src)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-af",
                    f"volume={gain:.2f}dB,alimiter=limit=0.84:level=false",
                    "-c:a", "libopus", "-b:a", "160k", "-ar", "48000", str(out)], check=True)


def build(preset: str, music_dir: Path = MUSIC_DIR) -> dict:
    found = preset_tracks(preset, music_dir)
    missing = [str(p) for p, _ in found if not p.exists()]
    if not found or missing:
        raise SystemExit(f"{preset}: {len(missing) or 'no'} tracks missing; run `show_music.py fetch {preset}`")
    out = preset_path(preset, music_dir)
    work = out.with_name(f".build-{preset}")
    work.mkdir(parents=True, exist_ok=True)
    try:
        parts = []  # each track levelled on its own, so its real length is known
        for i, (p, title) in enumerate(found):
            part = work / f"{i:02d}.wav"
            cut = FOLK_CUT.get(title) if preset == "folk" else None
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(p), "-af", track_filter(preset, cut),
                            "-ac", "2", "-c:a", "pcm_s16le", str(part)], check=True)
            parts.append(part)
        order = plan([(title, duration(part)) for part, (_, title) in zip(parts, found)])
        total = order[-1]["start_s"] + order[-1]["dur_s"]
        tmp = work / "mix.ogg"
        subprocess.run(mix_args(parts, total, tmp, PRESETS[preset]["acoustic"]), check=True)
        match_loudness(tmp, work / "level.ogg")
        (work / "level.ogg").replace(out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    preset_path(preset, music_dir, "tracklist.json").write_text(
        json.dumps({"total_s": round(total, 2), "tracks": order}, indent=1))
    write_license(preset, found, music_dir)
    return {"file": str(out), "tracks": len(order), "total_s": round(total, 2)}


def write_license(preset: str, found: list[tuple[Path, str]], music_dir: Path = MUSIC_DIR) -> None:
    lines = [f"# Show music, {PRESETS[preset]['title']}: sources and licenses", "",
             f"Built {time.strftime('%Y-%m-%d')} by tools/live/show_music.py into {preset}.ogg.", ""]
    if preset == "folk":
        lines += ["License: CC0 1.0 Universal (https://creativecommons.org/publicdomain/zero/1.0/),",
                  "a public domain dedication: no permission or credit needed, commercial use allowed.", ""]
        for key, srcinfo in FOLK_SOURCES.items():
            names = [t for t, k, _, _ in FOLK if k == key]
            lines += [f"## {srcinfo['title']} by {srcinfo['artist']}", "", f"License of record: {srcinfo['page']}"]
            lines += [f"Dated snapshot: {srcinfo['snapshot']}"] if "snapshot" in srcinfo else []
            lines += [f"Downloaded from: {srcinfo['download']}", *([srcinfo["note"]] if "note" in srcinfo else []),
                      "", "Tracks:", *[f"- {n}" for n in names], ""]
    else:
        used = {p.name for p, _ in found}
        for name, pack in PACKS.items():
            names = sorted(title_of(p) for p, pk in tracks(music_dir) if pk is pack and p.name in used)
            if names:
                lines += [f"## {pack['title']} by {pack['artist']}", "",
                          f"Source: {pack['page']}", f"Download: {pack['url']}", "", LICENSE_NOTE,
                          "Tracks:", *[f"- {n}" for n in names], ""]
    preset_path(preset, music_dir, "LICENSE.md").write_text("\n".join(lines))


def built(music_dir: Path = MUSIC_DIR) -> list[str]:
    return [n for n in PRESETS if preset_path(n, music_dir).exists()]


# --- OBS -----------------------------------------------------------------------
def _settings(mix: Path) -> dict:
    return {"is_local_file": True, "local_file": str(mix), "looping": True,
            "restart_on_activate": False, "close_when_inactive": False}


def setup(c, music_dir: Path | None = None) -> None:
    music_dir = music_dir or MUSIC_DIR
    inputs = {i["inputName"] for i in c.call("GetInputList")["inputs"]}
    mix = preset_path((MUSIC in inputs and current_preset(c, music_dir)) or DEFAULT, music_dir)
    if not mix.exists():
        raise obsctl.ObsError(f"{mix} doesn't exist; run `show_music.py build {mix.stem}` first")
    settings = _settings(mix)
    scenes = [*show_flow.PAGES.values(), show_flow.HOLD]  # Hold too, or H goes silent
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


def current_preset(c, music_dir: Path = MUSIC_DIR) -> str | None:
    """The preset whose file OBS is playing, or None for any other file."""
    f = Path(c.call("GetInputSettings", {"inputName": MUSIC})["inputSettings"].get("local_file", ""))
    return f.stem if f.parent == music_dir / PRESETS_DIR and f.stem in PRESETS else None


def now_playing(cursor_ms: int | None, preset: str | None, music_dir: Path = MUSIC_DIR) -> str | None:
    if preset is None:
        return None
    try:
        tl = json.loads(preset_path(preset, music_dir, "tracklist.json").read_text())
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
        return {"ready": False, "on": False, "db": None, "track": None, "preset": None, "presets": []}
    muted = c.call("GetInputMute", {"inputName": MUSIC})["inputMuted"]
    media = c.call("GetMediaInputStatus", {"inputName": MUSIC})
    playing = media.get("mediaState") == PLAYING
    preset = current_preset(c, MUSIC_DIR)
    return {"ready": True, "on": playing and not muted,
            "db": round(c.call("GetInputVolume", {"inputName": MUSIC})["inputVolumeDb"], 1),
            "track": now_playing(media.get("mediaCursor"), preset, MUSIC_DIR) if playing and not muted else None,
            "preset": preset, "presets": built(MUSIC_DIR)}


def set_on(c, on: bool) -> dict:
    """Fade the music in or out. Returns {"on", "changed"}."""
    if not _ready(c):
        raise obsctl.ObsError(f"no {MUSIC!r} source in OBS; run `show_music.py setup`")
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


def use(c, preset: str, music_dir: Path | None = None) -> dict:
    """Switch to another preset: fade out, swap the file, fade back in if it was on.
    Off stays off (paused at the new mix's start)."""
    music_dir = music_dir or MUSIC_DIR
    mix = preset_path(preset, music_dir)
    if preset not in PRESETS or not mix.exists():
        raise obsctl.ObsError(f"no {preset!r} preset built; run `show_music.py build {preset}`")
    if not _ready(c):
        raise obsctl.ObsError(f"no {MUSIC!r} source in OBS; run `show_music.py setup`")
    if current_preset(c, music_dir) == preset:
        return {"preset": preset, "changed": False}
    was_on = state(c)["on"]
    if was_on:
        set_on(c, False)
    c.call("SetInputSettings", {"inputName": MUSIC, "inputSettings": _settings(mix)})
    if was_on:
        set_on(c, True)
    else:  # a media source starts playing as soon as it loads; off means paused
        c.call("TriggerMediaInputAction", {"inputName": MUSIC, "mediaAction": ACTION + "PAUSE"})
    return {"preset": preset, "changed": True}


# --- CLI -----------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, what in (("fetch", "download a preset's CC0 tracks into MUSIC_DIR/src"),
                       ("build", "level + crossfade a preset's tracks into one looping file"),
                       ("use", "switch OBS to a preset (fades out and back in if on)")):
        sub.add_parser(name, help=what).add_argument("preset", choices=list(PRESETS))
    sub.add_parser("setup", help="OBS: the Music source in every page, ducked under the mic")
    for name in ("on", "off", "status"):
        sub.add_parser(name).add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    if a.cmd == "fetch":
        print(f"{a.preset}: {fetch(a.preset)} tracks in {MUSIC_DIR / 'src'}")
        return 0
    if a.cmd == "build":
        r = build(a.preset)
        print(f"{r['file']}: {r['tracks']} tracks, {r['total_s'] / 60:.1f} min (loops)")
        return 0
    try:
        c = show_flow.connect()
        try:
            if a.cmd == "setup":
                setup(c)
                print(f"{MUSIC!r} in {', '.join([*show_flow.PAGES.values(), show_flow.HOLD])}; muted until `on`")
                return 0
            if a.cmd == "use":
                use(c, a.preset)
            elif a.cmd != "status":
                set_on(c, a.cmd == "on")
            st = state(c)
            if getattr(a, "json", False):  # `use` has no --json
                print(json.dumps(st))
            elif not st["ready"]:
                print("music: not set up (show_music.py setup)")
            else:
                print(f"music: {'ON' if st['on'] else 'off'}  preset {st['preset'] or '?'}  fader {st['db']} dB"
                      + (f"  now: {st['track']}" if st["track"] else ""))
        finally:
            c.close()
    except obsctl.ObsError as e:
        print(f"show_music: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
