#!/usr/bin/env python3
"""SHOW FLOW — run an eBay Live show's camera views from the shell (no OBS GUI).

Two looks on the vertical 1080x1920 canvas (scene collection "eBay Live"):

  Product   scene "eBay Main": the Nikon D5100 full frame, the webcam full
            width across the top third.
  Up Next   scene "Up Next": a desktop window (the next lot's page, the
            store, ...) centered on a dark card, the same webcam strip on top.

`next` fades to Up Next while the host swaps the item on the podium. `back`
refocuses the D5100 while Up Next is still on air, then fades to Product, so
viewers never see the ~5 s freeze a refocus costs (the camera takes one USB
connection: live view has to stop, autofocus, restart).

    python tools/live/show_flow.py setup [--window "Fair Hand"]
    python tools/live/show_flow.py feed start|stop|status
    python tools/live/show_flow.py next
    python tools/live/show_flow.py back
    python tools/live/show_flow.py refocus           # hidden behind Up Next if Product is on air
    python tools/live/show_flow.py golive ebay|twitch --confirm
    python tools/live/show_flow.py stop
    python tools/live/show_flow.py status

`golive` only sends video. On eBay the show starts when the host clicks
Start event in Stream manager; Twitch is public the moment it connects.
Profiles: "Ebay" (WHIP, docs/live-streaming-setup.md step 2) and "Untitled"
(Twitch, RTMP). Both keep their stream keys in OBS's own config, never here.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import obsctl  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FEED = REPO / "tools" / "d5100_webcam.sh"
FEED_PATTERN = r"^/bin/bash .*d5100_webcam\.sh$"
FEED_LOG = Path(tempfile.gettempdir()) / "d5100_feed.log"

COLLECTION = "eBay Live"
PRODUCT, UPNEXT = "eBay Main", "Up Next"
D5, WEBCAM, WINDOW, MIC = "Nikon D5100", "Webcam", "Window Capture (Xcomposite)", "Mic/Aux"
BG = "Up Next BG"
OVERLAY = "Live Overlay"  # eBay draws its own lot overlay; ours stays hidden
FADE_MS = 300
W, H = 1080, 1920
PROFILES = {"ebay": "Ebay", "twitch": "Untitled"}

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

    c.call("SetCurrentSceneTransition", {"transitionName": "Fade"})
    c.call("SetCurrentSceneTransitionDuration", {"transitionDuration": FADE_MS})
    print(f"looks ready: {PRODUCT!r} (Product), {UPNEXT!r} (Up Next); fade {FADE_MS} ms")


def cmd_feed(c, a):
    if a.action == "stop":
        feed_stop()
    elif a.action == "start":
        feed_start()
        time.sleep(3)
    print(f"D5100 feed: {'running' if feed_pids() else 'stopped'}  (log {FEED_LOG})")


def cmd_next(c, a):
    c.call("SetCurrentProgramScene", {"sceneName": UPNEXT})
    print("on air: Up Next")


def focus_note(locked: bool) -> str:
    return "focused" if locked else "autofocus couldn't lock (empty podium? low contrast at the AF point?); focus unchanged"


def cmd_back(c, a):
    if program(c) == PRODUCT:
        print("on air: Product (already)")
        return
    locked, secs = refocus_d5(c)
    c.call("SetCurrentProgramScene", {"sceneName": PRODUCT})
    print(f"{focus_note(locked)} ({secs:.1f}s); on air: Product")


def cmd_refocus(c, a):
    was = program(c)
    if was == PRODUCT:  # hide the freeze
        c.call("SetCurrentProgramScene", {"sceneName": UPNEXT})
        time.sleep(FADE_MS / 1000)
    locked, secs = refocus_d5(c)
    if was == PRODUCT:
        c.call("SetCurrentProgramScene", {"sceneName": PRODUCT})
    print(f"{focus_note(locked)} ({secs:.1f}s); on air: {program(c)}")


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
    st = c.call("GetStreamStatus")
    print(f"profile: {c.call('GetProfileList')['currentProfileName']}  "
          f"scenes: {c.call('GetSceneCollectionList')['currentSceneCollectionName']}")
    print(f"on air: {program(c)}")
    if st["outputActive"]:
        print(f"stream: LIVE {st['outputTimecode']}  "
              f"skipped {st['outputSkippedFrames']}/{st['outputTotalFrames']}")
    else:
        print("stream: off")
    print(f"D5100 feed: {'running' if feed_pids() else 'stopped'}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="build/refresh the Product and Up Next looks")
    s.add_argument("--window", help="title substring of the desktop window for Up Next")
    s.add_argument("--d5-rotation", type=float, default=270, help="D5100 mounted on its side: 270")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("feed", help="D5100 live view -> /dev/video10")
    s.add_argument("action", choices=["start", "stop", "status"])
    s.set_defaults(fn=cmd_feed)
    sub.add_parser("next", help="fade to Up Next").set_defaults(fn=cmd_next)
    sub.add_parser("back", help="refocus D5 behind Up Next, fade to Product").set_defaults(fn=cmd_back)
    sub.add_parser("refocus", help="refocus the D5 (hidden if Product is on air)").set_defaults(fn=cmd_refocus)
    s = sub.add_parser("golive", help="start streaming the eBay Live scenes")
    s.add_argument("dest", choices=sorted(PROFILES))
    s.add_argument("--confirm", action="store_true", help="required: this broadcasts")
    s.add_argument("--check", type=float, default=5, help="seconds to watch the connection")
    s.set_defaults(fn=cmd_golive)
    sub.add_parser("stop", help="stop streaming").set_defaults(fn=cmd_stop)
    sub.add_parser("status").set_defaults(fn=cmd_status)
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
