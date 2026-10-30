"""webapp/ui.py — the app's shell (#311 slice 3): one bar on top of every page
the app draws itself, with the store switcher, the item tabs, and the buttons
that run a row of webapp/actions.py.

The pages under it are still drawn by their own renderers (tools/dashboard.py,
tools/review_card_html.py, tools/prep_sheet_html.py). `wrap()` drops the bar
in right after their `<meta charset>`, so a renderer keeps writing the same
self-contained file it always has and the app adds the chrome. Every class
here is `fh-` prefixed so it can't collide with a page's own CSS.

Look (brand/pops-games/README.md, art director's notes in #311): ink #141210
text on paper #FBFAF8, barn red #a8322b for the shop name and the one main
action only, grey #4a443c for secondary text, green/amber status kept apart
from red. Georgia Bold tracked caps for the name, system sans for the UI,
mono for SKUs and frame numbers. 1px rules, 3px corners. Dark by
prefers-color-scheme. No third-party fonts or scripts.

The store lives in the URL (`?store=`), never in a session, so two tabs can't
act on each other's store. On an item page the switcher shows the store the
draft publishes to, and its links go to that store's dashboard.

Buttons work without a page reload: `fhRun()` POSTs the action and polls
`GET /api/jobs/{id}` until it finishes. That is the only script the shell
carries; the pages below it still work with it switched off.
"""
from __future__ import annotations

import html
import json
from typing import Optional, Sequence
from urllib.parse import quote

SHOP_NAME = "The Fair Hand"

STYLE = """
.fh-bar{--fh-ink:#141210;--fh-paper:#FBFAF8;--fh-red:#a8322b;--fh-grey:#4a443c;
  --fh-rule:#d9d4cc;--fh-ok:#2f6b47;--fh-amber:#8a5a12;
  font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--fh-ink);
  background:var(--fh-paper);border-bottom:1px solid var(--fh-rule);
  display:flex;flex-wrap:wrap;align-items:center;gap:6px 18px;padding:8px 16px;
  margin:0 0 12px;position:relative;z-index:50}
.fh-bar *{box-sizing:border-box}
.fh-name{font:700 13px/1 Georgia,serif;letter-spacing:.26em;text-transform:uppercase;
  color:var(--fh-red);text-decoration:none;white-space:nowrap}
.fh-nav,.fh-stores,.fh-tabs,.fh-acts{display:flex;flex-wrap:wrap;gap:4px;align-items:center;
  margin:0;padding:0;list-style:none}
.fh-lab{color:var(--fh-grey);font-size:12px;margin-right:2px}
.fh-bar a.fh-l{display:inline-block;min-height:36px;padding:8px 11px;border:1px solid transparent;
  border-radius:3px;color:var(--fh-ink);text-decoration:none}
.fh-bar a.fh-l:hover{border-color:var(--fh-rule)}
.fh-bar a.fh-l[aria-current]{border-color:var(--fh-ink);font-weight:600}
.fh-mono{font-family:ui-monospace,"Courier New",monospace;font-size:12.5px}
.fh-store-fixed{padding:8px 11px;border:1px solid var(--fh-rule);border-radius:3px}
.fh-bar button{font:inherit;min-height:36px;padding:7px 13px;border-radius:3px;cursor:pointer;
  border:1px solid var(--fh-ink);background:transparent;color:var(--fh-ink)}
.fh-bar button.fh-main{background:var(--fh-red);border-color:var(--fh-red);color:#fff}
.fh-bar button:disabled{opacity:.55;cursor:progress}
.fh-msg{flex-basis:100%;min-height:0;margin:0;font-size:13px;color:var(--fh-grey)}
.fh-msg:empty{display:none}
.fh-msg.ok{color:var(--fh-ok)}.fh-msg.warn{color:var(--fh-amber)}
.fh-msg.err{color:var(--fh-ink);font-weight:600}
.fh-msg pre{white-space:pre-wrap;max-height:12em;overflow:auto;margin:4px 0 0;
  font:12px/1.4 ui-monospace,"Courier New",monospace}
.fh-sp{flex:1}
@media (prefers-color-scheme: dark){
  .fh-bar{--fh-ink:#ece8e1;--fh-paper:#171512;--fh-grey:#b3aa9c;--fh-rule:#3a352e;
    --fh-red:#c9473e;--fh-ok:#6fbf8e;--fh-amber:#d9a54e}
}
"""

