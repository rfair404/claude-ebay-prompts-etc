#!/usr/bin/env python3
"""SHOW FLOW — run an eBay Live show's camera views from the shell (no OBS GUI).

A show is three pages on the vertical 1080x1920 canvas (scene collection
"eBay Live"). The pager puts one on air with a single command:

  intro   scene "Intro": the pre-show card ("Starting soon") on the dark
          background. No cameras.
  item    scene "eBay Main": the Nikon D5100 full frame, the webcam full
          width across the top third.
  closeup scene "Close-up": the OM TG-7 (over Wi-Fi, tools/live/tg7cam)
          under the same webcam strip. `tg7 start` runs its feed.
  next    scene "Up Next": the pause / next-item card. A desktop window (the
          next lot's page, the store, ...) centered on the dark background,
          the same webcam strip on top.

  desktop scene "Desktop with Face Cam": one app window (Claude, say) on
          white under the webcam strip; `setup --desktop-window` picks it.
  webcam  scene "Webcam Only": the fallback. The webcam alone, filling the
          frame, with the mic. Never touches the D5100.
  multi   scene "Multi View": everything at once. The D5100 (left) and the
          webcam (right) side by side in the top 1080x810, the desktop
          page's window under them. Built with the desktop page.

Every `page` is one scene switch (300 ms fade): it never waits for the
camera. `page next` fades to Up Next while the host swaps the item on the
podium; `page item` fades straight back. Refocusing is its own command,
`refocus` (key R on the pager page): on the item or multi view it covers
the camera with Up Next for the ~5 s freeze (the camera takes one USB
connection: live view has to stop, autofocus, restart), then fades back. `page item
--refocus` does both in one go, refocusing behind the current page first.
A failed refocus never leaves the D5 feed stopped. On the closeup page,
`refocus` is the TG-7's half-press instead: its live view keeps running, so
nothing is hidden. `wb` reads or sets the D5100's white balance the same
way, hidden like a refocus; it never touches the TG-7.

`hold` freezes the camera page on air (item or closeup) as a still, with the
live webcam strip still on top, so the host can turn the item without
viewers watching it spin; `release` fades back to the live camera. `step`
walks the item sequence one key at a time:

    item (D5100, wide) -> closeup (TG-7) -> hold (turn the item now)
      -> item (the new side, wide) -> closeup -> hold -> ...

From a hold, `step` releases to the *other* camera than the one held; from
any other page it goes to item.

OBS can sit black or frozen on a v4l2 camera whose device has frames again.
`feed start` and a refocus make OBS reopen the D5 source when the feed has
frames but OBS's picture doesn't move; `reconnect` (the pager's Reconnect
cameras button) reopens the D5 and the webcam by hand.

`watch start` runs the camera watch in the background. Once a second it
looks at the D5100, the webcam and the TG-7. A camera that stays bad for 3
looks (feed stopped, no frames, no picture in OBS, or a frozen one) gets a
black panel over its slot on every page that shows it, so viewers see
black instead of a frozen frame. The panel comes off after 5 good looks in
a row. While a panel is on and the device has frames, the watch reopens
the camera in OBS (`reconnect`), at most every 15 s. It leaves the D5 alone
during a refocus, WB change or still, and while the stand-in is on.
Panels are only on while a watch runs: it clears them when it ends, and
`watch stop` clears them too.

    python tools/live/show_flow.py setup [--window "Fair Hand"] [--intro-text "Starting\\nsoon"]
    python tools/live/show_flow.py feed start|stop|status
    python tools/live/show_flow.py reconnect [d5|webcam]  # OBS reopens a black/frozen camera
    python tools/live/show_flow.py tg7 start|stop|status   # TG-7 live view -> OBS (tg7cam stream)
    python tools/live/show_flow.py watch start|stop|status # black panel over any camera that drops
    python tools/live/show_flow.py page intro|item|closeup|next|desktop|webcam|multi [--json] [--refocus]
    python tools/live/show_flow.py next              # = page next
    python tools/live/show_flow.py back              # = page item
    python tools/live/show_flow.py hold [--json]     # freeze the camera page on air
    python tools/live/show_flow.py release [--to item|closeup] [--json]
    python tools/live/show_flow.py step [--json]     # item -> closeup -> hold -> item ...
    python tools/live/show_flow.py refocus [--json]  # hidden behind Up Next if item is on air
    python tools/live/show_flow.py wb [PRESET] [--json]  # D5100 white balance: auto, daylight, tungsten, ...
    python tools/live/show_flow.py standin on|off    # white panel for the D5100 (dead battery)
    python tools/live/show_flow.py golive ebay|twitch --confirm   # each independently; both can run
    python tools/live/show_flow.py stop [ebay|twitch]               # default: both
    python tools/live/show_flow.py twitch-setup                     # once, then paste the URL in OBS
    python tools/live/show_flow.py status [--json]

`--json` prints the same objects webapp/pager.py serves at /api/pager, for
scripts and agents. A refocus or `wb` takes a lock ($XDG_RUNTIME_DIR/show_flow.lock),
so the shell and the web app never drive the camera at the same time; a
second refocus is refused at once (Busy), never queued. Page switches don't
take it: a switch made during a refocus goes on air immediately, and the
refocus then leaves that page alone.

`golive` only sends video. On eBay the show starts when the host clicks
Start event in Stream manager; Twitch is public the moment it connects.
Both run from the "Ebay" profile: eBay on the stream output (WHIP,
docs/live-streaming-setup.md step 2), Twitch on the recording output (see
DESTS below). The keys stay in OBS's own config, never here.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows: the web app (and its tests) import this module there too
    fcntl = None
    import msvcrt

sys.path.insert(0, str(Path(__file__).resolve().parent))
import obsctl  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FEED = REPO / "tools" / "d5100_webcam.sh"
FEED_PATTERN = r"^/bin/bash .*d5100_webcam\.sh$"
FEED_LOG = Path(tempfile.gettempdir()) / "d5100_feed.log"
# The feed script writes a frame to $SNAP (default ${TMPDIR:-/tmp}/<this>)
# every 2 s while frames flow. Its age tells "running" from "running but
# failing every 2 s" (camera off, unplugged).
FEED_SNAP = "d5100_latest.jpg"
FEED_STALE = 10  # seconds without a new frame before status says so

COLLECTION = "eBay Live"
INTRO, PRODUCT, UPNEXT = "Intro", "eBay Main", "Up Next"
CLOSEUP = "Close-up"
DESKTOP = "Desktop with Face Cam"
WEBCAM_ONLY = "Webcam Only"
MULTI = "Multi View"
# The pager's pages, in running order: what callers say -> OBS scene name.
PAGES = {"intro": INTRO, "item": PRODUCT, "closeup": CLOSEUP, "next": UPNEXT,
         "desktop": DESKTOP, "webcam": WEBCAM_ONLY, "multi": MULTI}
# Pages with the D5100 on air: a refocus there hides behind Up Next.
D5_PAGES = {"item": PRODUCT, "multi": MULTI}
D5, WEBCAM, WINDOW, MIC = "Nikon D5100", "Webcam", "Window Capture (Xcomposite)", "Mic/Aux"

# The OM TG-7 close-up camera, over its own Wi-Fi (tools/live/tg7cam). Its
# feed lands on /dev/video11 when that loopback device exists (a v4l2 source,
# like the D5), else on UDP for an OBS Media Source; `setup` builds the source
# to match, and `tg7 start` runs `tg7cam stream`, which picks the same way.
TG7 = "OM TG-7"
TG7_CAM = Path(__file__).resolve().parent / "tg7cam"
TG7_PATTERN = r"python[0-9.]* \S*tg7cam stream"
TG7_LOG = Path(tempfile.gettempdir()) / "tg7_feed.log"
# 640x480 runs 25 fps; 1280x960 is sharper but 15 fps (measured 2026-10-21)
TG7_SIZE = "0640x0480"
# below the webcam strip, the part of the canvas the strip doesn't cover
TG7_TOP = 608

# Hold: a still of the camera page on air, the live webcam strip on top (it
# covers its own frozen copy exactly), so the host can turn the item off
# camera. The still is OBS's own screenshot of the scene, written and read
# by OBS, so the folder is one the Flatpak OBS can reach: its config dir.
HOLD, HOLD_FRAME = "Hold", "Hold Frame"
HOLDABLE = ("item", "closeup")  # pages with a camera worth freezing
HELD = "show_flow_held"  # Hold Frame setting: the page the still was taken of
HOLD_DIR = Path(os.environ.get("OBS_CONFIG_DIR") or obsctl.DEFAULT_CONFIG_DIR) / "show_flow_hold"
# step: what the item sequence does next, by what's on air
SWAP = {"item": "closeup", "closeup": "item"}
BG = "Up Next BG"
INTRO_TEXT = "Intro Text"
# White panel in the item view in place of the D5100 (dead battery, rehearsal).
# Its visibility in OBS *is* the switch, so every caller sees the same state.
STANDIN = "D5 Stand-in"
# The desktop page: one app window (e.g. Claude) under the webcam strip, on
# white. Its own capture, not WINDOW: the host picks it deliberately, so the
# PRIVATE_WINDOW guard doesn't apply. `setup --desktop-window` picks it.
DESKTOP_WIN = "Desktop Window"
OVERLAY = "Live Overlay"  # eBay draws its own lot overlay; ours stays hidden
FADE_MS = 300
W, H = 1080, 1920
# Both destinations run at once from the one "Ebay" profile: eBay on OBS's
# stream output (WHIP), Twitch on its *recording* output, set to Custom Output
# (FFmpeg) -> Output to URL. OBS has a single stream output, and this ffmpeg
# (7.1) has no WHIP muxer, so a relay can't carry eBay. The cost: no local
# recording while Twitch is live, and a second x264 encode. `twitch-setup`
# writes the non-secret half of that output; the user pastes the
# rtmp://live.twitch.tv/app/<key> URL into OBS (keys never pass through here).
SHOW_PROFILE = "Ebay"
DESTS = {"ebay": "Stream", "twitch": "Record"}  # destination -> OBS output
# Twitch on its own, the older way: the "Untitled" profile's stream output
# (signed in to Twitch in OBS). go_live("twitch") falls back to it when the
# recording output has no Twitch URL and eBay isn't live. It can't run
# alongside eBay: one profile at a time.
TWITCH_PROFILE = "Untitled"
TWITCH_OUT = {"RecType": "FFmpeg", "FFOutputToFile": "false", "FFFormat": "flv",
              "FFFormatMimeType": "video/x-flv", "FFExtension": "flv",
              "FFVEncoder": "libx264", "FFVEncoderId": "27",      # AV_CODEC_ID_H264
              "FFAEncoder": "aac", "FFAEncoderId": "86018",       # AV_CODEC_ID_AAC
              "FFVBitrate": "3000", "FFVGOPSize": "60", "FFABitrate": "160",
              "FFVCustom": "preset=veryfast", "FFUseRescale": "false", "FFAudioMixes": "1"}
# Up Next is the only page that may ever show the desktop window (WINDOW);
# every other page only ever hides it. Up Next shows whatever the captured
# window shows *now*: one browser window can be on the store page at setup
# and on the stream itself (a feedback loop), mail or My eBay later. So
# before Up Next goes on air the pager reads the window's live title (OBS's
# own list can be stale) and shows the window only when the title matches
# SAFE_WINDOW and not PRIVATE_WINDOW. Anything else (a full-screen capture,
# the desktop shell, Google Ads, GitHub, a closed window) is hidden, and
# viewers get the dark card and the webcam strip.
#
# SAFE_WINDOW, the allowlist: public pages of ours. eBay titles listing,
# search and store pages "<title> | eBay" and "<store> | eBay Stores".
SAFE_WINDOW = re.compile(r"\| eBay\b|pop[’']?s games|popsgames", re.I)
# PRIVATE_WINDOW, a veto on top: hidden even with "| eBay" in the title
# (eBay Live, Seller Hub, My eBay, Twitch, mail, terminals, ...).
PRIVATE_WINDOW = re.compile(
    r"twitch|ebay live|stream manager|show pager|gmail|inbox|mail|private browsing|my ebay|"
    r"bids & offers|seller hub|orders|paypal|bank|konsole|terminal|bash|claude", re.I)

# One lock for every caller (shell, web app), so it can't live in a per-process TMPDIR.
LOCK = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "show_flow.lock"
# Refocuses don't queue: a second one is refused. The short wait only rides
# out refocusing() probing the lock for an instant.
LOCK_WAIT = 0.5
FRAMES_WAIT = 30  # seconds for the D5's frames to come back after a refocus
GPHOTO_TIMEOUT = 15  # a wedged camera mustn't hold the lock (and the feed) forever
STILL_TIMEOUT = 30  # still_d5: autofocus, shutter and the download of a full-size frame
SAVED = re.compile(r"^Saving file as (.+)$", re.M)  # gphoto2's line per downloaded file
REOPEN_GRACE = 3  # seconds after reopen_d5 before the feed's own frame is taken as proof
FEED_START_WAIT = 15  # `feed start`: seconds to wait for frames before reopening in OBS
D5_DEVICE = "/dev/video10"  # the feed's v4l2loopback device (tools/d5100_webcam.sh DEV)
# The D5100's white balance (PTP 0x5005). `wb` checks a preset against the
# choices the camera reports; libgphoto2 labels the D5100's as Automatic,
# Daylight, Fluorescent, Tungsten, Flash, Cloudy, Shade, Preset
# (camlibs/ptp2/config.c whitebalance[], camlibs/ptp2/cameras/nikon-d5100.txt).
WB_CONFIG = "whitebalance"
WB_ALIASES = {"auto": "Automatic", "daylight": "Daylight", "cloudy": "Cloudy", "shade": "Shade",
              "tungsten": "Tungsten", "incandescent": "Tungsten", "fluorescent": "Fluorescent",
              "flash": "Flash"}
# The v4l2 cameras `reconnect` reopens -> device to use if OBS's setting is empty
# (the webcam's own device_id is read from OBS; it has no safe default).
CAMERAS = {D5: D5_DEVICE, WEBCAM: None}

# webcam: full canvas width, anchored to the top (16:9 -> 1080x608)
WEBCAM_BOX = {"positionX": 0, "positionY": 0, "alignment": 5, "rotation": 0,
              "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 4,
              "boundsWidth": W, "boundsHeight": 608, "cropToBounds": False}
# the webcam page: the webcam cover-cropped into the top 1080x1350 (4:5);
# the values of the hand-built "Webcam Only" scene setup now maintains
WEBCAM_ONLY_BOX = {"positionX": 0, "positionY": 0, "alignment": 5,
                   "boundsType": "OBS_BOUNDS_SCALE_OUTER", "boundsWidth": W, "boundsHeight": 1350,
                   "cropToBounds": True}
# the multi page: the top row's height. Each camera gets half the width, so
# 540x810 is 2:3, the D5's live view (640x426) on its side, uncropped.
MULTI_ROW = 810

# The camera watch (`watch run`): a black panel over a camera that drops.
# Camera ids as the JSON and the pager name them -> OBS source.
CAM_IDS = {"d5": D5, "webcam": WEBCAM, "tg7": TG7}
CAM_LABEL = {"d5": "D5100", "webcam": "Webcam", "tg7": "TG-7"}
# One black color source per camera, an item in every scene that shows it.
DOWN = {D5: "D5 Down", WEBCAM: "Webcam Down", TG7: "TG-7 Down"}
STRIP = WEBCAM_BOX["boundsHeight"]  # the webcam strip across the top
# Each camera's slot per scene, (x, y, w, h) from the top left: what its panel covers.
SLOTS = {PRODUCT: {D5: (0, 0, W, H), WEBCAM: (0, 0, W, STRIP)},
         MULTI: {D5: (0, 0, W / 2, MULTI_ROW), WEBCAM: (W / 2, 0, W / 2, MULTI_ROW)},
         CLOSEUP: {TG7: (0, TG7_TOP, W, H - TG7_TOP), WEBCAM: (0, 0, W, STRIP)},
         UPNEXT: {WEBCAM: (0, 0, W, STRIP)},
         DESKTOP: {WEBCAM: (0, 0, W, STRIP)},
         HOLD: {WEBCAM: (0, 0, W, STRIP)},
         WEBCAM_ONLY: {WEBCAM: (0, 0, W, WEBCAM_ONLY_BOX["boundsHeight"])}}
# The cameras each page puts on air.
PAGE_CAMS = {"intro": [], "item": ["d5", "webcam"], "closeup": ["tg7", "webcam"], "next": ["webcam"],
             "desktop": ["webcam"], "webcam": ["webcam"], "multi": ["d5", "webcam"]}
WATCH_EVERY = 1.0  # seconds between looks
DOWN_AFTER = 3  # bad looks in a row before the panel goes on
UP_AFTER = 5  # good looks in a row before it comes off
D5_STALE = 5  # seconds without a feed frame (FEED_SNAP, every 2 s) before the D5 counts as sending nothing
RECONNECT_EVERY = 15  # at most one reopen in OBS per camera this often
REASSERT_EVERY = 10  # seconds between re-writing each panel's state (someone may toggle it in OBS)
WATCH_PATTERN = r"^\S*python\S* \S*show_flow\.py watch run"
WATCH_LOG = Path(tempfile.gettempdir()) / "show_flow_watch.log"
WATCH_LOCK = LOCK.with_name("show_flow.watch.lock")  # one watch; not the refocus LOCK
CAMS_FILE = LOCK.with_name("show_flow.cams.json")  # the watch's verdicts, for state()


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
    if scene in SLOTS:  # the listed items moved down past the black panels: put those back
        _seat_panels(c, scene)


def _seat_panels(c, scene) -> None:
    """Each black panel (DOWN) in `scene` directly above its camera, so what
    sits over the camera (the webcam strip over the D5) still covers it."""
    items = c.call("GetSceneItemList", {"sceneName": scene})["sceneItems"]
    names, ids = [i["sourceName"] for i in items], {i["sourceName"]: i["sceneItemId"] for i in items}
    for cam, panel in DOWN.items():
        if cam not in names or panel not in names:
            continue
        p, k = names.index(panel), names.index(cam)
        if p == k + 1:
            continue
        to = k + 1 if p > k else k  # from below, the camera shifts down one as the panel leaves
        c.call("SetSceneItemIndex", {"sceneName": scene, "sceneItemId": ids[panel], "sceneItemIndex": to})
        names.insert(to, names.pop(p))


def program(c) -> str:
    return c.call("GetCurrentProgramScene")["currentProgramSceneName"]


def scene_shot(c, scene: str, width: int, quality: int) -> bytes:
    """A JPEG of one scene as OBS renders it now. No camera, no lock."""
    d = c.call("GetSourceScreenshot", {"sourceName": scene, "imageFormat": "jpg",
                                       "imageWidth": width, "imageCompressionQuality": quality})
    return base64.b64decode(d["imageData"].split(",", 1)[1])


def program_shot(c, width: int = 360, quality: int = 60) -> bytes:
    """A JPEG of what's on air (the program scene), for the pager's preview.
    No camera, no lock: OBS renders the scene it is already sending."""
    return scene_shot(c, program(c), width, quality)


def page_shot(c, page: str, width: int = 160, quality: int = 50) -> bytes:
    """A small JPEG of one page's scene, on air or not: the pager's multiview
    tile. A camera OBS only opens while it is shown renders black here."""
    return scene_shot(c, PAGES[page], width, quality)


class Busy(obsctl.ObsError):
    """A refocus, WB change or still is already running (from the shell or the web app)."""


def _lock_nb(f) -> None:
    """Take an exclusive lock on open file f, or raise OSError at once."""
    if fcntl:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)


@contextmanager
def exclusive():
    with open(LOCK, "w") as f:
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                _lock_nb(f)
                break
            except OSError:  # BlockingIOError (flock) / PermissionError (msvcrt)
                if time.monotonic() > deadline:
                    raise Busy("a D5100 refocus, WB change or still is already running (pager or shell); "
                               "it takes about 5 s, try again when it's done")
                time.sleep(0.05)
        try:
            yield
        finally:
            if not fcntl:  # flock goes with the file when it closes; msvcrt needs an unlock
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def refocusing() -> bool:
    """Is a refocus holding the lock right now? A non-blocking probe."""
    try:
        f = open(LOCK, "a")
    except OSError:
        return False
    with f:
        try:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)  # released when f closes
            else:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            return True
    return False


# Every go() leaves this mark and a refocus clears it when it starts, so the
# refocus can tell the host asked for a page meanwhile, even one already on
# air (Next, while the refocus hides behind Up Next). Next to LOCK, so the
# shell and the web app see each other's.
def _switch_mark() -> Path:
    return LOCK.with_name(LOCK.name + ".switched")


def _mark_switch(clear: bool = False) -> None:
    try:
        if clear:
            _switch_mark().unlink(missing_ok=True)
        else:
            _switch_mark().touch()
    except OSError:
        pass


def _switch_asked() -> bool:
    return _switch_mark().exists()


def source_hash(c, name: str) -> str | None:
    """A hash of a source's picture, or None when OBS can't render one
    (code 702 "Failed to render screenshot": no frames, e.g. right after the
    feed stops or the stand-in comes off). A source OBS doesn't have (code
    600) is raised."""
    try:
        d = c.call("GetSourceScreenshot", {"sourceName": name, "imageFormat": "jpg",
                                           "imageWidth": 160, "imageCompressionQuality": 90})
    except obsctl.ObsError as e:
        if "code 600" in str(e):
            raise
        return None
    return hashlib.md5(d["imageData"].encode()).hexdigest()


def d5_hash(c) -> str | None:
    try:
        return source_hash(c, D5)
    except obsctl.ObsError:  # no D5 source: no picture either
        return None


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


def feed_start() -> bool:
    """Start the feed loop unless it's running; True if this started it."""
    if feed_pids():
        return False
    log = open(FEED_LOG, "ab")
    subprocess.Popen([str(FEED)], cwd=REPO, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     start_new_session=True)
    return True


