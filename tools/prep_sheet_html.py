#!/usr/bin/env python3
"""Render the PREP review as an interactive page instead of a tall JPEG.

A fourteen-frame shoot makes a contact sheet 4,000px tall, and a picture you
scroll past in a viewer is not a surface anyone can decide on. This renders the
review as a page: three tabs in the order the work happens, a card per frame,
every option beside it, click the one you want.

  THE CORE OF THIS PAGE RUNS WITHOUT JAVASCRIPT, ON PURPOSE.

The first two versions built the whole DOM in JS and routed every click through
a JS handler. Both rendered perfectly and neither responded to a single click in
the viewer the operator actually uses — the failure mode of script-dependent UI
is a page that looks finished and does nothing, and it cost two rounds to find
because it was verified over localhost instead of where it runs.

So selection is native radio inputs, the shown picture is a CSS `:has()` rule,
tab switching is a radio group, and the full-size preview is `:target`. None of
that can be broken by a sandbox that dislikes script. JavaScript is layered on
top for one job only — assembling the command and copying it — and the page is
fully usable when it never runs, because every choice is legible on the page and
can be read back in words.

Usage:
    python tools/prep_sheet_html.py <shoot> [-o out.html]
"""
from __future__ import annotations

import argparse
import base64
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2                                                   # noqa: E402

from lib.photo_prep import color as colormod                 # noqa: E402
from lib.photo_prep import orientation as orientmod          # noqa: E402
from lib.photo_prep import stages as stagemod                # noqa: E402
from lib.photo_prep.prep import _load_bgr, load_manifest     # noqa: E402

# One encode per distinct picture serves the card, the option thumbnail and the
# full-size preview. Rotations reuse the same bytes and are turned in CSS, so a
# frame costs one image no matter how many turns it offers.
LONG = 820
LONG_DENSE = 640          # the colour stage carries three pictures per frame
QUALITY = 70


def _uri(img, long_edge: int) -> str:
    h, w = img.shape[:2]
    s = long_edge / max(h, w)
    if s < 1:
        img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))),
                         interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, QUALITY])
    if not ok:
        raise RuntimeError("could not encode a thumbnail")
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def _frames(shoot: Path, m: dict):
    for name, rec in (m.get("photos") or {}).items():
        if (shoot / name).exists():
            yield name, rec


def _upright(shoot: Path, name: str, rec: dict):
    return orientmod.rotate_bgr(_load_bgr(shoot / name), rec["orientation"]["applied"])


def _shipping(shoot: Path, name: str, rec: dict):
    up = _upright(shoot, name, rec)
    crop = rec.get("crop") or {}
    if crop.get("applied") and crop.get("box"):
        x0, y0, x1, y1 = crop["box"]
        up = up[y0:y1, x0:x1]
    return up


# ---------------------------------------------------------------------------
# cards
#
# A card is: one or more pictures, a set of options, the reason it looks the way
# it does, and a free-text box. Every card gets the text box — the options only
# cover the overrides we thought of, and "the smokestack is clipped" has to have
# somewhere to go.
# ---------------------------------------------------------------------------

def _cards_orientation(shoot, m):
    cards = []
    for name, rec in _frames(shoot, m):
        o = rec["orientation"]
        base = _uri(orientmod.rotate_bgr(_load_bgr(shoot / name),
                                         o.get("exif_angle", 0)), LONG)
        chosen = int(o.get("subject_angle") or 0)
        cards.append({
            "name": name,
            "chosen": str(chosen),
            "status": ("held" if o.get("needs_ask") else
                       "answered" if o.get("source") == "vision" else "ships"),
            "note": (f"camera {o.get('exif_angle', 0)}° baked in · "
                     + ("subject turn answered by hand" if o.get("source") == "vision"
                        else f"subject turn from {o.get('source')}")),
            "why": (o.get("notes") or [""])[0],
            "options": [{"label": f"+{a}", "value": str(a), "img": base, "spin": a}
                        for a in (0, 90, 180, 270)],
        })
    return cards


