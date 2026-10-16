#!/usr/bin/env python3
"""SHOW FLOW — run an eBay Live show's camera views from the shell (no OBS GUI).

A show is three pages on the vertical 1080x1920 canvas (scene collection
"eBay Live"). The pager puts one on air with a single command:

  intro   scene "Intro": the pre-show card ("Starting soon") on the dark
          background. No cameras.
  item    scene "eBay Main": the Nikon D5100 full frame, the webcam full
          width across the top third.
  next    scene "Up Next": the pause / next-item card. A desktop window (the
          next lot's page, the store, ...) centered on the dark background,
          the same webcam strip on top.

`page next` fades to Up Next while the host swaps the item on the podium.
`page item` refocuses the D5100 while the current page is still on air
(starting the feed if it's off), then fades to the item view, so viewers
never see the ~5 s freeze a refocus costs (the camera takes one USB
connection: live view has to stop, autofocus, restart).

    python tools/live/show_flow.py setup [--window "Fair Hand"] [--intro-text "Starting\\nsoon"]
    python tools/live/show_flow.py feed start|stop|status
    python tools/live/show_flow.py page intro|item|next [--json] [--no-refocus]
    python tools/live/show_flow.py next              # = page next
    python tools/live/show_flow.py back              # = page item
    python tools/live/show_flow.py refocus [--json]  # hidden behind Up Next if item is on air
    python tools/live/show_flow.py golive ebay|twitch --confirm
    python tools/live/show_flow.py stop
    python tools/live/show_flow.py status [--json]

`--json` prints the same objects webapp/pager.py serves at /api/pager, for
scripts and agents. Page switches and refocuses take a lock
($XDG_RUNTIME_DIR/show_flow.lock), so the shell and the web app never drive
the camera at the same time; a second caller waits for the first.

`golive` only sends video. On eBay the show starts when the host clicks
Start event in Stream manager; Twitch is public the moment it connects.
Profiles: "Ebay" (WHIP, docs/live-streaming-setup.md step 2) and "Untitled"
(Twitch, RTMP). Both keep their stream keys in OBS's own config, never here.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import obsctl  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FEED = REPO / "tools" / "d5100_webcam.sh"
FEED_PATTERN = r"^/bin/bash .*d5100_webcam\.sh$"
FEED_LOG = Path(tempfile.gettempdir()) / "d5100_feed.log"

COLLECTION = "eBay Live"
INTRO, PRODUCT, UPNEXT = "Intro", "eBay Main", "Up Next"
# The pager's pages, in running order: what callers say -> OBS scene name.
PAGES = {"intro": INTRO, "item": PRODUCT, "next": UPNEXT}
D5, WEBCAM, WINDOW, MIC = "Nikon D5100", "Webcam", "Window Capture (Xcomposite)", "Mic/Aux"
BG = "Up Next BG"
INTRO_TEXT = "Intro Text"
OVERLAY = "Live Overlay"  # eBay draws its own lot overlay; ours stays hidden
FADE_MS = 300
W, H = 1080, 1920
PROFILES = {"ebay": "Ebay", "twitch": "Untitled"}
# Up Next shows whatever the captured window shows *now*: one browser window
# can be on the store page at setup and on the stream itself (a feedback
# loop), mail or My eBay later. Before Up Next goes on air the pager reads
# the window's live title (OBS's own list can be stale) and hides the window
# when it matches this, or can't be read; viewers then get the dark card and
# the webcam strip.
PRIVATE_WINDOW = re.compile(
    r"twitch|ebay live|stream manager|show pager|gmail|inbox|mail|private browsing|my ebay|"
    r"bids & offers|seller hub|orders|paypal|bank|konsole|terminal|bash|claude", re.I)

# One lock for every caller (shell, web app), so it can't live in a per-process TMPDIR.
LOCK = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "show_flow.lock"
LOCK_WAIT = 30  # seconds: a refocus takes ~5, so a queued click still goes through

# webcam: full canvas width, anchored to the top (16:9 -> 1080x608)
WEBCAM_BOX = {"positionX": 0, "positionY": 0, "alignment": 5, "rotation": 0,
              "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 4,
              "boundsWidth": W, "boundsHeight": 608, "cropToBounds": False}


# --- OBS helpers -------------------------------------------------------------
def connect() -> obsctl.ObsClient:
    return obsctl.ObsClient(os.environ.get("OBS_WS_HOST", "127.0.0.1"),
                            int(os.environ.get("OBS_WS_PORT", "4455")), obsctl.load_password())


def item(c, scene: str, source: str) -> int:
    """Scene item id for source in scene, adding it if it isn't there."""
    for i in c.call("GetSceneItemList", {"sceneName": scene})["sceneItems"]:
        if i["sourceName"] == source:
            return i["sceneItemId"]
    return c.call("CreateSceneItem", {"sceneName": scene, "sourceName": source})["sceneItemId"]