def _proc_env(pid: int) -> dict:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
    except OSError:
        return {}
    return dict(kv.split("=", 1) for kv in raw.split("\0") if "=" in kv)


def feed_snaps(pids) -> list[Path]:
    """Where the feed writes its 2 s frame: $SNAP or $TMPDIR as the feed's own
    process sees them (a feed started from another shell can differ), then ours."""
    out = []
    for p in pids:
        env = _proc_env(p)
        out.append(Path(env.get("SNAP") or Path(env.get("TMPDIR") or "/tmp") / FEED_SNAP))
    return out + [Path(os.environ.get("SNAP") or Path(tempfile.gettempdir()) / FEED_SNAP)]


def feed_status(pids=None) -> str:
    """"stopped", "running", or "running, no frames for Ns" when the loop is
    alive but no frame has landed lately (camera off: gphoto2 fails every 2 s)."""
    pids = feed_pids() if pids is None else pids
    if not pids:
        return "stopped"
    age = _snap_age(feed_snaps(pids))
    if age is None:
        return "running, no frames yet"
    return "running" if age <= FEED_STALE else f"running, no frames for {age:.0f}s"


def _snap_age(snaps: list[Path], now: float | None = None) -> float | None:
    """Seconds since the feed's newest frame (FEED_SNAP), or None if there's none."""
    now = time.time() if now is None else now
    ages = []
    for snap in snaps:
        try:
            ages.append(now - snap.stat().st_mtime)
        except OSError:
            pass
    return min(ages) if ages else None


