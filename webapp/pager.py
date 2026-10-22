"""webapp/pager.py — /api/pager: put a live show's page on air over HTTP.

The same pager as `tools/live/show_flow.py page` (one implementation, in
show_flow: `state()`, `go()`, `refocus()`), served as JSON so a control
page, a phone, or another agent's web app can drive the show:

    GET  /pager                 the control page: a button per page, live status
    GET  /api/pager             what's on air, plus `music`: {"ready", "on", "db", "track"}
    GET  /api/pager/preview.jpg a small JPEG of the program scene (what viewers see), no audio
    POST /api/pager             {"page": "intro" | "item" | "closeup" | "next" | "desktop" | "webcam" | "multi", "refocus": false}
    POST /api/pager/refocus     refocus without changing page (D5100; on closeup the TG-7)
    POST /api/pager/wb          {"preset": "daylight"}: the D5100's white balance (no preset: read it)
    POST /api/pager/hold        {"on": true}: freeze the camera page on air; false: back to it live
    POST /api/pager/step        the item sequence: item -> closeup -> hold -> the other camera
    POST /api/pager/tg7         {"action": "start" | "stop"}: the TG-7 feed (tg7cam stream)
    POST /api/pager/watch       {"action": "start" | "stop"}: the camera watch (black over a dead camera)
    POST /api/pager/reconnect   OBS reopens the D5100 and webcam sources (black / frozen picture)
    POST /api/pager/stream      {"dest": "ebay" | "twitch", "action": "start" | "stop"}
    POST /api/pager/standin     {"on": true}: white panel for the D5100 in the item view
    POST /api/pager/music       {"on": true | false}: fade it in / out (~2 s, show_music.py)

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
twice a second while the tab is visible and the preview is shown.

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

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

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


class PageRequest(BaseModel):
    page: Literal["intro", "item", "closeup", "next", "desktop", "webcam", "multi"]
    refocus: bool = False  # true: item/multi refocus the D5100 first, behind the current page (~5 s)


class HoldRequest(BaseModel):
    on: bool


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
        return {**_show_flow.state(c), "music": _show_music.state(c)}


@router.get("/api/pager/preview.jpg")
def pager_preview() -> Response:
    with _obs() as c:
        jpg = _show_flow.program_shot(c)
    return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/api/pager")
def pager_go(req: PageRequest) -> dict:
    with _obs() as c:
        switch = _show_flow.go(c, req.page, refocus=req.refocus)
        return {**_show_flow.state(c), "switch": switch}


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
def pager_music(req: HoldRequest) -> dict:  # {"on": bool}
    with _obs() as c:
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


# The control page. Plain HTML + fetch against the routes above, same
# origin, so the guard's Origin check passes. It polls every 1 s while the
# tab is visible (not at all while hidden: OBS is short of CPU during a
# show), so a switch made from the shell or in OBS shows up here too.
# Page clicks are sent one at a time, latest wins: clicking 2 then 3 fast
# ends on 3, never on whichever request OBS happened to get last. A page
# click is never blocked by a refocus or a stream start in flight.
# The On-air preview fetches a frame ~2x a second (the next only after the
# last one lands), and nothing while the tab is hidden or the preview is off.
# Hold / release / step join the same queue, so a step pressed during a
# switch lands after it, never before.
# Keys: 1 intro, 2 item, 3 next, 4 desktop, 5 webcam, 6 close-up, 7 multi, H hold/release,
# Space step, R refocus, C reconnect cameras, M music on/off. Keys are ignored
# while the white balance list has focus (its own letters pick a preset).
PAGE_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Show Pager</title>
<style>
:root { --bg:#f6f5f2; --panel:#fff; --ink:#1d1d1b; --muted:#6b6a66; --line:#dddbd5;
        --on:#1f7a4d; --on-ink:#fff; --live:#c62828; --warn:#a15c00; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161616; --panel:#202020; --ink:#ececea; --muted:#9a9994; --line:#333;
          --on:#2f9e66; --on-ink:#fff; --live:#ef5350; --warn:#e0a040; } }
* { box-sizing:border-box; }
[hidden] { display:none !important; }  /* else .row's flex shows a hidden #wbrow */
body { margin:0; background:var(--bg); color:var(--ink);
       font:16px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:560px; margin:0 auto; padding:20px 16px 40px; }
header { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:18px; }
h1 { font-size:20px; margin:0; }
.badge { font-size:13px; font-weight:600; padding:4px 10px; border-radius:999px;
         border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
.badge.live { background:var(--live); border-color:var(--live); color:#fff; }
.cams { display:flex; flex-wrap:wrap; gap:8px; margin:-6px 0 18px; }
.chip { font-size:13px; font-weight:600; padding:4px 10px; border-radius:999px;
        border:1px solid var(--line); color:var(--muted); }
.chip.live { background:var(--on); border-color:var(--on); color:var(--on-ink); }
.chip.down { background:var(--live); border-color:var(--live); color:#fff; }
.chip.busy { border-color:var(--warn); color:var(--warn); }
.pages { display:grid; gap:12px; }
button { font:inherit; color:inherit; cursor:pointer; }
.page { display:flex; align-items:center; gap:14px; width:100%; text-align:left;
        padding:18px; border-radius:14px; border:2px solid var(--line); background:var(--panel); }
.page:hover { border-color:var(--muted); }
.page .key { flex:none; width:34px; height:34px; border-radius:8px; display:grid; place-items:center;
             border:1px solid var(--line); color:var(--muted); font-weight:600; }
.page b { display:block; font-size:19px; }
.page small { color:var(--muted); }
.page.on { background:var(--on); border-color:var(--on); color:var(--on-ink); }
.page.on small, .page.on .key { color:var(--on-ink); border-color:rgba(255,255,255,.5); }
.page.pending { border-color:var(--on); border-style:dashed; }
.row button:disabled, .go:disabled, #standin:disabled { opacity:.55; cursor:wait; }
.row { display:flex; gap:12px; margin-top:12px; }
.row button, .row select { flex:1; padding:14px; border-radius:12px; border:1px solid var(--line); background:var(--panel);
                           font:inherit; color:inherit; }
.row button.on { background:var(--warn); border-color:var(--warn); color:#fff; }
h2 { font-size:14px; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin:22px 2px 8px; }
.dests { display:grid; gap:10px; }
.dest { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:12px 14px;
        border-radius:12px; border:1px solid var(--line); background:var(--panel); }
.dest b { display:block; } .dest small { color:var(--muted); }
.dest a.open { font-size:13px; font-weight:400; color:var(--muted); margin-left:8px; }
.dest.live { border-color:var(--live); } .dest.live small { color:var(--live); font-weight:600; }
.dest .go { padding:9px 16px; border-radius:10px; border:1px solid var(--line); background:var(--bg); min-width:86px; }
.dest.live .go { background:var(--live); border-color:var(--live); color:#fff; }
#music.on { border-color:var(--on); } #music.on small { color:var(--on); font-weight:600; }
#music.on .go { background:var(--on); border-color:var(--on); color:var(--on-ink); }
.opt { display:flex; gap:8px; align-items:center; margin-top:12px; font-size:14px; color:var(--muted); }
#msg { min-height:1.4em; margin:16px 2px 0; color:var(--muted); }
#msg.err { color:var(--live); } #msg.warn { color:var(--warn); }
footer { margin-top:22px; font-size:13px; color:var(--muted); }
.monitor { display:flex; gap:14px; align-items:flex-start; margin-bottom:18px; }
.monitor img { flex:none; width:108px; aspect-ratio:9/16; object-fit:contain; background:#000;
               border-radius:8px; border:2px solid var(--line); }
.monitor.live img { border-color:var(--live); }
.monitor.stale img { opacity:.35; }
.monitor b { display:block; } .monitor small { display:block; color:var(--muted); margin-bottom:8px; }
.monitor.stale small { color:var(--warn); }
.monitor button { padding:6px 12px; border-radius:8px; border:1px solid var(--line); background:var(--panel); font-size:14px; }
.monitor.off img, .monitor.off small { display:none; }
</style></head>
<body><main>
<header><h1>Show Pager</h1><span id="badge" class="badge">connecting...</span></header>
<div class="cams" id="cams" aria-label="Cameras">
  <span class="chip" data-cam="d5">D5100 unknown</span>
  <span class="chip" data-cam="webcam">Webcam unknown</span>
  <span class="chip" data-cam="tg7">TG-7 unknown</span>
</div>
<div class="monitor" id="monitor">
  <img id="shot" alt="What OBS is sending now"
       src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==">
  <div><b id="shottitle">Program</b><small id="shotnote">waiting for OBS...</small>
    <button id="shottoggle">Hide preview</button></div>
</div>
<div class="pages">
  <button class="page" data-page="intro"><span class="key">1</span>
    <span><b>Intro</b><small>Pre-show card, no cameras</small></span></button>
  <button class="page" data-page="item"><span class="key">2</span>
    <span><b>Item</b><small>D5100 + webcam (R to refocus)</small></span></button>
  <button class="page" data-page="closeup"><span class="key">6</span>
    <span><b>Close-up</b><small>TG-7 + webcam (R: TG-7 autofocus)</small></span></button>
  <button class="page" data-page="next"><span class="key">3</span>
    <span><b>Next</b><small>Pause / next item: window + webcam</small></span></button>
  <button class="page" data-page="desktop"><span class="key">4</span>
    <span><b>Desktop</b><small>Desktop with face cam: app window + webcam</small></span></button>
  <button class="page" data-page="webcam"><span class="key">5</span>
    <span><b>Webcam only</b><small>Fallback: face cam + mic, no D5100</small></span></button>
  <button class="page" data-page="multi"><span class="key">7</span>
    <span><b>Multi view</b><small>D5100 + webcam + desktop window, all at once</small></span></button>
</div>
<div class="row"><button id="hold">Hold still <small>(H)</small></button>
  <button id="step">Step <small>(Space)</small></button></div>
<div class="row"><button id="refocus">Refocus <small>(R)</small></button>
  <button id="reconnect">Reconnect cameras <small>(C)</small></button>
  <button id="tg7">TG-7 feed</button>
  <button id="watch">Start camera watch</button></div>
<div class="row" id="wbrow"><select id="wb" aria-label="Set the D5100 white balance to">
    <option value="" selected disabled>Set D5100 WB to...</option>
    <option value="auto">Auto</option><option value="daylight">Daylight</option>
    <option value="cloudy">Cloudy</option><option value="shade">Shade</option>
    <option value="tungsten">Tungsten</option><option value="fluorescent">Fluorescent</option>
    <option value="flash">Flash</option></select>
  <button id="wbset">Set</button></div>
<h2>Streams</h2>
<div class="dests">
  <div class="dest" data-dest="ebay"><span><b>eBay<a class="open" href="https://www.ebay.com/ebaylive/studio" target="_blank" rel="noopener">Live Studio &#8599;</a></b><small class="st">off</small></span>
    <button class="go">Start</button></div>
  <div class="dest" data-dest="twitch"><span><b>Twitch<a class="open" href="https://www.twitch.tv/popebasement" target="_blank" rel="noopener">twitch.tv/popebasement &#8599;</a></b><small class="st">off</small></span>
    <button class="go">Start</button></div>
</div>
<h2>Music</h2>
<div class="dests">
  <div class="dest" id="music"><span><b>Background music</b><small class="st">...</small></span>
    <button class="go">Start</button></div>
</div>
<label class="opt"><input type="checkbox" id="standin"> D5100 stand-in: white panel in the item view, no refocus (dead battery, rehearsal)</label>
<p id="msg" role="status" aria-live="polite"></p>
<footer id="foot"></footer>
</main>
<script>
const $ = s => document.querySelector(s), buttons = [...document.querySelectorAll('.page')];
const LABEL = { intro: 'Intro', item: 'Item', closeup: 'Close-up', next: 'Next', desktop: 'Desktop',
                webcam: 'Webcam only', multi: 'Multi view', hold: 'Hold' };
const D5_PAGES = ['item', 'multi'];  // pages with the D5100 on air
const CAM_LABEL = { d5: 'D5100', webcam: 'Webcam', tg7: 'TG-7' };
// the cameras each page puts on air (show_flow.PAGE_CAMS)
const PAGE_CAMS = { intro: [], item: ['d5', 'webcam'], closeup: ['tg7', 'webcam'], next: ['webcam'],
                    desktop: ['webcam'], webcam: ['webcam'], multi: ['d5', 'webcam', 'tg7'] };
// Hold, release and step ride the page queue as actions: one at a time, in order.
const ACTION = { '@hold': ['/api/pager/hold', { on: true }], '@release': ['/api/pager/hold', { on: false }],
                 '@step': ['/api/pager/step', null] };
let held = null, tg7Busy = false, watchBusy = false, lastCams = null;
// Each control has its own in-flight flag: a refocus or stream start never blocks a page click.
let pending = null, switching = false, refocusing = false, streaming = false, standinBusy = false;
let epoch = 0;  // bumped by every action, so a poll answer from before it can't undo it

function say(text, cls = '') { const m = $('#msg'); m.textContent = text; m.className = cls; }

function show(st) {
  curPage = st.page;
  buttons.forEach(b => { b.classList.toggle('on', b.dataset.page === st.page);
                         b.classList.toggle('pending', b.dataset.page === pending); });
  const badge = $('#badge');
  badge.textContent = st.live ? 'LIVE ' + (st.timecode || '').split('.')[0] : 'Off air';
  badge.classList.toggle('live', !!st.live);
  $('#monitor').classList.toggle('live', !!st.live);
  $('#shottitle').textContent = st.live ? 'On air' : 'Program (off air)';
  for (const el of document.querySelectorAll('.dest[data-dest]')) {
    const s = (st.streams || {})[el.dataset.dest] || {};
    el.classList.toggle('live', !!s.live);
    el.querySelector('.st').textContent = s.live ? 'LIVE ' + (s.timecode || '').split('.')[0] : 'off';
    el.querySelector('.go').textContent = s.live ? 'Stop' : 'Start';
  }
  if (!standinBusy) $('#standin').checked = !!st.standin;
  held = st.page === 'hold' ? (st.held || 'item') : null;
  $('#hold').classList.toggle('on', !!held);
  $('#hold').firstChild.textContent = held ? 'Release to ' + LABEL[held] + ' ' : 'Hold still ';
  // the D5 refocus needs the D5; the TG-7's works on the close-up page whatever the stand-in
  $('#refocus').hidden = !!st.standin && st.page !== 'closeup';
  $('#refocus').disabled = refocusing || !!st.refocusing;
  $('#wbrow').hidden = !!st.standin;  // the D5's, not the TG-7's
  $('#wbset').disabled = wbBusy || !!st.refocusing;
  if (!musicBusy && st.music) showMusic(st.music);  // only GET /api/pager carries it
  if (!tg7Busy) $('#tg7').textContent = st.tg7_status === 'running' ? 'Stop TG-7 feed' : 'Start TG-7 feed';
  if (!watchBusy) $('#watch').textContent = st.watch ? 'Stop camera watch' : 'Start camera watch';
  showCams(st);
  $('#foot').textContent = 'OBS scene: ' + st.scene + (st.page ? '' : ' (not a pager page)') +
                           ' \\u00b7 D5100 feed: ' + (st.feed_status || (st.feed ? 'running' : 'stopped')) +
                           ' \\u00b7 TG-7 feed: ' + (st.tg7_status || '?') +
                           (st.refocusing ? ' \\u00b7 D5100 busy (refocus, WB or still)' : '');
}

// One chip per camera; a camera on the page on air going black (or coming back) is said.
function showCams(st) {
  const cams = st.cameras || {};
  for (const el of document.querySelectorAll('#cams .chip')) {
    const v = cams[el.dataset.cam] || { state: 'unknown' };
    el.className = 'chip' + ({ live: ' live', down: ' down', busy: ' busy' }[v.state] || '');
    el.textContent = CAM_LABEL[el.dataset.cam] + ' ' + v.state + (v.blacked ? ' \\u00b7 black on air' : '');
    el.title = v.reason || '';
  }
  if (lastCams) for (const k of PAGE_CAMS[st.page] || []) {
    const was = !!(lastCams[k] || {}).blacked, now = !!(cams[k] || {}).blacked;
    if (now && !was) say(CAM_LABEL[k] + ' went down: its slot is black on air.', 'warn');
    else if (was && !now && (cams[k] || {}).state === 'live') say(CAM_LABEL[k] + ' is back.');
  }
  lastCams = cams;
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

let musicBusy = false;

function showMusic(m) {
  const el = $('#music'), go = el.querySelector('.go');
  el.classList.toggle('on', !!m.on);
  el.querySelector('.st').textContent = !m.ready ? 'not set up: run show_music.py setup'
    : m.on ? 'playing' + (m.track ? ': ' + m.track : '') + ' (' + m.db + ' dB)' : 'off';
  go.textContent = m.on ? 'Stop' : 'Start';
  go.disabled = musicBusy || !m.ready;
}

// Fades run on their own flag, so a fade never blocks a page switch.
async function music() {
  if (musicBusy) return;
  const on = !$('#music').classList.contains('on');
  musicBusy = true; $('#music .go').disabled = true;
  say(on ? 'Fading music in...' : 'Fading music out...');
  try { const m = await api('POST', '/api/pager/music', { on }, 15000); musicBusy = false; showMusic(m);
        say(on ? 'Music on.' : 'Music off.'); }
  catch (e) { musicBusy = false; $('#music .go').disabled = false; say(e.message, 'err'); }
}

const NAMES = { ebay: 'eBay', twitch: 'Twitch' };
const ASK = {
  'twitch start': 'Go live on Twitch? It is public the moment it connects.',
  'ebay start': 'Start sending video to eBay? Viewers see it after you click Start event in Stream manager.',
  'twitch stop': 'Stop the Twitch stream?',
  'ebay stop': 'Stop sending video to eBay? This ends the eBay feed for your show.',
};

function streamButtons(on) { document.querySelectorAll('.dest[data-dest] .go').forEach(b => b.disabled = on); }

async function stream(dest) {
  if (streaming) return;
  const live = document.querySelector('.dest[data-dest="' + dest + '"]').classList.contains('live');
  const action = live ? 'stop' : 'start';
  if (!confirm(ASK[dest + ' ' + action])) return;
  streaming = true; streamButtons(true);
  say((action === 'start' ? 'Connecting to ' : 'Stopping ') + NAMES[dest] + '...');
  try {
    const st = await api('POST', '/api/pager/stream', { dest, action }, 30000);
    if (!switching) show(st);
    const r = st.stream;
    say(action === 'start' ? (r.changed ? NAMES[dest] + ' is live: ' + r.kbps + ' kbps.' : NAMES[dest] + ' was already live.')
                           : NAMES[dest] + ' stopped.');
  } catch (e) { say(NAMES[dest] + ' ' + action + ' failed: ' + e.message, 'err'); }
  streaming = false; streamButtons(false);
}

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
    if (ACTION[page]) { await act(page); continue; }
    say('Switching to ' + LABEL[page] + '...');
    try {
      const st = await api('POST', '/api/pager', { page }, 12000);
      epoch++;
      if (!pending) { show(st); switched(page, st); }
    } catch (e) { say(LABEL[page] + ' did not go on air: ' + e.message, 'err'); }
  }
  buttons.forEach(b => b.classList.remove('pending'));
  switching = false;
}

async function act(name) {
  const [path, body] = ACTION[name];
  try {
    const st = await api('POST', path, body, 12000);
    epoch++;
    if (pending) return;
    show(st);
    const r = st.hold || st.step || st.switch || {};
    if (st.page === 'hold') say('Holding a still of ' + LABEL[st.held || r.held] +
                                ': turn the item, then Step or Release.', 'warn');
    else if (r.changed === false && name === '@release') say('Not on hold.');
    else say('On air: ' + LABEL[st.page] + '.');
  } catch (e) { say({ '@hold': 'Hold', '@release': 'Release', '@step': 'Step' }[name] + ' failed: ' + e.message, 'err'); }
}

let curPage = null;
function holdKey() { go(held ? '@release' : '@hold'); }

async function tg7() {
  if (tg7Busy) return;
  const action = $('#tg7').textContent.startsWith('Stop') ? 'stop' : 'start';
  if (action === 'stop' && curPage === 'closeup' &&
      !confirm('The close-up page is on air. Stop the TG-7 feed anyway?')) return;
  tg7Busy = true; $('#tg7').disabled = true; epoch++;
  say(action === 'start' ? 'Starting the TG-7 feed...' : 'Stopping the TG-7 feed...');
  try { const st = await api('POST', '/api/pager/tg7', { action }, 15000);
        tg7Busy = false; if (!switching) show(st);
        say(action === 'start' ? 'TG-7 feed started (it waits for the camera Wi-Fi if needed).'
                               : 'TG-7 feed stopped.'); }
  catch (e) { say('TG-7 feed ' + action + ' failed: ' + e.message, 'err'); }
  tg7Busy = false; $('#tg7').disabled = false;
}

async function refocus() {
  if (refocusing || $('#refocus').hidden) return;
  refocusing = true; $('#refocus').disabled = true; epoch++;
  say(curPage === 'closeup' ? 'TG-7 autofocus...'
                            : 'Refocusing the D5100 (about 5 s; a D5100 view hides behind Next)...');
  try { const st = await api('POST', '/api/pager/refocus', null, 60000);
        refocusing = false;
        if (!switching) show(st);
        say(focusText(st.refocus), st.refocus.focused ? '' : 'warn'); }
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
        say('D5100 white balance: ' + st.wb.wb + (st.wb.changed ? '.' : ' (already).')); }
  catch (e) { say('White balance failed: ' + e.message.replace(/^wb failed: /, ''), 'err'); }
  wbBusy = false; $('#wbset').disabled = false;
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

let reconnecting = false;
async function reconnect() {
  if (reconnecting) return;
  reconnecting = true; $('#reconnect').disabled = true; epoch++;
  say('Reconnecting the cameras in OBS...');
  try { const st = await api('POST', '/api/pager/reconnect', null, 12000);
        if (!switching) show(st);
        const r = Object.entries(st.reconnect), bad = r.filter(([, v]) => !v.ok);
        say(bad.length ? 'Reconnect: ' + bad.map(([k, v]) => k + ': ' + v.error).join('; ')
                       : 'Reconnected: ' + r.map(([k]) => k).join(', ') + '.', bad.length ? 'err' : ''); }
  catch (e) { say('Reconnect failed: ' + e.message, 'err'); }
  reconnecting = false; $('#reconnect').disabled = false;
}

// 1 s while the tab is visible, nothing while it's hidden: cheap for OBS and the server.
async function poll() {
  if (!document.hidden) {
    const mine = epoch;
    try { const st = await api('GET', '/api/pager', null, 5000);
          if (mine === epoch && !switching) show(st);
          if ($('#msg').textContent.startsWith('OBS')) say(''); }
    catch (e) { $('#badge').textContent = 'No OBS'; $('#badge').classList.remove('live');
                say('OBS: ' + e.message, 'err'); }
  }
  setTimeout(poll, 1000);
}

// The On-air preview: OBS's render of the program scene. One frame at a time,
// so a slow OBS just lowers the frame rate instead of piling up requests.
let previewOn = true, shotUrl = null;
try { previewOn = localStorage.getItem('pager.preview') !== 'off'; } catch (e) {}
function previewShown() {
  $('#monitor').classList.toggle('off', !previewOn);
  $('#shottoggle').textContent = previewOn ? 'Hide preview' : 'Show preview';
}
async function frame() {
  if (previewOn && !document.hidden) {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 5000);
    try {
      const r = await fetch('/api/pager/preview.jpg', { cache: 'no-store', signal: ctl.signal });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'HTTP ' + r.status);
      const url = URL.createObjectURL(await r.blob());
      $('#shot').src = url;
      if (shotUrl) URL.revokeObjectURL(shotUrl);
      shotUrl = url;
      $('#monitor').classList.remove('stale');
      $('#shotnote').textContent = 'What OBS is sending, a few frames a second. No sound.';
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

buttons.forEach(b => b.addEventListener('click', () => go(b.dataset.page)));
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
        if (!switching) show(st);
        say(on ? 'Stand-in on: the item view shows white.' : 'Stand-in off: the item view shows the D5100.'); }
  catch (err) { e.target.checked = !on; say('Stand-in ' + (on ? 'on' : 'off') + ' failed: ' + err.message, 'err'); }
  standinBusy = false; e.target.disabled = false;
});
document.querySelectorAll('.dest[data-dest]').forEach(el =>
  el.querySelector('.go').addEventListener('click', () => stream(el.dataset.dest)));
document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey || e.repeat || e.target.tagName === 'SELECT') return;
  const k = { '1': 'intro', '2': 'item', '3': 'next', '4': 'desktop', '5': 'webcam', '6': 'closeup',
              '7': 'multi' }[e.key];
  if (k) go(k); else if (e.key === 'r' || e.key === 'R') refocus();
  else if (e.key === 'c' || e.key === 'C') reconnect();
  else if (e.key === 'h' || e.key === 'H') holdKey();
  else if (e.key === ' ') { e.preventDefault(); go('@step'); }  // not also a click on a focused button
  else if (e.key === 'm' || e.key === 'M') music();
});
poll();
</script>
</body></html>
"""