def set_box(c, scene, source, box=None, enabled=True):
    i = item(c, scene, source)
    if box:
        c.call("SetSceneItemTransform", {"sceneName": scene, "sceneItemId": i, "sceneItemTransform": box})
    c.call("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": i, "sceneItemEnabled": enabled})
    return i


def stack(c, scene, sources_bottom_up):
    for idx, src in enumerate(sources_bottom_up):
        c.call("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": item(c, scene, src),
                                     "sceneItemIndex": idx})


def program(c) -> str:
    return c.call("GetCurrentProgramScene")["currentProgramSceneName"]


class Busy(obsctl.ObsError):
    """Another page switch or refocus held the lock past LOCK_WAIT."""


@contextmanager
def exclusive():
    with open(LOCK, "w") as f:
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise Busy(f"another page switch or refocus is still running after {LOCK_WAIT}s")
                time.sleep(0.1)
        yield  # the lock goes with the file when it closes


def d5_hash(c) -> str:
    d = c.call("GetSourceScreenshot", {"sourceName": D5, "imageFormat": "jpg",
                                       "imageWidth": 160, "imageCompressionQuality": 90})
    return hashlib.md5(d["imageData"].encode()).hexdigest()


# --- D5100 feed (tools/d5100_webcam.sh) ----------------------------------------
def feed_pids() -> list[int]:
    r = subprocess.run(["pgrep", "-f", FEED_PATTERN], capture_output=True, text=True)
    return [int(p) for p in r.stdout.split()]


def feed_stop() -> None:
    for p in feed_pids():
        os.kill(p, 15)
    subprocess.run(["pkill", "-x", "gphoto2"])
    deadline = time.monotonic() + 10
    while subprocess.run(["pgrep", "-x", "gphoto2"], capture_output=True).returncode == 0:
        if time.monotonic() > deadline:
            raise obsctl.ObsError("gphoto2 didn't release the camera within 10s")
        time.sleep(0.05)


def feed_start() -> None:
    if feed_pids():
        return
    log = open(FEED_LOG, "ab")
    subprocess.Popen([str(FEED)], cwd=REPO, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     start_new_session=True)


def refocus_d5(c) -> tuple[bool, float]:
    """Stop live view, autofocus, restart; returns (focus locked, seconds until
    OBS shows moving D5 frames again). The D5100 refuses autofocus ("Unspecified
    error") when its AF point sees nothing with contrast, e.g. an empty podium;
    focus then stays where it was."""
    t = time.monotonic()
    feed_stop()
    found = subprocess.run(["gphoto2", "--auto-detect"], capture_output=True, text=True).stdout
    if "usb:" not in found:  # fail now, not after the 30 s wait for frames below
        raise obsctl.ObsError("D5100 not found on USB; is it switched on and plugged in?")
    af = subprocess.run(["gphoto2", "--set-config", "autofocusdrive=1"], capture_output=True)
    time.sleep(0.2)
    frozen = d5_hash(c)
    feed_start()
    deadline = time.monotonic() + 30
    while d5_hash(c) == frozen:
        if time.monotonic() > deadline:
            raise obsctl.ObsError(f"D5100 feed didn't come back within 30s; see {FEED_LOG}")
        time.sleep(0.05)
    time.sleep(0.25)  # let live view settle its exposure before it goes on air
    return af.returncode == 0, time.monotonic() - t