def clock_marker(snap: Path) -> Path:
    """The feed's note beside its frame that the D5100's clock had reset
    (tools/d5100_webcam.sh: "${SNAP%.*}.clock")."""
    return snap.with_suffix(".clock")


def clock_reset(snaps: list[Path], last_frame: float | None) -> str | None:
    """What the D5100 said its date was when the feed last found its clock
    reset, if that was after `last_frame` (the newest frame's time; None: no
    frame at all), so the reset belongs to this outage. None otherwise."""
    for snap in snaps:
        mark = clock_marker(snap)
        try:
            when = mark.stat().st_mtime
            line = mark.read_text(errors="replace").strip()
        except OSError:
            continue
        if line and (last_frame is None or when > last_frame):
            return line.splitlines()[0].split("\t")[-1].strip() or "?"
    return None


def _gphoto2(*args, timeout=GPHOTO_TIMEOUT, **kw):
    try:
        return subprocess.run(["gphoto2", *args], capture_output=True, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        raise obsctl.ObsError(f"gphoto2 {' '.join(args)} hung for {timeout}s; "
                              "switch the D5100 off and on") from e
    except OSError as e:  # gphoto2 not installed
        raise obsctl.ObsError(f"can't run gphoto2: {e}") from e


def _snap_fresh(snaps: list[Path], since: float) -> bool:
    """Has the feed written a frame (FEED_SNAP, every 2 s) since `since`? Then
    /dev/video10 has frames, whatever OBS shows."""
    for snap in snaps:
        try:
            if snap.stat().st_mtime > since:
                return True
        except OSError:
            pass
    return False


def reopen_source(c, name: str, fallback: str | None = None) -> str:
    """Make OBS reopen a v4l2 source's device; returns the device. A v4l2
    source can sit black or frozen on one frame while its device has frames
    again (the D5 after its feed restarts, the webcam mid-stream: both seen
    live 2026-10-19). Clearing device_id and setting it back forces OBS to
    reopen it. `fallback` is the device when the setting is empty."""
    r = c.call("GetInputSettings", {"inputName": name})
    if r.get("inputKind", "v4l2_input") != "v4l2_input":
        raise obsctl.ObsError(f"{name!r} isn't a v4l2 camera source ({r['inputKind']}); nothing to reopen")
    dev = r["inputSettings"].get("device_id") or fallback
    if not dev:
        raise obsctl.ObsError(f"{name!r} has no device set; pick its camera in OBS")
    c.call("SetInputSettings", {"inputName": name, "inputSettings": {"device_id": ""}})
    c.call("SetInputSettings", {"inputName": name, "inputSettings": {"device_id": dev}})
    return dev


def reopen_d5(c) -> str:
    return reopen_source(c, D5, D5_DEVICE)


def reconnect(c, names=None) -> dict:
    """Reopen the show's cameras in OBS (CAMERAS, or `names`): {source:
    {"ok", "device" | "error"}}. One camera failing doesn't stop the other.
    Takes no lock: it's the fix for a frozen picture, so it must never wait.
    Viewers see a blink of black on that camera while OBS reopens it."""
    out = {}
    for name in names or CAMERAS:
        try:
            out[name] = {"ok": True, "device": reopen_source(c, name, CAMERAS.get(name))}
        except obsctl.ObsError as e:
            out[name] = {"ok": False, "error": str(e)}
    return out


def wait_d5_frames(c, frozen: str | None, since: float, snaps: list[Path], wait: float | None = None) -> None:
    """Wait until OBS shows moving D5 frames after the feed (re)started at
    `since`. Once the feed itself has frames and OBS's picture still hasn't
    moved, reopen the source in OBS (reopen_d5), once. With no `frozen`
    picture to compare against (OBS couldn't render one, code 702), moving
    means two different pictures in a row, and a few seconds after the
    reopen the feed's own fresh frame is taken as the answer."""
    wait = FRAMES_WAIT if wait is None else wait
    deadline = time.monotonic() + wait
    reopened = last = None
    while True:
        h = d5_hash(c)
        if h is not None and h != frozen and (frozen is not None or (last is not None and h != last)):
            return
        last = h
        if _snap_fresh(snaps, since):
            if reopened is None:
                reopen_d5(c)
                reopened = time.monotonic()
            elif frozen is None and time.monotonic() - reopened > REOPEN_GRACE:
                return
        if time.monotonic() > deadline:
            raise obsctl.ObsError(
                f"D5100 frames didn't come back in OBS within {wait:g}s (the feed is running and keeps "
                f"retrying; {'it has frames, OBS shows none' if reopened else 'no frames from the camera'}"
                f"); see {FEED_LOG}")
        time.sleep(0.1)


def d5_usb(c, fn):
    """Stop live view, run fn() (gphoto2 calls: the camera takes one USB
    connection), restart; returns (fn's result, seconds until OBS shows
    moving D5 frames again).

    Whatever fails, this never leaves the feed stopped: it's restarted if it
    was running before or the camera answered on USB (the feed loop then
    retries on its own until frames flow)."""
    t = time.monotonic()
    restart = bool(feed_pids())
    started = False
    try:
        feed_stop()
        found = _gphoto2("--auto-detect", text=True).stdout
        if "usb:" not in found:  # fail now, not after the wait for frames below
            raise obsctl.ObsError("D5100 not found on USB; is it switched on and plugged in?")
        restart = True
        r = fn()
        time.sleep(0.2)
        frozen = d5_hash(c)
        since = time.time()
        feed_start()
        started = True
        wait_d5_frames(c, frozen, since, feed_snaps(feed_pids()))
        time.sleep(0.25)  # let live view settle its exposure before it goes on air
        return r, time.monotonic() - t
    finally:
        if restart and not started:
            feed_start()


def refocus_d5(c) -> tuple[bool, float]:
    """Autofocus behind d5_usb; returns (focus locked, seconds until OBS shows
    moving D5 frames again). The D5100 refuses autofocus ("Unspecified
    error") when its AF point sees nothing with contrast, e.g. an empty podium;
    focus then stays where it was."""
    af, secs = d5_usb(c, lambda: _gphoto2("--set-config", "autofocusdrive=1"))
    return af.returncode == 0, secs


def wb_parse(out: str) -> tuple[str | None, list[str]]:
    """(Current, [Choice labels]) from `gphoto2 --get-config whitebalance`."""
    current, choices = None, []
    for line in out.splitlines():
        if line.startswith("Current:"):
            current = line.split(":", 1)[1].strip()
        elif line.startswith("Choice:"):  # "Choice: 3 Tungsten"
            choices.append(line.split(None, 2)[2].strip())
    return current, choices


def wb_choice(preset: str, choices: list[str]) -> str | None:
    """The camera's label for `preset` (an alias or a label, any case), or None."""
    want = WB_ALIASES.get(preset.lower(), preset).lower()
    return next((ch for ch in choices if ch.lower() == want), None)


def wb_d5(c, preset: str | None = None) -> dict:
    """Read the D5100's white balance and, with `preset`, set it, behind
    d5_usb. An unknown preset or one the camera refuses is raised once the
    feed is back, so a cover never lifts on a stopped D5."""
    def usb():
        r = _gphoto2("--get-config", WB_CONFIG, text=True)
        current, choices = wb_parse(r.stdout)
        if not choices:
            raise obsctl.ObsError(f"the D5100 reported no white balance choices: "
                                  f"{(r.stderr or r.stdout).strip() or 'no output'}")
        label = wb_choice(preset, choices) if preset else None
        put = None
        if label and label != current:
            put = _gphoto2("--set-config", f"{WB_CONFIG}={label}", text=True)
        return current, choices, label, put

    (current, choices, label, put), secs = d5_usb(c, usb)
    out = {"wb": current, "choices": choices, "changed": False, "wb_s": round(secs, 1)}
    if preset and not label:
        raise obsctl.ObsError(f"unknown white balance {preset!r}; the D5100 offers {', '.join(choices)} "
                              f"(or {', '.join(WB_ALIASES)})")
    if put and put.returncode:
        raise obsctl.ObsError(f"the D5100 refused white balance {label!r} (dial on P, S, A or M?): "
                              f"{put.stderr.strip()}")
    return {**out, "wb": label, "changed": True} if put else out


# --- OM TG-7 (tools/live/tg7cam) ---------------------------------------------
_tg7cam = None


def tg7cam():
    """tools/live/tg7cam as a module (it's a script with no .py)."""
    global _tg7cam
    if _tg7cam is None:
        loader = importlib.machinery.SourceFileLoader("tg7cam", str(TG7_CAM))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("tg7cam", loader))
        loader.exec_module(mod)
        _tg7cam = mod
    return _tg7cam


def tg7_source() -> tuple[str, dict]:
    """(inputKind, inputSettings) of the TG-7 source for the feed's output."""
    out = tg7cam().default_out()
    if out.startswith("/dev/video"):
        return "v4l2_input", {"device_id": out}
    return "ffmpeg_source", {"is_local_file": False, "input": out, "input_format": "mpegts",
                             "buffering_mb": 1, "reconnect_delay_sec": 1,
                             "restart_on_activate": False, "close_when_inactive": False,
                             "ffmpeg_options": "fflags=nobuffer flags=low_delay"}


def tg7_pids() -> list[int]:
    r = subprocess.run(["pgrep", "-f", TG7_PATTERN], capture_output=True, text=True)
    return [int(p) for p in r.stdout.split()]


def tg7_start() -> bool:
    """Start `tg7cam stream` detached unless it's running; True if this
    started it. It waits for the camera by itself if it isn't up yet."""
    if tg7_pids():
        return False
    log = open(TG7_LOG, "ab")
    subprocess.Popen([sys.executable, str(TG7_CAM), "stream", "--size", TG7_SIZE], cwd=REPO,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return True


def tg7_stop() -> None:
    """Ctrl+C first, so tg7cam stops the camera's live view on its way out."""
    pids = tg7_pids()
    for p in pids:
        os.kill(p, 2)
    deadline = time.monotonic() + 5
    while tg7_pids() and time.monotonic() < deadline:
        time.sleep(0.1)
    for p in tg7_pids():
        os.kill(p, 15)


def tg7_status(pids=None) -> str:
    pids = tg7_pids() if pids is None else pids
    return "running" if pids else "stopped"


def tg7_refocus() -> dict:
    """The TG-7's half-press at the center. Its live view keeps running
    through it (measured), so nothing needs hiding."""
    t = time.monotonic()
    try:
        af = tg7cam().autofocus()
    except tg7cam().CamError as e:
        raise obsctl.ObsError(f"TG-7: {e}") from e
    return {"camera": "tg7", "focused": af != "ng", "refocus_s": round(time.monotonic() - t, 1)}


# --- the camera watch: black in place of a camera that drops ------------------
def _panel_box(x, y, w, h) -> dict:
    return {"positionX": x, "positionY": y, "alignment": 5, "rotation": 0,
            "boundsType": "OBS_BOUNDS_STRETCH", "boundsAlignment": 0,
            "boundsWidth": w, "boundsHeight": h, "cropToBounds": False}


def panels_on(c) -> dict:
    """Which cameras' black panels are on in OBS, {cam_id: bool}: the item
    view's for the D5100 and the webcam, the close-up's for the TG-7
    (set_panel keeps every scene's the same)."""
    seen = {}
    for scene in (PRODUCT, CLOSEUP):
        try:
            seen[scene] = c.call("GetSceneItemList", {"sceneName": scene})["sceneItems"]
        except obsctl.ObsError:  # not built yet
            seen[scene] = []
    return {cam: any(i["sourceName"] == DOWN[name] and i["sceneItemEnabled"]
                     for i in seen[CLOSEUP if name == TG7 else PRODUCT])
            for cam, name in CAM_IDS.items()}


def set_panel(c, cam: str, on: bool) -> int:
    """Show or hide camera `cam`'s black panel in every scene that has one;
    returns how many items changed. Writes only where the state differs."""
    scenes = {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}
    n = 0
    for scene, slots in SLOTS.items():
        if cam not in slots or scene not in scenes:
            continue
        for i in c.call("GetSceneItemList", {"sceneName": scene})["sceneItems"]:
            if i["sourceName"] == DOWN[cam] and i["sceneItemEnabled"] != on:
                c.call("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": i["sceneItemId"],
                                               "sceneItemEnabled": on})
                n += 1
    return n