# One job runner for every button: POST the action, poll the job, say what
# happened in words. `onDone` reloads the page so it shows the new state.
SCRIPT = r"""
async function fhRun(btn, action, slots, opts) {
  opts = opts || {};
  const msg = document.getElementById('fh-msg');
  const say = (t, cls, detail) => {
    msg.className = 'fh-msg ' + (cls || ''); msg.textContent = t;
    if (detail) { const p = document.createElement('pre'); p.textContent = detail; msg.appendChild(p); }
  };
  if (btn) btn.disabled = true;
  say((opts.busy || 'Working') + '...');
  try {
    const r = await fetch('/api/actions/' + action, {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({slots: slots})});
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.statusText);
    let job;
    for (;;) {
      await new Promise(res => setTimeout(res, 700));
      job = await (await fetch('/api/jobs/' + j.id, {cache: 'no-store'})).json();
      if (job.status !== 'queued' && job.status !== 'running') break;
    }
    const res = job.result || {};
    if (job.status === 'done' && res.ok) {
      say((opts.done || 'Done') + '.', 'ok', (res.stdout || '').trim().split('\n').slice(-6).join('\n'));
      if (opts.reload) setTimeout(() => location.reload(), 900);
    } else {
      say((opts.failed || 'That did not work') + '.', 'err',
          ((res.stderr || '') + '\n' + (res.stdout || '') + (job.error || '')).trim().slice(-1500));
    }
  } catch (e) { say('That did not work: ' + e.message, 'err'); }
  if (btn) btn.disabled = false;
}
"""


def _e(v) -> str:
    return html.escape(str(v), quote=True)


# Outside the f-strings: Python 3.11 refuses a backslash in an f-string expression.
_CUR = ' aria-current="page"'


def _store_links(stores: Sequence[str], current: Optional[str], path: str) -> str:
    """`All stores` plus one link per store. A view choice, never pooled —
    the dashboard draws one panel per store either way."""
    items = [("All stores", path, current is None)]
    items += [(s, f"{path}?store={quote(s)}", s == current) for s in stores]
    return "".join(
        f'<li><a class="fh-l" href="{_e(href)}"{_CUR if on else ""}>'
        f'{_e(label)}</a></li>' for label, href, on in items)


def _tabs(shoot: str, current: str) -> str:
    q = "/".join(quote(p) for p in shoot.split("/"))
    tabs = [("review", "Review", f"/review/{q}"),
            ("prep", "Frame Check", f"/prep/{q}"),
            ("card", "Contact sheet", f"/prep/{q}/card.jpg")]
    return "".join(
        f'<li><a class="fh-l" href="{_e(href)}"{_CUR if key == current else ""}>'
        f'{_e(label)}</a></li>' for key, label, href in tabs)


def button(label: str, action: str, slots: dict, *, main: bool = False,
           busy: str = "", done: str = "", reload: bool = True) -> str:
    """A button that runs one row of webapp/actions.py with these slots."""
    opts = {"busy": busy or label, "done": done or label + ": done", "reload": reload}
    onclick = (f"fhRun(this,{json.dumps(action)},{json.dumps(slots)},"
               f"{json.dumps(opts)})")
    cls = ' class="fh-main"' if main else ""
    return (f'<button type="button"{cls} '
            f'onclick="{_e(onclick)}">{_e(label)}</button>')


def bar(*, stores: Sequence[str], store: Optional[str] = None, path: str = "/",
        shoot: Optional[str] = None, tab: str = "", draft_store: Optional[str] = None,
        buttons: Sequence[str] = ()) -> str:
    """The shell bar. On an item page (`shoot` set) the store is the draft's
    and is shown, not picked; the switcher's links go to the dashboard."""
    parts = [f'<a class="fh-name" href="/">{_e(SHOP_NAME)}</a>',
             '<ul class="fh-nav" aria-label="Pages">'
             f'<li><a class="fh-l" href="/"{_CUR if path == "/" and not shoot else ""}>Dashboard</a></li>'
             '<li><a class="fh-l" href="/pager">Live</a></li>'
             '<li><a class="fh-l" href="/freight">Freight</a></li></ul>']
    if shoot:
        parts.append(f'<ul class="fh-tabs" aria-label="This item">'
                     f'<li><span class="fh-lab">Item</span> <span class="fh-mono">{_e(shoot)}</span></li>'
                     f'{_tabs(shoot, tab)}</ul>')
        if draft_store:
            parts.append('<span class="fh-sp"></span>'
                         f'<span class="fh-lab">Publishes to</span>'
                         f'<span class="fh-store-fixed">{_e(draft_store)}</span>')
    else:
        parts.append('<span class="fh-sp"></span><span class="fh-lab" id="fh-store-lab">Store</span>'
                     f'<ul class="fh-stores" aria-labelledby="fh-store-lab">'
                     f'{_store_links(stores, store, "/")}</ul>')
    if buttons:
        parts.append(f'<div class="fh-acts" role="group" aria-label="Actions">{"".join(buttons)}</div>')
    parts.append('<p class="fh-msg" id="fh-msg" role="status" aria-live="polite"></p>')
    return (f'<style>{STYLE}</style><script>{SCRIPT}</script>'
            f'<nav class="fh-bar" aria-label="The Fair Hand">{"".join(parts)}</nav>')


def wrap(page: str, shell: str) -> str:
    """Put the bar at the top of a renderer's page: right after its
    `<meta charset>` (every renderer here writes that first), else first."""
    head = '<meta charset="utf-8">'
    if page.startswith(head):
        return head + "\n" + shell + page[len(head):]
    return '<meta charset="utf-8">\n' + shell + page