# --- commands ----------------------------------------------------------------
def cmd_setup(c, a):
    if c.call("GetSceneCollectionList")["currentSceneCollectionName"] != COLLECTION:
        c.call("SetCurrentSceneCollection", {"sceneCollectionName": COLLECTION})
        time.sleep(2)
    inputs = {i["inputName"] for i in c.call("GetInputList")["inputs"]}
    missing = {D5, WEBCAM, WINDOW} - inputs
    if missing:
        raise obsctl.ObsError(f"scene collection {COLLECTION!r} lacks {sorted(missing)}; "
                              "install tools/live/obs/scenes/eBay_Live.json (docs/live-streaming-setup.md)")
    if a.window:
        props = c.call("GetInputPropertiesListPropertyItems",
                       {"inputName": WINDOW, "propertyName": "capture_window"})["propertyItems"]
        match = [p for p in props if a.window.lower() in p["itemName"].lower()]
        if not match:
            raise obsctl.ObsError(f"no open window matches {a.window!r}")
        c.call("SetInputSettings", {"inputName": WINDOW, "inputSettings": {"capture_window": match[0]["itemValue"]}})
        print(f"window: {match[0]['itemName']}")

    # Product: D5 rotated to portrait (the camera is mounted on its side), cover-cropped
    set_box(c, PRODUCT, D5, {"positionX": W / 2, "positionY": H / 2, "alignment": 0, "rotation": a.d5_rotation,
                             "boundsType": "OBS_BOUNDS_SCALE_OUTER", "boundsAlignment": 0,
                             "boundsWidth": H, "boundsHeight": W, "cropToBounds": True})
    set_box(c, PRODUCT, WEBCAM, WEBCAM_BOX)
    set_box(c, PRODUCT, WINDOW, enabled=False)
    if OVERLAY in inputs:
        set_box(c, PRODUCT, OVERLAY, enabled=False)
    stack(c, PRODUCT, [D5, WEBCAM])

    # Up Next: dark card, window centered, webcam strip on top
    if UPNEXT not in {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}:
        c.call("CreateScene", {"sceneName": UPNEXT})
    if BG not in inputs:
        c.call("CreateInput", {"sceneName": UPNEXT, "inputName": BG, "inputKind": "color_source_v3",
                               "inputSettings": {"color": 0xFF1E1E1E, "width": W, "height": H}})
    item(c, UPNEXT, MIC)
    set_box(c, UPNEXT, WINDOW, {"positionX": W / 2, "positionY": H / 2, "alignment": 0, "rotation": 0,
                                "scaleX": 1, "scaleY": 1, "boundsType": "OBS_BOUNDS_SCALE_INNER",
                                "boundsAlignment": 0, "boundsWidth": W - 40, "boundsHeight": H - 800})
    set_box(c, UPNEXT, WEBCAM, WEBCAM_BOX)
    stack(c, UPNEXT, [BG, WINDOW, WEBCAM])

    # Intro: the pre-show card, text centered on the same dark background
    if INTRO not in {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}:
        c.call("CreateScene", {"sceneName": INTRO})
    text = {"text": a.intro_text.replace("\\n", "\n"), "font": {"face": "Sans Serif", "style": "Bold", "size": 150, "flags": 1},
            "color1": 0xFFFFFFFF, "color2": 0xFFFFFFFF, "drop_shadow": True}
    if INTRO_TEXT in inputs:
        c.call("SetInputSettings", {"inputName": INTRO_TEXT, "inputSettings": text})
    else:
        c.call("CreateInput", {"sceneName": INTRO, "inputName": INTRO_TEXT,
                               "inputKind": "text_ft2_source_v2", "inputSettings": text})
    item(c, INTRO, BG)
    item(c, INTRO, MIC)
    set_box(c, INTRO, INTRO_TEXT, {"positionX": W / 2, "positionY": H / 2, "alignment": 0, "rotation": 0})
    stack(c, INTRO, [BG, INTRO_TEXT])

    c.call("SetCurrentSceneTransition", {"transitionName": "Fade"})
    c.call("SetCurrentSceneTransitionDuration", {"transitionDuration": FADE_MS})
    print("pages ready: " + ", ".join(f"{k} = {v!r}" for k, v in PAGES.items()) + f"; fade {FADE_MS} ms")


def cmd_feed(c, a):
    if a.action == "stop":
        feed_stop()
    elif a.action == "start":
        feed_start()
        time.sleep(3)
    print(f"D5100 feed: {'running' if feed_pids() else 'stopped'}  (log {FEED_LOG})")