def _cards_crop(shoot, m):
    cards = []
    for name, rec in _frames(shoot, m):
        up = _upright(shoot, name, rec)
        crop = rec.get("crop") or {}
        opts = [{"label": "as shot", "value": "off", "img": _uri(up, LONG), "spin": 0}]
        if crop.get("applied") and crop.get("box"):
            x0, y0, x1, y1 = crop["box"]
            opts.insert(0, {"label": "cropped", "value": "on",
                            "img": _uri(up[y0:y1, x0:x1], LONG), "spin": 0})
        else:
            # No preview to show, but the refusal still has to be overridable —
            # a card you cannot argue with is not a review.
            opts.append({"label": "force a crop", "value": "on", "img": None, "spin": 0})
        cards.append({
            "name": name,
            "chosen": "on" if crop.get("applied") else "off",
            "status": "ships" if crop.get("applied") else "held",
            "note": ("crop proposed" if crop.get("applied")
                     else "no crop — " + (crop.get("reason") or "no reason recorded")),
            "why": (f"detectors agree {crop.get('agreement', 0):.0%}"
                    if crop.get("applied") else ""),
            "options": opts,
        })
    return cards


def _cards_color(shoot, m):
    cards, rendered = [], 0
    for name, rec in _frames(shoot, m):
        opts = [{"label": "as shot", "value": "__none__",
                 "img": _uri(_shipping(shoot, name, rec), LONG_DENSE), "spin": 0}]
        for p in colormod.PRESETS:
            entry = (rec.get("presets") or {}).get(p) or {}
            path = shoot / entry.get("path", "")
            if entry.get("path") and path.exists():
                opts.append({"label": p, "value": p,
                             "img": _uri(_load_bgr(path), LONG_DENSE), "spin": 0})
                rendered += 1
        plan = rec.get("color_plan") or {}
        cards.append({
            "name": name,
            "chosen": m.get("chosen_preset") or "__none__",
            "status": "ships" if plan.get("is_sweep", True) else "held",
            "note": (f"backdrop {plan.get('bg_class_effective', '?')} "
                     f"(luma {plan.get('bg_luma', 0):.0f})"
                     + ("" if plan.get("is_sweep", True)
                        else " · no studio backdrop, so it is left alone")),
            "why": f"spread {plan.get('bg_iqr', 0):.0f}",
            "options": opts,
        })
    return cards, rendered


BLURB = {
    "orientation": ("Every frame turned the way it will ship. Pick a different turn to "
                    "override it. Nothing was guessed — a frame with no readable text "
                    "gets no automatic answer, so it was looked at by hand."),
    "crop": ("How each frame will be framed. A frame that refuses a crop says why; "
             "those refusals are deliberate, and a detail macro with no studio behind "
             "it is supposed to refuse. You can force one anyway."),
    "color": ("One look ships for the whole shoot. Compare on any frame; the pick "
              "applies everywhere."),
}
FLAG = {"orientation": "--rotate", "crop": "--crop", "color": "--pick"}
SLUG = {"orientation": "o", "crop": "c", "color": "k"}


# ---------------------------------------------------------------------------
# markup
# ---------------------------------------------------------------------------

def _esc(v) -> str:
    return html.escape(str(v), quote=True)