def clear_panels(c) -> None:
    for name in CAM_IDS.values():
        set_panel(c, name, False)


def _clock(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


def _look(c, cam: str, m: dict, now: float) -> tuple[str | None, str, str, bool]:
    """One look at one camera, cheapest checks first: (verdict, state,
    reason, reopenable). verdict is "ok", "bad", "skip" (stand-in, busy:
    nothing to judge) or None (a first picture: nothing to compare it with
    yet). reopenable: the device has frames but OBS's picture doesn't move,
    what reconnect() fixes."""
    name = CAM_IDS[cam]
    reopenable = True
    if cam == "d5":
        if standin_on(c):
            return "skip", "standin", "stand-in on", False
        if refocusing():  # a probe: never takes or waits on the lock
            return "skip", "busy", "refocus, WB or still", False
        pids = feed_pids()
        if not pids:
            return "bad", "off", "D5100 feed stopped", False
        snaps = feed_snaps(pids)
        age = _snap_age(snaps, now)
        if age is None or age > D5_STALE:
            said = clock_reset(snaps, None if age is None else now - age)
            if said:  # the feed set it again on its restart; frames back = live as usual
                return "bad", "down", f"clock reset (camera said {said})", False
            why = "no frames from the camera" + (f" for {age:.0f}s" if age is not None else "")
            return "bad", "down", why, False
    elif cam == "webcam":
        dev = c.call("GetInputSettings", {"inputName": WEBCAM})["inputSettings"].get("device_id")
        if dev and not os.path.exists(dev):
            return "bad", "down", f"device {dev} missing", False
        reopenable = bool(dev)
    elif not tg7_pids():
        return "bad", "off", "TG-7 feed stopped", False
    h = source_hash(c, name)
    last, m["last"] = m["last"], h
    if h is None:
        return "bad", "down", "OBS has no picture", reopenable
    if last is None:
        return None, m["state"], m["reason"], False
    if h == last:
        return ("bad", "down", "picture frozen in OBS" + (" (the camera is sending)" if cam == "d5" else ""),
                reopenable)
    return "ok", "live", "", False


def watch_tick(c, mem: dict, now: float) -> dict:
    """One look at every camera: count good and bad looks per camera in
    `mem` (keep it between ticks), turn a panel on after DOWN_AFTER bad
    looks in a row and off after UP_AFTER good ones, reopen a camera in OBS
    when its panel is on and its device has frames. Returns {cam_id:
    {"state", "reason", "since"}}. One camera failing never stops the others;
    a lost OBS connection is raised."""
    out = {}
    for cam, name in CAM_IDS.items():
        m = mem.setdefault(cam, {"bad": 0, "ok": 0, "last": None, "state": "unknown", "reason": "",
                                 "since": now, "reopened": None, "skip": None, "asserted": now})
        label = CAM_LABEL[cam]
        try:
            try:
                verdict, kind, reason, reopenable = _look(c, cam, m, now)
            except obsctl.ObsError as e:
                if not re.search(r" failed: code \d+", str(e)):
                    raise  # the connection, not this camera: the loop reconnects
                why = f"no {label} source in OBS; run setup" if "code 600" in str(e) else str(e)
                verdict, kind, reason, reopenable = "skip", "unknown", why, False
            if verdict == "skip":
                # no verdict: the next picture isn't compared with one from before
                m["bad"] = m["ok"] = 0
                m["last"] = None
                if not m["skip"] or m["skip"][0] != kind:
                    m["skip"] = [kind, now]
                if kind == "standin":  # the stand-in wins: no black over the white panel
                    set_panel(c, name, False)
                    m.update(state="unknown", reason="", since=now)
                out[cam] = {"state": kind, "reason": reason, "since": m["skip"][1]}
                continue
            m["skip"] = None
            if (verdict == "bad" and reopenable and m["state"] == "down"
                    and (m["reopened"] is None or now - m["reopened"] >= RECONNECT_EVERY)):
                # the panel is already on, so viewers don't see the reopen's blink
                m["reopened"] = now
                r = reconnect(c, [name])[name]
                if r["ok"]:
                    m["bad"] = m["ok"] = 0
                    m["last"] = None
                    print(f"{_clock(now)} {label} reopened in OBS ({r['device']})", flush=True)
            elif verdict == "bad":
                m["bad"], m["ok"] = m["bad"] + 1, 0
                if m["state"] in ("down", "off"):
                    if m["state"] != kind:
                        m.update(state=kind, since=now)
                    m["reason"] = reason
                elif m["bad"] >= DOWN_AFTER:
                    set_panel(c, name, True)
                    m.update(state=kind, reason=reason, since=now, asserted=now)
                    print(f"{_clock(now)} {label} {kind} ({reason}); black on", flush=True)
            elif verdict == "ok":
                m["ok"], m["bad"] = m["ok"] + 1, 0
                if m["state"] != "live" and m["ok"] >= UP_AFTER:
                    off = set_panel(c, name, False)
                    took = now - m["since"]
                    m.update(state="live", reason="", since=now, asserted=now)
                    print(f"{_clock(now)} {label} live" + (f"; black off after {took:.0f}s" if off else ""),
                          flush=True)
            if m["state"] != "unknown" and now - m["asserted"] >= REASSERT_EVERY:
                m["asserted"] = now
                set_panel(c, name, m["state"] != "live")
        except obsctl.ObsError as e:
            if not re.search(r" failed: code \d+", str(e)):
                raise
            print(f"{_clock(now)} {label}: {e}", flush=True)
        out[cam] = {"state": m["state"], "reason": m["reason"], "since": m["since"]}
    return out


def _write_cams(cams: dict, every: float) -> None:
    tmp = CAMS_FILE.with_name(CAMS_FILE.name + ".tmp")
    tmp.write_text(json.dumps({"pid": os.getpid(), "t": time.time(), "every": every, "cameras": cams}))
    os.replace(tmp, CAMS_FILE)  # state() never reads half a file


def _alive(pid) -> bool:
    if not isinstance(pid, int):
        return False
    if os.name == "nt":  # os.kill(pid, 0) would end the process there; the file's age has to do
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def watch_cams() -> dict | None:
    """The running watch's last word, {"pid", "t", "every", "cameras"}, or
    None when no watch is running (no file, a stale one, or its process gone)."""
    try:
        st = json.loads(CAMS_FILE.read_text())
    except (OSError, ValueError):
        return None
    if time.time() - st.get("t", 0) > max(5, 3 * st.get("every", WATCH_EVERY)) or not _alive(st.get("pid")):
        return None
    return st


def cam_states(c, w: dict | None, standin: bool, busy: bool) -> dict:
    """Each camera for state(): the watch's verdict (`w`, from watch_cams;
    "unknown" without one), the D5 busy or stand-in as they are right now,
    and "blacked": its panel is on in OBS."""
    on = panels_on(c)
    seen = (w or {}).get("cameras", {})
    out = {}
    for cam in CAM_IDS:
        v = {"state": "unknown", "reason": "", "since": None, **seen.get(cam, {})}
        if cam == "d5" and standin:
            v.update(state="standin", reason="stand-in on")
        elif cam == "d5" and busy:
            v.update(state="busy", reason="refocus, WB or still")
        out[cam] = {**v, "blacked": on[cam]}
    return out


def watch_pids() -> list[int]:
    r = subprocess.run(["pgrep", "-f", WATCH_PATTERN], capture_output=True, text=True)
    return [int(p) for p in r.stdout.split()]


def watch_start() -> bool:
    """Start `watch run` detached unless it's running; True if this started it."""
    if watch_pids():
        return False
    log = open(WATCH_LOG, "ab")
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "watch", "run"], cwd=REPO,
                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return True


def watch_stop(c=None) -> None:
    """End the watch (it clears its panels on the way out) and clear every
    panel anyway, so none stays on with no watch running. Without `c` it
    connects itself; with OBS down there's nothing on air to clear."""
    for p in watch_pids():
        os.kill(p, 15)
    deadline = time.monotonic() + 5
    while watch_pids() and time.monotonic() < deadline:
        time.sleep(0.1)
    if c is not None:
        clear_panels(c)
        return
    try:
        c = connect()
    except (obsctl.ObsError, OSError):
        return
    try:
        clear_panels(c)
    finally:
        c.close()


def _sigterm(signum, frame):
    raise KeyboardInterrupt  # `watch stop`: leave through watch_run's cleanup


