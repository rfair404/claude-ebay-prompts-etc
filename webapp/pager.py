"""webapp/pager.py — /api/pager: put a live show's page on air over HTTP.

The same pager as `tools/live/show_flow.py page` (one implementation, in
show_flow: `state()`, `go()`, `refocus()`), served as JSON so a control
page, a phone, or another agent's web app can drive the show:

    GET  /pager                 the control page: a button per page, live status
    GET  /api/pager             what's on air
    POST /api/pager             {"page": "intro" | "item" | "next", "refocus": true}
    POST /api/pager/refocus     refocus the D5100 without changing page

Every answer is the state object, plus `switch` or `refocus` on a POST:

    {"page": "item", "scene": "eBay Main", "pages": ["intro", "item", "next"],
     "live": true, "timecode": "00:12:03.400", "feed": true,
     "switch": {"page": "item", "changed": true, "focused": true, "refocus_s": 5.2}}

`page` is null when OBS is on a scene that isn't one of the three.
A switch to `next` also carries `window: {"title", "shown"}`: the Up Next
window is hidden (card + webcam only) when its live title looks private or
is the stream itself (show_flow.PRIVATE_WINDOW). `focused: false` means the
D5100's autofocus couldn't lock (nothing with
contrast at the center of the frame); the page still switched.

A switch to `item` blocks ~5 s while the camera refocuses behind the
current page. Calls are serialized with the shell's (show_flow's lock): a
second click waits for the first, and gets 409 if it waits more than 30 s.
503 means OBS isn't reachable; 502 is any other OBS or camera failure,
with OBS's message as the detail.

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
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from webapp.freight import _local_only

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "live"))
import obsctl as _obsctl          # noqa: E402
import show_flow as _show_flow    # noqa: E402

router = APIRouter(dependencies=[Depends(_local_only)])


class PageRequest(BaseModel):
    page: Literal["intro", "item", "next"]
    refocus: bool = True  # false: item without the D5100 (rehearsal, battery swap)


@contextmanager
def _obs():
    try:
        c = _show_flow.connect()
    except _obsctl.ObsError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        yield c
    except _show_flow.Busy as exc:
        raise HTTPException(409, str(exc)) from exc
    except _obsctl.ObsError as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        c.close()


@router.get("/pager", response_class=HTMLResponse)
def pager_page() -> str:
    return PAGE_HTML


@router.get("/api/pager")
def pager_state() -> dict:
    with _obs() as c:
        return _show_flow.state(c)


@router.post("/api/pager")
def pager_go(req: PageRequest) -> dict:
    with _obs() as c:
        switch = _show_flow.go(c, req.page, refocus=req.refocus)
        return {**_show_flow.state(c), "switch": switch}


@router.post("/api/pager/refocus")
def pager_refocus() -> dict:
    with _obs() as c:
        r = _show_flow.refocus(c)
        return {**_show_flow.state(c), "refocus": r}


# The control page. Plain HTML + fetch against the routes above, same
# origin, so the guard's Origin check passes. It polls every 2 s, so a
# switch made from the shell (show_flow.py page ...) shows up here too.
# Keys: 1 intro, 2 item, 3 next, R refocus.
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
body { margin:0; background:var(--bg); color:var(--ink);
       font:16px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:560px; margin:0 auto; padding:20px 16px 40px; }
header { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:18px; }
h1 { font-size:20px; margin:0; }
.badge { font-size:13px; font-weight:600; padding:4px 10px; border-radius:999px;
         border:1px solid var(--line); color:var(--muted); white-space:nowrap; }
.badge.live { background:var(--live); border-color:var(--live); color:#fff; }
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
.page:disabled, #refocus:disabled { opacity:.55; cursor:wait; }
.row { display:flex; gap:12px; margin-top:12px; }
#refocus { flex:1; padding:14px; border-radius:12px; border:1px solid var(--line); background:var(--panel); }
.opt { display:flex; gap:8px; align-items:center; margin-top:12px; font-size:14px; color:var(--muted); }
#msg { min-height:1.4em; margin:16px 2px 0; color:var(--muted); }
#msg.err { color:var(--live); } #msg.warn { color:var(--warn); }
footer { margin-top:22px; font-size:13px; color:var(--muted); }
</style></head>
<body><main>
<header><h1>Show Pager</h1><span id="badge" class="badge">connecting...</span></header>
<div class="pages">
  <button class="page" data-page="intro"><span class="key">1</span>
    <span><b>Intro</b><small>Pre-show card, no cameras</small></span></button>
  <button class="page" data-page="item"><span class="key">2</span>
    <span><b>Item</b><small>D5100 + webcam (refocuses first, ~5 s)</small></span></button>
  <button class="page" data-page="next"><span class="key">3</span>
    <span><b>Next</b><small>Pause / next item: window + webcam</small></span></button>
</div>
<div class="row"><button id="refocus">Refocus D5100 <small>(R)</small></button></div>
<label class="opt"><input type="checkbox" id="nod5"> No D5100: Item skips the refocus (rehearsal, battery swap)</label>
<p id="msg"></p>
<footer id="foot"></footer>
</main>
<script>
const $ = s => document.querySelector(s), buttons = [...document.querySelectorAll('.page')];
let busy = false;

function say(text, cls = '') { const m = $('#msg'); m.textContent = text; m.className = cls; }

function show(st) {
  buttons.forEach(b => b.classList.toggle('on', b.dataset.page === st.page));
  const badge = $('#badge');
  badge.textContent = st.live ? 'LIVE ' + (st.timecode || '').split('.')[0] : 'Off air';
  badge.classList.toggle('live', !!st.live);
  $('#foot').textContent = 'OBS scene: ' + st.scene + (st.page ? '' : ' (not a pager page)') +
                           ' \\u00b7 D5100 feed: ' + (st.feed ? 'running' : 'stopped');
}

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: body ? { 'Content-Type': 'application/json' } : {},
                                body: body ? JSON.stringify(body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ? (typeof data.detail === 'string' ? data.detail
                                            : JSON.stringify(data.detail)) : 'HTTP ' + r.status);
  return data;
}

function lock(on) { busy = on; buttons.concat($('#refocus')).forEach(b => b.disabled = on); }

function focusText(r) {
  return r.focused ? 'Focused (' + r.refocus_s + ' s).'
                   : 'Autofocus could not lock: center the item and refocus.';
}

async function go(page) {
  if (busy) return;
  lock(true); say(page === 'item' && !$('#nod5').checked ? 'Refocusing behind the current page...' : 'Switching...');
  try {
    const st = await api('POST', '/api/pager', { page, refocus: !$('#nod5').checked });
    show(st);
    const sw = st.switch;
    if (!sw.changed) say('Already on ' + page + '.');
    else if (sw.window && !sw.window.shown)
      say('On air: next. Window hidden (' + (sw.window.title || 'closed') +
          ' is not safe to show). Open the next lot in its own window.', 'warn');
    else if (sw.focused === null) say('On air: ' + page + '.');
    else say('On air: item. ' + focusText(sw), sw.focused ? '' : 'warn');
  } catch (e) { say(e.message, 'err'); }
  lock(false);
}

async function refocus() {
  if (busy) return;
  lock(true); say('Refocusing...');
  try { const st = await api('POST', '/api/pager/refocus'); show(st);
        say(focusText(st.refocus), st.refocus.focused ? '' : 'warn'); }
  catch (e) { say(e.message, 'err'); }
  lock(false);
}

async function poll() {
  if (!busy) {
    try { show(await api('GET', '/api/pager'));
          if ($('#msg').textContent.startsWith('OBS')) say(''); }
    catch (e) { $('#badge').textContent = 'No OBS'; $('#badge').classList.remove('live');
                say('OBS: ' + e.message, 'err'); }
  }
  setTimeout(poll, 2000);
}

buttons.forEach(b => b.addEventListener('click', () => go(b.dataset.page)));
$('#refocus').addEventListener('click', refocus);
document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const k = { '1': 'intro', '2': 'item', '3': 'next' }[e.key];
  if (k) go(k); else if (e.key === 'r' || e.key === 'R') refocus();
});
poll();
</script>
</body></html>
"""