def _card_html(stage: str, card: dict, idx: int) -> str:
    """One card.

    Each picture's bytes appear ONCE, as a CSS custom property on the card, and
    are then painted by the thumbnail, the option chip and the full-size preview
    alike. Writing the data URI at each of those three places instead pushed a
    fourteen-frame shoot to 15 MB, against a 16 MB ceiling — the colour stage,
    with three pictures per frame, would not have fit at all.
    """
    fid = f"{SLUG[stage]}{idx}"
    vars_, pics, opts = [], [], []
    for j, o in enumerate(card["options"]):
        on = o["value"] == card["chosen"]
        cls = "pick" + (" proposed" if on else "")
        spin = f"transform:rotate({o['spin']}deg);" if o.get("spin") else ""
        var = f"--p{j}"

        if o["img"]:
            vars_.append(f"{var}:url({o['img']})")
            pics.append(f'<div class="v" data-v="{_esc(o["value"])}" role="img"'
                        f' aria-label="{_esc(card["name"])} — {_esc(o["label"])}"'
                        f' style="background-image:var({var});{spin}"></div>')
            mini = f'<span class="mini" style="background-image:var({var});{spin}"></span>'
        else:
            pics.append(f'<div class="v nopic" data-v="{_esc(o["value"])}">'
                        f'No preview — PREP did not work out this framing.<br>'
                        f'Pick it and it gets rendered.</div>')
            mini = '<span class="mini nopic">?</span>'

        opts.append(
            f'<label class="opt"><input type="radio" name="{fid}" class="{cls}"'
            f' value="{_esc(o["value"])}"{" checked" if on else ""}'
            f' data-frame="{_esc(card["name"])}">'
            f'{mini}<span>{_esc(o["label"])}</span></label>')

    why = f' · {_esc(card["why"])}' if card["why"] else ""
    return f'''<div class="card" id="card_{fid}" style="{";".join(vars_)}">
  <div class="cap"><span class="nm">{_esc(card["name"])}</span>
    <span class="tag {card["status"]} t-auto">{card["status"]}</span>
    <span class="tag changed t-edit">changed</span></div>
  <a class="stage" href="#big_{fid}" title="Open full size">{"".join(pics)}
    <span class="zoom" aria-hidden="true">&#10530;</span></a>
  <div class="opts">{"".join(opts)}</div>
  <div class="note">{_esc(card["note"])}{why}</div>
  <label class="say"><span>Something else about this frame</span>
    <textarea rows="2" data-frame="{_esc(card["name"])}"
      placeholder="e.g. the smokestack is clipped, or crop tighter on the cab"></textarea></label>
  <div class="big" id="big_{fid}">
    <a class="shut" href="#card_{fid}">Close</a>
    <div class="bigwrap">{"".join(pics)}</div>
    <div class="bigcap">{_esc(card["name"])}</div>
  </div>
</div>'''


def _panel(stage: str, cards: list, ready: bool, locked: str, preview: bool,
           shoot: str) -> str:
    if not ready:
        return (f'<section class="panel" id="panel-{stage}">'
                f'<p class="lede">{_esc(BLURB[stage])}</p>'
                f'<div class="locked">{_esc(locked)}</div></section>')

    notice = ('<div class="notice"><b>Preview.</b> The stage before this one is not '
              'approved yet, so this is drawn from the current plan and will be redrawn '
              'if that changes. Answer the earlier tab first.</div>') if preview else ""
    body = "".join(_card_html(stage, c, i) for i, c in enumerate(cards))
    return f'''<section class="panel" id="panel-{stage}">
  <p class="lede">{_esc(BLURB[stage])}</p>
  {notice}
  <div class="grid">{body}</div>
  <div class="accept">
    <div class="acc-txt"><b>Happy with this stage?</b> Accept it as shown. Anything
      you changed above, and anything you typed in a box, rides along.</div>
    <a class="btn primary" href="#send-{stage}">Accept {stage}</a>
  </div>
  <div class="send" id="send-{stage}">
    <a class="shut" href="#panel-{stage}">Close</a>
    <div class="sendbox">
      <h2>Send this back</h2>
      <p>Copy this into the chat. It is the exact command, and any notes you typed
        come with it.</p>
      <pre class="cmd" data-stage="{stage}" data-flag="{FLAG[stage]}" data-shoot="{_esc(shoot)}"
>python -m lib.photo_prep.prep {_esc(shoot)} --approve-stage {stage}</pre>
      <button class="btn primary copy" type="button" data-for="{stage}">Copy</button>
      <p class="fine">If Copy does nothing, select the text above — the page works
        either way.</p>
    </div>
  </div>
</section>'''