def watch_run(every: float = WATCH_EVERY) -> int:
    """The watch, in the foreground: watch_tick every `every` seconds with
    its own OBS connection, the verdicts in CAMS_FILE. If OBS goes away it
    retries every 2 s and leaves the panels as they are. Ctrl+C or SIGTERM
    ends it with every panel off. One watch at a time (WATCH_LOCK)."""
    lock = open(WATCH_LOCK, "w")
    try:
        _lock_nb(lock)
    except OSError:
        lock.close()
        print("camera watch already running")
        return 0
    before = signal.signal(signal.SIGTERM, _sigterm)
    mem, c, lost = {}, None, False
    print(f"{_clock(time.time())} camera watch started, every {every:g}s", flush=True)
    try:
        while True:
            now = time.time()
            try:
                c = c or connect()
                cams = watch_tick(c, mem, now)
            except (obsctl.ObsError, OSError) as e:
                if c is not None:
                    c.close()
                    c = None
                if not lost:
                    print(f"{_clock(now)} OBS unreachable ({e}); retrying every 2s", flush=True)
                lost = True
                _write_cams({k: {"state": "unknown", "reason": "OBS unreachable", "since": now}
                             for k in CAM_IDS}, every)
                time.sleep(2)
                continue
            lost = False
            _write_cams(cams, every)
            time.sleep(every)
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, before)
        try:
            c = c or connect()
            clear_panels(c)
        except (obsctl.ObsError, OSError):
            pass  # OBS is gone: nothing on air
        finally:
            if c is not None:
                c.close()
            CAMS_FILE.unlink(missing_ok=True)
            lock.close()
        print(f"{_clock(time.time())} camera watch stopped; every black panel off", flush=True)
    return 0


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
        value, live = pick_window(c, a.window)
        c.call("SetInputSettings", {"inputName": WINDOW, "inputSettings": {"capture_window": value}})
        print(f"window: {live}")
        if not window_safe(live):
            print("  Up Next will hide it: the title isn't a public eBay / Pop's Games page "
                  "(show_flow.SAFE_WINDOW)")

    # Product: D5 rotated to portrait (the camera is mounted on its side), cover-cropped.
    # The stand-in keeps its state: with it on, the D5 stays off, the white
    # panel under the webcam.
    sub = standin_on(c)
    set_box(c, PRODUCT, D5, {"positionX": W / 2, "positionY": H / 2, "alignment": 0, "rotation": a.d5_rotation,
                             "boundsType": "OBS_BOUNDS_SCALE_OUTER", "boundsAlignment": 0,
                             "boundsWidth": H, "boundsHeight": W, "cropToBounds": True}, enabled=not sub)
    set_box(c, PRODUCT, WEBCAM, WEBCAM_BOX)
    set_box(c, PRODUCT, WINDOW, enabled=False)
    if OVERLAY in inputs:
        set_box(c, PRODUCT, OVERLAY, enabled=False)
    has_standin = STANDIN in {i["sourceName"] for i in
                              c.call("GetSceneItemList", {"sceneName": PRODUCT})["sceneItems"]}
    stack(c, PRODUCT, [STANDIN, D5, WEBCAM] if has_standin else [D5, WEBCAM])

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
    guard_window(c)  # set_box just showed it; Up Next may be on air

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

    # Webcam Only: the fallback page, the webcam alone on the dark background
    if WEBCAM_ONLY not in {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}:
        c.call("CreateScene", {"sceneName": WEBCAM_ONLY})
    set_box(c, WEBCAM_ONLY, BG)
    set_box(c, WEBCAM_ONLY, WEBCAM, WEBCAM_ONLY_BOX)
    item(c, WEBCAM_ONLY, MIC)
    stack(c, WEBCAM_ONLY, [BG])
    n = len(c.call("GetSceneItemList", {"sceneName": WEBCAM_ONLY})["sceneItems"])
    c.call("SetSceneItemIndex", {"sceneName": WEBCAM_ONLY, "sceneItemId": item(c, WEBCAM_ONLY, WEBCAM),
                                 "sceneItemIndex": n - 1})  # on top of anything hand-added

    if a.desktop_window or DESKTOP_WIN in inputs:
        build_desktop(c, a.desktop_window, a.desktop_crop_left)
        build_multi(c, a.d5_rotation, a.desktop_crop_left)

    build_closeup(c, a.tg7_rotation)
    build_hold(c)

    # Only Up Next may show the desktop window (a stray one turned up in Intro)
    scenes = {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}
    for scene in [*PAGES.values(), HOLD]:
        if scene != UPNEXT and scene in scenes:
            hide_window(c, scene)

    build_down_panels(c)  # last: over every camera every page above has
    c.call("SetCurrentSceneTransition", {"transitionName": "Fade"})
    c.call("SetCurrentSceneTransitionDuration", {"transitionDuration": FADE_MS})
    print("pages ready: " + ", ".join(f"{k} = {v!r}" for k, v in PAGES.items()) + f"; fade {FADE_MS} ms")


def feed_up(c) -> str:
    """Start the feed (if it's off) and, once it has frames, make OBS reopen
    the D5 source, so the item view isn't left black. Returns what happened."""
    if feed_pids():
        return "already running"
    frozen, since = d5_hash(c), time.time()
    feed_start()
    try:
        wait_d5_frames(c, frozen, since, feed_snaps(feed_pids()), wait=FEED_START_WAIT)
    except obsctl.ObsError:
        return f"started, but no frames in OBS within {FEED_START_WAIT}s (camera off? see {FEED_LOG})"
    return "started"


def cmd_feed(c, a):
    if a.action == "stop":
        feed_stop()
    elif a.action == "start":
        print(f"D5100 feed {feed_up(c)}")
    print(f"D5100 feed: {feed_status()}  (log {FEED_LOG})")


def cmd_reconnect(c, a):
    names = {"d5": [D5], "webcam": [WEBCAM]}.get(a.camera)
    r = reconnect(c, names)
    for name, res in r.items():
        print(f"{name}: " + (f"reopened {res['device']}" if res["ok"] else res["error"]))
    if a.json:
        print(json.dumps({**state(c), "reconnect": r}))
    if not all(res["ok"] for res in r.values()):
        raise obsctl.ObsError("not every camera reopened (above)")


# --- the pager: the API the CLI below and webapp/pager.py share --------------
# --- destinations: eBay and Twitch, independently ----------------------------
def _on_twitch_profile(c) -> bool:
    return c.call("GetProfileList")["currentProfileName"] == TWITCH_PROFILE


def outputs(c) -> dict:
    """OBS's raw status per destination; {} for a destination with no output.
    One read serves both state() and health() in the pager's poll."""
    out = {dest: c.call(f"Get{kind}Status") for dest, kind in DESTS.items()}
    if _on_twitch_profile(c):  # the stream output is Twitch here, not eBay
        out["twitch"], out["ebay"] = out["ebay"], {}
    return out


def streams(c, outs: dict | None = None) -> dict:
    out = {}
    for dest, st in (outs or outputs(c)).items():
        on = bool(st.get("outputActive"))
        out[dest] = {"live": on, "timecode": st["outputTimecode"] if on else None}
    return out


# dest -> (outputBytes, outputDuration ms, kbps): health()'s last reading per output
_LAST_OUT: dict[str, tuple[int, int, int | None]] = {}
KBPS_MIN_MS = 500  # readings closer than this (two pages polling) reuse the last rate


def kbps(dest: str, st: dict) -> int | None:
    """kbit/s sent since health() last read this output, timed by OBS's own
    outputDuration (as go_live does), so poll jitter doesn't skew it. None
    off air, on the first reading, and after the output restarted."""
    if not st.get("outputActive"):
        _LAST_OUT.pop(dest, None)
        return None
    sent, ms = st.get("outputBytes", 0), st.get("outputDuration", 0)
    prev = _LAST_OUT.get(dest)
    if prev and sent >= prev[0] and 0 <= ms - prev[1] < KBPS_MIN_MS:
        return prev[2]
    rate = None
    if prev and sent >= prev[0] and ms > prev[1]:
        rate = round((sent - prev[0]) * 8 / (ms - prev[1]))  # bytes * 8 / ms = kbit/s
    _LAST_OUT[dest] = (sent, ms, rate)
    return rate


def health(c, outs: dict | None = None) -> dict:
    """The pager's telemetry: OBS's render and encode counters, and per
    destination its bytes sent and kbps (kbps()). Counters are cumulative
    since OBS started (frames) or the output started (bytes); `skipped`,
    `frames` and `congestion` are null where OBS doesn't report them (its
    record output, which carries Twitch)."""
    s = c.call("GetStats")
    out = {}
    for dest, st in (outs or outputs(c)).items():
        on = bool(st.get("outputActive"))
        out[dest] = {"live": on, "bytes": st.get("outputBytes", 0) if on else 0, "kbps": kbps(dest, st),
                      "skipped": st.get("outputSkippedFrames"), "frames": st.get("outputTotalFrames"),
                      "congestion": st.get("outputCongestion"),
                      "reconnecting": bool(st.get("outputReconnecting"))}

    def n(key, places=None):
        return round(s[key], places)

    return {"cpu": n("cpuUsage", 1), "mem_mb": n("memoryUsage"), "fps": n("activeFps", 1),
            "frame_ms": n("averageFrameRenderTime", 1),
            "render_skipped": n("renderSkippedFrames"), "render_frames": n("renderTotalFrames"),
            "encode_skipped": n("outputSkippedFrames"), "encode_frames": n("outputTotalFrames"),
            "disk_mb": n("availableDiskSpace"), "outputs": out}


def _profile_param(c, name: str) -> str:
    return c.call("GetProfileParameter", {"parameterCategory": "AdvOut",
                                          "parameterName": name}).get("parameterValue") or ""


def twitch_problem(c) -> str | None:
    """Why Twitch can't start, or None. Never returns the URL itself."""
    if any(_profile_param(c, k) != v for k, v in (("RecType", "FFmpeg"), ("FFOutputToFile", "false"))):
        return "Twitch isn't set up: run `show_flow.py twitch-setup` (with nothing live)"
    if not _profile_param(c, "FFURL").startswith("rtmp"):
        return ("no Twitch URL: OBS Settings > Output > Recording > File path or URL, "
                "paste rtmp://live.twitch.tv/app/<your stream key>")
    return None


def ebay_problem(c) -> str | None:
    """Why the stream output isn't eBay, or None. Signing in to Twitch in
    OBS Settings > Stream silently replaces the WHIP service (and its URL)."""
    svc = c.call("GetStreamServiceSettings")
    if svc.get("streamServiceType") != "whip_custom" or not (svc.get("streamServiceSettings") or {}).get("server"):
        return ("the stream output isn't eBay's WHIP (it's "
                f"{svc.get('streamServiceType')!r}): OBS Settings > Stream > Service WHIP, "
                "paste the Stream URL from Stream manager > No delay (WHIP)")
    return None


def show_profile(c) -> None:
    """Make the show profile current (vertical canvas, eBay Live scenes)."""
    if c.call("GetProfileList")["currentProfileName"] != SHOW_PROFILE:
        if any(s["live"] for s in streams(c).values()):
            raise obsctl.ObsError(f"an output is live on another profile; stop it before using {SHOW_PROFILE!r}")
        c.call("SetCurrentProfile", {"profileName": SHOW_PROFILE})
        time.sleep(2)
    if c.call("GetSceneCollectionList")["currentSceneCollectionName"] != COLLECTION:
        c.call("SetCurrentSceneCollection", {"sceneCollectionName": COLLECTION})
        time.sleep(2)
    vs = c.call("GetVideoSettings")
    if (vs["baseWidth"], vs["baseHeight"]) != (W, H):
        c.call("SetVideoSettings", {"baseWidth": W, "baseHeight": H, "outputWidth": W,
                                    "outputHeight": H, "fpsNumerator": 30, "fpsDenominator": 1})


def go_live(c, dest: str, check: float = 5) -> dict:
    """Start one destination, leaving the other as it is, and check that
    bytes are flowing. Not under the camera lock: a 5 s check mustn't hold
    up a page switch."""
    if dest not in DESTS:
        raise ValueError(f"unknown destination {dest!r}; {', '.join(DESTS)}")
    if streams(c)[dest]["live"]:
        return {"dest": dest, "changed": False}
    kind = DESTS[dest]
    if _on_twitch_profile(c) and not c.call("GetStreamStatus")["outputActive"]:
        show_profile(c)  # idle on the Twitch-only profile: back to the show profile
    if dest == "ebay" and _on_twitch_profile(c):
        raise obsctl.ObsError("Twitch is live on its own profile; stop it to start eBay "
                              "(or set the Twitch URL so both can run)")
    if not _on_twitch_profile(c):
        show_profile(c)
    why = twitch_problem(c) if dest == "twitch" else ebay_problem(c)
    if dest == "twitch" and why and not c.call("GetStreamStatus")["outputActive"]:
        _twitch_profile(c)  # Twitch alone, the older way
        kind, why = "Stream", None
    if why:
        raise obsctl.ObsError(why)
    c.call(f"Start{kind}")
    time.sleep(check)
    st = c.call(f"Get{kind}Status")
    if not st["outputActive"] or not st.get("outputBytes"):
        raise obsctl.ObsError(f"{dest} didn't connect: {obsctl.redact(st)}")
    kbps = st["outputBytes"] * 8 / 1000 / max(st["outputDuration"] / 1000, 0.001)
    return {"dest": dest, "changed": True, "kbps": round(kbps), "profile": c.call(
                "GetProfileList")["currentProfileName"],
            "skipped": (f"{st['outputSkippedFrames']}/{st['outputTotalFrames']}"
                        if "outputSkippedFrames" in st else None)}


