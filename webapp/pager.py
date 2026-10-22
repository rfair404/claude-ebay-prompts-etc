"""webapp/pager.py — /api/pager: put a live show's page on air over HTTP.

The same pager as `tools/live/show_flow.py page` (one implementation, in
show_flow: `state()`, `go()`, `refocus()`), served as JSON so a control
page, a phone, or another agent's web app can drive the show:

    GET  /pager                 the control page: a control-room screen (switcher, monitors, telemetry, log)
    GET  /api/pager             what's on air, plus `music`: {"ready", "on", "db", "track", "preset", "presets"}
                                and `health`: OBS and output telemetry (show_flow.health())
    GET  /api/pager/preview.jpg a small JPEG of the program scene (what viewers see), no audio
    GET  /api/pager/tile/<page>.jpg  a JPEG of one page's scene, on air or not (?width=80-480, default 160)
    GET  /api/pager/transitions {"transitions": ["Cut", "Fade", ...], "current", "duration_ms"}
    POST /api/pager             {"page": "intro" | "item" | "closeup" | "next" | "desktop" | "webcam" | "multi",
                                 "refocus": false, "transition": "Fade", "duration_ms": 300}: straight to program
    POST /api/pager/preview     {"page": "item" | null}: line a page up on the pager's preview bus
    POST /api/pager/take        {"page": "item", "transition": "Fade", "duration_ms": 300}: preview to
                                program (no page: the preview bus's); program and preview then swap
    POST /api/pager/refocus     refocus without changing page (D5100; on closeup the TG-7)
    POST /api/pager/wb          {"preset": "daylight"}: the D5100's white balance (no preset: read it)
    POST /api/pager/hold        {"on": true}: freeze the camera page on air; false: back to it live
    POST /api/pager/step        the item sequence: item -> closeup -> hold -> the other camera
    POST /api/pager/tg7         {"action": "start" | "stop"}: the TG-7 feed (tg7cam stream)
    POST /api/pager/watch       {"action": "start" | "stop"}: the camera watch (black over a dead camera)
    POST /api/pager/reconnect   OBS reopens the D5100 and webcam sources (black / frozen picture)
    POST /api/pager/stream      {"dest": "ebay" | "twitch", "action": "start" | "stop"}
    POST /api/pager/standin     {"on": true}: white panel for the D5100 in the item view
    POST /api/pager/music       {"on": true | false}: fade it in / out (~2 s, show_music.py);
                                {"preset": "lofi" | "folk"}: switch mixes (fades out and back in if on)

Every answer is the state object, plus `switch`, `refocus` or `wb` on a POST:

    {"page": "item", "scene": "eBay Main", "pages": ["intro", "item", "next"],
     "live": true, "timecode": "00:12:03.400", "feed": true, "feed_status": "running",
     "streams": {"ebay": {"live": true, "timecode": "00:12:03.400"},
                 "twitch": {"live": false, "timecode": null}},
     "standin": false, "refocusing": false, "held": null, "tg7_status": "running",
     "watch": true,
     "cameras": {"d5": {"state": "down", "reason": "no frames from the camera for 6s",
                        "since": 1792617852.1, "blacked": true},
                 "webcam": {"state": "live", "reason": "", "since": 1792617790.4, "blacked": false},
                 "tg7": {"state": "off", "reason": "TG-7 feed stopped", "since": 1792617790.4,
                         "blacked": true}},
     "switch": {"page": "item", "changed": true, "focused": null, "refocus_s": null,
                "blacked": ["d5"]}}

`page` is null when OBS is on a scene that isn't one of the pager's, and
"hold" on the hold still; `held` then names the page the still was taken of.
A hold or step answers with `hold` / `step` (show_flow.hold() / step()).
`feed_status` is "stopped", "running", or "running, no frames for Ns" (the
feed loop is up but the camera sends nothing). eBay and
Twitch start and stop independently (show_flow DESTS: eBay on OBS's stream
output, Twitch on its recording output); a start blocks ~5 s while it
checks that bytes are flowing, and is public on Twitch the moment it lands.
A switch to `next` also carries `window: {"title", "shown"}`: the Up Next
window is shown only when its live title is a public eBay / Pop's Games
page (show_flow.SAFE_WINDOW) and not a private one (PRIVATE_WINDOW);
otherwise card + webcam only. Every other page hides the window.
`webcam` is the fallback page: the webcam alone, no D5100. `multi` shows
the D5100, the webcam and the desktop page's window at once.

`cameras` is the camera watch's word on each camera (`show_flow.py watch`):
`state` "live", "down" (no moving picture), "off" (its feed isn't running),
"busy" (the D5100 mid refocus, WB or still), "standin", or "unknown" (no
watch running, or no verdict yet); `reason` says why, `since` is when (Unix
time). `blacked` is read from OBS: the camera's black panel is on air. A
switch to a page with a blacked camera carries `blacked: [ids]`.

The preview is OBS's own render of the program scene, 360 px wide, so it
works on or off air and shows the same frame for eBay and Twitch. It takes
no lock and never touches a camera; the control page asks for one about
twice a second while the tab is visible and the preview is shown. A tile is
the same render of one page's scene, so the page can show every page at
once like a switcher's multiview; a camera OBS only opens while it is
shown may render black there. A scene OBS doesn't have is 502, naming it.

`health` is OBS's counters, read in the same poll as the state:

    {"cpu": 14.2, "mem_mb": 812, "fps": 30.0, "frame_ms": 3.1,
     "render_skipped": 0, "render_frames": 90211, "encode_skipped": 0, "encode_frames": 90211,
     "disk_mb": 51200, "outputs": {"ebay": {"live": true, "bytes": 412004112, "kbps": 4800, "skipped": 0,
     "frames": 21600, "congestion": 0.0, "reconnecting": false}, "twitch": {...}}}

Counters are cumulative; `kbps` is the rate since the last poll, timed by
OBS's outputDuration (null on the first poll and off air), and the page
turns two of the `*_skipped` counts into "dropping now". `skipped`, `frames`
and `congestion` are null on Twitch (OBS's record output reports none). If
OBS refuses GetStats, `health` is {"error": ...} and the rest still answers.

A page switch is one OBS scene switch (300 ms fade) and never touches the
camera, so it answers at once, even while a refocus runs (`refocusing`
in the state). Refocus is its own call, POST /api/pager/refocus (on the item
or multi view it hides behind Up Next for ~5 s); `{"page": "item", "refocus": true}`
refocuses first, behind the current page. `focused: false` means the
D5100's autofocus couldn't lock (nothing with contrast at the center of the
frame); focus is unchanged. A refocus while another is running (shell or
web) is 409 at once, not queued. A failed refocus leaves the D5100 feed
running. White balance answers `wb: {"wb", "choices", "changed", "wb_s"}`
and works like a refocus: 5-12 s with the D5100 frozen, hidden the same way,
the same lock (409 while either runs; `refocusing` is true meanwhile). An
unknown preset is 502, naming the camera's choices. Reconnect answers
`reconnect: {source: {"ok", "device" | "error"}}` per camera; it never
waits on a refocus, and viewers see a blink of black on each camera as OBS
reopens it. 503 means OBS isn't reachable, 504 that it stopped answering
mid-call; 502 is any other OBS or camera failure, with the reason as the
detail.

These routes change what a public broadcast shows, so they get freight's
guard (webapp/freight.py `_local_only`): loopback Host only, and writes
from loopback origins only. Build the control page as a route of this app
(same origin) or a page on localhost; there are no CORS headers.
"""
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from webapp.freight import _local_only

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "live"))
import obsctl as _obsctl          # noqa: E402
import show_flow as _show_flow    # noqa: E402
import show_music as _show_music  # noqa: E402

router = APIRouter(dependencies=[Depends(_local_only)])


class StandinRequest(BaseModel):
    on: bool


class StreamRequest(BaseModel):
    dest: Literal["ebay", "twitch"]
    action: Literal["start", "stop"]


Page = Literal["intro", "item", "closeup", "next", "desktop", "webcam", "multi"]


class PageRequest(BaseModel):
    page: Page
    refocus: bool = False  # true: item/multi refocus the D5100 first, behind the current page (~5 s)
    transition: str = "Fade"  # any of GET /api/pager/transitions
    duration_ms: int = Field(_show_flow.FADE_MS, ge=50, le=5000)


class PreviewRequest(BaseModel):
    page: Page | None


class TakeRequest(BaseModel):
    page: Page | None = None  # what was on preview when take was pressed; none: the pager's preview
    transition: str = "Fade"
    duration_ms: int = Field(_show_flow.FADE_MS, ge=50, le=5000)


# The pager's preview bus: the page lined up to take next. The pager's own,
# not OBS's studio mode, which would render a second scene all the time on a
# laptop already short of CPU; the page shows it from screenshots instead.
_preview: dict = {"page": None}


class HoldRequest(BaseModel):
    on: bool


class MusicRequest(BaseModel):
    on: bool | None = None
    preset: Literal[tuple(_show_music.PRESETS)] | None = None


class Tg7Request(BaseModel):
    action: Literal["start", "stop"]


class WatchRequest(BaseModel):
    action: Literal["start", "stop"]


class WbRequest(BaseModel):
    preset: str | None = None  # an alias (show_flow.WB_ALIASES) or a camera label; none: read it


@contextmanager
def _obs():
    try:
        c = _show_flow.connect()
    except (_obsctl.ObsError, OSError) as exc:  # OSError: timed out in the handshake
        raise HTTPException(503, str(exc)) from exc
    try:
        yield c
    except _show_flow.Busy as exc:
        raise HTTPException(409, str(exc)) from exc
    except _obsctl.ObsError as exc:
        raise HTTPException(502, str(exc)) from exc
    except OSError as exc:  # socket timeout / reset mid-request: say so, not a bare 500
        raise HTTPException(504, f"OBS stopped answering: {exc}") from exc
    finally:
        c.close()


@router.get("/pager", response_class=HTMLResponse)
def pager_page() -> str:
    return PAGE_HTML