def build(shoot: Path, out: Path) -> Path:
    m = load_manifest(shoot)
    st = stagemod.stage_state(m)
    approved = {s: bool(st[s]["approved"]) for s in stagemod.STAGES}
    sp = shoot.as_posix()

    color_cards, rendered = _cards_color(shoot, m)
    built = {
        "orientation": (True, _cards_orientation(shoot, m), ""),
        "crop": (True, _cards_crop(shoot, m), ""),
        "color": (rendered > 0, color_cards if rendered else [],
                  "The looks are not rendered yet. Approve crop, then run --apply, "
                  "and this tab fills with as shot / studio / punch side by side."),
    }

    first = next((s for s in stagemod.STAGES if not approved[s] and built[s][0]),
                 stagemod.STAGES[0])
    tabs, panels = [], []
    for i, s in enumerate(stagemod.STAGES):
        ready, cards, locked = built[s]
        word = "approved" if approved[s] else ("your call" if ready else "not yet")
        kind = "done" if approved[s] else ("wait" if ready else "lock")
        tabs.append(
            f'<label class="tab"><input type="radio" name="stage" class="tabin"'
            f' value="{s}"{" checked" if s == first else ""}>'
            f'<span class="n">{i + 1}</span><span>{s}</span>'
            f'<span class="st {kind}">{word}</span></label>')
        prev = stagemod.STAGES[i - 1] if i else None
        panels.append(_panel(s, cards, ready, locked,
                             bool(prev) and not approved[prev], sp))

    page = (TEMPLATE
            .replace("__SHOOT__", _esc(sp))
            .replace("__TABS__", "".join(tabs))
            .replace("__PANELS__", "".join(panels)))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out


