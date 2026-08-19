#!/usr/bin/env python3
"""Render the PREP review as an interactive console instead of a tall JPEG.

The JPEG contact sheets work, but a fourteen-frame shoot makes one 4,000px
tall, and a picture you have to scroll past in a viewer is not a surface you
can decide on. This renders the review as a page: three tabs in the order the
work has to happen, a card per frame, every option side by side, click the one
you want. It keeps a running list of the overrides and writes out the exact
command to hand back.

All three stages are built into one page so the tabs are navigable, but the
ordering still means something. Crop and colour are drawn from whatever the
manifest currently says, so opening them before orientation is signed off shows
a preview with a notice, not a decision — and the CLI still refuses to approve
out of order. Colour cannot be previewed at all until `--apply` has rendered
the looks, so that tab says what unlocks it rather than sitting there dead.

The page cannot talk to the CLI, and pretending otherwise would be worse than
not trying — so it ends at a copyable command rather than a fake Apply button.

Usage:
    python tools/prep_sheet_html.py <shoot> [-o out.html]
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2                                                   # noqa: E402

from lib.photo_prep import color as colormod                 # noqa: E402
from lib.photo_prep import orientation as orientmod          # noqa: E402
from lib.photo_prep import stages as stagemod                # noqa: E402
from lib.photo_prep.prep import _load_bgr, load_manifest     # noqa: E402

LONG = 560
QUALITY = 72


def _uri(img, long_edge=LONG) -> str:
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


def _camera_upright(shoot: Path, name: str, rec: dict):
    """EXIF baked, subject rotation NOT applied — the base the options turn."""
    return orientmod.rotate_bgr(_load_bgr(shoot / name),
                                rec["orientation"].get("exif_angle", 0))


def _shipping_frame(shoot: Path, name: str, rec: dict):
    up = orientmod.rotate_bgr(_load_bgr(shoot / name), rec["orientation"]["applied"])
    crop = rec.get("crop") or {}
    if crop.get("applied") and crop.get("box"):
        x0, y0, x1, y1 = crop["box"]
        up = up[y0:y1, x0:x1]
    return up


# ---------------------------------------------------------------------------
# per-stage cards
# ---------------------------------------------------------------------------

def _cards_orientation(shoot, m):
    cards = []
    for name, rec in _frames(shoot, m):
        o = rec["orientation"]
        cards.append({
            "name": name,
            "base": _uri(_camera_upright(shoot, name, rec)),
            "chosen": int(o.get("subject_angle") or 0),
            "proposed": int(o.get("subject_angle") or 0),
            "asked": o.get("source") == "vision",
            "blocked": bool(o.get("needs_ask")),
            "note": (f"camera {o.get('exif_angle', 0)}° baked in · "
                     + ("subject turn answered by hand" if o.get("source") == "vision"
                        else f"subject turn from {o.get('source')}")),
            "why": (o.get("notes") or [""])[0],
            "options": [{"label": f"+{a}", "value": a, "rotate": a}
                        for a in (0, 90, 180, 270)],
        })
    return cards


def _cards_crop(shoot, m):
    cards = []
    for name, rec in _frames(shoot, m):
        up = orientmod.rotate_bgr(_load_bgr(shoot / name), rec["orientation"]["applied"])
        crop = rec.get("crop") or {}
        boxed = up.copy()
        result = None
        if crop.get("applied") and crop.get("box"):
            x0, y0, x1, y1 = crop["box"]
            cv2.rectangle(boxed, (x0, y0), (x1, y1), (90, 220, 120),
                          max(3, int(0.004 * max(up.shape[:2]))))
            result = up[y0:y1, x0:x1]
        opts = [{"label": "as shot", "value": "off", "img": _uri(up)}]
        if result is not None:
            opts.insert(0, {"label": "cropped", "value": "on", "img": _uri(result)})
        cards.append({
            "name": name,
            "base": _uri(boxed),
            "chosen": "on" if crop.get("applied") else "off",
            "proposed": "on" if crop.get("applied") else "off",
            "asked": False,
            "blocked": not crop.get("applied"),
            "note": ("crop proposed — the green box is the new edge"
                     if crop.get("applied")
                     else "no crop — " + (crop.get("reason") or "no reason recorded")),
            "why": (f"detectors agree {crop.get('agreement', 0):.0%}"
                    if crop.get("applied") else ""),
            "options": opts,
        })
    return cards


def _cards_color(shoot, m):
    cards, rendered = [], 0
    presets = list(colormod.PRESETS)
    for name, rec in _frames(shoot, m):
        opts = [{"label": "as shot", "value": "__none__",
                 "img": _uri(_shipping_frame(shoot, name, rec))}]
        for p in presets:
            entry = (rec.get("presets") or {}).get(p) or {}
            path = shoot / entry.get("path", "")
            if entry.get("path") and path.exists():
                opts.append({"label": p, "value": p, "img": _uri(_load_bgr(path))})
                rendered += 1
        plan = rec.get("color_plan") or {}
        cards.append({
            "name": name,
            "base": opts[-1]["img"],
            "chosen": m.get("chosen_preset") or "__none__",
            "proposed": m.get("chosen_preset") or "__none__",
            "asked": False,
            "blocked": not plan.get("is_sweep", True),
            "note": (f"backdrop {plan.get('bg_class_effective', '?')} "
                     f"(luma {plan.get('bg_luma', 0):.0f})"
                     + ("" if plan.get("is_sweep", True)
                        else " · no studio backdrop, so it is left alone")),
            "why": f"spread {plan.get('bg_iqr', 0):.0f}",
            "options": opts,
        })
    return cards, rendered


BLURB = {
    "orientation": ("Every frame turned the way it will ship. Click a different turn to "
                    "override it. Nothing was guessed: a frame with no readable text gets "
                    "no automatic answer, so it was looked at by hand and marked answered."),
    "crop": ("The green box is the proposed framing, drawn on the already-upright frame. "
             "A frame that refuses a crop says why — those refusals are deliberate, and a "
             "detail macro with no studio behind it is supposed to refuse."),
    "color": ("One look ships for the whole shoot. Compare on any frame you like; picking "
              "anywhere sets it everywhere."),
}
FLAG = {"orientation": "--rotate", "crop": "--crop", "color": "--pick"}


def build(shoot: Path, out: Path) -> Path:
    m = load_manifest(shoot)
    st = stagemod.stage_state(m)
    approved = {s: bool(st[s]["approved"]) for s in stagemod.STAGES}

    color_cards, rendered = _cards_color(shoot, m)
    stages = {
        "orientation": {"ready": True, "cards": _cards_orientation(shoot, m)},
        "crop": {"ready": True, "cards": _cards_crop(shoot, m)},
        "color": {
            "ready": rendered > 0,
            "cards": color_cards if rendered else [],
            "locked": ("The looks are not rendered yet. Approve crop, then run "
                       "--apply, and this tab fills with as-shot / studio / punch "
                       "side by side."),
        },
    }
    for name, s in stages.items():
        s.update(stage=name, flag=FLAG[name], blurb=BLURB[name],
                 shootwide=(name == "color"),
                 preview=(not approved[stagemod.STAGES[stagemod.STAGES.index(name) - 1]]
                          if name != "orientation" else False))

    data = {"shoot": shoot.as_posix(), "name": shoot.name,
            "approved": approved, "stages": stages,
            "order": list(stagemod.STAGES)}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.replace("__DATA__", json.dumps(data)), encoding="utf-8")
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

header{position:sticky;top:0;z-index:20;background:var(--surface);
  border-bottom:1px solid var(--rule);padding:12px 20px 0}
.hrow{display:flex;flex-wrap:wrap;align-items:center;gap:14px;max-width:1500px;margin:0 auto}
h1{font-size:17px;font-weight:700;letter-spacing:-.01em;margin:0}
.shoot{font-size:12px;color:var(--muted);
  font-family:"JetBrains Mono",ui-monospace,Consolas,monospace}
.tabs{display:flex;gap:2px;max-width:1500px;margin:10px auto 0}
.tab{appearance:none;background:none;font:inherit;font-size:11px;font-weight:600;
  letter-spacing:.07em;text-transform:uppercase;color:var(--muted);cursor:pointer;
  padding:9px 14px;border:1px solid transparent;border-bottom:2px solid transparent;
  display:flex;align-items:center;gap:7px}
.tab:hover{color:var(--ink)}
.tab:focus-visible{outline:2px solid var(--loupe);outline-offset:-2px}
.tab[aria-selected="true"]{color:var(--ink);border-bottom-color:var(--loupe)}
.tab .n{font-family:"JetBrains Mono",monospace;font-size:10px;opacity:.65}
.tab .st{font-size:9.5px;padding:1px 6px;border-radius:2px;border:1px solid currentColor;
  letter-spacing:.05em}
.tab .st.done{color:var(--ships)}
.tab .st.wait{color:var(--changed)}
.tab .st.lock{color:var(--muted)}

main{max-width:1500px;margin:0 auto;padding:20px 20px 128px}
.lede{color:var(--muted);font-size:13.5px;max-width:70ch;margin:0 0 16px}
.notice{border:1px solid var(--changed);border-left-width:3px;border-radius:2px;
  padding:10px 13px;margin:0 0 18px;font-size:13px;color:var(--ink);background:var(--surface)}
.notice b{color:var(--changed)}
.locked{border:1px dashed var(--rule);border-radius:3px;padding:44px 24px;text-align:center;
  color:var(--muted);font-size:14px;max-width:62ch;margin:8px auto}
.grid{display:grid;gap:16px;grid-template-columns:repeat(auto-fill,minmax(290px,1fr))}

.card{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
  box-shadow:var(--shadow);overflow:hidden;display:flex;flex-direction:column}
.card.edited{border-color:var(--changed);box-shadow:0 0 0 1px var(--changed),var(--shadow)}
.cap{display:flex;align-items:center;gap:8px;padding:9px 11px;border-bottom:1px solid var(--rule)}
.nm{font-family:"JetBrains Mono",ui-monospace,Consolas,monospace;font-size:12px;font-weight:600}
.tag{margin-left:auto;font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;
  padding:2px 7px;border-radius:2px;border:1px solid currentColor}
.tag.ships{color:var(--ships)}
.tag.edited{color:var(--changed)}
.tag.held{color:var(--held)}
.tag.ask{color:var(--loupe)}

.stage{aspect-ratio:1/1;background:var(--sunk);display:grid;place-items:center;padding:8px}
.stage img{max-width:100%;max-height:100%;display:block;transition:transform .18s ease}
@media (prefers-reduced-motion:reduce){.stage img{transition:none}}

.opts{display:flex;gap:6px;padding:9px;border-top:1px solid var(--rule);flex-wrap:wrap}
.opt{flex:1 1 0;min-width:56px;background:none;border:1px solid var(--rule);border-radius:2px;
  padding:4px;cursor:pointer;color:var(--muted);font:inherit;font-size:10px;font-weight:600;
  letter-spacing:.04em;text-transform:uppercase;display:flex;flex-direction:column;gap:4px;
  align-items:center}
.opt .mini{width:100%;aspect-ratio:1/1;background:var(--sunk);display:grid;place-items:center;
  overflow:hidden}
.opt .mini img{max-width:100%;max-height:100%}
.opt:hover{border-color:var(--loupe);color:var(--loupe)}
.opt:focus-visible{outline:2px solid var(--loupe);outline-offset:2px}
.opt[aria-pressed="true"]{border-color:var(--ships);color:var(--ships);
  box-shadow:inset 0 0 0 1px var(--ships)}
.card.edited .opt[aria-pressed="true"]{border-color:var(--changed);color:var(--changed);
  box-shadow:inset 0 0 0 1px var(--changed)}

.note{padding:8px 11px 11px;font-size:11.5px;color:var(--muted);border-top:1px solid var(--rule)}

footer{position:fixed;left:0;right:0;bottom:0;z-index:30;background:var(--surface);
  border-top:1px solid var(--rule);padding:12px 20px}
.frow{max-width:1500px;margin:0 auto;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.count{font-size:12.5px;color:var(--muted);white-space:nowrap}
.count b{color:var(--changed);font-variant-numeric:tabular-nums}
.cmd{flex:1 1 380px;min-width:0;overflow-x:auto;background:var(--sunk);border:1px solid var(--rule);
  border-radius:2px;padding:9px 11px;font-family:"JetBrains Mono",ui-monospace,Consolas,monospace;
  font-size:11.5px;white-space:pre;color:var(--ink)}
button.act{font:inherit;font-size:12px;font-weight:600;letter-spacing:.03em;padding:9px 14px;
  border-radius:2px;border:1px solid var(--ink);background:var(--ink);color:var(--surface);
  cursor:pointer}
button.act.ghost{background:none;color:var(--ink)}
button.act:focus-visible{outline:2px solid var(--loupe);outline-offset:2px}
button.act[disabled]{opacity:.4;cursor:not-allowed}
</style>

<header>
  <div class="hrow">
    <div>
      <h1 id="title">Frame Check</h1>
      <div class="shoot" id="shoot"></div>
    </div>
  </div>
  <div class="tabs" id="tabs" role="tablist"></div>
</header>

<main>
  <div id="panel"></div>
</main>

<footer>
  <div class="frow">
    <div class="count" id="count"></div>
    <div class="cmd" id="cmd"></div>
    <button class="act ghost" id="reset">Reset</button>
    <button class="act" id="copy">Copy</button>
  </div>
</footer>

<script>
const D = __DATA__;
let active = D.order.find(s => !D.approved[s] && D.stages[s].ready) || D.order[0];

const picks = {};
const wide = {};
D.order.forEach(s => {
  picks[s] = new Map(D.stages[s].cards.map(c => [c.name, c.chosen]));
  wide[s] = D.stages[s].cards.length ? D.stages[s].cards[0].chosen : null;
});

document.getElementById("shoot").textContent = D.shoot;

function tabState(s){
  if (D.approved[s]) return ["done", "approved"];
  if (!D.stages[s].ready) return ["lock", "not yet"];
  return ["wait", "your call"];
}

function tabs(){
  document.getElementById("tabs").innerHTML = D.order.map((s, i) => {
    const [cls, word] = tabState(s);
    return '<button type="button" class="tab" role="tab" aria-selected="'
      + (s === active) + '" data-tab="' + s + '">'
      + '<span class="n">' + (i + 1) + '</span>' + s
      + '<span class="st ' + cls + '">' + word + '</span></button>';
  }).join("");
}

function go(s){ active = s; render(); window.scrollTo({top: 0}); }

function changes(s){
  const S = D.stages[s];
  if (!S.ready) return [];
  if (S.shootwide) {
    return (!S.cards.length || wide[s] === S.cards[0].proposed) ? [] : [wide[s]];
  }
  return S.cards.filter(c => picks[s].get(c.name) !== c.proposed)
                .map(c => c.name + "=" + picks[s].get(c.name));
}

function line(){
  const S = D.stages[active];
  const base = "python -m lib.photo_prep.prep " + D.shoot + " ";
  if (!S.ready) return base + "--apply";
  const ch = changes(active);
  if (ch.length) return base + S.flag + " " + ch.join(" ");
  return base + "--approve-stage " + active;
}

function cardHTML(S, c){
  const cur = S.shootwide ? wide[S.stage] : picks[S.stage].get(c.name);
  const edited = cur !== c.proposed;
  const opt = c.options.find(o => o.value === cur) || c.options[0];
  const spin = opt.rotate ? "transform:rotate(" + opt.rotate + "deg)" : "";
  const tag = edited ? '<span class="tag edited">changed</span>'
    : c.blocked ? '<span class="tag held">held</span>'
    : c.asked ? '<span class="tag ask">answered</span>'
    : '<span class="tag ships">ships</span>';
  const opts = c.options.map(o => {
    const ospin = o.rotate ? "transform:rotate(" + o.rotate + "deg)" : "";
    return '<button type="button" class="opt" aria-pressed="' + (o.value === cur) + '"'
      + " data-frame='" + esc(c.name) + "' data-value='" + esc(String(o.value)) + "'>"
      + '<span class="mini"><img src="' + (o.img || c.base) + '" alt="" style="' + ospin + '">'
      + '</span>' + o.label + '</button>';
  }).join("");
  // One option is not a choice. A frame that refuses a crop has nothing to pick
  // between, so it gets no button row at all — a lone control that does nothing
  // when clicked reads as broken, and this page has already been that once.
  const row = c.options.length > 1 ? '<div class="opts">' + opts + '</div>' : "";
  return '<div class="card ' + (edited ? "edited" : "") + '">'
    + '<div class="cap"><span class="nm">' + c.name + '</span>' + tag + '</div>'
    + '<div class="stage"><img src="' + (opt.img || c.base) + '" alt="' + c.name
    + '" style="' + spin + '"></div>'
    + row
    + '<div class="note">' + c.note + (c.why ? " · " + c.why : "") + '</div></div>';
}

function render(){
  tabs();
  const S = D.stages[active];
  let html = '<p class="lede">' + S.blurb + '</p>';
  if (!S.ready) {
    html += '<div class="locked">' + S.locked + '</div>';
  } else {
    if (S.preview) {
      html += '<div class="notice"><b>Preview.</b> The stage before this one is not '
        + 'approved yet, so these are drawn from the current plan and will be redrawn if '
        + 'that changes. Answer the earlier tab first.</div>';
    }
    html += '<div class="grid">' + S.cards.map(c => cardHTML(S, c)).join("") + '</div>';
  }
  document.getElementById("panel").innerHTML = html;
  bar();
}

function pick(name, value){
  const S = D.stages[active];
  if (S.shootwide) wide[active] = value; else picks[active].set(name, value);
  render();
}

function bar(){
  const S = D.stages[active];
  const n = changes(active).length;
  document.getElementById("count").innerHTML = !S.ready
    ? "nothing to decide here yet"
    : n ? "<b>" + n + "</b> override" + (n > 1 ? "s" : "") + " to send"
        : "nothing to change — " + S.cards.length + " frames as proposed";
  document.getElementById("cmd").textContent = line();
  document.getElementById("reset").disabled = !S.ready;
}

// Everything below is wired with addEventListener and event delegation rather
// than inline onclick attributes. Inline handlers resolve against the global
// scope, and this script does not necessarily run in it — under a module or a
// stricter CSP the page renders perfectly and every button silently does
// nothing, which is exactly how it failed the first time.
function esc(v){ return String(v).replace(/&/g, "&amp;").replace(/'/g, "&#39;")
  .replace(/</g, "&lt;").replace(/"/g, "&quot;"); }

document.addEventListener("click", e => {
  const tab = e.target.closest("[data-tab]");
  if (tab) { go(tab.dataset.tab); return; }

  const opt = e.target.closest("[data-frame]");
  if (opt) {
    const S = D.stages[active];
    const raw = opt.dataset.value;
    const val = S.stage === "orientation" ? Number(raw) : raw;
    pick(opt.dataset.frame, val);
    return;
  }

  if (e.target.closest("#copy")) {
    const b = document.getElementById("copy");
    const done = t => { b.textContent = t; setTimeout(() => { b.textContent = "Copy"; }, 1400); };
    if (navigator.clipboard) {
      navigator.clipboard.writeText(line()).then(() => done("Copied"), () => done("Select it"));
    } else { done("Select it"); }
    return;
  }

  if (e.target.closest("#reset")) {
    const S = D.stages[active];
    S.cards.forEach(c => picks[active].set(c.name, c.proposed));
    wide[active] = S.cards.length ? S.cards[0].proposed : null;
    render();
  }
});

// Left/right arrows move between tabs; the whole review is keyboard-reachable.
document.addEventListener("keydown", e => {
  if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
  if (e.target.closest("input, textarea")) return;
  const i = D.order.indexOf(active);
  const n = (i + (e.key === "ArrowRight" ? 1 : D.order.length - 1)) % D.order.length;
  go(D.order[n]);
});

render();
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