def _twitch_profile(c) -> None:
    if not _on_twitch_profile(c):
        c.call("SetCurrentProfile", {"profileName": TWITCH_PROFILE})
        time.sleep(2)
    vs = c.call("GetVideoSettings")
    if (vs["baseWidth"], vs["baseHeight"]) != (W, H):  # this profile defaults to 16:9
        c.call("SetVideoSettings", {"baseWidth": W, "baseHeight": H, "outputWidth": 720,
                                    "outputHeight": 1280, "fpsNumerator": 30, "fpsDenominator": 1})


def stop_dest(c, dest: str) -> dict:
    if dest not in DESTS:
        raise ValueError(f"unknown destination {dest!r}; {', '.join(DESTS)}")
    if not streams(c)[dest]["live"]:
        return {"dest": dest, "changed": False}
    kind = "Stream" if dest == "twitch" and _on_twitch_profile(c) else DESTS[dest]
    c.call(f"Stop{kind}")
    deadline = time.monotonic() + 10
    while c.call(f"Get{kind}Status")["outputActive"] and time.monotonic() < deadline:
        time.sleep(0.25)
    return {"dest": dest, "changed": True}


def twitch_setup(c) -> None:
    """Point the recording output at Twitch (everything but the URL)."""
    if any(s["live"] for s in streams(c).values()):
        raise obsctl.ObsError("OBS won't change outputs while one is live; stop both first")
    show_profile(c)
    for k, v in TWITCH_OUT.items():
        c.call("SetProfileParameter", {"parameterCategory": "AdvOut", "parameterName": k,
                                       "parameterValue": v})


def page_of(scene: str) -> str | None:
    """The pager page for an OBS scene; "hold" for the hold still."""
    if scene == HOLD:
        return "hold"
    return next((k for k, v in PAGES.items() if v == scene), None)


def live_title(capture: str) -> str | None:
    """The live X title of an Xcomposite `capture_window` value
    ("<window id>\\r\\n<title>\\r\\n<class>"), or None if that window is gone."""
    wid = (capture or "").split("\r\n", 1)[0].strip()
    if not wid.isdigit():
        return None
    try:
        r = subprocess.run(["xprop", "-id", wid, "_NET_WM_NAME"], capture_output=True, text=True)
    except OSError:  # no xprop: treat as unreadable, so the guard hides
        return None
    title = r.stdout.partition("=")[2].strip().strip('"') if r.returncode == 0 else ""
    return title or None


def window_title(c) -> str | None:
    """The Up Next window's current title, or None if it's gone."""
    return live_title(c.call("GetInputSettings", {"inputName": WINDOW})["inputSettings"]
                      .get("capture_window", ""))


def window_safe(title: str | None) -> bool:
    """May Up Next show a window with this live title? SAFE_WINDOW, minus PRIVATE_WINDOW."""
    return bool(title) and bool(SAFE_WINDOW.search(title)) and not PRIVATE_WINDOW.search(title)


def pick_window(c, title: str) -> tuple[str, str]:
    """(capture_window value, live title) of the first window whose name in
    OBS's list contains `title` and that is still open. OBS's list keeps
    windows that have since closed."""
    props = c.call("GetInputPropertiesListPropertyItems",
                   {"inputName": WINDOW, "propertyName": "capture_window"})["propertyItems"]
    match = [p for p in props if title.lower() in p["itemName"].lower()]
    if not match:
        raise obsctl.ObsError(f"no open window matches {title!r}")
    for p in match:
        live = live_title(p["itemValue"])
        if live:
            return p["itemValue"], live
    raise obsctl.ObsError(f"{title!r} matches {', '.join(repr(p['itemName']) for p in match)} in "
                          "OBS's window list, but that window is closed; open it and re-run setup")


def hide_window(c, scene: str) -> None:
    """Disable the desktop window in `scene`, if it's there. Every page but
    Up Next goes through this: they may hide the window, never show it."""
    for i in c.call("GetSceneItemList", {"sceneName": scene})["sceneItems"]:
        if i["sourceName"] == WINDOW and i["sceneItemEnabled"]:
            c.call("SetSceneItemEnabled", {"sceneName": scene, "sceneItemId": i["sceneItemId"],
                                           "sceneItemEnabled": False})


def guard_window(c) -> dict | None:
    """Show or hide the window in Up Next by its live title (window_safe).
    None if Up Next doesn't carry the window."""
    items = c.call("GetSceneItemList", {"sceneName": UPNEXT})["sceneItems"]
    ids = [i["sceneItemId"] for i in items if i["sourceName"] == WINDOW]
    if not ids:
        return None
    title = window_title(c)
    safe = window_safe(title)
    for i in ids:
        c.call("SetSceneItemEnabled", {"sceneName": UPNEXT, "sceneItemId": i, "sceneItemEnabled": safe})
    return {"title": title, "shown": safe}


def state(c, outs: dict | None = None) -> dict:
    """What's on air. `page` is None when the program scene isn't one of the
    pager's (someone switched in OBS by hand). `watch`: a camera watch is
    running; `cameras`: what it says of each (cam_states)."""
    scene = program(c)
    out = streams(c, outs)
    on = [s["timecode"] for s in out.values() if s["live"]]
    pids = feed_pids()
    sub, busy, w = standin_on(c), refocusing(), watch_cams()
    return {"page": page_of(scene), "scene": scene, "pages": list(PAGES),
            "live": bool(on), "timecode": max(on) if on else None,
            "streams": out, "feed": bool(pids), "feed_status": feed_status(pids),
            "standin": sub, "refocusing": busy,
            "held": held_page(c) if scene == HOLD else None, "tg7_status": tg7_status(),
            "watch": w is not None, "cameras": cam_states(c, w, sub, busy)}


def build_desktop(c, title: str | None = None, crop_left: int | None = None) -> None:
    """The desktop page: white background, DESKTOP_WIN under the webcam strip.
    `title` (substring) picks the window; `crop_left` trims source pixels
    off its left edge (Claude's session sidebar is ~216 at 1440 wide)."""
    if DESKTOP not in {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}:
        c.call("CreateScene", {"sceneName": DESKTOP})
    inputs = {i["inputName"] for i in c.call("GetInputList")["inputs"]}
    if STANDIN not in inputs:
        c.call("CreateInput", {"sceneName": DESKTOP, "inputName": STANDIN, "inputKind": "color_source_v3",
                               "inputSettings": {"color": 0xFFFFFFFF, "width": W, "height": H}})
    if title:
        settings = {"capture_window": pick_window(c, title)[0], "show_cursor": True}
        if DESKTOP_WIN in inputs:
            c.call("SetInputSettings", {"inputName": DESKTOP_WIN, "inputSettings": settings})
        else:
            c.call("CreateInput", {"sceneName": DESKTOP, "inputName": DESKTOP_WIN,
                                   "inputKind": "xcomposite_input", "inputSettings": settings})
    elif DESKTOP_WIN not in inputs:
        raise obsctl.ObsError("the desktop page needs a window: setup --desktop-window <title>")
    box = {"positionX": W / 2, "positionY": 608 + (H - 608) / 2, "alignment": 0, "rotation": 0,
           "scaleX": 1, "scaleY": 1, "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
           "boundsWidth": W, "boundsHeight": H - 608}
    if crop_left is not None:
        box["cropLeft"] = crop_left
    set_box(c, DESKTOP, STANDIN, {"positionX": 0, "positionY": 0, "alignment": 5, "rotation": 0,
                                  "boundsType": "OBS_BOUNDS_NONE", "scaleX": 1, "scaleY": 1})
    set_box(c, DESKTOP, DESKTOP_WIN, box)
    set_box(c, DESKTOP, WEBCAM, WEBCAM_BOX)
    item(c, DESKTOP, MIC)
    stack(c, DESKTOP, [STANDIN, DESKTOP_WIN, WEBCAM])


def build_multi(c, d5_rotation: float = 270, crop_left: int | None = None) -> None:
    """The multi page: the D5100 and the webcam side by side in the top
    MULTI_ROW, each cover-cropped into half the width, and DESKTOP_WIN (the
    desktop page's window, so build_desktop first) full width under them,
    hung from the row. The D5 follows the stand-in, like the item view."""
    if MULTI not in {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}:
        c.call("CreateScene", {"sceneName": MULTI})
    w, h = W / 2, MULTI_ROW
    if d5_rotation % 180:  # mounted on its side: the bounds turn with it
        w, h = h, w
    set_box(c, MULTI, BG)
    set_box(c, MULTI, D5, {"positionX": W / 4, "positionY": MULTI_ROW / 2, "alignment": 0,
                           "rotation": d5_rotation, "boundsType": "OBS_BOUNDS_SCALE_OUTER",
                           "boundsAlignment": 0, "boundsWidth": w, "boundsHeight": h,
                           "cropToBounds": True}, enabled=not standin_on(c))
    set_box(c, MULTI, WEBCAM, {"positionX": W * 3 / 4, "positionY": MULTI_ROW / 2, "alignment": 0,
                               "rotation": 0, "boundsType": "OBS_BOUNDS_SCALE_OUTER",
                               "boundsAlignment": 0, "boundsWidth": W / 2, "boundsHeight": MULTI_ROW,
                               "cropToBounds": True})
    box = {"positionX": W / 2, "positionY": MULTI_ROW, "alignment": 4, "rotation": 0,  # 4: top center
           "scaleX": 1, "scaleY": 1, "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 4,
           "boundsWidth": W, "boundsHeight": H - MULTI_ROW}
    if crop_left is not None:
        box["cropLeft"] = crop_left
    set_box(c, MULTI, DESKTOP_WIN, box)
    item(c, MULTI, MIC)
    stack(c, MULTI, [BG, D5, WEBCAM, DESKTOP_WIN])


def build_closeup(c, rotation: float = 0) -> None:
    """The closeup page: the TG-7 cover-cropped into the canvas below the
    webcam strip, on the dark background. The source matches the feed's
    output (tg7_source); one of the other kind is replaced."""
    if CLOSEUP not in {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}:
        c.call("CreateScene", {"sceneName": CLOSEUP})
    kind, settings = tg7_source()
    have = {i["inputName"]: i.get("inputKind") for i in c.call("GetInputList")["inputs"]}
    if TG7 in have and have[TG7] not in (None, kind):
        c.call("RemoveInput", {"inputName": TG7})
        del have[TG7]
    if TG7 in have:
        c.call("SetInputSettings", {"inputName": TG7, "inputSettings": settings})
    else:
        c.call("CreateInput", {"sceneName": CLOSEUP, "inputName": TG7, "inputKind": kind,
                               "inputSettings": settings})
    w, h = W, H - TG7_TOP
    if rotation % 180:  # mounted on its side: the bounds turn with it
        w, h = h, w
    set_box(c, CLOSEUP, BG)
    set_box(c, CLOSEUP, TG7, {"positionX": W / 2, "positionY": TG7_TOP + (H - TG7_TOP) / 2,
                              "alignment": 0, "rotation": rotation,
                              "boundsType": "OBS_BOUNDS_SCALE_OUTER", "boundsAlignment": 0,
                              "boundsWidth": w, "boundsHeight": h, "cropToBounds": True})
    set_box(c, CLOSEUP, WEBCAM, WEBCAM_BOX)
    item(c, CLOSEUP, MIC)
    stack(c, CLOSEUP, [BG, TG7, WEBCAM])