# --- the pager: the API the CLI below and webapp/pager.py share --------------
def page_of(scene: str) -> str | None:
    return next((k for k, v in PAGES.items() if v == scene), None)


def window_title(c) -> str | None:
    """The Up Next window's current title, or None if it's gone."""
    v = c.call("GetInputSettings", {"inputName": WINDOW})["inputSettings"].get("capture_window", "")
    wid = v.split("\r\n", 1)[0].strip()
    if not wid.isdigit():
        return None
    r = subprocess.run(["xprop", "-id", wid, "_NET_WM_NAME"], capture_output=True, text=True)
    title = r.stdout.partition("=")[2].strip().strip('"') if r.returncode == 0 else ""
    return title or None


def guard_window(c) -> dict:
    """Show or hide the Up Next window by its live title (PRIVATE_WINDOW)."""
    title = window_title(c)
    safe = bool(title) and not PRIVATE_WINDOW.search(title)
    set_box(c, UPNEXT, WINDOW, enabled=safe)
    return {"title": title, "shown": safe}


def state(c) -> dict:
    """What's on air. `page` is None when the program scene isn't one of the
    pager's (someone switched in OBS by hand)."""
    scene = program(c)
    st = c.call("GetStreamStatus")
    return {"page": page_of(scene), "scene": scene,
            "pages": list(PAGES), "live": st["outputActive"],
            "timecode": st["outputTimecode"] if st["outputActive"] else None,
            "feed": bool(feed_pids())}


def go(c, page: str, refocus: bool = True) -> dict:
    """Put a page on air. Going to `item` from another page refocuses the
    D5100 first, while the current page still hides it; refocus=False skips
    that (rehearsal or battery swap: the item view goes up with whatever the
    D5 source shows, which with the camera off is its last frame, frozen).
    A page that's already on air is left alone."""
    if page not in PAGES:
        raise ValueError(f"unknown page {page!r}; pages: {', '.join(PAGES)}")
    with exclusive():
        out = {"page": page, "changed": False, "focused": None, "refocus_s": None}
        if program(c) == PAGES[page]:
            return out
        if page == "next":
            out["window"] = guard_window(c)
        if page == "item" and refocus:
            out["focused"], secs = refocus_d5(c)
            out["refocus_s"] = round(secs, 1)
        c.call("SetCurrentProgramScene", {"sceneName": PAGES[page]})
        out["changed"] = True
        return out


def refocus(c) -> dict:
    """Refocus the D5100 without changing page. If the item view is on air
    it hides behind Up Next for the ~5 s the camera is frozen."""
    with exclusive():
        hide = program(c) == PRODUCT
        if hide:
            guard_window(c)
            c.call("SetCurrentProgramScene", {"sceneName": UPNEXT})
            time.sleep(FADE_MS / 1000)
        locked, secs = refocus_d5(c)
        if hide:
            c.call("SetCurrentProgramScene", {"sceneName": PRODUCT})
        return {"focused": locked, "refocus_s": round(secs, 1)}


def focus_note(locked: bool) -> str:
    return "focused" if locked else "autofocus couldn't lock (empty podium? low contrast at the AF point?); focus unchanged"


def cmd_page(c, a):
    r = go(c, a.page, refocus=not a.no_refocus)
    if a.json:
        print(json.dumps({**state(c), "switch": r}))
    elif not r["changed"]:
        print(f"on air: {a.page} (already)")
    elif r["focused"] is None:
        print(f"on air: {a.page}")
        w = r.get("window")
        if w and not w["shown"]:
            print(f"  window hidden: {w['title'] or '(closed)'!r} isn't safe to show; card + webcam only")
    else:
        print(f"{focus_note(r['focused'])} ({r['refocus_s']}s); on air: {a.page}")


def cmd_refocus(c, a):
    r = refocus(c)
    if a.json:
        print(json.dumps({**state(c), "refocus": r}))
    else:
        print(f"{focus_note(r['focused'])} ({r['refocus_s']}s); on air: {program(c)}")