TEMPLATE = r"""<title>Frame Check</title>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap">
<style>
:root{
  --ground:#EEEDEA; --surface:#FBFAF8; --sunk:#E4E2DD;
  --ink:#16171A; --muted:#6E7077; --rule:#D8D6D0;
  --ships:#1C7A4C; --changed:#B4690E; --held:#A44B54; --loupe:#1F6F8B;
  --shadow:0 1px 2px rgba(20,22,26,.08), 0 8px 24px -12px rgba(20,22,26,.18);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ground:#131416; --surface:#1B1D20; --sunk:#0E0F11;
    --ink:#ECEBE7; --muted:#94969D; --rule:#2C2F34;
    --ships:#4FBF86; --changed:#E0A040; --held:#D98C93; --loupe:#5FB3CE;
    --shadow:0 1px 2px rgba(0,0,0,.5), 0 8px 24px -12px rgba(0,0,0,.7);
  }
}
:root[data-theme="dark"]{
  --ground:#131416; --surface:#1B1D20; --sunk:#0E0F11;
  --ink:#ECEBE7; --muted:#94969D; --rule:#2C2F34;
  --ships:#4FBF86; --changed:#E0A040; --held:#D98C93; --loupe:#5FB3CE;
  --shadow:0 1px 2px rgba(0,0,0,.5), 0 8px 24px -12px rgba(0,0,0,.7);
}
*{box-sizing:border-box}
body{margin:0;background:var(--ground);color:var(--ink);
  font-family:Archivo,"Helvetica Neue",Arial,sans-serif;font-size:15px;line-height:1.5;
  -webkit-font-smoothing:antialiased}
h1{font-size:17px;font-weight:700;letter-spacing:-.01em;margin:0}

header{position:sticky;top:0;z-index:20;background:var(--surface);
  border-bottom:1px solid var(--rule);padding:12px 20px 0}
.hrow{max-width:1500px;margin:0 auto}
.shoot{font-size:12px;color:var(--muted);
  font-family:"JetBrains Mono",ui-monospace,Consolas,monospace}
.tabs{display:flex;gap:3px;max-width:1500px;margin:10px auto 0;flex-wrap:wrap}
.tab{display:flex;align-items:center;gap:7px;cursor:pointer;padding:9px 14px;
  font-size:11px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;
  color:var(--muted);border-bottom:2px solid transparent;border-radius:3px 3px 0 0;
  transition:background .12s ease,color .12s ease;-webkit-user-select:none;user-select:none}
.tab:hover{color:var(--ink);background:var(--sunk)}
.tab .n{font-family:"JetBrains Mono",monospace;font-size:10px;opacity:.65}
.tab .st{font-size:9.5px;padding:1px 6px;border-radius:2px;border:1px solid currentColor;
  letter-spacing:.05em}
.tab .st.done{color:var(--ships)}
.tab .st.wait{color:var(--changed)}
.tab .st.lock{color:var(--muted)}
.tabin{position:absolute;width:1px;height:1px;opacity:0;margin:0}
.tab:has(.tabin:checked){color:var(--ink);background:var(--ground);
  border-bottom-color:var(--loupe)}
.tab:focus-within{outline:2px solid var(--loupe);outline-offset:-2px}

main{max-width:1500px;margin:0 auto;padding:20px 20px 40px}
.panel{display:none}
body:has(.tabin[value="orientation"]:checked) #panel-orientation,
body:has(.tabin[value="crop"]:checked) #panel-crop,
body:has(.tabin[value="color"]:checked) #panel-color{display:block}

.lede{color:var(--muted);font-size:13.5px;max-width:70ch;margin:0 0 16px}
.notice{border:1px solid var(--changed);border-left-width:3px;border-radius:2px;
  padding:10px 13px;margin:0 0 18px;font-size:13px;background:var(--surface)}
.notice b{color:var(--changed)}
.locked{border:1px dashed var(--rule);border-radius:3px;padding:44px 24px;text-align:center;
  color:var(--muted);font-size:14px;max-width:62ch;margin:8px auto}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(290px,1fr))}

.card{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
  box-shadow:var(--shadow);overflow:hidden;display:flex;flex-direction:column;
  scroll-margin-top:120px}
.card:has(.pick:checked:not(.proposed)){border-color:var(--changed);
  box-shadow:0 0 0 1px var(--changed),var(--shadow)}
.cap{display:flex;align-items:center;gap:8px;padding:9px 11px;border-bottom:1px solid var(--rule)}
.nm{font-family:"JetBrains Mono",ui-monospace,Consolas,monospace;font-size:12px;font-weight:600}
.tag{margin-left:auto;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
  padding:2px 7px;border-radius:2px;border:1px solid currentColor}
.tag.ships{color:var(--ships)}
.tag.held{color:var(--held)}
.tag.answered{color:var(--loupe)}
.tag.changed{color:var(--changed)}
.t-edit{display:none}
.card:has(.pick:checked:not(.proposed)) .t-auto{display:none}
.card:has(.pick:checked:not(.proposed)) .t-edit{display:block}

.stage{aspect-ratio:1/1;background:var(--sunk);display:grid;place-items:center;padding:8px;
  position:relative;cursor:zoom-in;text-decoration:none}
.stage .v{width:100%;height:100%;display:none;background-size:contain;
  background-repeat:no-repeat;background-position:center}
.zoom{position:absolute;top:7px;right:7px;width:22px;height:22px;display:grid;
  place-items:center;font-size:13px;border-radius:2px;opacity:0;background:var(--surface);
  color:var(--ink);border:1px solid var(--rule);transition:opacity .15s ease}
.card:hover .zoom,.stage:focus .zoom{opacity:1}
.nopic{color:var(--muted);font-size:12px;text-align:center;padding:16px;line-height:1.45}

.opts{display:flex;gap:6px;padding:9px;border-top:1px solid var(--rule);flex-wrap:wrap}
.opt{flex:1 1 0;min-width:56px;border:1px solid var(--rule);border-radius:2px;padding:4px;
  cursor:pointer;color:var(--muted);font-size:10px;font-weight:600;letter-spacing:.04em;
  text-transform:uppercase;display:flex;flex-direction:column;gap:4px;align-items:center;
  -webkit-user-select:none;user-select:none;position:relative}
.opt:hover{border-color:var(--loupe);color:var(--loupe)}
.opt:focus-within{outline:2px solid var(--loupe);outline-offset:2px}
.opt input{position:absolute;width:1px;height:1px;opacity:0;margin:0}
.opt .mini{width:100%;aspect-ratio:1/1;background-color:var(--sunk);display:grid;
  place-items:center;overflow:hidden;background-size:contain;background-repeat:no-repeat;
  background-position:center}
.opt .mini.nopic{font-size:16px;padding:0}
.opt:has(input:checked){border-color:var(--ships);color:var(--ships);
  box-shadow:inset 0 0 0 1px var(--ships)}
.card:has(.pick:checked:not(.proposed)) .opt:has(input:checked){border-color:var(--changed);
  color:var(--changed);box-shadow:inset 0 0 0 1px var(--changed)}

.note{padding:8px 11px;font-size:11.5px;color:var(--muted);border-top:1px solid var(--rule)}
.say{display:block;padding:0 11px 11px}
.say span{display:block;font-size:10px;font-weight:600;letter-spacing:.06em;
  text-transform:uppercase;color:var(--muted);margin-bottom:4px}
.say textarea{width:100%;font:inherit;font-size:12px;color:var(--ink);background:var(--sunk);
  border:1px solid var(--rule);border-radius:2px;padding:6px 8px;resize:vertical}
.say textarea:focus{outline:2px solid var(--loupe);outline-offset:-1px}
.card:has(textarea:not(:placeholder-shown)){border-color:var(--changed)}

/* Which picture a card shows is decided here, from the checked radio. */
.card:has(.pick[value="0"]:checked) .v[data-v="0"],
.card:has(.pick[value="90"]:checked) .v[data-v="90"],
.card:has(.pick[value="180"]:checked) .v[data-v="180"],
.card:has(.pick[value="270"]:checked) .v[data-v="270"],
.card:has(.pick[value="on"]:checked) .v[data-v="on"],
.card:has(.pick[value="off"]:checked) .v[data-v="off"],
.card:has(.pick[value="__none__"]:checked) .v[data-v="__none__"],
.card:has(.pick[value="studio"]:checked) .v[data-v="studio"],
.card:has(.pick[value="punch"]:checked) .v[data-v="punch"]{display:block}

.big{position:fixed;inset:0;z-index:60;background:rgba(8,9,10,.94);display:none;
  grid-template-rows:auto 1fr auto;padding:14px 18px 20px;gap:10px}
.big:target{display:grid}
.bigwrap{display:grid;place-items:center;min-height:0}
.bigwrap .v{width:min(100%,86vh);aspect-ratio:1/1;display:none;background-size:contain;
  background-repeat:no-repeat;background-position:center}
.bigcap{text-align:center;color:#94969D;font-size:12px;
  font-family:"JetBrains Mono",ui-monospace,Consolas,monospace}
.shut{justify-self:end;color:#ECEBE7;text-decoration:none;border:1px solid #3A3D42;
  border-radius:2px;padding:7px 13px;font-size:12px;font-weight:600}
.shut:hover{border-color:#5FB3CE;color:#5FB3CE}

.accept{display:flex;gap:16px;align-items:center;flex-wrap:wrap;margin:22px 0 0;
  padding:16px 18px;background:var(--surface);border:1px solid var(--rule);border-radius:3px}
.acc-txt{font-size:13px;color:var(--muted);max-width:60ch}
.acc-txt b{color:var(--ink)}
.btn{display:inline-block;font:inherit;font-size:13px;font-weight:600;letter-spacing:.02em;
  padding:11px 20px;border-radius:2px;border:1px solid var(--ink);text-decoration:none;
  color:var(--ink);cursor:pointer;background:none;margin-left:auto}
.btn.primary{background:var(--ink);color:var(--surface)}
.btn:hover{border-color:var(--loupe);background:var(--loupe);color:#fff}
.btn:focus-visible{outline:2px solid var(--loupe);outline-offset:2px}

.send{position:fixed;inset:0;z-index:70;background:rgba(8,9,10,.94);display:none;
  grid-template-rows:auto 1fr;padding:14px 18px 20px}
.send:target{display:grid}
.sendbox{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
  padding:22px;max-width:70ch;margin:auto;width:100%}
.sendbox h2{margin:0 0 6px;font-size:16px}
.sendbox p{margin:0 0 14px;font-size:13px;color:var(--muted)}
.fine{margin:12px 0 0;font-size:11.5px}
.cmd{background:var(--sunk);border:1px solid var(--rule);border-radius:2px;padding:12px;
  font-family:"JetBrains Mono",ui-monospace,Consolas,monospace;font-size:12px;
  white-space:pre-wrap;word-break:break-word;margin:0 0 14px;color:var(--ink)}
.copy{margin-left:0}
</style>

<header>
  <div class="hrow">
    <h1>Frame Check</h1>
    <div class="shoot">__SHOOT__</div>
  </div>
  <div class="tabs">__TABS__</div>
</header>

<main>__PANELS__</main>

<script>
/* Everything above works with this block deleted: selection is radio inputs, the
   shown picture is a CSS :has() rule, the tabs are a radio group, the previews
   are :target. This adds one thing — writing the command out of what is on the
   page — and if it never runs, the page still reviews. */
(function(){
  function cmdFor(stage){
    var panel = document.getElementById("panel-" + stage);
    if (!panel) return "";
    var pre = panel.querySelector(".cmd");
    if (!pre) return "";
    var base = "python -m lib.photo_prep.prep " + pre.getAttribute("data-shoot") + " ";

    var over = [];
    var picked = panel.querySelectorAll(".pick:checked:not(.proposed)");
    for (var i = 0; i < picked.length; i++) {
      over.push(stage === "color" ? picked[i].value
                : picked[i].getAttribute("data-frame") + "=" + picked[i].value);
    }
    if (stage === "color" && over.length > 1) over = [over[0]];

    var said = [], boxes = panel.querySelectorAll("textarea");
    for (var j = 0; j < boxes.length; j++) {
      if (boxes[j].value.trim()) {
        said.push("# " + boxes[j].getAttribute("data-frame") + ": " + boxes[j].value.trim());
      }
    }

    var line = over.length
      ? base + pre.getAttribute("data-flag") + " " + over.join(" ")
      : base + "--approve-stage " + stage;
    return said.length ? said.join("\n") + "\n" + line : line;
  }

  function refresh(){
    var stages = ["orientation", "crop", "color"];
    for (var i = 0; i < stages.length; i++) {
      var panel = document.getElementById("panel-" + stages[i]);
      if (!panel) continue;
      var pre = panel.querySelector(".cmd");
      if (pre) pre.textContent = cmdFor(stages[i]);
    }
  }

  document.addEventListener("change", refresh);
  document.addEventListener("input", refresh);
  document.addEventListener("click", function(e){
    var b = e.target && e.target.closest && e.target.closest(".copy");
    if (!b) return;
    var txt = cmdFor(b.getAttribute("data-for"));
    var done = function(w){
      b.textContent = w;
      setTimeout(function(){ b.textContent = "Copy"; }, 1500);
    };
    if (navigator.clipboard) {
      navigator.clipboard.writeText(txt).then(function(){ done("Copied"); },
                                             function(){ done("Select it"); });
    } else { done("Select it"); }
  });
  refresh();
})();
</script>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("shoot", type=Path)
    ap.add_argument("-o", "--out", type=Path)
    a = ap.parse_args()
    out = a.out or (a.shoot / ".prep" / "review.html")
    p = build(a.shoot, out)
    print(f"{p}  ({p.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