def build_hold(c) -> None:
    """The hold page: a full-canvas still (hold() fills it) under the live
    webcam strip, with the mic."""
    if HOLD not in {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}:
        c.call("CreateScene", {"sceneName": HOLD})
    if HOLD_FRAME not in {i["inputName"] for i in c.call("GetInputList")["inputs"]}:
        c.call("CreateInput", {"sceneName": HOLD, "inputName": HOLD_FRAME, "inputKind": "image_source",
                               "inputSettings": {"file": "", "unload": False}})
    set_box(c, HOLD, HOLD_FRAME, {"positionX": 0, "positionY": 0, "alignment": 5, "rotation": 0,
                                  "boundsType": "OBS_BOUNDS_STRETCH", "boundsAlignment": 0,
                                  "boundsWidth": W, "boundsHeight": H})
    set_box(c, HOLD, WEBCAM, WEBCAM_BOX)
    item(c, HOLD, MIC)
    stack(c, HOLD, [HOLD_FRAME, WEBCAM])


def build_down_panels(c) -> None:
    """The camera watch's black panels (DOWN): one item per scene that
    shows the camera, over its slot (SLOTS), directly above it, so the
    webcam strip still covers the D5's. All start off, so setup never
    flashes black on air and never keeps one a dead watch left on; a
    running watch puts a down camera's back within REASSERT_EVERY."""
    scenes = {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}
    inputs = {i["inputName"] for i in c.call("GetInputList")["inputs"]}
    for scene, slots in SLOTS.items():
        if scene not in scenes:
            continue
        have = {i["sourceName"] for i in c.call("GetSceneItemList", {"sceneName": scene})["sceneItems"]}
        for name in CAM_IDS.values():
            if name not in slots or name not in have:
                continue
            panel = DOWN[name]
            if panel not in inputs:
                c.call("CreateInput", {"sceneName": scene, "inputName": panel, "inputKind": "color_source_v3",
                                       "inputSettings": {"color": 0xFF000000, "width": W, "height": H},
                                       "sceneItemEnabled": False})
                inputs.add(panel)
            elif panel not in have:
                c.call("CreateSceneItem", {"sceneName": scene, "sourceName": panel, "sceneItemEnabled": False})
            set_box(c, scene, panel, _panel_box(*slots[name]), enabled=False)
        _seat_panels(c, scene)


def standin_on(c) -> bool:
    for i in c.call("GetSceneItemList", {"sceneName": PRODUCT})["sceneItems"]:
        if i["sourceName"] == STANDIN:
            return i["sceneItemEnabled"]
    return False


def standin(c, on: bool) -> None:
    """Swap the D5100 in the item view for a white panel, or back. While it's
    on, `go("item")` and `refocus()` leave the camera alone, and the camera
    watch leaves its black panel off."""
    if on:
        set_panel(c, D5, False)
    if on and STANDIN not in {i["inputName"] for i in c.call("GetInputList")["inputs"]}:
        c.call("CreateInput", {"sceneName": PRODUCT, "inputName": STANDIN, "inputKind": "color_source_v3",
                               "inputSettings": {"color": 0xFFFFFFFF, "width": W, "height": H}})
    if on or STANDIN in {i["sourceName"] for i in
                         c.call("GetSceneItemList", {"sceneName": PRODUCT})["sceneItems"]}:
        set_box(c, PRODUCT, STANDIN, {"positionX": 0, "positionY": 0, "alignment": 5, "rotation": 0,
                                      "boundsType": "OBS_BOUNDS_NONE", "scaleX": 1, "scaleY": 1},
                enabled=on)
        stack(c, PRODUCT, [STANDIN, D5, WEBCAM])
    set_box(c, PRODUCT, D5, enabled=not on)
    if MULTI in {s["sceneName"] for s in c.call("GetSceneList")["scenes"]}:
        set_box(c, MULTI, D5, enabled=not on)  # the multi page's D5 slot goes dark


def _no_scene(e: obsctl.ObsError, page: str) -> obsctl.ObsError:
    """OBS's "code 600" (not found) on a page's scene, said plainly."""
    if "code 600" in str(e):
        how = "setup --desktop-window <title>" if page in ("desktop", "multi") else "setup"
        return obsctl.ObsError(f"OBS has no scene {PAGES[page]!r} for the {page} page "
                               f"(collection {COLLECTION!r}); run `show_flow.py {how}`")
    return e


def set_transition(c, name: str, duration_ms: int | None = None) -> None:
    """The transition OBS's next scene switch uses ("Cut", "Fade", or any the
    collection has). OBS keeps it until it's changed; Cut has no duration."""
    c.call("SetCurrentSceneTransition", {"transitionName": name})
    if duration_ms is not None and name != "Cut":
        c.call("SetCurrentSceneTransitionDuration", {"transitionDuration": duration_ms})


def transitions(c) -> dict:
    """The collection's transitions, for the pager's transition list."""
    r = c.call("GetSceneTransitionList")
    cur = c.call("GetCurrentSceneTransition")
    return {"transitions": [t["transitionName"] for t in r["transitions"]],
            "current": r["currentSceneTransitionName"], "duration_ms": cur.get("transitionDuration")}


def go(c, page: str, refocus: bool = False, transition: str = "Fade", duration_ms: int = FADE_MS) -> dict:
    """Put a page on air: one scene switch with `transition` (by default the
    300 ms fade, set each time, so a cut made from the pager doesn't stick).
    No camera work and no lock, so it never waits behind a refocus.

    refocus=True (item and multi) refocuses the D5100 first, while the current
    page still hides it. If that fails, the feed is left running and the
    current page stays on air (the error says why). If someone asks for a
    page during that refocus, their switch wins and this one isn't made
    (`superseded_by`). A page that's already on air is left alone.

    `blacked` lists the page's cameras (PAGE_CAMS) the camera watch has
    blacked out, when there are any."""
    if page not in PAGES:
        raise ValueError(f"unknown page {page!r}; pages: {', '.join(PAGES)}")
    scene = PAGES[page]
    _mark_switch()
    out = {"page": page, "changed": False, "focused": None, "refocus_s": None}
    before = program(c)
    if before == scene:
        return _blacked(c, page, out)
    try:
        if page == "next":  # the one page allowed to show the desktop window
            w = guard_window(c)
            if w:
                out["window"] = w
        else:
            hide_window(c, scene)
    except obsctl.ObsError as e:
        raise _no_scene(e, page) from e
    if page in D5_PAGES and refocus and not standin_on(c):
        with exclusive():
            _mark_switch(clear=True)
            out["focused"], secs = refocus_d5(c)
            asked = _switch_asked()
        out["refocus_s"] = round(secs, 1)
        now = program(c)
        if now != before or asked:
            out["superseded_by"] = page_of(now) or now
            return out
    set_transition(c, transition, duration_ms)
    try:
        c.call("SetCurrentProgramScene", {"sceneName": scene})
    except obsctl.ObsError as e:
        raise _no_scene(e, page) from e
    out["changed"] = True
    return _blacked(c, page, out)


def _blacked(c, page: str, out: dict) -> dict:
    if PAGE_CAMS[page]:
        on = panels_on(c)
        ids = [cam for cam in PAGE_CAMS[page] if on[cam]]
        if ids:
            out["blacked"] = ids
    return out


def d5_covered(c, what: str, fn):
    """Run fn(c), D5100 camera work that freezes its picture, under the lock.
    If a page showing the D5 (item, multi) is on air it hides behind Up Next
    for the ~5 s the camera is frozen, then fades back to that page, also when
    fn fails (the feed is left running), but not if someone asked for a page
    meanwhile (Next included, though it's already on air): that page stays.
    Errors start with "<what> failed:"."""
    if standin_on(c):
        raise obsctl.ObsError("the D5100 stand-in is on; turn it off (standin off) to use the camera")
    with exclusive():
        _mark_switch(clear=True)
        back = program(c)
        hide = back in D5_PAGES.values()
        if hide:
            guard_window(c)
            c.call("SetCurrentProgramScene", {"sceneName": UPNEXT})
            time.sleep(FADE_MS / 1000)
        try:
            return fn(c)
        except obsctl.ObsError as e:
            raise obsctl.ObsError(f"{what} failed: {e}") from e
        finally:
            if hide and not _switch_asked() and program(c) == UPNEXT:
                c.call("SetCurrentProgramScene", {"sceneName": back})


def refocus(c) -> dict:
    """Refocus the D5100 without changing page, hidden as d5_covered says.

    On the closeup page it's the TG-7's autofocus instead (tg7_refocus):
    no lock, no cover, the D5 is left alone."""
    if program(c) == CLOSEUP:
        return tg7_refocus()
    locked, secs = d5_covered(c, "refocus", refocus_d5)
    return {"focused": locked, "refocus_s": round(secs, 1)}


def wb(c, preset: str | None = None) -> dict:
    """The D5100's white balance: {"wb", "choices", "changed", "wb_s"}. With
    `preset` (WB_ALIASES or one of the camera's labels) it's set. Reading
    needs the USB connection too, so both freeze the D5 like a refocus,
    hidden the same way (d5_covered). The D5 only: on the closeup page it
    isn't on air, so nothing is hidden, and the TG-7 is never touched."""
    return d5_covered(c, "wb", lambda c: wb_d5(c, preset))



def still_d5(c, dest: Path) -> dict:
    """A full-size D5100 still, downloaded into `dest`: the camera's own
    autofocus and shutter, behind d5_usb (live view stops for it) and hidden
    as d5_covered says. RAW+JPEG gives two files.
    Returns {"camera": "d5", "files": [...], "still_s": seconds}."""
    dest.mkdir(parents=True, exist_ok=True)
    r, secs = d5_covered(c, "still", lambda c: d5_usb(c, lambda: _gphoto2(
        "--capture-image-and-download", "--force-overwrite",
        "--filename", str(dest / "D5_%Y%m%d_%H%M%S.%C"), timeout=STILL_TIMEOUT, text=True)))
    files = [Path(f.strip()) for f in SAVED.findall(r.stdout or "")]
    if r.returncode or not files:
        why = (r.stderr or r.stdout or "").strip().splitlines()
        why = why[-1] if why else "no file saved"
        if re.search(r"out of focus|unspecified error", why, re.I):
            why += " (autofocus can't lock on an empty or low-contrast frame)"
        raise obsctl.ObsError(f"the D5100 didn't take the still: {why}")
    return {"camera": "d5", "files": [str(f) for f in files], "still_s": round(secs, 1)}

def held_page(c) -> str | None:
    """The page the hold still was taken of (hold() records it on the source)."""
    try:
        return c.call("GetInputSettings", {"inputName": HOLD_FRAME})["inputSettings"].get(HELD)
    except obsctl.ObsError:
        return None


def hold(c) -> dict:
    """Freeze the camera page on air (item or closeup): OBS screenshots the
    scene, the Hold page shows that still with the live webcam on top, and
    goes on air with the usual fade. The cameras keep running underneath."""
    scene = program(c)
    if scene == HOLD:
        return {"held": held_page(c), "changed": False}
    page = page_of(scene)
    if page not in HOLDABLE:
        raise obsctl.ObsError(f"hold freezes a camera page ({' or '.join(HOLDABLE)}); "
                              f"on air: {page or scene}")
    HOLD_DIR.mkdir(parents=True, exist_ok=True)
    try:
        last = c.call("GetInputSettings", {"inputName": HOLD_FRAME})["inputSettings"].get("file", "")
    except obsctl.ObsError as e:
        raise obsctl.ObsError(f"OBS has no {HOLD_FRAME!r}; run `show_flow.py setup`") from e
    # two files in turn: a new path makes the image source load the new still
    path = HOLD_DIR / ("hold-b.jpg" if last.endswith("hold-a.jpg") else "hold-a.jpg")
    c.call("SaveSourceScreenshot", {"sourceName": scene, "imageFormat": "jpg",
                                    "imageFilePath": str(path), "imageWidth": W, "imageHeight": H,
                                    "imageCompressionQuality": 95})
    c.call("SetInputSettings", {"inputName": HOLD_FRAME,
                                "inputSettings": {"file": str(path), HELD: page}})
    hide_window(c, HOLD)
    c.call("SetCurrentProgramScene", {"sceneName": HOLD})
    return {"held": page, "changed": True}