def cmd_golive(c, a):
    if not a.confirm:
        raise obsctl.ObsError(f"going live on {a.dest} is public-facing; re-run with --confirm")
    if c.call("GetStreamStatus")["outputActive"]:
        raise obsctl.ObsError("already streaming; `stop` first")
    profile = PROFILES[a.dest]
    if c.call("GetProfileList")["currentProfileName"] != profile:
        c.call("SetCurrentProfile", {"profileName": profile})
        time.sleep(2)
    if c.call("GetSceneCollectionList")["currentSceneCollectionName"] != COLLECTION:
        c.call("SetCurrentSceneCollection", {"sceneCollectionName": COLLECTION})
        time.sleep(2)
    vs = c.call("GetVideoSettings")
    if (vs["baseWidth"], vs["baseHeight"]) != (W, H):  # Twitch profile defaults to 16:9
        c.call("SetVideoSettings", {"baseWidth": W, "baseHeight": H, "outputWidth": 720,
                                    "outputHeight": 1280, "fpsNumerator": 30, "fpsDenominator": 1})
    c.call("StartStream")
    time.sleep(a.check)
    st = c.call("GetStreamStatus")
    if not st["outputActive"] or st["outputBytes"] == 0:
        raise obsctl.ObsError(f"stream didn't connect: {obsctl.redact(st)}")
    kbps = st["outputBytes"] * 8 / 1000 / max(st["outputDuration"] / 1000, 0.001)
    print(f"LIVE on {a.dest} ({profile}): {kbps:.0f} kbps, "
          f"skipped {st['outputSkippedFrames']}/{st['outputTotalFrames']} frames")
    if a.dest == "ebay":
        print("video is flowing; the show starts when you click Start event in Stream manager")


def cmd_stop(c, a):
    if c.call("GetStreamStatus")["outputActive"]:
        c.call("StopStream")
    deadline = time.monotonic() + 10
    while c.call("GetStreamStatus")["outputActive"] and time.monotonic() < deadline:
        time.sleep(0.25)
    print("stream: off")


def cmd_status(c, a):
    if a.json:
        print(json.dumps(state(c)))
        return
    st = c.call("GetStreamStatus")
    print(f"profile: {c.call('GetProfileList')['currentProfileName']}  "
          f"scenes: {c.call('GetSceneCollectionList')['currentSceneCollectionName']}")
    scene = program(c)
    page = page_of(scene)
    print(f"on air: {page + ' (' + scene + ')' if page else scene}")
    if st["outputActive"]:
        print(f"stream: LIVE {st['outputTimecode']}  "
              f"skipped {st['outputSkippedFrames']}/{st['outputTotalFrames']}")
    else:
        print("stream: off")
    print(f"D5100 feed: {'running' if feed_pids() else 'stopped'}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="build/refresh the intro, item and next pages")
    s.add_argument("--window", help="title substring of the desktop window for Up Next")
    s.add_argument("--intro-text", default="Starting\nsoon", help="the pre-show card's text; \\n breaks a line")
    s.add_argument("--d5-rotation", type=float, default=270, help="D5100 mounted on its side: 270")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("feed", help="D5100 live view -> /dev/video10")
    s.add_argument("action", choices=["start", "stop", "status"])
    s.set_defaults(fn=cmd_feed)
    s = sub.add_parser("page", help="put a page on air (item refocuses the D5 first, hidden)")
    s.add_argument("page", choices=list(PAGES))
    s.add_argument("--json", action="store_true")
    s.add_argument("--no-refocus", action="store_true", help="item without the D5 refocus (no camera)")
    s.set_defaults(fn=cmd_page)
    sub.add_parser("next", help="= page next").set_defaults(fn=cmd_page, page="next", json=False,
                                                            no_refocus=False)
    sub.add_parser("back", help="= page item").set_defaults(fn=cmd_page, page="item", json=False,
                                                            no_refocus=False)
    s = sub.add_parser("refocus", help="refocus the D5 (hidden if the item page is on air)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_refocus)
    s = sub.add_parser("golive", help="start streaming the eBay Live scenes")
    s.add_argument("dest", choices=sorted(PROFILES))
    s.add_argument("--confirm", action="store_true", help="required: this broadcasts")
    s.add_argument("--check", type=float, default=5, help="seconds to watch the connection")
    s.set_defaults(fn=cmd_golive)
    sub.add_parser("stop", help="stop streaming").set_defaults(fn=cmd_stop)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)
    a = p.parse_args(argv)
    try:
        c = connect()
        try:
            a.fn(c, a)
        finally:
            c.close()
    except obsctl.ObsError as e:
        print(f"show_flow: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