@router.get("/api/pager")
def pager_state() -> dict:
    with _obs() as c:
        outs = _show_flow.outputs(c)  # one status read for the state and the telemetry
        try:
            health = _show_flow.health(c, outs)
        except _obsctl.ObsError as exc:  # no telemetry is no reason to lose the pager
            health = {"error": str(exc)}
        return {**_show_flow.state(c, outs), "music": _show_music.state(c), "health": health,
                "preview": _preview["page"]}


@router.get("/api/pager/preview.jpg")
def pager_preview() -> Response:
    with _obs() as c:
        jpg = _show_flow.program_shot(c)
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/pager/tile/{page}.jpg")
def pager_tile(page: Page, width: int = Query(160, ge=80, le=480)) -> Response:
    with _obs() as c:
        jpg = _show_flow.page_shot(c, page, width=width)
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/api/pager")
def pager_go(req: PageRequest) -> dict:
    with _obs() as c:
        switch = _show_flow.go(c, req.page, refocus=req.refocus,
                               transition=req.transition, duration_ms=req.duration_ms)
        return {**_show_flow.state(c), "switch": switch}


@router.post("/api/pager/preview")
def pager_set_preview(req: PreviewRequest) -> dict:
    _preview["page"] = req.page  # no OBS call: the page screenshots the scene itself
    return {"preview": req.page}


@router.post("/api/pager/take")
def pager_take(req: TakeRequest) -> dict:
    page = req.page or _preview["page"]
    if not page:
        raise HTTPException(409, "nothing on preview: pick a page first")
    with _obs() as c:
        before = _show_flow.page_of(_show_flow.program(c))
        switch = _show_flow.go(c, page, transition=req.transition, duration_ms=req.duration_ms)
        if switch["changed"]:  # program and preview swap, like a vision mixer
            _preview["page"] = before if before in _show_flow.PAGES else None
        return {**_show_flow.state(c), "switch": switch, "preview": _preview["page"]}


@router.get("/api/pager/transitions")
def pager_transitions() -> dict:
    with _obs() as c:
        return _show_flow.transitions(c)


@router.post("/api/pager/stream")
def pager_stream(req: StreamRequest) -> dict:
    with _obs() as c:
        fn = _show_flow.go_live if req.action == "start" else _show_flow.stop_dest
        r = fn(c, req.dest)
        return {**_show_flow.state(c), "stream": r}


@router.post("/api/pager/standin")
def pager_standin(req: StandinRequest) -> dict:
    with _obs() as c:
        _show_flow.standin(c, req.on)
        return _show_flow.state(c)


# Not under show_flow's lock: a fade never touches the camera or the page, so
# it can run during a refocus. The pager disables the button while one runs.
@router.post("/api/pager/music")
def pager_music(req: MusicRequest) -> dict:
    if (req.on is None) == (req.preset is None):
        raise HTTPException(422, 'send one of "on" or "preset"')
    with _obs() as c:
        if req.preset:
            r = _show_music.use(c, req.preset)  # before state(), so the answer shows the new preset
            return {**_show_music.state(c), "switch": r}
        r = _show_music.set_on(c, req.on)
        return {**_show_music.state(c), "fade": r}


@router.post("/api/pager/refocus")
def pager_refocus() -> dict:
    with _obs() as c:
        r = _show_flow.refocus(c)
        return {**_show_flow.state(c), "refocus": r}


@router.post("/api/pager/wb")
def pager_wb(req: WbRequest) -> dict:
    with _obs() as c:
        r = _show_flow.wb(c, req.preset)
        return {**_show_flow.state(c), "wb": r}


@router.post("/api/pager/reconnect")
def pager_reconnect() -> dict:
    with _obs() as c:
        r = _show_flow.reconnect(c)
        return {**_show_flow.state(c), "reconnect": r}


@router.post("/api/pager/hold")
def pager_hold(req: HoldRequest) -> dict:
    with _obs() as c:
        if req.on:
            r = _show_flow.hold(c)
            return {**_show_flow.state(c), "hold": r}
        r = _show_flow.release(c)
        return {**_show_flow.state(c), "switch": r}


@router.post("/api/pager/step")
def pager_step() -> dict:
    with _obs() as c:
        r = _show_flow.step(c)
        return {**_show_flow.state(c), "step": r}


@router.post("/api/pager/tg7")
def pager_tg7(req: Tg7Request) -> dict:
    with _obs() as c:
        if req.action == "start":
            _show_flow.tg7_start()
        else:
            _show_flow.tg7_stop()
        return _show_flow.state(c)


@router.post("/api/pager/watch")
def pager_watch(req: WatchRequest) -> dict:
    with _obs() as c:
        if req.action == "start":
            _show_flow.watch_start()
        else:
            _show_flow.watch_stop(c)
        return _show_flow.state(c)


# The control page: tally bar; program monitor + lineup | switcher | telemetry;
# event log. Plain HTML + fetch against the routes above, same origin, so the
# guard's Origin check passes. It polls state + telemetry every 1 s while the tab is visible (not at all
# while hidden: OBS is short of CPU during a show), so a switch made from the
# shell or in OBS shows up here too, and is logged. Page clicks are sent one
# at a time, latest wins: clicking 2 then 3 fast ends on 3, never on
# whichever request OBS happened to get last. A page click is never blocked
# by a refocus or a stream start in flight. Hold / release / step join the
# same queue, so a step pressed during a switch lands after it, never before.
# The program monitor fetches a frame ~2x a second (the next only after the
# last one lands). The multiview's pictures are off unless switched on (each
# is an OBS render, and the show laptop is short of CPU); on, it refreshes
# one off-air tile a second, round robin. Both stop while the tab is hidden.
# A click's answer and every poll are diffed by changes() into the log.
# Keys: 1 intro, 2 item, 3 next, 4 desktop, 5 webcam, 6 close-up, 7 multi, H hold/release,
# Space step, R refocus, C reconnect cameras, M music on/off, [ / ] previous / next lot.
# Keys are ignored while a list or text box has focus (the white balance list's own
# letters pick a preset; the show name is typed).
PAGE_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Show Control</title>
<style>
:root { --bg:#0a0c0f; --panel:#11151a; --panel2:#171c22; --ink:#d9dfe7; --muted:#7c8794; --line:#232a33;
        --ok:#2fbf71; --warn:#f0a020; --crit:#ff4d4d; --pgm:#e3262b; --accent:#4aa3ff; color-scheme:dark; }
* { box-sizing:border-box; }
[hidden] { display:none !important; }
html, body { height:100%; }
body { margin:0; background:var(--bg); color:var(--ink); font:13px/1.35 system-ui, -apple-system, "Segoe UI", sans-serif; }
button, select, input { font:inherit; color:inherit; }
button { cursor:pointer; }
button:disabled { opacity:.5; cursor:wait; }
.mono, .tc, .kpi b, #log, .clock { font-family:ui-monospace, "JetBrains Mono", "DejaVu Sans Mono", Menlo, monospace;
                                   font-variant-numeric:tabular-nums; }
.noc { display:grid; grid-template-rows:auto minmax(0, 1fr) 150px; height:100vh; gap:8px; padding:8px; }
.panel { background:var(--panel); border:1px solid var(--line); border-radius:6px; padding:10px; min-height:0; overflow:auto; }
.panel h2 { display:flex; align-items:center; justify-content:space-between; gap:8px; margin:0 0 8px;
            font-size:11px; font-weight:600; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); }
.panel h2 + h2, .panel * + h2 { margin-top:14px; }
.panel h2 button { font-size:11px; letter-spacing:.06em; padding:2px 8px; border-radius:4px;
                   border:1px solid var(--line); background:var(--panel2); color:var(--muted); }

/* top bar */
.bar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:6px 10px; background:var(--panel);
       border:1px solid var(--line); border-radius:6px; }
.bar h1 { margin:0 6px 0 0; font-size:12px; font-weight:700; letter-spacing:.16em; text-transform:uppercase; color:var(--muted); }
.tally { display:flex; align-items:center; gap:8px; padding:4px 12px; border-radius:4px; font-weight:800;
         letter-spacing:.12em; background:var(--panel2); border:1px solid var(--line); color:var(--muted); }