def release(c, to: str | None = None) -> dict:
    """Leave the hold for the live camera: the page held, or `to`."""
    if program(c) != HOLD:
        return {"page": page_of(program(c)), "changed": False}
    if to is not None and to not in HOLDABLE:
        raise ValueError(f"release to a camera page: {', '.join(HOLDABLE)}")
    return go(c, to or held_page(c) or "item")


def step(c) -> dict:
    """One step of the item sequence (module docstring): item -> closeup ->
    hold -> the other camera. Anything else on air goes to item."""
    scene = program(c)
    page = page_of(scene)
    if scene == HOLD:
        r = release(c, SWAP.get(held_page(c) or "closeup", "item"))
        return {**r, "action": "release"}
    if page == "item":
        return {**go(c, "closeup"), "action": "page"}
    if page == "closeup":
        return {**hold(c), "action": "hold"}
    return {**go(c, "item"), "action": "page"}


def focus_note(locked: bool) -> str:
    return "focused" if locked else "autofocus couldn't lock (empty podium? low contrast at the AF point?); focus unchanged"


def cmd_page(c, a):
    r = go(c, a.page, refocus=a.refocus)
    if a.json:
        print(json.dumps({**state(c), "switch": r}))
    elif r.get("superseded_by"):
        print(f"{focus_note(r['focused'])} ({r['refocus_s']}s); not switched: "
              f"{r['superseded_by']} went on air during the refocus")
    elif not r["changed"]:
        print(f"on air: {a.page} (already)")
    elif r["focused"] is None:
        print(f"on air: {a.page}")
        w = r.get("window")
        if w and not w["shown"]:
            print(f"  window hidden: {w['title'] or '(closed)'!r} isn't safe to show; card + webcam only")
    else:
        print(f"{focus_note(r['focused'])} ({r['refocus_s']}s); on air: {a.page}")
    if r.get("blacked") and not a.json:
        seen = (watch_cams() or {}).get("cameras", {})
        why = [CAM_LABEL[k] + (f" ({seen[k]['reason']})" if seen.get(k, {}).get("reason") else "")
               for k in r["blacked"]]
        print(f"  black in place of: {', '.join(why)}; `reconnect` or `page webcam`")


def cmd_standin(c, a):
    standin(c, a.state == "on")
    print(f"D5100 stand-in: {a.state}" + ("  (item view shows white; no refocus)" if a.state == "on" else ""))


def cmd_refocus(c, a):
    r = refocus(c)
    if a.json:
        print(json.dumps({**state(c), "refocus": r}))
    else:
        print(f"{focus_note(r['focused'])} ({r['refocus_s']}s); on air: {program(c)}")


def cmd_wb(c, a):
    r = wb(c, a.preset)
    if a.json:
        print(json.dumps({**state(c), "wb": r}))
        return
    print(f"D5100 white balance: {r['wb']}" + (" (already)" if a.preset and not r["changed"] else "")
          + f" ({r['wb_s']}s); on air: {program(c)}")
    if not a.preset:
        print("choices: " + ", ".join(r["choices"]))


def cmd_hold(c, a):
    r = hold(c)
    if a.json:
        print(json.dumps({**state(c), "hold": r}))
    else:
        print(f"on hold: a still of {r['held']}{'' if r['changed'] else ' (already)'}; "
              "turn the item, then `release` (or `step`)")


def cmd_release(c, a):
    r = release(c, a.to)
    if a.json:
        print(json.dumps({**state(c), "switch": r}))
    else:
        print(f"on air: {r['page']}" + ("" if r["changed"] else " (wasn't on hold)"))


def cmd_step(c, a):
    r = step(c)
    if a.json:
        print(json.dumps({**state(c), "step": r}))
    elif r["action"] == "hold":
        print(f"on hold: a still of {r['held']}; turn the item, then step again")
    else:
        print(f"on air: {r['page']}")


def cmd_tg7(c, a):
    if a.action == "stop":
        tg7_stop()
    elif a.action == "start":
        print("TG-7 feed " + ("started" if tg7_start() else "already running")
              + f"; it waits for the camera's Wi-Fi if it isn't up (log {TG7_LOG})")
    print(f"TG-7 feed: {tg7_status()}  (log {TG7_LOG})")


def cmd_watch(c, a):
    """start, stop and run get no `c` (main): they work with OBS down."""
    if a.action == "run":
        return watch_run()
    if a.action == "start":
        print("camera watch " + ("started" if watch_start() else "already running") + f"  (log {WATCH_LOG})")
        return 0
    if a.action == "stop":
        watch_stop(c)
        print("camera watch: stopped; every black panel off")
        return 0
    w = watch_cams()
    print(f"camera watch: running (pid {w['pid']})" if w else "camera watch: off (show_flow.py watch start)")
    for k, v in cam_states(c, w, standin_on(c), refocusing()).items():
        print(f"{CAM_LABEL[k]}: {v['state']}" + (f" ({v['reason']})" if v["reason"] else "")
              + ("; black on air" if v["blacked"] else ""))
    return 0


def cmd_golive(c, a):
    if not a.confirm:
        raise obsctl.ObsError(f"going live on {a.dest} is public-facing; re-run with --confirm")
    r = go_live(c, a.dest, check=a.check)
    if not r["changed"]:
        print(f"{a.dest}: already live")
        return
    print(f"LIVE on {a.dest}: {r['kbps']} kbps"
          + (f", skipped {r['skipped']}" if r.get("skipped") is not None else ""))
    if a.dest == "ebay":
        print("video is flowing; the show starts when you click Start event in Stream manager")


def cmd_stop(c, a):
    for d in [a.dest] if a.dest else list(DESTS):
        stop_dest(c, d)
        print(f"{d}: off")


def cmd_twitch_setup(c, a):
    twitch_setup(c)
    print("Twitch output configured (Custom Output (FFmpeg) -> URL, flv, x264 veryfast 3000k, AAC).\n"
          "Last step, yours: OBS Settings > Output > Recording > \"File path or URL\": paste\n"
          "  rtmp://live.twitch.tv/app/<your Twitch stream key>\n"
          "then OK. (Twitch dashboard > Settings > Stream > Primary Stream key.)")


def cmd_status(c, a):
    if a.json:
        print(json.dumps(state(c)))
        return
    print(f"profile: {c.call('GetProfileList')['currentProfileName']}  "
          f"scenes: {c.call('GetSceneCollectionList')['currentSceneCollectionName']}")
    scene = program(c)
    page = page_of(scene)
    print(f"on air: {page + ' (' + scene + ')' if page else scene}")
    for dest, s in streams(c).items():
        print(f"{dest}: {'LIVE ' + s['timecode'] if s['live'] else 'off'}")
    print(f"D5100 feed: {feed_status()}")
    print(f"TG-7 feed: {tg7_status()}")
    w = watch_cams()
    if w:
        cams = cam_states(c, w, standin_on(c), refocusing())
        print("cameras: " + " · ".join(f"{CAM_LABEL[k]} {v['state']}" for k, v in cams.items())
              + "  (watch running)")
    else:
        print("camera watch: off (show_flow.py watch start)")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="build/refresh the pages (desktop and multi need --desktop-window once)")
    s.add_argument("--window", help="title substring of the desktop window for Up Next")
    s.add_argument("--intro-text", default="Starting\nsoon", help="the pre-show card's text; \\n breaks a line")
    s.add_argument("--desktop-window", help="title substring of the app window for the desktop and multi pages")
    s.add_argument("--desktop-crop-left", type=int, help="source pixels to trim off that window's left")
    s.add_argument("--d5-rotation", type=float, default=270, help="D5100 mounted on its side: 270")
    s.add_argument("--tg7-rotation", type=float, default=0, help="TG-7 on its side: 90 or 270")
    s.set_defaults(fn=cmd_setup)
    s = sub.add_parser("feed", help="D5100 live view -> /dev/video10")
    s.add_argument("action", choices=["start", "stop", "status"])
    s.set_defaults(fn=cmd_feed)
    s = sub.add_parser("reconnect", help="make OBS reopen the cameras (picture black or frozen)")
    s.add_argument("camera", nargs="?", choices=["d5", "webcam"], help="default: both")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_reconnect)
    s = sub.add_parser("page", help="put a page on air (one scene switch, instant)")
    s.add_argument("page", choices=list(PAGES))
    s.add_argument("--json", action="store_true")
    s.add_argument("--refocus", action="store_true",
                   help="item, multi: refocus the D5 first, hidden behind the current page (~5 s)")
    s.add_argument("--no-refocus", action="store_true", help=argparse.SUPPRESS)  # now the default
    s.set_defaults(fn=cmd_page)
    sub.add_parser("next", help="= page next").set_defaults(fn=cmd_page, page="next", json=False,
                                                            refocus=False)
    sub.add_parser("back", help="= page item").set_defaults(fn=cmd_page, page="item", json=False,
                                                            refocus=False)
    s = sub.add_parser("tg7", help="TG-7 live view -> OBS (tg7cam stream, detached)")
    s.add_argument("action", choices=["start", "stop", "status"])
    s.set_defaults(fn=cmd_tg7)
    s = sub.add_parser("watch", help="black panel over a camera that drops (run: in the foreground)")
    s.add_argument("action", choices=["start", "stop", "status", "run"])
    s.set_defaults(fn=cmd_watch)
    s = sub.add_parser("hold", help="freeze the camera page on air while the item turns")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_hold)
    s = sub.add_parser("release", help="leave the hold for the live camera")
    s.add_argument("--to", choices=list(HOLDABLE), help="default: the page held")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_release)
    s = sub.add_parser("step", help="item -> closeup -> hold -> the other camera ...")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_step)
    s = sub.add_parser("standin", help="white panel instead of the D5100 in the item view")
    s.add_argument("state", choices=["on", "off"])
    s.set_defaults(fn=cmd_standin)
    s = sub.add_parser("refocus", help="refocus the D5 (hidden behind Up Next if item/multi is on air)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_refocus)
    s = sub.add_parser("wb", help="D5100 white balance: show it, or set PRESET (hidden like refocus)")
    s.add_argument("preset", nargs="?", help=f"{', '.join(WB_ALIASES)}, or one of the camera's labels")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_wb)
    s = sub.add_parser("golive", help="start one destination (the other keeps running)")
    s.add_argument("dest", choices=list(DESTS))
    s.add_argument("--confirm", action="store_true", help="required: this broadcasts")
    s.add_argument("--check", type=float, default=5, help="seconds to watch the connection")
    s.set_defaults(fn=cmd_golive)
    s = sub.add_parser("stop", help="stop one destination, or both")
    s.add_argument("dest", nargs="?", choices=list(DESTS))
    s.set_defaults(fn=cmd_stop)
    sub.add_parser("twitch-setup", help="point the recording output at Twitch (once; nothing live)"
                   ).set_defaults(fn=cmd_twitch_setup)
    s = sub.add_parser("status")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)
    a = p.parse_args(argv)
    try:
        if a.cmd == "watch" and a.action != "status":
            return cmd_watch(None, a)  # these work with OBS down (run reconnects on its own)
        c = connect()
        try:
            a.fn(c, a)
        finally:
            c.close()
    except obsctl.ObsError as e:
        print(f"show_flow: {e}", file=sys.stderr)
        return 1
    except OSError as e:  # the OBS socket timed out or dropped mid-command
        print(f"show_flow: lost the connection to OBS: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
