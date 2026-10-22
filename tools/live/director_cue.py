#!/usr/bin/env python3
"""Spoken shoot cues ("set", "done") played through OBS, so the host hears
them on the monitor and they land in the show mix next to the mic.

    python tools/live/director_cue.py setup        # make the clips, add the OBS source
    python tools/live/director_cue.py say set      # before a capture
    python tools/live/director_cue.py say done     # after it
    python tools/live/director_cue.py say "any words"   # a one-off clip, cached

Clips are made once with gTTS (Google's TTS; only the cue words are sent) and
kept in CUE_DIR, under $HOME so the Flatpak OBS can read them, like show_music.
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import obsctl  # noqa: E402
import show_flow  # noqa: E402

CUE = "Director Cue"
CUE_DIR = Path(os.environ.get("DIRECTOR_CUE_DIR") or Path.home() / "Music" / "show-cues")
WORDS = ("set", "done")
ACTION = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_"


def clip(words: str) -> Path:
    path = CUE_DIR / (re.sub(r"[^a-z0-9]+", "-", words.lower()).strip("-") + ".mp3")
    if not path.exists():
        from gtts import gTTS
        CUE_DIR.mkdir(parents=True, exist_ok=True)
        gTTS(words).save(str(path))
    return path


def _settings(path: Path) -> dict:
    return {"is_local_file": True, "local_file": str(path), "looping": False,
            "restart_on_activate": False, "close_when_inactive": False}


def setup(c) -> None:
    first = clip(WORDS[0])
    for w in WORDS[1:]:
        clip(w)
    scenes = [*show_flow.PAGES.values(), show_flow.HOLD]
    if not any(i["inputName"] == CUE for i in c.call("GetInputList")["inputs"]):
        c.call("CreateInput", {"sceneName": scenes[0], "inputName": CUE,
                               "inputKind": "ffmpeg_source", "inputSettings": _settings(first)})
        c.call("TriggerMediaInputAction", {"inputName": CUE, "mediaAction": ACTION + "STOP"})
    # monitor and output: in the host's headphones and in the show mix
    c.call("SetInputAudioMonitorType", {"inputName": CUE,
                                        "monitorType": "OBS_MONITORING_TYPE_MONITOR_AND_OUTPUT"})
    have = {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}
    for scene in scenes:  # every page, so a cue plays whatever is on air
        if scene in have:
            show_flow.item(c, scene, CUE)


def say(c, words: str) -> None:
    c.call("SetInputSettings", {"inputName": CUE, "inputSettings": _settings(clip(words))})
    time.sleep(0.2)  # let the source load the new file
    c.call("TriggerMediaInputAction", {"inputName": CUE, "mediaAction": ACTION + "RESTART"})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    p = sub.add_parser("say")
    p.add_argument("words", nargs="+")
    a = ap.parse_args(argv)
    c = obsctl._connect("127.0.0.1", 4455)
    if a.cmd == "setup":
        setup(c)
        print(f"{CUE}: ready ({', '.join(WORDS)}) from {CUE_DIR}")
    else:
        say(c, " ".join(a.words))
        print(f"cue: {' '.join(a.words)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