.tally.live { background:var(--pgm); border-color:var(--pgm); color:#fff; }
.tally .tc { font-weight:600; letter-spacing:0; }
.pill { display:flex; align-items:center; gap:6px; padding:4px 10px; border-radius:4px; border:1px solid var(--line);
        background:var(--panel2); white-space:nowrap; }
.pill b { font-weight:600; }
.led { width:8px; height:8px; border-radius:50%; background:var(--muted); flex:none; }
.ok > .led, .led.ok { background:var(--ok); box-shadow:0 0 6px var(--ok); }
.warn > .led, .led.warn { background:var(--warn); box-shadow:0 0 6px var(--warn); }
.crit > .led, .led.crit { background:var(--crit); box-shadow:0 0 6px var(--crit); }
.live > .led, .led.live { background:var(--pgm); box-shadow:0 0 8px var(--pgm); }
#msg { flex:1 1 240px; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--muted); }
#msg.err { color:var(--crit); } #msg.warn { color:var(--warn); }
.clock { font-size:15px; color:var(--ink); }

.grid { display:grid; grid-template-columns:minmax(220px, 17vw) minmax(0, 1fr) minmax(300px, 24vw); gap:8px; min-height:0; }

/* preview / program monitors and the transition bar between them */
.bus { display:flex; justify-content:center; align-items:stretch; gap:12px; margin-bottom:10px; }
.mon { margin:0; display:flex; flex-direction:column; align-items:center; gap:4px; min-width:0; }
.mon figcaption { font-size:11px; letter-spacing:.12em; color:var(--muted); white-space:nowrap; }
.mon figcaption b { padding:1px 6px; border-radius:3px; color:#fff; }
.pvw figcaption b { background:var(--ok); color:#06150d; } .pgmon figcaption b { background:var(--pgm); }
.mon img { display:block; height:min(36vh, 460px); max-width:100%; aspect-ratio:9/16; object-fit:contain; background:#000;
           border:3px solid var(--line); border-radius:4px; }
.pvw img { border-color:var(--ok); } .pgmon img { border-color:#5a1d1f; } .pgmon.live img { border-color:var(--pgm); }
.mon.stale img { opacity:.35; }
.bus.off .mon img, .bus.off #shotnote { display:none; }
#shotnote { color:var(--muted); font-size:11px; text-align:center; max-width:220px; }
.pgmon.stale #shotnote { color:var(--warn); }
.tbar { display:flex; flex-direction:column; justify-content:center; gap:8px; width:128px; flex:none; }
.tbar button { padding:14px 8px; border-radius:4px; border:1px solid var(--line); background:var(--panel2); font-weight:800;
               letter-spacing:.12em; }
.tbar button small { display:block; font-weight:400; letter-spacing:0; color:var(--muted); font-size:10px; }
#auto { background:#3a1214; border-color:var(--pgm); }
#auto.go, #cut.go { background:var(--pgm); color:#fff; }
.tbar select, .tbar input { width:100%; padding:6px; border-radius:4px; border:1px solid var(--line); background:var(--panel2); }
.tbar .dur { display:flex; align-items:center; gap:4px; color:var(--muted); font-size:11px; }
.tile .pv { position:absolute; top:6px; right:6px; padding:1px 6px; border-radius:3px; font-size:10px; font-weight:800;
            letter-spacing:.12em; background:var(--ok); color:#06150d; display:none; }
.tile.pvw { border-color:var(--ok); box-shadow:0 0 0 1px var(--ok); }
.tile.pvw .pv { display:block; }
.tile.pvw:not(.on) .lab, .tile.pvw:not(.on) .sub { background:#123222; }

/* lineup */
.lot { padding:8px; border:1px solid var(--line); border-radius:4px; background:var(--panel2); }
.lot + .lot { margin-top:6px; }
.lot small { display:block; font-size:10px; letter-spacing:.14em; text-transform:uppercase; color:var(--muted); }
.lot .t { font-weight:600; }
.lot .meta { color:var(--muted); font-size:12px; }
.lot .flags { color:var(--warn); font-size:12px; }
.lot.cur { border-color:var(--accent); }
.lotnav { display:flex; gap:6px; margin-top:6px; }
.lotnav button, .lotnav input { flex:1; min-width:0; padding:6px 8px; border-radius:4px; border:1px solid var(--line); background:var(--panel2); }
.lots { list-style:none; margin:8px 0 0; padding:0; }
.lots li { display:flex; gap:8px; padding:3px 6px; border-radius:3px; cursor:pointer; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.lots li:hover { background:var(--panel2); }
.lots li.cur { background:var(--accent); color:#05101c; }
.lots li .n { flex:none; width:28px; color:var(--muted); } .lots li.cur .n { color:inherit; }
.copy { padding:1px 6px; border-radius:3px; border:1px solid var(--line); background:transparent; font-size:11px; }

/* switcher */
.mv { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:8px; }
.tile { position:relative; display:block; padding:0; border:2px solid var(--line); border-radius:4px; background:#000;
        overflow:hidden; text-align:left; }
.tile img { display:block; width:100%; height:clamp(80px, calc((100vh - 36vh - 560px) / 2), 300px); object-fit:contain; background:#000; }
.tile .lab { display:flex; align-items:center; gap:6px; padding:5px 6px; background:var(--panel2); border-top:1px solid var(--line); }
.tile .key { flex:none; min-width:20px; padding:0 4px; border:1px solid var(--line); border-radius:3px; text-align:center;
             color:var(--muted); font-weight:700; font-size:11px; }
.tile .name { font-weight:700; letter-spacing:.06em; text-transform:uppercase; font-size:12px; }
.tile .sub { display:block; padding:0 6px 5px; background:var(--panel2); color:var(--muted); font-size:11px;
             white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tile .flag { position:absolute; top:6px; left:6px; padding:1px 6px; border-radius:3px; font-size:10px; font-weight:800;
              letter-spacing:.12em; background:var(--pgm); color:#fff; display:none; }
.tile:hover { border-color:var(--muted); }
.tile.on { border-color:var(--pgm); box-shadow:0 0 0 1px var(--pgm); }
.tile.on .lab, .tile.on .sub { background:var(--pgm); color:#fff; border-color:var(--pgm); }
.tile.on .key { color:#fff; border-color:rgba(255,255,255,.6); }
.tile.on .flag { display:block; }
.tile.pending { border-color:var(--ok); border-style:dashed; }
.tile.stale img { opacity:.4; }
.tile.missing img { opacity:.15; }
.tile .note { position:absolute; top:40%; left:0; right:0; text-align:center; font-size:11px; color:var(--warn); display:none; }
.tile.missing .note { display:block; }
.mv.off .tile img { height:0; }
.mv.off .tile .flag { left:auto; right:6px; top:5px; }  /* no picture: tally beside the label, not over its key */
.mv.off .tile.on.pvw .pv { display:none; }
#hold.on { border-color:var(--warn); }
#hold.on .lab, #hold.on .sub { background:var(--warn); color:#111; }
.ctl { display:grid; grid-template-columns:repeat(auto-fit, minmax(118px, 1fr)); gap:6px; margin-top:8px; }
.ctl button, .ctl select, .ctl label { padding:9px 10px; border-radius:4px; border:1px solid var(--line); background:var(--panel2);
                                       text-align:left; }
.ctl button small, .ctl label small { color:var(--muted); }
.ctl label { display:flex; align-items:center; gap:6px; cursor:pointer; }
.ctl .wb { display:flex; gap:6px; grid-column:span 2; }
.ctl .wb select { flex:1; min-width:0; }

/* telemetry */
.dest { padding:8px; border:1px solid var(--line); border-radius:4px; background:var(--panel2); }
.dest + .dest { margin-top:6px; }
.dh { display:flex; align-items:center; gap:8px; }
.dh b { font-size:13px; } .dh .st { flex:1; color:var(--muted); font-family:ui-monospace, monospace; }
.dest a.open { font-size:11px; color:var(--muted); text-decoration:none; white-space:nowrap; }
.dest a.open:hover { color:var(--accent); }
.dest .go { padding:4px 12px; border-radius:4px; border:1px solid var(--line); background:var(--panel); min-width:64px; }
.dest.live { border-color:var(--pgm); } .dest.live .st { color:var(--pgm); font-weight:700; }
.dest.live .go { background:var(--pgm); border-color:var(--pgm); color:#fff; }
button.armed { background:var(--warn) !important; border-color:var(--warn) !important; color:#111 !important; }
button.armed::after { content:"? click again"; }
#music.on { border-color:var(--ok); } #music.on .st { color:var(--ok); }
#music.on .dh .go { background:var(--ok); border-color:var(--ok); color:#06150d; }
#presets { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; } #presets:empty { display:none; }
#presets .go.on { background:var(--ok); border-color:var(--ok); color:#06150d; }
.kpis { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:6px; margin-top:6px; }
.kpi { padding:5px 7px; border-radius:4px; background:var(--panel); border:1px solid var(--line); min-width:0; }
.kpi small { display:block; font-size:10px; letter-spacing:.1em; text-transform:uppercase; color:var(--muted); }
.kpi b { font-size:17px; font-weight:600; white-space:nowrap; }
.kpi.ok b { color:var(--ok); } .kpi.warn b { color:var(--warn); } .kpi.crit b { color:var(--crit); }
.kpi.crit { border-color:var(--crit); }
canvas.spark { display:block; width:100%; height:34px; margin-top:6px; }
.src { display:flex; align-items:center; gap:8px; padding:5px 2px; border-bottom:1px solid var(--line); }
.src:last-child { border-bottom:0; }
.src span { flex:1; } .src em { font-style:normal; color:var(--muted); font-family:ui-monospace, monospace; font-size:12px; text-align:right; }

/* event log */
#logp { display:flex; flex-direction:column; }
#log { list-style:none; margin:0; padding:0; overflow:auto; flex:1; font-size:12px; }
#log li { padding:1px 0; white-space:pre-wrap; }
#log time { color:var(--muted); margin-right:10px; }
#log .err { color:var(--crit); } #log .warn { color:var(--warn); } #log .ok { color:var(--ok); }
#log .sys { color:#9fb3c8; }

@media (max-width: 1100px) {
  .noc { height:auto; grid-template-rows:auto; }
  .grid { grid-template-columns:minmax(0, 1fr); }
  .panel { overflow:visible; }
  .mon img { height:auto; width:min(100%, 200px); }
  .tbar { width:96px; }
  .tile img { height:150px; }
  #logp { height:260px; }
}
@media (max-width: 520px) { .mv { grid-template-columns:repeat(2, minmax(0, 1fr)); } .ctl .wb { grid-column:auto; } }
</style></head>
<body><div class="noc">

<header class="bar">
  <h1>Show Control</h1>
  <span class="tally" id="tally"><span id="tallytext">OFF AIR</span> <span class="tc" id="tc">--:--:--</span></span>
  <span class="pill"><small>PGM</small> <b id="pgmname">...</b></span>
  <span class="pill" id="pill-ebay"><span class="led"></span><b>eBay</b> <span class="tc">off</span></span>
  <span class="pill" id="pill-twitch"><span class="led"></span><b>Twitch</b> <span class="tc">off</span></span>
  <span class="pill" id="link"><span class="led"></span>OBS <span id="linktext">connecting...</span></span>
  <span id="msg" role="status" aria-live="polite"></span>
  <span class="clock" id="clock"></span>
</header>

<div class="grid">

<section class="panel" aria-label="Lineup">
  <h2>Lineup <span class="mono" id="lotcount"></span></h2>
  <div class="lotnav"><input id="show" type="text" placeholder="show name (inventory/_live/...)" aria-label="Show name"
    autocomplete="off" spellcheck="false"><button id="showload">Load</button></div>
  <div id="lineup">
    <div class="lot cur"><small>On screen (host reference, not on air)</small><div class="t" id="lotnow">no show loaded</div>
      <div class="meta" id="lotnowmeta"></div><div class="flags" id="lotnowflags"></div></div>
    <div class="lot"><small>Next</small><div class="t" id="lotnext">-</div><div class="meta" id="lotnextmeta"></div></div>
    <div class="lotnav"><button id="lotprev">&#9664; Prev lot <small>[</small></button>
      <button id="lotfwd">Next lot <small>]</small> &#9654;</button></div>
    <ol class="lots" id="lots"></ol>
  </div>
</section>

<section class="panel" aria-label="Switcher">
  <h2>Switcher <span>1-7 / click: preview &middot; Enter: take &middot; Shift+1-7: straight to air
    <button id="shottoggle">Hide monitors</button> <button id="tiletoggle">Multiview off</button></span></h2>
  <div class="bus">
    <figure class="mon pvw" id="pvwmon"><figcaption><b>PREVIEW</b> <span id="pvwname">click a page</span></figcaption>
      <img id="pvwshot" alt="The page lined up to take next"></figure>
    <div class="tbar">
      <button id="auto" title="Take preview to program with the transition below (Enter)">AUTO <small>Enter</small></button>
      <button id="cut" title="Take preview to program with no transition (Shift+Enter)">CUT <small>Shift+Enter</small></button>
      <select id="trans" aria-label="Transition"><option value="Fade">Fade</option><option value="Cut">Cut</option></select>
      <label class="dur"><input id="dur" type="number" min="50" max="5000" step="50" value="300" aria-label="Transition length"> ms</label>
    </div>
    <figure class="mon pgmon" id="monitor"><figcaption><b>PROGRAM</b> <span id="pgmlabel"></span></figcaption>
      <img id="shot" alt="What OBS is sending now"
           src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==">
      <div id="shotnote">waiting for OBS...</div></figure>
  </div>
  <div class="mv" id="mv">
    <button class="tile page" data-page="intro"><span class="flag">ON AIR</span><span class="pv">PVW</span><img alt=""><span class="note">no scene in OBS</span>
      <span class="lab"><span class="key">1</span><span class="name">Intro</span></span><span class="sub">Pre-show card, no cameras</span></button>
    <button class="tile page" data-page="item"><span class="flag">ON AIR</span><span class="pv">PVW</span><img alt=""><span class="note">no scene in OBS</span>
      <span class="lab"><span class="key">2</span><span class="name">Item</span></span><span class="sub">D5100 + webcam (R refocus)</span></button>
    <button class="tile page" data-page="next"><span class="flag">ON AIR</span><span class="pv">PVW</span><img alt=""><span class="note">no scene in OBS</span>
      <span class="lab"><span class="key">3</span><span class="name">Next</span></span><span class="sub">Pause / next item: window + webcam</span></button>
    <button class="tile page" data-page="desktop"><span class="flag">ON AIR</span><span class="pv">PVW</span><img alt=""><span class="note">no scene in OBS</span>
      <span class="lab"><span class="key">4</span><span class="name">Desktop</span></span><span class="sub">App window + webcam</span></button>
    <button class="tile page" data-page="webcam"><span class="flag">ON AIR</span><span class="pv">PVW</span><img alt=""><span class="note">no scene in OBS</span>
      <span class="lab"><span class="key">5</span><span class="name">Webcam</span></span><span class="sub">Fallback: face cam + mic, no D5100</span></button>
    <button class="tile page" data-page="closeup"><span class="flag">ON AIR</span><span class="pv">PVW</span><img alt=""><span class="note">no scene in OBS</span>
      <span class="lab"><span class="key">6</span><span class="name">Close-up</span></span><span class="sub">TG-7 + webcam (R: TG-7 AF)</span></button>
    <button class="tile page" data-page="multi"><span class="flag">ON AIR</span><span class="pv">PVW</span><img alt=""><span class="note">no scene in OBS</span>
      <span class="lab"><span class="key">7</span><span class="name">Multi</span></span><span class="sub">D5100 + webcam + TG-7 + window</span></button>
    <button class="tile" id="hold"><span class="flag">ON AIR</span><img alt="">
      <span class="lab"><span class="key">H</span><span class="name">Hold still</span></span><span class="sub">Freeze the camera page</span></button>
  </div>
  <div class="ctl">
    <button id="step">Step <small>Space</small></button>
    <button id="refocus">Refocus <small>R</small></button>
    <button id="reconnect">Reconnect cams <small>C</small></button>
    <button id="tg7">TG-7 feed</button>
    <button id="watch">Start camera watch</button>
    <label><input type="checkbox" id="standin"> D5 stand-in <small>white panel</small></label>
    <span class="wb" id="wbrow"><select id="wb" aria-label="Set the D5100 white balance to">
      <option value="" selected disabled>D5100 WB...</option>
      <option value="auto">Auto</option><option value="daylight">Daylight</option>
      <option value="cloudy">Cloudy</option><option value="shade">Shade</option>
      <option value="tungsten">Tungsten</option><option value="fluorescent">Fluorescent</option>
      <option value="flash">Flash</option></select>
      <button id="wbset">Set</button></span>
  </div>
</section>

<section class="panel" aria-label="Telemetry">
  <h2>Outputs</h2>
  <div class="dest" data-dest="ebay">
    <div class="dh"><b>eBay</b><a class="open" href="https://www.ebay.com/ebaylive/studio" target="_blank" rel="noopener">Live Studio &#8599;</a><span class="st">off</span><button class="go">Start</button></div>
    <div class="kpis"><div class="kpi"><small>kbps</small><b class="kbps">-</b></div>
      <div class="kpi"><small>dropped</small><b class="drop">-</b></div>
      <div class="kpi"><small>congestion</small><b class="cong">-</b></div></div>
    <canvas class="spark" width="300" height="34"></canvas>
  </div>
  <div class="dest" data-dest="twitch">
    <div class="dh"><b>Twitch</b><a class="open" href="https://www.twitch.tv/popebasement" target="_blank" rel="noopener">popebasement &#8599;</a><span class="st">off</span><button class="go">Start</button></div>
    <div class="kpis"><div class="kpi"><small>kbps</small><b class="kbps">-</b></div>
      <div class="kpi"><small>dropped</small><b class="drop">-</b></div>
      <div class="kpi"><small>congestion</small><b class="cong">-</b></div></div>
    <canvas class="spark" width="300" height="34"></canvas>
  </div>
  <h2>OBS</h2>
  <div class="kpis">
    <div class="kpi" id="k-cpu"><small>CPU</small><b>-</b></div>
    <div class="kpi" id="k-fps"><small>FPS</small><b>-</b></div>
    <div class="kpi" id="k-ms"><small>render ms</small><b>-</b></div>
    <div class="kpi" id="k-rlag"><small>render drops</small><b>-</b></div>
    <div class="kpi" id="k-elag"><small>encode drops</small><b>-</b></div>
    <div class="kpi" id="k-disk"><small>disk free</small><b>-</b></div>
  </div>
  <canvas class="spark" id="spark-cpu" width="300" height="34" title="CPU %, last 2 minutes"></canvas>
  <h2>Sources</h2>
  <div class="src" id="s-d5"><i class="led"></i><span>D5100</span><em>-</em></div>
  <div class="src" id="s-tg7"><i class="led"></i><span>TG-7</span><em>-</em></div>
  <div class="src" id="s-webcam"><i class="led"></i><span>Webcam</span><em>-</em></div>
  <div class="src" id="s-busy"><i class="led"></i><span>D5100 refocus / WB</span><em>-</em></div>
  <div class="src" id="s-scene"><i class="led"></i><span>OBS scene</span><em>-</em></div>
  <h2>Audio</h2>
  <div class="dest" id="music"><div class="dh"><b>Music</b><span class="st">...</span><button class="go">Start</button></div>
    <div id="presets"></div></div>
</section>

</div>

<section class="panel" id="logp" aria-label="Event log">
  <h2>Event log <button id="logclear">Clear</button></h2>
  <ol id="log"></ol>
</section>

</div>
<script>
const $ = s => document.querySelector(s), buttons = [...document.querySelectorAll('.page')];
const tileOf = p => p === 'hold' ? $('#hold') : document.querySelector('.tile[data-page="' + p + '"]');
const LABEL = { intro: 'Intro', item: 'Item', closeup: 'Close-up', next: 'Next', desktop: 'Desktop',
                webcam: 'Webcam only', multi: 'Multi view', hold: 'Hold' };
const D5_PAGES = ['item', 'multi'];  // pages with the D5100 on air
const CAM_LABEL = { d5: 'D5100', webcam: 'Webcam', tg7: 'TG-7' };
// the cameras each page puts on air (show_flow.PAGE_CAMS)
const PAGE_CAMS = { intro: [], item: ['d5', 'webcam'], closeup: ['tg7', 'webcam'], next: ['webcam'],
                    desktop: ['webcam'], webcam: ['webcam'], multi: ['d5', 'webcam', 'tg7'] };
const NAMES = { ebay: 'eBay', twitch: 'Twitch' };
// Hold, release and step ride the page queue as actions: one at a time, in order.
const ACTION = { '@hold': ['/api/pager/hold', { on: true }], '@release': ['/api/pager/hold', { on: false }],
                 '@step': ['/api/pager/step', null] };
let held = null, tg7Busy = false, watchBusy = false, curPage = null, prev = null;
let pvw = null, pendingTake = null;  // the preview bus; the take waiting in the queue
// Each control has its own in-flight flag: a refocus or stream start never blocks a page click.
let pending = null, switching = false, refocusing = false, streaming = false, standinBusy = false;
let epoch = 0;  // bumped by every action, so a poll answer from before it can't undo it

const pad = n => String(n).padStart(2, '0');
const hms = d => pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());

// ---- event log: every action's outcome, plus what a poll sees change ----
function log(text, cls = '') {
  const li = document.createElement('li'), t = document.createElement('time');
  t.textContent = hms(new Date());
  li.className = cls; li.append(t, text);
  const ol = $('#log'); ol.prepend(li);
  while (ol.children.length > 300) ol.lastChild.remove();
}
function say(text, cls = '') {
  const m = $('#msg'); m.textContent = text; m.className = cls;
  if (text) log(text, cls);
}
$('#logclear').addEventListener('click', () => { $('#log').textContent = ''; });

function setLevel(el, level) {
  el.classList.remove('ok', 'warn', 'crit', 'live');
  if (level) el.classList.add(level);
}
function feedLevel(status, onAir) {
  if (status === 'running') return 'ok';
  if (!status || status === 'stopped') return onAir ? 'crit' : '';
  return 'warn';  // running, no frames
}

// ---- state: what's on air ----
// `own`: what the click this answers reports itself (it says so in the status
// line), so the log skips those and only those; a poll's answer passes none.
function show(st, own = []) {
  if (prev) for (const [text, cls] of changes(prev, st, own)) log(text, cls);
  // A click's answer has no music or telemetry: keep the last poll's, so the
  // next poll still sees a reconnect or a music change that began in between.
  prev = prev ? { ...st, music: st.music || prev.music, health: st.health || prev.health } : st;
  for (const el of document.querySelectorAll('.dest[data-dest]')) {  // the state flipped: ask again
    const go = el.querySelector('.go'), live = !!((st.streams || {})[el.dataset.dest] || {}).live;
    if (go.dataset.armed && go.dataset.armed !== el.dataset.dest + (live ? ' stop' : ' start')) disarm(go);
  }
  if ($('#tg7').dataset.armed && !(PAGE_CAMS[st.page] || []).includes('tg7')) disarm($('#tg7'));  // nothing on air to warn about
  curPage = st.page;
  if ('preview' in st) showPreview(st.preview);
  buttons.forEach(b => { b.classList.toggle('on', b.dataset.page === st.page);
                         b.classList.toggle('pending', b.dataset.page === pending); });
  $('#tally').classList.toggle('live', !!st.live);
  $('#tallytext').textContent = st.live ? 'ON AIR' : 'OFF AIR';
  $('#tc').textContent = st.live ? (st.timecode || '').split('.')[0] : '--:--:--';
  $('#monitor').classList.toggle('live', !!st.live);
  held = st.page === 'hold' ? (st.held || 'item') : null;
  $('#pgmname').textContent = held ? 'Hold (' + LABEL[held] + ')' : st.page ? LABEL[st.page] : st.scene + ' (not a pager page)';
  $('#pgmlabel').textContent = $('#pgmname').textContent;
  for (const el of document.querySelectorAll('.dest[data-dest]')) {
    const d = el.dataset.dest, s = (st.streams || {})[d] || {};
    el.classList.toggle('live', !!s.live);
    el.querySelector('.st').textContent = s.live ? 'LIVE ' + (s.timecode || '').split('.')[0] : 'off';
    el.querySelector('.go').textContent = s.live ? 'Stop' : 'Start';
    const pill = $('#pill-' + d);
    setLevel(pill, s.live ? 'live' : '');
    pill.querySelector('.tc').textContent = s.live ? (s.timecode || '').split('.')[0] : 'off';
  }
  if (!standinBusy) $('#standin').checked = !!st.standin;
  const hb = $('#hold');
  if (!held && hb.classList.contains('on')) hb.querySelector('img').removeAttribute('src');  // released: no stale still
  hb.classList.toggle('on', !!held);
  hb.querySelector('.name').textContent = held ? 'Release' : 'Hold still';
  hb.querySelector('.sub').textContent = held ? 'Back to ' + LABEL[held] + ' live' : 'Freeze the camera page';
  // the D5 refocus needs the D5; the TG-7's works on the close-up page whatever the stand-in
  $('#refocus').hidden = !!st.standin && st.page !== 'closeup';
  $('#refocus').disabled = refocusing || !!st.refocusing;
  $('#wbrow').hidden = !!st.standin;  // the D5's, not the TG-7's
  $('#wbset').disabled = wbBusy || !!st.refocusing;
  if (!musicBusy && st.music) showMusic(st.music);  // only GET /api/pager carries it
  if (!tg7Busy) $('#tg7').textContent = st.tg7_status === 'running' ? 'Stop TG-7 feed' : 'Start TG-7 feed';
  // sources
  const d5Air = D5_PAGES.includes(st.page) && !st.standin;
  const fs = st.feed_status || (st.feed ? 'running' : 'stopped');
  const onAir = PAGE_CAMS[st.page] || [];
  // the camera watch's verdict when it has one (show_flow.py watch), else the feed's own status
  camRow('#s-d5', st, 'd5', st.standin ? 'stand-in' : fs, st.standin ? 'warn' : feedLevel(fs, d5Air), onAir);
  camRow('#s-tg7', st, 'tg7', st.tg7_status || '?', feedLevel(st.tg7_status, onAir.includes('tg7')), onAir);
  camRow('#s-webcam', st, 'webcam', 'no camera watch', '', onAir);
  if (!watchBusy) $('#watch').textContent = st.watch ? 'Stop camera watch' : 'Start camera watch';
  srcRow('#s-busy', st.refocusing ? 'busy (refocus, WB or still)' : 'ready', st.refocusing ? 'warn' : 'ok');
  srcRow('#s-scene', st.scene, st.page ? 'ok' : 'warn');
  if (st.health && st.health.error) {
    if (!healthErr) log('No OBS telemetry: ' + st.health.error, 'warn');
    healthErr = true;
  } else if (st.health) { healthErr = false; showHealth(st.health); }
}
let healthErr = false;
function srcRow(id, text, level, title = '') {
  const row = $(id); row.querySelector('em').textContent = text; row.title = title;
  setLevel(row.querySelector('.led'), level);
}
const CAM_LEVEL = { live: 'ok', busy: 'warn', standin: 'warn', down: 'crit' };
function camRow(id, st, k, fallback, fallbackLevel, onAir) {
  const v = (st.cameras || {})[k];
  if (!v || v.state === 'unknown') return srcRow(id, fallback, fallbackLevel, v ? v.reason : '');
  const level = v.blacked ? 'crit' : v.state === 'off' ? (onAir.includes(k) ? 'crit' : '') : CAM_LEVEL[v.state] || '';
  srcRow(id, v.state + (v.blacked ? ' \u00b7 black on air' : ''), level, v.reason || '');
}

// Pure: what changed between two states, as [text, class] log lines, so a
// switch made in OBS or from the shell, a dropped feed or a stream ending is
// on record. Keys in `own` ('page', 'stream:ebay', 'tg7', 'standin') are
// skipped: the click that caused them already logged its own result.
function changes(a, b, own = []) {
  const out = [], mine = k => own.includes(k);
  if (!mine('page') && (a.page !== b.page || a.scene !== b.scene))
    out.push(['PGM -> ' + (b.page === 'hold' ? 'Hold (' + LABEL[b.held || 'item'] + ')' : b.page ? LABEL[b.page] : b.scene), 'sys']);
  for (const d of Object.keys(NAMES)) {
    const was = ((a.streams || {})[d] || {}).live, now = ((b.streams || {})[d] || {}).live;
    if (!mine('stream:' + d) && was !== now) out.push([NAMES[d] + (now ? ' is LIVE' : ' went off air'), now ? 'ok' : 'warn']);
    const ra = ((a.health || {}).outputs || {})[d], rb = ((b.health || {}).outputs || {})[d];
    if (ra && rb && ra.reconnecting !== rb.reconnecting)
      out.push([NAMES[d] + (rb.reconnecting ? ' lost its connection: OBS is reconnecting' : ' reconnected'),
                rb.reconnecting ? 'err' : 'ok']);
  }
  // "running, no frames for 245s" ticks every poll: a change of kind is news, the count isn't
  const kind = s => (s || '').replace(/\d+s\b/, 'Ns');
  if (kind(a.feed_status) !== kind(b.feed_status))
    out.push(['D5100 feed: ' + b.feed_status, b.feed_status === 'running' ? 'ok' : 'warn']);
  if (!mine('tg7') && a.tg7_status !== b.tg7_status) out.push(['TG-7 feed: ' + b.tg7_status, b.tg7_status === 'running' ? 'ok' : 'warn']);
  if (!mine('standin') && a.standin !== b.standin) out.push(['D5 stand-in ' + (b.standin ? 'on' : 'off'), 'sys']);
  if (a.music && b.music && a.music.on !== b.music.on) out.push(['Music ' + (b.music.on ? 'on' : 'off'), 'sys']);
  // a camera on the page on air going black (the camera watch's panel) or coming back
  if (a.cameras && b.cameras) for (const k of PAGE_CAMS[b.page] || []) {
    const was = !!(a.cameras[k] || {}).blacked, v = b.cameras[k] || {};
    if (v.blacked && !was) out.push([CAM_LABEL[k] + ' went down: its slot is black on air' + (v.reason ? ' (' + v.reason + ')' : '') + '.', 'err']);
    else if (was && !v.blacked && v.state === 'live') out.push([CAM_LABEL[k] + ' is back.', 'ok']);
  }
  return out;
}

// ---- telemetry ----
const HIST = 120, hist = { ebay: [], twitch: [], cpu: [] };
let lastStats = null, dropLogged = 0;
function push(a, v) { a.push(v); if (a.length > HIST) a.shift(); }
function spark(cv, data, color, max) {
  const w = cv.width = cv.clientWidth * devicePixelRatio, h = cv.height = cv.clientHeight * devicePixelRatio;
  const g = cv.getContext('2d'); g.clearRect(0, 0, w, h);
  if (data.length < 2) return;
  const top = Math.max(max || 0, ...data) || 1, step = w / (HIST - 1), x0 = w - (data.length - 1) * step;
  g.beginPath();
  data.forEach((v, i) => { const x = x0 + i * step, y = h - 2 - (v / top) * (h - 4); i ? g.lineTo(x, y) : g.moveTo(x, y); });
  g.strokeStyle = color; g.lineWidth = 1.5 * devicePixelRatio; g.stroke();
  g.lineTo(x0 + (data.length - 1) * step, h); g.lineTo(x0, h); g.closePath();
  g.globalAlpha = .15; g.fillStyle = color; g.fill(); g.globalAlpha = 1;
}
function kpi(el, text, level) { el.querySelector('b').textContent = text; setLevel(el, level); }
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

function showHealth(h) {
  const now = performance.now();
  for (const el of document.querySelectorAll('.dest[data-dest]')) {
    const d = el.dataset.dest, o = (h.outputs || {})[d] || {}, kbps = o.kbps ?? null;  // show_flow.kbps()
    push(hist[d], kbps || 0);
    const k = el.querySelectorAll('.kpi');
    kpi(k[0], !o.live ? '-' : kbps === null ? '...' : kbps.toLocaleString(),
        !o.live || kbps === null ? '' : kbps < 300 ? 'crit' : kbps < 1500 ? 'warn' : 'ok');
    const pct = o.live && o.frames ? 100 * o.skipped / o.frames : null;
    kpi(k[1], pct === null ? '-' : pct.toFixed(pct < 1 ? 2 : 1) + '%', pct === null ? '' : pct > 2 ? 'crit' : pct > 0.5 ? 'warn' : 'ok');
    const cg = o.live && o.congestion != null ? 100 * o.congestion : null;
    kpi(k[2], o.reconnecting ? 'RECONN' : cg === null ? '-' : cg.toFixed(0) + '%',
        o.reconnecting ? 'crit' : cg === null ? '' : cg > 50 ? 'crit' : cg > 15 ? 'warn' : 'ok');
    spark(el.querySelector('canvas'), hist[d], o.live ? css('--pgm') : css('--muted'));
  }
  push(hist.cpu, h.cpu);
  kpi($('#k-cpu'), h.cpu.toFixed(0) + '%', h.cpu > 90 ? 'crit' : h.cpu > 75 ? 'warn' : 'ok');
  kpi($('#k-fps'), h.fps.toFixed(1), h.fps < 20 ? 'crit' : h.fps < 28 ? 'warn' : 'ok');
  kpi($('#k-ms'), h.frame_ms.toFixed(1), h.frame_ms > 25 ? 'crit' : h.frame_ms > 12 ? 'warn' : 'ok');
  // drops since the last poll: OBS's counters only ever grow, so the change is what matters now
  const dr = lastStats ? Math.max(0, h.render_skipped - lastStats.render_skipped) : 0;
  const de = lastStats ? Math.max(0, h.encode_skipped - lastStats.encode_skipped) : 0;
  kpi($('#k-rlag'), String(h.render_skipped), dr ? 'crit' : 'ok');
  kpi($('#k-elag'), String(h.encode_skipped), de ? 'crit' : 'ok');
  if ((dr || de) && now - dropLogged > 10000) {
    dropLogged = now;
    log('OBS dropped frames: ' + (dr ? dr + ' rendering (GPU/CPU busy) ' : '') + (de ? de + ' encoding (encoder overloaded)' : ''), 'warn');
  }
  lastStats = h;
  kpi($('#k-disk'), h.disk_mb >= 10240 ? (h.disk_mb / 1024).toFixed(0) + ' GB' : (h.disk_mb / 1024).toFixed(1) + ' GB',
      h.disk_mb < 2048 ? 'crit' : h.disk_mb < 10240 ? 'warn' : 'ok');
  spark($('#spark-cpu'), hist.cpu, css('--accent'), 100);
}

function camText(st, k) {
  const r = ((st.cameras || {})[k] || {}).reason;
  return CAM_LABEL[k] + (r ? ' (' + r + ')' : '');
}

// Every call has a deadline, so a stuck server or OBS can't leave the page waiting forever.
async function api(method, path, body, ms) {
  const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), ms);
  let r, data;
  try {
    r = await fetch(path, { method, signal: ctl.signal, body: body ? JSON.stringify(body) : undefined,
                            headers: body ? { 'Content-Type': 'application/json' } : {} });
    data = await r.json().catch(() => ({}));
  } catch (e) {
    throw new Error(e.name === 'AbortError' ? 'no answer after ' + ms / 1000 + ' s'
                                            : 'pager server not reachable (is lib.cli serve running?)');
  } finally { clearTimeout(timer); }
  if (!r.ok) throw new Error(data.detail ? (typeof data.detail === 'string' ? data.detail
                                            : JSON.stringify(data.detail)) : 'HTTP ' + r.status);
  return data;
}

// ---- music ----
let musicBusy = false;
function showMusic(m) {
  const el = $('#music'), go = el.querySelector('.go');
  el.classList.toggle('on', !!m.on);
  el.querySelector('.st').textContent = !m.ready ? 'not set up: show_music.py setup'
    : (m.on ? 'playing' + (m.track ? ': ' + m.track : '') + ' (' + m.db + ' dB)' : 'off')
      + (m.preset ? ' \u00b7 ' + m.preset : '');
  go.textContent = m.on ? 'Stop' : 'Start';
  go.disabled = musicBusy || !m.ready;
  const box = $('#presets'), names = m.presets || [];
  if (box.dataset.names !== names.join()) {  // rebuild only when the built set changes
    box.dataset.names = names.join();
    box.replaceChildren(...names.map(n => {
      const b = document.createElement('button');
      b.className = 'go'; b.dataset.preset = n; b.textContent = n;
      b.addEventListener('click', () => musicPreset(n));
      return b; }));
  }
  box.querySelectorAll('.go').forEach(b => { b.classList.toggle('on', b.dataset.preset === m.preset);
                                            b.disabled = musicBusy; });
}

async function musicPreset(preset) {
  if (musicBusy) return;
  musicBusy = true; $('#presets').querySelectorAll('.go').forEach(b => b.disabled = true);
  say('Switching music to ' + preset + '...');
  try { const m = await api('POST', '/api/pager/music', { preset }, 20000); musicBusy = false; showMusic(m);
        if (prev) prev.music = m;
        say('Music: ' + preset + (m.switch.changed ? '.' : ' (already on it).')); }
  catch (e) { musicBusy = false; say(e.message, 'err'); }
}

// Fades run on their own flag, so a fade never blocks a page switch.
async function music() {
  if (musicBusy) return;
  const on = !$('#music').classList.contains('on');
  musicBusy = true; $('#music .go').disabled = true;
  say(on ? 'Fading music in...' : 'Fading music out...');
  try { const m = await api('POST', '/api/pager/music', { on }, 15000); musicBusy = false; showMusic(m);
        if (prev) prev.music = m;
        say(on ? 'Music on.' : 'Music off.', 'ok'); }
  catch (e) { musicBusy = false; $('#music .go').disabled = false; say(e.message, 'err'); }
}

// ---- streams ----
const ASK = {
  'twitch start': 'Go live on Twitch? It is public the moment it connects.',
  'ebay start': 'Start sending video to eBay? Viewers see it after you click Start event in Stream manager.',
  'twitch stop': 'Stop the Twitch stream?',
  'ebay stop': 'Stop sending video to eBay? This ends the eBay feed for your show.',
};
// Two clicks, not a native dialog (the app's browser pane blocks those): the first arms for 5 s, a second on the same `key` fires.
function sure(btn, key, question) {
  if (btn.dataset.armed === key) { disarm(btn); return true; }
  disarm(btn);
  btn.dataset.armed = key; btn.classList.add('armed');
  btn._disarm = setTimeout(() => { disarm(btn); if ($('#msg').textContent === question) say(''); }, 5000);
  $('#msg').textContent = question; $('#msg').className = 'warn';
  return false;
}
function disarm(btn) { clearTimeout(btn._disarm); delete btn.dataset.armed; btn.classList.remove('armed'); }
function streamButtons(on) { document.querySelectorAll('.dest[data-dest] .go').forEach(b => b.disabled = on); }
async function stream(dest) {
  if (streaming) return;
  const live = document.querySelector('.dest[data-dest="' + dest + '"]').classList.contains('live');
  const action = live ? 'stop' : 'start';
  const btn = document.querySelector('.dest[data-dest="' + dest + '"] .go');
  if (!sure(btn, dest + ' ' + action, ASK[dest + ' ' + action])) return;
  streaming = true; streamButtons(true);
  say((action === 'start' ? 'Connecting to ' : 'Stopping ') + NAMES[dest] + '...');
  try {
    const st = await api('POST', '/api/pager/stream', { dest, action }, 30000);
    if (!switching) show(st, ['stream:' + dest]);
    const r = st.stream;
    say(action === 'start' ? (r.changed ? NAMES[dest] + ' is live: ' + r.kbps + ' kbps.' : NAMES[dest] + ' was already live.')
                           : NAMES[dest] + ' stopped.', action === 'start' ? 'ok' : 'warn');
  } catch (e) { say(NAMES[dest] + ' ' + action + ' failed: ' + e.message, 'err'); }
  streaming = false; streamButtons(false);
}

// ---- the switcher ----
function focusText(r) {
  return r.focused ? 'Focused (' + r.refocus_s + ' s).'
                   : 'Autofocus could not lock: center the item and refocus.';
}
function switched(page, st) {
  const sw = st.switch;
  if (sw.superseded_by) say('Not switched: ' + sw.superseded_by + ' went on air first.', 'warn');
  else if (!sw.changed) say('Already on ' + LABEL[page] + '.');
  else if (sw.window && !sw.window.shown)
    say('On air: Next. Window hidden (' + (sw.window.title || 'closed') +
        ' is not safe to show). Open the next lot in its own window.', 'warn');
  else if ((sw.blacked || []).length)
    say('On air: ' + LABEL[page] + '. Black in place of ' + sw.blacked.map(k => camText(st, k)).join(', ') +
        '. Reconnect (C) or Webcam only (5).', 'warn');
  else if (D5_PAGES.includes(page) && st.refocusing)
    say('On air: ' + LABEL[page] + '. The D5100 is busy (refocus, WB or still): its picture is frozen for a few seconds.', 'warn');
  else if (D5_PAGES.includes(page) && !st.standin && st.feed_status !== 'running')
    say('On air: ' + LABEL[page] + ', but the D5100 feed is ' + st.feed_status + '.', 'warn');
  else say('On air: ' + LABEL[page] + '.');
}

// Page clicks go out one at a time, and only the latest waiting click is kept,
// so the last key pressed is what ends up on air.
function go(page) {
  pending = page; epoch++;
  buttons.forEach(b => b.classList.toggle('pending', b.dataset.page === page));
  if (!switching) drain();
}
async function drain() {
  switching = true;
  while (pending) {
    const page = pending; pending = null;
    if (page === '@take') { await takeNow(); continue; }
    if (ACTION[page]) { await act(page); continue; }
    $('#msg').textContent = 'Taking ' + LABEL[page] + '...'; $('#msg').className = '';
    try {  // straight to program (Shift+number): a cut, the emergency way
      const st = await api('POST', '/api/pager', { page, transition: 'Cut' }, 12000);
      epoch++;
      if (!pending) { show(st, ['page']); switched(page, st); }
    } catch (e) { say(LABEL[page] + ' did not go on air: ' + e.message, 'err'); }
  }
  buttons.forEach(b => b.classList.remove('pending'));
  switching = false;
}
// ---- the preview bus: line a page up, then take it with a transition ----
function showPreview(page) {
  pvw = page || null;
  buttons.forEach(b => b.classList.toggle('pvw', b.dataset.page === pvw));
  $('#pvwname').textContent = pvw ? LABEL[pvw] + (pvw === curPage ? ' (on air now)' : '') : 'click a page';
  if (!pvw) $('#pvwshot').removeAttribute('src');
}
function setPreview(page) {
  epoch++; showPreview(page);
  api('POST', '/api/pager/preview', { page }, 5000).catch(e => say('Preview: ' + e.message, 'err'));
}
function take(cut) {
  if (!pvw) return say('Nothing on preview: click a page (or press 1-7) first.', 'warn');
  const btn = cut ? $('#cut') : $('#auto');
  btn.classList.add('go'); setTimeout(() => btn.classList.remove('go'), 300);
  pendingTake = { page: pvw, transition: cut ? 'Cut' : $('#trans').value || 'Fade',
                  duration_ms: Math.min(5000, Math.max(50, +$('#dur').value || 300)) };
  go('@take');
}
async function takeNow() {
  const body = pendingTake;
  $('#msg').textContent = (body.transition === 'Cut' ? 'Cut to ' : body.transition + ' to ') + LABEL[body.page] + '...';
  $('#msg').className = '';
  try {
    const st = await api('POST', '/api/pager/take', body, 12000);
    epoch++;
    if (!pending) { show(st, ['page']); switched(body.page, st); }
  } catch (e) { say(LABEL[body.page] + ' did not go on air: ' + e.message, 'err'); }
}
async function act(name) {
  const [path, body] = ACTION[name];
  try {
    const st = await api('POST', path, body, 12000);
    epoch++;
    if (pending) return;
    show(st, ['page']);
    const r = st.hold || st.step || st.switch || {};
    if (st.page === 'hold') say('Holding a still of ' + LABEL[st.held || r.held] +
                                ': turn the item, then Step or Release.', 'warn');
    else if (r.changed === false && name === '@release') say('Not on hold.');
    else say('On air: ' + LABEL[st.page] + '.');
  } catch (e) { say({ '@hold': 'Hold', '@release': 'Release', '@step': 'Step' }[name] + ' failed: ' + e.message, 'err'); }
}
function holdKey() { go(held ? '@release' : '@hold'); }

// ---- cameras ----
async function tg7() {
  if (tg7Busy) return;
  const action = $('#tg7').textContent.startsWith('Stop') ? 'stop' : 'start';
  if (action === 'stop' && (PAGE_CAMS[curPage] || []).includes('tg7') &&
      !sure($('#tg7'), 'stop', 'The TG-7 is on air (' + LABEL[curPage] + '). Stop its feed anyway?')) return;
  tg7Busy = true; $('#tg7').disabled = true; epoch++;
  say(action === 'start' ? 'Starting the TG-7 feed...' : 'Stopping the TG-7 feed...');
  try { const st = await api('POST', '/api/pager/tg7', { action }, 15000);
        tg7Busy = false; if (!switching) show(st, ['tg7']);
        say(action === 'start' ? 'TG-7 feed started (it waits for the camera Wi-Fi if needed).'
                               : 'TG-7 feed stopped.'); }
  catch (e) { say('TG-7 feed ' + action + ' failed: ' + e.message, 'err'); }
  tg7Busy = false; $('#tg7').disabled = false;
}
async function watch() {
  if (watchBusy) return;
  const action = $('#watch').textContent.startsWith('Stop') ? 'stop' : 'start';
  watchBusy = true; $('#watch').disabled = true; epoch++;
  say(action === 'start' ? 'Starting the camera watch...' : 'Stopping the camera watch...');
  try { const st = await api('POST', '/api/pager/watch', { action }, 15000);
        watchBusy = false; if (!switching) show(st);
        say(action === 'start' ? 'Camera watch started: a camera that drops goes black on air.'
                               : 'Camera watch stopped: every black panel is off.'); }
  catch (e) { say('Camera watch ' + action + ' failed: ' + e.message, 'err'); }
  watchBusy = false; $('#watch').disabled = false;
}
async function refocus() {
  if (refocusing || $('#refocus').hidden) return;
  refocusing = true; $('#refocus').disabled = true; epoch++;
  say(curPage === 'closeup' ? 'TG-7 autofocus...'
                            : 'Refocusing the D5100 (about 5 s; a D5100 view hides behind Next)...');
  try { const st = await api('POST', '/api/pager/refocus', null, 60000);
        refocusing = false;
        if (!switching) show(st);
        say(focusText(st.refocus), st.refocus.focused ? 'ok' : 'warn'); }
  catch (e) { say('Refocus failed: ' + e.message.replace(/^refocus failed: /, ''), 'err'); }
  refocusing = false; $('#refocus').disabled = false;
}
let wbBusy = false;
async function setWb() {
  const preset = $('#wb').value;
  if (wbBusy) return;
  if (!preset) return say('Pick a white balance to set first.', 'warn');
  wbBusy = true; $('#wbset').disabled = true; epoch++;
  say('Setting the D5100 white balance (5-12 s; a D5100 view hides behind Next)...');
  try { const st = await api('POST', '/api/pager/wb', { preset }, 60000);
        wbBusy = false;
        if (!switching) show(st);
        say('D5100 white balance: ' + st.wb.wb + (st.wb.changed ? '.' : ' (already).'), 'ok'); }
  catch (e) { say('White balance failed: ' + e.message.replace(/^wb failed: /, ''), 'err'); }
  wbBusy = false; $('#wbset').disabled = false;
}
let reconnecting = false;
async function reconnect() {
  if (reconnecting) return;
  reconnecting = true; $('#reconnect').disabled = true; epoch++;
  say('Reconnecting the cameras in OBS...');
  try { const st = await api('POST', '/api/pager/reconnect', null, 12000);
        if (!switching) show(st);
        const r = Object.entries(st.reconnect), bad = r.filter(([, v]) => !v.ok);
        say(bad.length ? 'Reconnect: ' + bad.map(([k, v]) => k + ': ' + v.error).join('; ')
                       : 'Reconnected: ' + r.map(([k]) => k).join(', ') + '.', bad.length ? 'err' : 'ok'); }
  catch (e) { say('Reconnect failed: ' + e.message, 'err'); }
  reconnecting = false; $('#reconnect').disabled = false;
}

// ---- polling: 1 s while the tab is visible, nothing while it's hidden: cheap for OBS and the server ----
let link = null;
async function poll() {
  if (!document.hidden) {
    const mine = epoch;
    try { const st = await api('GET', '/api/pager', null, 5000);
          if (mine === epoch && !switching) show(st);
          if (link === false) log('OBS link back.', 'ok');
          link = true; setLevel($('#link'), 'ok'); $('#linktext').textContent = 'linked';
          if ($('#msg').textContent.startsWith('OBS')) say(''); }
    catch (e) {
      setLevel($('#link'), 'crit'); $('#linktext').textContent = 'no link';
      if (link !== false) say('OBS: ' + e.message, 'err');
      link = false;
    }
  }
  setTimeout(poll, 1000);
}

// ---- the program monitor: OBS's render of the program scene. One frame at a time,
// so a slow OBS just lowers the frame rate instead of piling up requests. ----
let previewOn = true, shotUrl = null;
try { previewOn = localStorage.getItem('pager.preview') !== 'off'; } catch (e) {}
function previewShown() {
  $('.bus').classList.toggle('off', !previewOn);
  $('#shottoggle').textContent = previewOn ? 'Hide monitors' : 'Show monitors';
}
// The preview monitor: one frame of the lined-up page's scene when it's lined
// up, and a fresh one every ~0.7 s only while Multiview is on: like the
// tiles, no continuous OBS render by default (a take always leaves the old
// program lined up). The program monitor covers it when it's the page on air.
let pvwShot = null;  // the page the monitor last showed
async function pvwFrame() {
  const page = pvw;
  if (page && page !== curPage && previewOn && !document.hidden && (tilesOn || pvwShot !== page)) {
    pvwShot = page;
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 5000);
    try {
      const r = await fetch('/api/pager/tile/' + page + '.jpg?width=270', { cache: 'no-store', signal: ctl.signal });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'HTTP ' + r.status);
      const url = URL.createObjectURL(await r.blob());
      if (page === pvw) {
        const old = $('#pvwshot').src;
        $('#pvwshot').src = url; tileOf(page).querySelector('img').src = url;
        if (old.startsWith('blob:') && old !== shotUrl) URL.revokeObjectURL(old);
      } else URL.revokeObjectURL(url);
      $('#pvwmon').classList.remove('stale');
    } catch (e) { $('#pvwmon').classList.add('stale'); }
    finally { clearTimeout(timer); }
  }
  setTimeout(pvwFrame, 700);
}
pvwFrame();
// The transitions OBS has, once; the choice is remembered on this browser.
(async () => {
  try {
    const r = await api('GET', '/api/pager/transitions', null, 5000), sel = $('#trans');
    sel.textContent = '';
    for (const n of r.transitions) { const o = document.createElement('option'); o.value = o.textContent = n; sel.append(o); }
    let want = 'Fade', ms = 300;
    try { want = localStorage.getItem('pager.trans') || want; ms = +localStorage.getItem('pager.dur') || ms; } catch (e) {}
    sel.value = r.transitions.includes(want) ? want : r.transitions.includes('Fade') ? 'Fade' : r.transitions[0];
    $('#dur').value = ms;
  } catch (e) { log('Transitions: ' + e.message + ' (Fade and Cut still work)', 'warn'); }
})();
// blur after a pick, so 1-7 and Enter work again at once (the key guard skips a focused list or box)
$('#trans').addEventListener('change', e => { e.target.blur(); try { localStorage.setItem('pager.trans', e.target.value); } catch (err) {} });
$('#dur').addEventListener('change', e => { e.target.blur(); try { localStorage.setItem('pager.dur', e.target.value); } catch (err) {} });
$('#auto').addEventListener('click', () => take(false));
$('#cut').addEventListener('click', () => take(true));
async function frame() {
  if (previewOn && !document.hidden) {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 5000);
    try {
      const r = await fetch('/api/pager/preview.jpg', { cache: 'no-store', signal: ctl.signal });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'HTTP ' + r.status);
      const url = URL.createObjectURL(await r.blob());
      $('#shot').src = url;
      const t = curPage && tileOf(curPage);  // the on-air tile shows the program feed, not a stale tile
      if (t) { t.querySelector('img').src = url; t.classList.remove('stale', 'missing'); }
      if (pvw && pvw === curPage) $('#pvwshot').src = url;
      if (shotUrl) URL.revokeObjectURL(shotUrl);
      shotUrl = url;
      $('#monitor').classList.remove('stale');
      $('#shotnote').textContent = 'What OBS is sending, ~2 frames a second. No sound.';
    } catch (e) {
      $('#monitor').classList.add('stale');
      $('#shotnote').textContent = 'No picture from OBS: ' + (e.name === 'AbortError' ? 'no answer after 5 s' : e.message);
    } finally { clearTimeout(timer); }
  }
  setTimeout(frame, 500);
}
$('#shottoggle').addEventListener('click', () => {
  previewOn = !previewOn; previewShown();
  try { localStorage.setItem('pager.preview', previewOn ? 'on' : 'off'); } catch (e) {}
});
previewShown();
frame();

// ---- the multiview: one off-air page's tile at a time, round robin ----
let tilesOn = false, tileNext = 0;  // off unless switched on: each tile is an OBS render, and the show laptop is short of CPU
const tileUrls = {};
try { tilesOn = localStorage.getItem('pager.tiles') === 'on'; } catch (e) {}
function tilesShown() {
  $('#mv').classList.toggle('off', !tilesOn);
  $('#tiletoggle').textContent = tilesOn ? 'Multiview off' : 'Multiview on';
}
async function tiles() {
  if (tilesOn && !document.hidden) {
    const pages = buttons.map(b => b.dataset.page).filter(p => p !== curPage && p !== pvw);
    const page = pages[tileNext++ % pages.length], t = tileOf(page);
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 5000);
    try {
      const r = await fetch('/api/pager/tile/' + page + '.jpg', { cache: 'no-store', signal: ctl.signal });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'HTTP ' + r.status);
      const url = URL.createObjectURL(await r.blob());
      t.querySelector('img').src = url;
      if (tileUrls[page]) URL.revokeObjectURL(tileUrls[page]);
      tileUrls[page] = url;
      t.classList.remove('stale', 'missing');
    } catch (e) {
      t.classList.add(/No source was found/.test(e.message) ? 'missing' : 'stale');
    } finally { clearTimeout(timer); }
  }
  setTimeout(tiles, 1000);
}
$('#tiletoggle').addEventListener('click', () => {
  tilesOn = !tilesOn; tilesShown();
  try { localStorage.setItem('pager.tiles', tilesOn ? 'on' : 'off'); } catch (e) {}
});
tilesShown();
tiles();

// ---- the lineup: /live/<show> (tools/live_lineup.py). The host's reference for
// which lot is on the podium; eBay draws its own lot overlay, so none of this is on air. ----
let showName = '', lineup = null, onLot = null, lotNote = '';
const money = v => v ? '$' + v : null;
const enc = () => encodeURIComponent(showName);
function lotMeta(l) {
  return [l.format === 'AUCTION' ? 'auction from ' + money(l.auction_start_price) : money(l.price),
          l.quantity > 1 ? 'qty ' + l.quantity : null, 'ID ' + l.item_id].filter(Boolean).join(' · ');
}
function renderLot() {
  const lots = lineup ? lineup.lots : [], i = lots.findIndex(l => l.lot === onLot);
  const cur = lots[i], nxt = lots[i + 1] || (i < 0 ? lots[0] : null);
  $('#lotnow').textContent = !lineup ? 'no show loaded' : cur ? '#' + cur.lot + '  ' + cur.title : 'no lot on screen';
  $('#lotnowmeta').textContent = cur ? lotMeta(cur) : '';
  $('#lotnowflags').textContent = cur ? (cur.flags || []).join('; ') : '';
  $('#lotnext').textContent = nxt ? '#' + nxt.lot + '  ' + nxt.title : '-';
  $('#lotnextmeta').textContent = nxt ? lotMeta(nxt) : '';
  for (const li of $('#lots').children) li.classList.toggle('cur', +li.dataset.lot === onLot);
}
async function loadLineup(name) {
  showName = name.trim(); lineup = null; onLot = null; $('#lots').textContent = ''; $('#lotcount').textContent = '';
  try { localStorage.setItem('pager.show', showName); } catch (e) {}
  if (!showName) return renderLot();
  try {
    lineup = await api('GET', '/live/' + enc() + '/lineup', null, 5000);
    for (const l of lineup.lots) {
      const li = document.createElement('li'), n = document.createElement('span');
      li.dataset.lot = l.lot; n.className = 'n'; n.textContent = '#' + l.lot;
      li.append(n, l.title || l.item_id); li.title = lotMeta(l);
      li.addEventListener('click', () => pickLot(l.lot));
      $('#lots').append(li);
    }
    $('#lotcount').textContent = lineup.lots.length + ' lots';
    log('Lineup loaded: ' + showName + ' (' + lineup.lots.length + ' lots).', 'sys');
    await lotState();
  } catch (e) { say('Lineup ' + showName + ': ' + e.message, 'err'); }
  renderLot();
}
async function lotState() {
  const s = await api('GET', '/live/' + enc() + '/state', null, 5000);
  const was = onLot;
  onLot = s.lot ? s.lot.lot : null; lotNote = s.note || '';
  if (was !== onLot) renderLot();
}
async function pickLot(n) {
  if (!lineup) return;
  try { const s = await api('POST', '/live/' + enc() + '/state', { lot: n, note: lotNote }, 5000);
        onLot = s.lot ? s.lot.lot : null; renderLot();
        const l = lineup.lots.find(x => x.lot === n);
        say('Lot #' + n + ' on the podium' + (l ? ': ' + l.title : '') + '.'); }
  catch (e) { say('Lot ' + n + ': ' + e.message, 'err'); }
}
function stepLot(d) {
  if (!lineup || !lineup.lots.length) return;
  const lots = lineup.lots, i = lots.findIndex(l => l.lot === onLot);
  const j = i < 0 ? 0 : Math.min(lots.length - 1, Math.max(0, i + d));
  if (lots[j].lot !== onLot) pickLot(lots[j].lot);
}
async function lotPoll() {  // the host page can move the lot too
  if (lineup && !document.hidden) { try { await lotState(); } catch (e) {} }
  setTimeout(lotPoll, 2000);
}
$('#showload').addEventListener('click', () => loadLineup($('#show').value));
$('#show').addEventListener('keydown', e => { if (e.key === 'Enter') loadLineup($('#show').value); });
$('#lotprev').addEventListener('click', () => stepLot(-1));
$('#lotfwd').addEventListener('click', () => stepLot(1));
{
  let name = new URLSearchParams(location.search).get('show');
  if (name === null) try { name = localStorage.getItem('pager.show'); } catch (e) {}
  $('#show').value = name || '';
  if (name) loadLineup(name);
  lotPoll();
}

// ---- wiring ----
buttons.forEach(b => b.addEventListener('click', () => setPreview(b.dataset.page)));
$('#refocus').addEventListener('click', refocus);
$('#reconnect').addEventListener('click', reconnect);
$('#wbset').addEventListener('click', setWb);
$('#hold').addEventListener('click', holdKey);
$('#step').addEventListener('click', () => go('@step'));
$('#tg7').addEventListener('click', tg7);
$('#watch').addEventListener('click', watch);
$('#music .go').addEventListener('click', music);
$('#standin').addEventListener('change', async e => {
  const on = e.target.checked;
  standinBusy = true; e.target.disabled = true; epoch++;
  try { const st = await api('POST', '/api/pager/standin', { on }, 12000);
        if (!switching) show(st, ['standin']);
        say(on ? 'Stand-in on: the item view shows white.' : 'Stand-in off: the item view shows the D5100.'); }
  catch (err) { e.target.checked = !on; say('Stand-in ' + (on ? 'on' : 'off') + ' failed: ' + err.message, 'err'); }
  standinBusy = false; e.target.disabled = false;
});
document.querySelectorAll('.dest[data-dest]').forEach(el =>
  el.querySelector('.go').addEventListener('click', () => stream(el.dataset.dest)));
document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.repeat || e.target.matches('select, input')) return;
  // by e.code too, so Shift+1 (which types "!") still names page 1
  const digit = /^Digit[1-7]$/.test(e.code) ? e.code.slice(5) : e.key;
  const k = { '1': 'intro', '2': 'item', '3': 'next', '4': 'desktop', '5': 'webcam', '6': 'closeup',
              '7': 'multi' }[digit];
  if (k && e.shiftKey) go(k);  // straight to air, a cut
  else if (k) setPreview(k);
  else if (e.key === 'Enter') { e.preventDefault(); take(e.shiftKey); }  // not also a click on a focused button
  else if (e.key === 'r' || e.key === 'R') refocus();
  else if (e.key === 'c' || e.key === 'C') reconnect();
  else if (e.key === 'h' || e.key === 'H') holdKey();
  else if (e.key === ' ') { e.preventDefault(); go('@step'); }  // not also a click on a focused button
  else if (e.key === 'm' || e.key === 'M') music();
  else if (e.key === '[') stepLot(-1);
  else if (e.key === ']') stepLot(1);
});
(function tick() { $('#clock').textContent = hms(new Date()); setTimeout(tick, 1000 - Date.now() % 1000); })();
log('Show control open.', 'sys');
poll();
</script>
</body></html>
"""
