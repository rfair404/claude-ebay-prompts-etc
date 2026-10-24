#!/usr/bin/env python3
"""MEDIA CART PLANS — write the locked PVC media-cart build sheet to an HTML file.

The design is frozen at v7 (2026-10-15), the version published as the "PVC Media
Cart" artifact. The sheet has a finished render, side and front elevations, the
shopping list, the cut list with a stick plan, and a drawn panel for each of the
11 build steps. Every drawing is generated in the page from one 3D model, so the
file needs no other assets.

Locked design: 36 x 18" c-c frame of 1-1/4" Sch 40 PVC, sized for a 6'4"
operator. Shelves at 16" and 42", horizontal push bars at 41" on both ends,
10" flat-free wheels on a 5/8" axle at the back, 8" pneumatic swivel casters on
floor flanges at the front. A 1" steel mast, socketed at the bottom and bolted
between twin top crossbars, carries two swing arms: the camera points forward,
the monitor faces the pushing operator. 12" tile turntable on a 12" bearing,
11" from the mast.

    python tools/media_cart_plans.py                        # ./media_cart_plans.html
    python tools/media_cart_plans.py -o ~/cart.html --png   # also a full-page PNG

--png needs a Chromium-family browser on PATH (chromium, google-chrome). The
page loads its fonts from Google Fonts; offline, the browser substitutes its
own. To change the design, edit the model inside PAGE, then update PAGE_SHA256
and VERSION so the lock check passes again.
"""
import argparse
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VERSION = "v7, 2026-10-15"
SOURCE = "https://claude.ai/artifact/WJnfBt7qZYSf7gAwByEBp7"
PAGE_SHA256 = "df43378c969a9eb5dea3f830cba747d804a68f6d8af988bfc3d731294423674d"
PNG_SIZE = (1200, 6900)   # the full sheet at desktop width

PAGE = r'''<title>PVC Media Cart</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=Barlow:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: shop drawing, same system as the D5100 PVC Steadicam sheet — title block, hero render, two elevations, lists, then one drawn panel per build step. */
:root {
  --paper: #edf1f3; --panel: #f6f8f9; --ink: #1d2b38; --muted: #5d6c79; --rule: #c4ced6; --grid: #dde4e9;
  --pvc: #fdfefe; --steel: #8a96a1; --rubber: #2a2f35; --tireface: #3b424a; --cam: #e1e7eb; --lcd: #c6cfd6;
  --tile: #e8e2d6; --wire: #8e9aa5; --shelf: rgba(110,125,138,.10); --port: #dbe8d2;
  --accent: #cf4f1c; --accent-soft: #f7dccd; --batt: #f7dccd;
  --f-display: "Barlow Condensed", "Arial Narrow", sans-serif;
  --f-body: "Barlow", "Segoe UI", system-ui, sans-serif;
  --f-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --paper: #0d1f31; --panel: #11263b; --ink: #e2ecf4; --muted: #8fa5b8; --rule: #284663; --grid: #183350;
  --pvc: #1a334d; --steel: #6f8396; --rubber: #04080c; --tireface: #1a232c; --cam: #142c43; --lcd: #24425e;
  --tile: #3a4652; --wire: #6f8aa3; --shelf: rgba(140,170,200,.10); --port: #23432f;
  --accent: #ff8a4e; --accent-soft: #4b2b1c; --batt: #4b2b1c; color-scheme: dark } }
:root[data-theme="dark"] {
  --paper: #0d1f31; --panel: #11263b; --ink: #e2ecf4; --muted: #8fa5b8; --rule: #284663; --grid: #183350;
  --pvc: #1a334d; --steel: #6f8396; --rubber: #04080c; --tireface: #1a232c; --cam: #142c43; --lcd: #24425e;
  --tile: #3a4652; --wire: #6f8aa3; --shelf: rgba(140,170,200,.10); --port: #23432f;
  --accent: #ff8a4e; --accent-soft: #4b2b1c; --batt: #4b2b1c; color-scheme: dark }

* { box-sizing: border-box }
body { background: var(--paper); color: var(--ink); font: 400 16px/1.55 var(--f-body); }
.wrap { max-width: 1140px; margin: 0 auto; padding-inline: 20px; padding-block: 28px 64px; display: grid; gap: 40px }
h1, h2, h3 { font-family: var(--f-display); font-weight: 600; letter-spacing: .01em; text-wrap: balance; margin: 0; line-height: 1.1 }
h1 { font-size: clamp(34px, 6vw, 54px); font-weight: 700 }
h2 { font-size: 28px; display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap }
h2 .ref { font: 500 12px var(--f-mono); color: var(--accent); letter-spacing: .1em }
h3 { font-size: 20px }
p { margin: 0; max-width: 68ch }

.tb { border: 2px solid var(--ink); display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr) }
.tb-main { padding: 22px 24px; display: grid; gap: 12px; border-right: 1px solid var(--ink); align-content: start }
.tb-main .eyebrow { font: 500 12px var(--f-mono); letter-spacing: .14em; text-transform: uppercase; color: var(--muted) }
.tb-fields { display: grid; grid-template-columns: 1fr 1fr; margin: 0 }
.tb-fields div { padding: 10px 14px; border-bottom: 1px solid var(--rule); border-left: 1px solid var(--rule); min-width: 0 }
.tb-fields div:nth-child(odd) { border-left: 0 }
.tb-fields dt { font: 500 10.5px var(--f-mono); letter-spacing: .12em; text-transform: uppercase; color: var(--muted) }
.tb-fields dd { margin: 2px 0 0; font-weight: 600; font-size: 15px; line-height: 1.3 }
.tb-fields .wide { grid-column: 1 / -1; border-left: 0 }
@media (max-width: 760px) { .tb { grid-template-columns: 1fr } .tb-main { border-right: 0; border-bottom: 1px solid var(--ink) } }

.legend { display: flex; flex-wrap: wrap; gap: 8px 18px; font: 12px var(--f-mono); color: var(--muted) }
.legend i { display: inline-block; width: 18px; height: 11px; vertical-align: -1px; margin-right: 6px; border: 1px solid var(--ink) }
.legend .l-pvc { background: var(--pvc) } .legend .l-steel { background: var(--steel) } .legend .l-port { background: var(--port) }
.legend .l-rub { background: var(--rubber) } .legend .l-new { background: var(--accent-soft); border-color: var(--accent) }
.legend .l-env { border: 0; height: 0; border-top: 2px dashed var(--accent); width: 22px; vertical-align: 3px }

.sheet { margin: 0; background-color: var(--panel); border: 1px solid var(--rule); display: grid; grid-template-rows: auto 1fr; min-width: 0;
  background-image: linear-gradient(var(--grid) 1px, transparent 1px), linear-gradient(90deg, var(--grid) 1px, transparent 1px);
  background-size: var(--g, 16px) var(--g, 16px); }
.sheet header { display: flex; justify-content: space-between; gap: 12px; padding: 10px 14px; border-bottom: 1px solid var(--rule); background: var(--panel); flex-wrap: wrap }
.sheet header b { font: 600 18px var(--f-display) }
.sheet header span { font: 11px var(--f-mono); color: var(--muted); letter-spacing: .06em; text-transform: uppercase }
.sheet .draw { overflow-x: auto; padding: 6px }
.sheet svg { display: block; width: 100%; min-width: 420px; height: auto }
.sheets { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 20px }
@media (max-width: 900px) { .sheets { grid-template-columns: 1fr } }

/* svg parts */
line.o, line.f { fill: none; stroke-linecap: butt }
.pvc .o { stroke: var(--ink) } .pvc .f { stroke: var(--pvc) } .pvc .fb { fill: var(--pvc); stroke: var(--ink); stroke-width: .9 }
.port .o { stroke: var(--ink) } .port .f { stroke: var(--port) }
.steel .o { stroke: var(--ink) } .steel .f { stroke: var(--steel) }
.camp .o { stroke: var(--muted) } .camp .f { stroke: var(--cam) }
.bx-cam { fill: var(--cam); stroke: var(--muted); stroke-width: .8; stroke-linejoin: round }
.bx-steel { fill: var(--steel); stroke: var(--ink); stroke-width: .7; stroke-linejoin: round }
.bx-tile { fill: var(--tile); stroke: var(--ink); stroke-width: .8; stroke-linejoin: round }
.bx-batt { fill: var(--batt); stroke: var(--accent); stroke-width: 1.3; stroke-linejoin: round }
.bx-lcd { fill: var(--lcd); stroke: var(--muted); stroke-width: .6 }
.shade { fill: var(--ink); stroke: none }
.shelf { fill: var(--shelf); stroke: var(--wire); stroke-width: .9 }
.wire { stroke: var(--wire); stroke-width: .55 }
.bar { stroke: var(--wire); stroke-width: 1.1 }
.lip { fill: var(--shelf); stroke: var(--wire); stroke-width: .8 }
.tire { fill: var(--rubber); stroke: var(--rubber); stroke-width: .6 }
.tireface { fill: var(--tireface); stroke: var(--muted); stroke-width: .6 }
.hubc { fill: var(--steel); stroke: var(--ink); stroke-width: .6 }
.ghost { fill: none; stroke: var(--accent); stroke-width: 1; stroke-dasharray: 5 4 }
.sweep { fill: none; stroke: var(--accent); stroke-width: 1; stroke-dasharray: 2 4 }
.fig { fill: var(--cam); stroke: var(--muted); stroke-width: 1 }
.arm { fill: none; stroke: var(--muted); stroke-width: 9; stroke-linecap: round; stroke-linejoin: round }
.ground { stroke: var(--ink); stroke-width: 1.4 }
.dim { stroke: var(--muted); stroke-width: .8 }
.extl { stroke: var(--muted); stroke-width: .6; stroke-dasharray: 1 2 }
.sight { stroke: var(--accent); stroke-width: .9; stroke-dasharray: 4 3; fill: none }
.tickm { stroke: var(--muted); stroke-width: 1.4 }
.leader { fill: none; stroke: var(--ink); stroke-width: .6; opacity: .65 }
.dot { fill: var(--ink) }
.lbl { font: 600 12px var(--f-body); fill: var(--ink) }
.lbl2 { font: 400 11px var(--f-body); fill: var(--muted) }
.dimtxt { font: 400 10.5px var(--f-mono); fill: var(--muted) }
.arrow { stroke: var(--accent); stroke-width: 1.2; fill: none }
.arrhead { fill: var(--accent) }
/* new this step */
.hl .o, .hl.steel .o, .hl.camp .o { stroke: var(--accent) }
.hl .f, .hl.steel .f, .hl.camp .f { stroke: var(--accent-soft) }
.hl .fb { fill: var(--accent-soft); stroke: var(--accent) }
.hl .bx-cam, .hl .bx-steel, .hl .bx-tile, .hl .bx-lcd, .hl .bx-batt { stroke: var(--accent); stroke-width: 1.3 }
.hl .shelf, .hl .lip { stroke: var(--accent) } .hl .wire, .hl .bar { stroke: var(--accent) }
.hl .tire { stroke: var(--accent); stroke-width: 1.4 } .hl .tireface { stroke: var(--accent) } .hl .hubc { stroke: var(--accent) }
/* stick plan */
.stk { fill: var(--pvc); stroke: var(--ink); stroke-width: 1 }
.stk-w { fill: url(#waste); stroke: var(--rule); stroke-width: 1 }
.stk-t { font: 600 13px var(--f-display); fill: var(--ink) }
.stk-n { font: 400 10px var(--f-mono); fill: var(--muted) }
.wasteline { stroke: var(--rule); stroke-width: 1.2 }

.cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 32px; align-items: start }
@media (max-width: 820px) { .cols { grid-template-columns: 1fr } }
section { display: grid; gap: 16px; min-width: 0; align-content: start }
.tbl { overflow-x: auto }
table { border-collapse: collapse; width: 100%; font-size: 15px; font-variant-numeric: tabular-nums }
th { font: 500 11px var(--f-mono); letter-spacing: .1em; text-transform: uppercase; color: var(--muted); text-align: left; padding: 6px 10px 6px 0; border-bottom: 1.5px solid var(--ink) }
td { padding: 7px 10px 7px 0; border-bottom: 1px solid var(--rule); vertical-align: top }
td.n { font-family: var(--f-mono); font-size: 14px; white-space: nowrap }
td.k { font: 600 16px var(--f-display); color: var(--accent) }
tr.grp td { font: 600 15px var(--f-display); letter-spacing: .04em; text-transform: uppercase; color: var(--accent); padding-top: 14px; border-bottom: 0 }
.small { font-size: 14px; color: var(--muted) }
ul.fix { margin: 0; padding-left: 18px; display: grid; gap: 4px }
.addon { border: 1.5px dashed var(--muted); padding: 16px; display: grid; gap: 8px; align-content: start }
.addon .tag { font: 500 11px var(--f-mono); letter-spacing: .12em; text-transform: uppercase; color: var(--muted) }
.callout { border: 1.5px solid var(--accent); padding: 14px 16px; display: grid; gap: 8px; align-content: start }
.callout h3 { color: var(--accent) }

.stepgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr)); gap: 20px }
.step { grid-template-rows: auto 1fr auto }
.step header b .n { color: var(--accent); margin-right: 6px }
.step .draw svg { min-width: 0 }
.step figcaption { padding: 10px 14px 14px; font-size: 15px; line-height: 1.45; background: var(--panel); border-top: 1px solid var(--rule) }
</style>

<div class="wrap">

<header class="tb">
  <div class="tb-main">
    <div class="eyebrow">Shop drawing · mobile media cart · POC</div>
    <h1>PVC Media Cart</h1>
    <p>A push-or-pull cart for walking estate shows. Push from the back: the camera looks ahead and the monitor faces you, so you see what your audience sees. Both ride on swing arms on a center steel mast, so they turn for selfie shots or for pulling. A horizontal push bar at each end, air-tire swivel casters up front, and rails sized for clamp-on attachments later.</p>
  </div>
  <dl class="tb-fields">
    <div><dt>Footprint</dt><dd>≈ 52 × 28″ over bars and wheels</dd></div>
    <div><dt>Sized for</dt><dd>6′4″ operator, walking</dd></div>
    <div><dt>Top shelf</dt><dd>42″ (a kitchen counter is 36″)</dd></div>
    <div><dt>Push bars</dt><dd>Both ends, 41″, full width</dd></div>
    <div><dt>Camera / monitor</dt><dd>Faces forward / faces you</dd></div>
    <div><dt>Casters</dt><dd>8″ pneumatic swivel, front</dd></div>
    <div class="wide"><dt>Tools</dt><dd>PVC cutter or hacksaw · drill + ⅛, ¼, 5⁄16, ⅝″ bits · bolt cutters · 15/16″, ½″, 7/16″ wrenches · nut driver · tape · marker</dd></div>
  </dl>
</header>

<div class="legend">
  <span><i class="l-pvc"></i>PVC, Sch 40</span><span><i class="l-steel"></i>Steel</span><span><i class="l-rub"></i>Rubber</span>
  <span><i class="l-new"></i>New this step</span><span><i class="l-env"></i>Item envelope, arm swing</span><span>Inches · drawn to scale</span>
</div>

<figure class="sheet" style="--g:16px">
  <header><b>A · Finished cart</b><span>Set for pushing: camera ahead, screen to you</span></header>
  <div class="draw" id="hero"></div>
</figure>

<div class="sheets">
  <figure class="sheet" style="--g:14px">
    <header><b>B · Side elevation</b><span>Pushing · 6′4″ operator</span></header>
    <div class="draw" id="front"></div>
  </figure>
  <figure class="sheet" style="--g:14px">
    <header><b>C · Front elevation</b><span>Caster end · camera looking at you</span></header>
    <div class="draw" id="side"></div>
  </figure>
</div>

<div class="cols">
  <section>
    <h2>Shopping list <span class="ref">≈ $330–380, EST.</span></h2>
    <div class="tbl"><table>
      <thead><tr><th>Qty</th><th>Part</th><th>Use</th></tr></thead>
      <tbody>
        <tr class="grp"><td colspan="3">Plumbing aisle, Sch 40</td></tr>
        <tr><td class="n">4</td><td>1¼″ pipe, 10′ plain end</td><td>frame (fourth mostly spare)</td></tr>
        <tr><td class="n">21</td><td>1¼″ tee</td><td>joints, crossbars, collars</td></tr>
        <tr><td class="n">4</td><td>1¼″ 90° elbow</td><td>push-bar corners</td></tr>
        <tr><td class="n">2</td><td>1¼″ cap</td><td>back legs</td></tr>
        <tr><td class="n">2</td><td>1¼″ male adapter (MPT × socket)</td><td>front caster mounts</td></tr>
        <tr><td class="n">2</td><td>1¼″ galvanized floor flange</td><td>front caster mounts</td></tr>
        <tr><td class="n">2</td><td>1¼″ × 1″ reducer bushing</td><td>arm sockets</td></tr>
        <tr><td class="n">1</td><td>1″ pipe, 2′</td><td>arms, posts, spacers</td></tr>
        <tr><td class="n">2</td><td>1″ 90° elbow</td><td>pan joints</td></tr>
        <tr><td class="n">2</td><td>1″ cap</td><td>device studs</td></tr>
        <tr><td class="n">1</td><td>1″ galvanized steel pipe, 60″</td><td>mast</td></tr>
        <tr class="grp"><td colspan="3">Hardware</td></tr>
        <tr><td class="n">2</td><td>10″ flat-free hand-truck wheel, ⅝″ bore</td><td>back wheels</td></tr>
        <tr><td class="n">2</td><td>8″ pneumatic swivel plate caster</td><td>front, soaks up bumps</td></tr>
        <tr><td class="n">8</td><td>5⁄16″ × 1″ bolt + nylon lock nut + washer</td><td>casters to flanges</td></tr>
        <tr><td class="n">1</td><td>⅝″-11 threaded rod, 3′</td><td>axle</td></tr>
        <tr><td class="n">4 + 2</td><td>⅝″ hex nut + ⅝″ nylon lock nut</td><td>axle</td></tr>
        <tr><td class="n">8</td><td>⅝″ flat washer</td><td>axle</td></tr>
        <tr><td class="n">1 + 2 + 2</td><td>¼-20 threaded rod 1′, nylon lock nuts, fender washers</td><td>mast to crossbars</td></tr>
        <tr><td class="n">4</td><td>Hose clamp, small (to 1¾″)</td><td>arm stops, arm locks</td></tr>
        <tr><td class="n">2 sets</td><td>¼-20 × 1″ bolt, hex nut, wing nut, fender washer</td><td>device studs</td></tr>
        <tr><td class="n">1 box</td><td>#8 × ½″ self-tapping screws</td><td>one per socket</td></tr>
        <tr class="grp"><td colspan="3">Storage, flooring, misc</td></tr>
        <tr><td class="n">1</td><td>Ventilated wire shelf, 12″ × 8′</td><td>four shelf pieces</td></tr>
        <tr><td class="n">1</td><td>12″ lazy-susan bearing</td><td>turntable</td></tr>
        <tr><td class="n">1</td><td>12″ porcelain floor tile</td><td>turntable top</td></tr>
        <tr><td class="n">1</td><td>Two-part epoxy</td><td>tile to bearing</td></tr>
        <tr><td class="n">1 bag</td><td>8″ zip ties</td><td>shelves, bearing</td></tr>
        <tr><td class="n">1 roll</td><td>Grip or friction tape</td><td>push bars</td></tr>
        <tr class="grp"><td colspan="3">Optional</td></tr>
        <tr><td class="n">1</td><td>3″ steel corner brace + ¼-20 × ½″ bolt</td><td>portrait camera</td></tr>
      </tbody>
    </table></div>
    <p class="small">The monitor needs a ¼-20 tripod socket. Many USB-C portable monitors have one.</p>
  </section>

  <section>
    <h2>Cut list</h2>
    <div class="tbl"><table>
      <thead><tr><th></th><th>Part</th><th>Stock</th><th>Qty</th><th>Length</th></tr></thead>
      <tbody>
        <tr><td class="k">A</td><td>Bottom rail, end rail, top crossbar, push bar</td><td>1¼″</td><td class="n">10</td><td class="n">15½″</td></tr>
        <tr><td class="k">B</td><td>Leg</td><td>1¼″</td><td class="n">8</td><td class="n">10½″</td></tr>
        <tr><td class="k">C</td><td>Top rail, end piece</td><td>1¼″</td><td class="n">4</td><td class="n">12½″</td></tr>
        <tr><td class="k">D</td><td>Top rail middle, front-leg stub</td><td>1¼″</td><td class="n">4</td><td class="n">3½″</td></tr>
        <tr><td class="k">E</td><td>Bottom crossbar half</td><td>1¼″</td><td class="n">2</td><td class="n">6½″</td></tr>
        <tr><td class="k">F</td><td>Back-leg stub</td><td>1¼″</td><td class="n">2</td><td class="n">11″</td></tr>
        <tr><td class="k">G</td><td>Push arm</td><td>1¼″</td><td class="n">4</td><td class="n">4½″</td></tr>
        <tr><td class="k">H</td><td>Mast socket</td><td>1¼″</td><td class="n">1</td><td class="n">3″</td></tr>
        <tr><td class="k">K</td><td>Collar sleeve</td><td>1¼″</td><td class="n">4</td><td class="n">2″</td></tr>
        <tr><td class="k">M</td><td>Arm</td><td>1″</td><td class="n">2</td><td class="n">6½″</td></tr>
        <tr><td class="k">N</td><td>Post, mast spacer</td><td>1″</td><td class="n">4</td><td class="n">1½″</td></tr>
        <tr><td class="k">P</td><td>Shelf piece</td><td>wire 12″</td><td class="n">4</td><td class="n">19½″</td></tr>
      </tbody>
    </table></div>
    <p class="small">Cut = center-to-center − 2½″, which assumes each fitting takes 1¼″. Dry-fit one tee first and adjust if yours differ.</p>
    <figure class="sheet" style="--g:12px">
      <header><b>Stick plan</b><span>Four 10′ sticks of 1¼″, the fourth mostly spare</span></header>
      <div class="draw" id="sticks"></div>
    </figure>
  </section>
</div>

<section>
  <h2>Build <span class="ref">IN ORDER</span></h2>
  <div class="stepgrid" id="steps"></div>
</section>

<div class="cols">
  <section>
    <h2>Using it</h2>
    <ul class="fix">
      <li><b>Pushing</b> from the back bar: camera points ahead, monitor faces you and shows what the camera sees.</li>
      <li><b>On camera:</b> swing the camera arm back so the lens sits just above the monitor.</li>
      <li><b>Pulling</b> from the front bar: swing both arms to the front, around the item, not over it.</li>
      <li><b>Swing an arm:</b> loosen its lock clamp, turn it, retighten. <b>Height:</b> slide the stop clamp.</li>
      <li><b>Camera aim:</b> pan by turning the post, tilt by rolling the arm, portrait on the corner brace. Tilted straight down, the forward camera looks onto the turntable.</li>
      <li><b>Running the show:</b> laptop on the top shelf, program on the monitor. A wireless keypad with your OBS hotkeys clamps to the push bar.</li>
    </ul>
  </section>
  <div class="callout">
    <h3>Attachment points</h3>
    <p>The mast's top is 1″ NPT thread. A coupling there takes a light, a mic or, later, a camera head.</p>
    <p>Every rail is 1.66″ (42 mm) OD. That takes 1¼″ pipe U-bolts, hose clamps and photo "super clamps" anywhere along it. The spare stick is for building more mounts.</p>
  </div>
</div>

<div class="addon">
  <div class="tag">Later</div>
  <h3>Motors and robotics</h3>
  <p><b>Drive:</b> the rod and 10″ wheels come off. Hub motors clamp to a steel-angle bracket on the same two legs. Mount the vision camera and lidar to that bracket, not the PVC.</p>
  <p><b>Arms:</b> a belt around a collar and a small stepper turns each arm from the Pi. After that, a robot arm with a camera head replaces the upper collar on the mast.</p>
</div>

</div>

<script>
(() => {
const D2R = Math.PI / 180, OD = 1.66, HUB = 2.15, HUBL = 2.93, OD1 = 1.315, HUB1 = 1.7, HUBL1 = 2.3;
const L = 36, W = 18, YC = 9, ZB = 15, ZE = 28, ZW = 41, XM = 18, XT = [15, 21], PB = 7;
const ZMON = 56, ZCAM = 67, ARM = 9;
const X = [1,0,0], NX = [-1,0,0], Y = [0,1,0], NY = [0,-1,0], Z = [0,0,1], NZ = [0,0,-1];
const add = (a,b) => [a[0]+b[0], a[1]+b[1], a[2]+b[2]], mul = (a,k) => [a[0]*k, a[1]*k, a[2]*k];
const sub = (a,b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]], len = a => Math.hypot(a[0], a[1], a[2]);
const lerp = (a,b,t) => add(a, mul(sub(b,a), t));
const f = n => n.toFixed(1);
const pts = arr => arr.map(q => f(q[0]) + ',' + f(q[1])).join(' ');

/* ---------- model ---------- */
const M = [];
const P = o => (M.push(o), o);
const pipe = (a,b,step,cls='pvc',d=OD,bias=0) => P({t:'pipe', a, b, d, cls, step, bias});
const fit = (c,dirs,step,d=HUB,l=HUBL) => P({t:'fit', c, dirs, d, l, step});
const box = (a,b,step,cls,extra) => P({t:'box', a, b, cls, step, extra});
const wheel = (c,r,w,step) => P({t:'wheel', c, r, w, step});

// 8″ pneumatic swivel plate caster on a floor flange + male adapter; plate top at z0
function casterMount(x, y, step, add_) {
  const put = add_ || P, z0 = 9.75;
  put({t:'box', a:[x-2.25,y-2,z0-.3], b:[x+2.25,y+2,z0], cls:'steel', step});
  put({t:'box', a:[x-1.1,y-1.1,z0-1.1], b:[x+1.1,y+1.1,z0-.3], cls:'steel', step});
  put({t:'box', a:[x-2.2,y-1.75,4], b:[x-.4,y-1.5,z0-1.1], cls:'steel', step});
  put({t:'box', a:[x-2.2,y+1.5,4], b:[x-.4,y+1.75,z0-1.1], cls:'steel', step});
  put({t:'wheel', c:[x-1.3,y,4], r:4, w:2.6, step});
  put({t:'pipe', a:[x,y,z0], b:[x,y,z0+.45], d:4.2, cls:'steel', step, bias:0});
  put({t:'pipe', a:[x,y,z0+.45], b:[x,y,z0+2.5], d:2.1, cls:'pvc', step, bias:-.3});
}
// 4 · end frames: legs, end rail, push bar loop with port, feet
for (const x of [0, L]) {
  const inward = x === 0 ? X : NX, out = x === 0 ? NX : X, back = x === 0, bot = back ? 2.75 : 10.6, xb = x + (back ? -PB : PB);
  pipe([x,0,ZE],[x,W,ZE],4);
  for (const y of [0, W]) {
    const toMid = y === 0 ? Y : NY;
    pipe([x,y,bot],[x,y,ZW],4);
    fit([x,y,ZB],[Z,NZ,inward],4); fit([x,y,ZE],[Z,NZ,toMid],4); fit([x,y,ZW],[NZ,inward,out],4);
    if (back) pipe([x,y,bot+1.68],[x,y,bot-.3],4,'pvc',2.0,-.4); else casterMount(x,y,4);
    pipe([x,y,ZW],[xb,y,ZW],4); fit([xb,y,ZW],[mul(out,-1),toMid],4);
  }
  pipe([xb,0,ZW],[xb,W,ZW],4);
}
// 5 · rails, crossbars, mast socket
for (const y of [0, W]) {
  const toMid = y === 0 ? Y : NY;
  pipe([0,y,ZB],[L,y,ZB],5); fit([XM,y,ZB],[X,NX,toMid],5);
  pipe([0,y,ZW],[L,y,ZW],5); for (const xt of XT) fit([xt,y,ZW],[X,NX,toMid],5);
}
pipe([XM,0,ZB],[XM,W,ZB],5); fit([XM,YC,ZB],[Y,NY,Z],5);
pipe([XM,YC,ZB+1.25],[XM,YC,ZB+4.25],5,'pvc',OD,-.3);
for (const xt of XT) pipe([xt,0,ZW],[xt,W,ZW],5);
// 6 · axle + wheels
pipe([0,-5.6,5.2],[0,W+5.6,5.2],6,'steel',.625);
wheel([0,-3.3,5.2],5.2,3.4,6); wheel([0,W+3.3,5.2],5.2,3.4,6);
// 7 · shelves (wires run end to end, bars span the rails)
for (const [x0,x1] of [[2,14],[22,34]]) for (const z of [ZB+.88, ZW+.88]) P({t:'shelf', x0, x1, y0:-.75, y1:W+.75, z, step:7});
// 8 · mast
pipe([XM,YC,ZB-.6],[XM,YC,ZB+59.4],8,'steel',OD1);
pipe([XT[0]-1.4,YC,ZW],[XT[1]+1.4,YC,ZW],8,'steel',.25,-1.5);
for (const [a,b] of [[XT[0]+.83,XM-.66],[XM+.66,XT[1]-.83]]) pipe([a,YC,ZW],[b,YC,ZW],8,'pvc',OD1,-1);
// 9 · swing arms: monitor arm back (−x), camera arm forward (+x)
for (const [zc, dir] of [[ZMON, NX], [ZCAM, X]]) {
  const tip = [XM + dir[0]*ARM, YC, zc];
  pipe([XM,YC,zc-3.25],[XM,YC,zc+3.25],9,'pvc',OD,-.25);
  fit([XM,YC,zc],[Z,NZ,dir],9);
  pipe([XM,YC,zc-3.65],[XM,YC,zc-3.25],9,'steel',1.55,-.3);
  pipe([XM + dir[0]*1.6,YC,zc],tip,9,'pvc',OD1);
  fit(tip,[mul(dir,-1),Z],9,HUB1,HUBL1);
  pipe(tip,add(tip,[0,0,1.9]),9,'pvc',OD1);
  pipe(add(tip,[0,0,1.2]),add(tip,[0,0,2.5]),9,'pvc',1.6,-.3);
  pipe(add(tip,[0,0,2.5]),add(tip,[0,0,2.8]),9,'steel',.6);
  P({t:'sweep', c:[XM,YC,zc], r:ARM, step:9});
}
// 10 · devices: monitor faces back (−x), camera looks forward (+x)
const xm = XM - ARM, xc = XM + ARM;
box([xm-.3,YC-7,ZMON+2.8],[xm+.3,YC+7,ZMON+11.8],10,'cam',{face:NX, inset:.45, cls:'lcd'});
box([xc-1.55,YC-2.5,ZCAM+2.8],[xc+1.55,YC+2.5,ZCAM+6.6],10,'cam',{face:NX, inset:.5, cls:'lcd'});
box([xc-.8,YC-1,ZCAM+6.6],[xc+.8,YC+1,ZCAM+7.6],10,'cam');
pipe([xc+1.55,YC,ZCAM+4.7],[xc+4.75,YC,ZCAM+4.7],10,'camp',2.7);
// 11 · turntable + load out
const TC = 29, ZR = ZW + .95, ZT = ZR + 1.15;   // turntable center 11″ from the mast, bearing on the shelf
box([TC-6,YC-6,ZR],[TC+6,YC+6,ZR+.75],11,'steel');
box([TC-6,YC-6,ZR+.75],[TC+6,YC+6,ZT],11,'tile');
P({t:'ghost', a:[TC-6,YC-6,ZT], b:[TC+6,YC+6,ZT+12], step:11});
P({t:'sweep', c:[TC,YC,ZT], r:8.49, step:11});
box([2,4,ZB+.95],[13.5,11.5,ZB+8.45],11,'batt');
box([24,6,ZB+.95],[27.5,8.5,ZB+2.1],11,'cam');
box([2,4.5,ZW+.95],[15,13.5,ZW+1.6],11,'cam');
P({t:'poly', p:[[2,13.5,ZW+1.6],[15,13.5,ZW+1.6],[15,15.9,ZW+9.9],[2,15.9,ZW+9.9]], cls:'cam', step:11,
   inner:[[2.6,13.62,ZW+2.2],[14.4,13.62,ZW+2.2],[14.4,15.75,ZW+9.3],[2.6,15.75,ZW+9.3]]});

/* ---------- projection ---------- */
function view(aDeg, eDeg) {
  const a = aDeg*D2R, e = eDeg*D2R, ca = Math.cos(a), sa = Math.sin(a), ce = Math.cos(e), se = Math.sin(e);
  return {
    p: q => { const x1 = q[0]*ca - q[1]*sa, y1 = q[0]*sa + q[1]*ca; return [x1, -(q[2]*ce + y1*se), y1*ce - q[2]*se]; },
    nD: n => (n[0]*sa + n[1]*ca)*ce - n[2]*se
  };
}
function circlePts(c, r, axis, n=40) {
  const out = [];
  for (let i = 0; i < n; i++) { const t = i/n*2*Math.PI, u = r*Math.cos(t), v = r*Math.sin(t);
    out.push(axis === 'y' ? [c[0]+u, c[1], c[2]+v] : axis === 'z' ? [c[0]+u, c[1]+v, c[2]] : [c[0], c[1]+u, c[2]+v]); }
  return out;
}
function corners(a, b) { const c = []; for (let i = 0; i < 8; i++) c.push([i&1 ? b[0] : a[0], i&2 ? b[1] : a[1], i&4 ? b[2] : a[2]]); return c; }
function samplePts(pr) {
  switch (pr.t) {
    case 'pipe': return [pr.a, pr.b];
    case 'fit': return [pr.c];
    case 'box': case 'ghost': return corners(pr.a, pr.b);
    case 'wheel': return circlePts(add(pr.c,[0,-pr.w/2,0]), pr.r, 'y', 12).concat(circlePts(add(pr.c,[0,pr.w/2,0]), pr.r, 'y', 12));
    case 'shelf': return corners([pr.x0,pr.y0,pr.z-1],[pr.x1,pr.y1,pr.z]);
    case 'poly': return pr.p;
    case 'sweep': return circlePts(pr.c, pr.r, 'z', 12);
    case 'fig': return pr.p;
    default: return [];
  }
}
function hull(ps) {
  ps = ps.slice().sort((a,b) => a[0]-b[0] || a[1]-b[1]);
  const cr = (o,a,b) => (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0]); const lo = [], up = [];
  for (const p of ps) { while (lo.length >= 2 && cr(lo[lo.length-2], lo[lo.length-1], p) <= 0) lo.pop(); lo.push(p); }
  for (const p of ps.slice().reverse()) { while (up.length >= 2 && cr(up[up.length-2], up[up.length-1], p) <= 0) up.pop(); up.push(p); }
  lo.pop(); up.pop(); return lo.concat(up);
}
const FACES = [ [NX,[0,2,6,4]], [X,[1,3,7,5]], [NY,[0,1,5,4]], [Y,[2,3,7,6]], [NZ,[0,1,3,2]], [Z,[4,5,7,6]] ];
const FILL = {steel:'steel', camp:'cam', port:'port', pvc:'pvc'};

function draw(pr, T, S, V, hl) {
  const g = s => hl ? `<g class="hl">${s}</g>` : s;
  const out = [];
  if (pr.t === 'pipe') {
    const n = Math.max(1, Math.ceil(len(sub(pr.b, pr.a)) / 3)), w = pr.d*S;
    const A0 = T(pr.a), B0 = T(pr.b);
    if (Math.hypot(A0[0]-B0[0], A0[1]-B0[1]) < .8) {
      const C = T(lerp(pr.a, pr.b, .5));
      out.push({d: Math.min(A0[2], B0[2]) + pr.bias, s: g(`<g class="pvc"><circle cx="${f(C[0])}" cy="${f(C[1])}" r="${f(w/2)}" class="fb" style="fill:var(--${FILL[pr.cls]})"/></g>`)});
      return out;
    }
    for (let i = 0; i < n; i++) {
      const p0 = lerp(pr.a, pr.b, i/n), p1 = lerp(pr.a, pr.b, (i+1)/n), A = T(p0), B = T(p1), Mi = T(lerp(p0, p1, .5));
      const ln = `x1="${f(A[0])}" y1="${f(A[1])}" x2="${f(B[0])}" y2="${f(B[1])}"`;
      out.push({d: Mi[2] + pr.bias, s: g(`<g class="${pr.cls}"><line class="o" ${ln} stroke-width="${f(w+1.8)}"/><line class="f" ${ln} stroke-width="${f(w)}"/></g>`)});
    }
  } else if (pr.t === 'fit') {
    const C = T(pr.c), w = pr.d*S; let o = '', fl = '';
    for (const dir of pr.dirs) {
      const E = T(add(pr.c, mul(dir, pr.l)));
      if (Math.hypot(E[0]-C[0], E[1]-C[1]) < .8) continue;
      const ln = `x1="${f(C[0])}" y1="${f(C[1])}" x2="${f(E[0])}" y2="${f(E[1])}"`;
      o += `<line class="o" ${ln} stroke-width="${f(w+1.8)}"/>`; fl += `<line class="f" ${ln} stroke-width="${f(w)}"/>`;
    }
    out.push({d: C[2] - .6, s: g(`<g class="pvc"><circle class="fb" cx="${f(C[0])}" cy="${f(C[1])}" r="${f(w/2)}"/>${o}${fl}</g>`)});
  } else if (pr.t === 'box') {
    const c = corners(pr.a, pr.b).map(T); let s = '';
    for (const [n, idx] of FACES) {
      const nd = V.nD(n); if (nd > -1e-6) continue;
      const poly = pts(idx.map(i => c[i]));
      s += `<polygon class="bx-${pr.cls}" points="${poly}"/>`;
      if (n !== Z) s += `<polygon class="shade" fill-opacity="${n[2] ? .14 : (Math.abs(n[0]) ? .1 : .04)}" points="${poly}"/>`;
      if (pr.extra && pr.extra.face === n) {
        const k = pr.extra.inset, a = pr.a, b = pr.b;
        const q = n === NY ? [[a[0]+k,a[1]-.01,a[2]+k],[b[0]-k,a[1]-.01,a[2]+k],[b[0]-k,a[1]-.01,b[2]-k],[a[0]+k,a[1]-.01,b[2]-k]]
                           : [[a[0]-.01,a[1]+k,a[2]+k],[a[0]-.01,b[1]-k,a[2]+k],[a[0]-.01,b[1]-k,b[2]-k],[a[0]-.01,a[1]+k,b[2]-k]];
        s += `<polygon class="bx-${pr.extra.cls}" points="${pts(q.map(T))}"/>`;
      }
    }
    out.push({d: T(lerp(pr.a, pr.b, .5))[2], s: g(s)});
  } else if (pr.t === 'ghost') {
    const c = corners(pr.a, pr.b).map(T); let s = '';
    for (let i = 0; i < 8; i++) for (const bit of [1,2,4]) if (!(i & bit)) s += `<line class="ghost" x1="${f(c[i][0])}" y1="${f(c[i][1])}" x2="${f(c[i|bit][0])}" y2="${f(c[i|bit][1])}"/>`;
    out.push({d: -1e9, s});
  } else if (pr.t === 'sweep') {
    out.push({d: -1e9, s: `<polygon class="sweep" points="${pts(circlePts(pr.c, pr.r, 'z', 72).map(T))}"/>`});
  } else if (pr.t === 'wheel') {
    const f1 = add(pr.c, [0,-pr.w/2,0]), f2 = add(pr.c, [0,pr.w/2,0]);
    const P1 = circlePts(f1, pr.r, 'y').map(T), P2 = circlePts(f2, pr.r, 'y').map(T);
    const near = T(f1)[2] < T(f2)[2] ? f1 : f2, NP = near === f1 ? P1 : P2;
    const s = `<polygon class="tire" points="${pts(hull(P1.concat(P2)))}"/><polygon class="tireface" points="${pts(NP)}"/>` +
      `<polygon class="hubc" points="${pts(circlePts(near, pr.r*.42, 'y').map(T))}"/>`;
    out.push({d: T(pr.c)[2], s: g(s)});
  } else if (pr.t === 'shelf') {
    const {x0,x1,y0,y1,z} = pr; let s = `<polygon class="shelf" points="${pts([[x0,y0,z],[x1,y0,z],[x1,y1,z],[x0,y1,z]].map(T))}"/>`;
    for (let y = y0 + 1; y < y1; y += 1) { const A = T([x0,y,z]), B = T([x1,y,z]); s += `<line class="wire" x1="${f(A[0])}" y1="${f(A[1])}" x2="${f(B[0])}" y2="${f(B[1])}"/>`; }
    for (const x of [x0+.3, (x0+x1)/2, x1-.3]) { const A = T([x,y0,z]), B = T([x,y1,z]); s += `<line class="bar" x1="${f(A[0])}" y1="${f(A[1])}" x2="${f(B[0])}" y2="${f(B[1])}"/>`; }
    s += `<polygon class="lip" points="${pts([[x0,y0,z],[x0,y1,z],[x0,y1,z-1],[x0,y0,z-1]].map(T))}"/>`;
    out.push({d: T([(x0+x1)/2,(y0+y1)/2,z])[2] + .6, s: g(s)});
  } else if (pr.t === 'poly') {
    let s = `<polygon class="bx-${pr.cls}" points="${pts(pr.p.map(T))}"/>`;
    if (pr.inner) s += `<polygon class="bx-lcd" points="${pts(pr.inner.map(T))}"/>`;
    const c = pr.p.reduce((a,q) => add(a, mul(q, 1/pr.p.length)), [0,0,0]);
    out.push({d: T(c)[2], s: g(s)});
  } else if (pr.t === 'fig') {
    out.push({d: T(pr.at)[2], s: pr.render(T, S)});
  }
  return out;
}

const NS = 'http://www.w3.org/2000/svg';
const DEFS = `<defs><marker id="tick" viewBox="0 0 10 10" refX="5" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M2,8 L8,2" class="tickm"/></marker>
<marker id="arr" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" class="arrhead"/></marker></defs>`;

function scene(el, prims, V, o) {
  const bp = o.boundsPrims || prims; let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
  for (const pr of bp) for (const q of samplePts(pr)) { const s = V.p(q); x0 = Math.min(x0, s[0]); x1 = Math.max(x1, s[0]); y0 = Math.min(y0, s[1]); y1 = Math.max(y1, s[1]); }
  x0 -= 2; x1 += 2; y0 -= 2; y1 += 2;
  const S = Math.min((o.w - o.pl - o.pr) / (x1 - x0), (o.hmax - o.pt - o.pb) / (y1 - y0));
  const T = q => { const s = V.p(q); return [(s[0]-x0)*S + o.pl, (s[1]-y0)*S + o.pt, s[2]]; };
  const H = Math.round((y1 - y0)*S + o.pt + o.pb);
  let items = [];
  for (const pr of prims) items = items.concat(draw(pr, T, S, V, o.hl ? o.hl(pr) : false));
  items.sort((a,b) => b.d - a.d);
  let body = items.map(i => i.s).join('');
  if (o.ground !== false) { const g = T([0,0,0]); body = `<line class="ground" x1="${o.pl - 6}" y1="${f(g[1])}" x2="${o.w - o.pr + 6}" y2="${f(g[1])}"/>` + body; }
  if (o.extra) body += o.extra(T, S, H);
  if (o.labels) body += labels(o.labels, T, o, H);
  el.innerHTML = `<svg viewBox="0 0 ${o.w} ${H}" xmlns="${NS}" role="img" aria-label="${o.aria}">${DEFS}${body}</svg>`;
}

function labels(list, T, o, H) {
  const mid = (o.pl + o.w - o.pr) / 2, Ls = [], Rs = [];
  for (const it of list) { const A = T(it.at); const e = {...it, ax: A[0], ay: A[1], h: it.sub ? 34 : 22}; ((it.side || (A[0] < mid ? 'l' : 'r')) === 'l' ? Ls : Rs).push(e); }
  const place = (arr) => { arr.sort((a,b) => a.ay - b.ay); let y = 18; for (const e of arr) { e.ly = Math.max(e.ay, y); y = e.ly + e.h; }
    let last = H - 14; for (let i = arr.length - 1; i >= 0; i--) { arr[i].ly = Math.min(arr[i].ly, last - arr[i].h + 18); last = arr[i].ly - 18; } };
  place(Ls); place(Rs);
  let s = '';
  for (const [arr, side] of [[Ls,'l'],[Rs,'r']]) for (const e of arr) {
    const lx = side === 'l' ? o.pl - 14 : o.w - o.pr + 14, sgn = side === 'l' ? 1 : -1, anchor = side === 'l' ? 'end' : 'start';
    s += `<polyline class="leader" points="${f(e.ax)},${f(e.ay)} ${f(lx + sgn*9)},${f(e.ly)} ${f(lx + sgn*3)},${f(e.ly)}"/><circle class="dot" cx="${f(e.ax)}" cy="${f(e.ay)}" r="2"/>`;
    s += `<text class="lbl" x="${f(lx)}" y="${f(e.ly + 4)}" text-anchor="${anchor}">${e.t}</text>`;
    if (e.sub) s += `<text class="lbl2" x="${f(lx)}" y="${f(e.ly + 18)}" text-anchor="${anchor}">${e.sub}</text>`;
  }
  return s;
}

/* ---------- A · hero ---------- */
const ISO = view(32, 22);
const heroLabels = [
  {at:[xm-.3,YC-5,ZMON+9], t:'Monitor faces back', sub:'shows you the camera’s view', side:'l'},
  {at:[xc+4.75,YC,ZCAM+4.7], t:'Camera faces forward', sub:'swing it back to be on camera', side:'r'},
  {at:[XM,YC,ZB+58.5], t:'Mast, 1″ steel', sub:'bolted between the top crossbars', side:'r'},
  {at:[XM,YC-ARM,ZCAM], t:'Swing path', sub:'stop clamp sets height', side:'r'},
  {at:[-PB,2,ZW], t:'Push bar, 41″', sub:'back, big wheels', side:'l'},
  {at:[L+PB,W-2,ZW], t:'Pull bar, 41″', sub:'front, casters', side:'r'},
  {at:[6,4.5,ZW+1.4], t:'Laptop', sub:'top shelf 42″', side:'l'},
  {at:[TC+6,YC-6,ZT], t:'12″ tile turntable', sub:'12″ item clears the mast by 1½″', side:'r'},
  {at:[2,6,ZB+6], t:'Anker SOLIX C300 DC', sub:'bottom shelf 16″', side:'l'},
  {at:[0,-5,8.5], t:'10″ flat-free wheels', sub:'⅝″ threaded-rod axle', side:'l'},
  {at:[L-1.3,-1.3,2], t:'8″ air-tire casters', sub:'swivel, soak up bumps', side:'r'},
];
scene(document.getElementById('hero'), M, ISO, {w: 940, hmax: 800, pl: 200, pr: 210, pt: 14, pb: 14, aria: 'Finished cart, front left view', labels: heroLabels});

/* ---------- B · side elevation, pushing ---------- */
const FRONT = view(0, 0);
const pusher = {t:'fig', at:[-24,YC+8,40], p:[[-30,YC,0],[-16,YC,76]], render: (T, S) => {
  const P2 = (h,z) => T([h,YC+8,z]); const hd = P2(-23,71.7), r = 4.6*S;
  const rect = (h0,h1,z0,z1,rx) => { const a = P2(h0,z1), b = P2(h1,z0); return `<rect class="fig" x="${f(a[0])}" y="${f(a[1])}" width="${f(b[0]-a[0])}" height="${f(b[1]-a[1])}" rx="${f(rx*S)}"/>`; };
  let s = rect(-27,-22,0,37.5,1.5) + rect(-24.5,-20,0,36.5,1.5) + rect(-28,-19,36.5,63.5,3) + rect(-24.5,-21.5,63,67,.6);
  s += `<circle class="fig" cx="${f(hd[0])}" cy="${f(hd[1])}" r="${f(r)}"/>` + rect(-27.5,-17.5,0,1.6,.8);
  const sh = P2(-23,61), el = P2(-17.5,48), hn = P2(-PB-.6,ZW+.3);
  s += `<polyline class="arm" points="${f(sh[0])},${f(sh[1])} ${f(el[0])},${f(el[1])} ${f(hn[0])},${f(hn[1])}"/>`;
  return s; }};
scene(document.getElementById('front'), M.filter(p => p.t !== 'sweep').concat([pusher]), FRONT, {w: 560, hmax: 560, pl: 16, pr: 118, pt: 14, pb: 40, aria: 'Side elevation with a 6 foot 4 operator pushing',
  extra: (T) => {
    let s = '';
    const xl = T([-28,0,0])[0], tx = 560 - 118 + 14;
    const eye = T([-20.5,YC,71]), mon = T([xm-.3,YC,ZMON+7.3]), lens = T([xc+4.75,YC,ZCAM+4.7]), far = T([xc+15,YC,ZCAM+4.7]);
    s += `<line class="sight" x1="${f(eye[0])}" y1="${f(eye[1])}" x2="${f(mon[0])}" y2="${f(mon[1])}"/>`;
    s += `<line class="arrow" x1="${f(lens[0])}" y1="${f(lens[1])}" x2="${f(far[0])}" y2="${f(far[1])}" marker-end="url(#arr)"/>`;
    const marks = [[ZCAM+4.7,'lens ≈ 72″'],[71,'eye ≈ 71″'],[ZMON+7.3,'monitor ≈ 63″'],[ZW+.9,'shelf 42″'],[ZB+.9,'shelf 16″']];
    let lastY = -1e9;
    for (const [z, t] of marks) { const y = T([0,0,z])[1], ty = Math.max(y, lastY + 14); lastY = ty;
      s += `<line class="extl" x1="${f(xl)}" y1="${f(y)}" x2="${f(tx - 4)}" y2="${f(y)}"/><text class="dimtxt" x="${tx}" y="${f(ty + 4)}">${t}</text>`; }
    const c36 = T([36,0,0])[1], cy = T([0,0,36])[1];
    s += `<text class="dimtxt" x="${f(T([L+PB+3,0,0])[0])}" y="${f(cy + 4)}" style="opacity:.8"></text>`;
    const a = T([0,0,-3.5]), b = T([L,0,-3.5]);
    s += `<line class="dim" x1="${f(a[0])}" y1="${f(a[1])}" x2="${f(b[0])}" y2="${f(b[1])}" marker-start="url(#tick)" marker-end="url(#tick)"/>`;
    s += `<text class="dimtxt" x="${f((a[0]+b[0])/2)}" y="${f(a[1] + 14)}" text-anchor="middle">36″ c-c</text>`;
    return s;
  }});

/* ---------- C · front elevation (caster end) ---------- */
const SIDE = view(-90, 0);
scene(document.getElementById('side'), M.filter(p => p.t !== 'sweep'), SIDE, {w: 440, hmax: 560, pl: 30, pr: 30, pt: 14, pb: 54, aria: 'Front elevation with widths',
  extra: (T) => {
    const dim = (ya, yb, z, t, dy) => { const a = T([L,ya,z]), b = T([L,yb,z]);
      return `<line class="dim" x1="${f(a[0])}" y1="${f(a[1])}" x2="${f(b[0])}" y2="${f(b[1])}" marker-start="url(#tick)" marker-end="url(#tick)"/><text class="dimtxt" x="${f((a[0]+b[0])/2)}" y="${f(a[1] + dy)}" text-anchor="middle">${t}</text>`; };
    return dim(0, W, -3.5, '18″ c-c', 14) + dim(-5, W+5, -7.5, '≈ 28″ over wheels', 14);
  }});

/* ---------- stick plan ---------- */
const sticks = [['S1',[['A',15.5],['A',15.5],['A',15.5],['A',15.5],['A',15.5],['A',15.5],['A',15.5],['E',6.5]]],
                ['S2',[['A',15.5],['A',15.5],['A',15.5],['B',10.5],['B',10.5],['B',10.5],['B',10.5],['B',10.5],['B',10.5],['E',6.5]]],
                ['S3',[['B',10.5],['B',10.5],['C',12.5],['C',12.5],['C',12.5],['C',12.5],['F',11],['F',11],['G',4.5],['G',4.5],['G',4.5],['G',4.5],['H',3]]],
                ['S4',[['D',3.5],['D',3.5],['D',3.5],['D',3.5],['K',2],['K',2],['K',2],['K',2]]]];
const frac = l => String(l).replace('.5','½') + '″';
function stickSVG() {
  const w = 470, x0 = 34, k = (w - x0 - 8) / 120, rowH = 44;
  let s = `<defs><pattern id="waste" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" class="wasteline"/></pattern></defs>`;
  sticks.forEach(([name, parts], r) => {
    const y = 10 + r*rowH; let x = x0;
    s += `<text class="stk-t" x="4" y="${y + 19}">${name}</text>`;
    for (const [lt, l] of parts) { const pw = l*k;
      s += `<rect class="stk" x="${f(x)}" y="${y}" width="${f(pw - 1.5)}" height="26"/>`;
      s += `<text class="stk-t" x="${f(x + pw/2 - .7)}" y="${y + 17}" text-anchor="middle" style="font-size:${pw > 14 ? 13 : 9}px">${lt}</text>`;
      if (pw > 34) s += `<text class="stk-n" x="${f(x + pw/2)}" y="${y + 38}" text-anchor="middle">${frac(l)}</text>`;
      x += pw; }
    const rest = 120 - parts.reduce((a,p) => a + p[1], 0);
    s += `<rect class="stk-w" x="${f(x)}" y="${y}" width="${f(x0 + 120*k - x)}" height="26"/>`;
    if (rest > 12) s += `<text class="stk-n" x="${f(x + (x0 + 120*k - x)/2)}" y="${y + 38}" text-anchor="middle">${frac(rest)} spare</text>`;
  });
  return `<svg viewBox="0 0 ${w} ${10 + sticks.length*rowH + 4}" xmlns="${NS}" role="img" aria-label="Stick plan" style="min-width:0">${s}</svg>`;
}
document.getElementById('sticks').innerHTML = stickSVG();

/* ---------- build steps ---------- */
const steps = [
  {n:1, t:'Cut', c:'Cut per the stick plan. Mark each piece with its letter. Deburr. Keep the leftover stick for attachments.'},
  {n:2, t:'Turntable top', c:'Epoxy the tile’s unglazed back to the bearing’s top plate. Cure overnight.'},
  {n:3, t:'Front caster mounts', c:'Thread a male adapter into each floor flange. Bolt the caster plate under the flange; drill the flange to match if needed.'},
  {n:4, t:'End frames and push bars', c:'Each end: two legs, end rail at 28″. Top tee on each leg lies flat: rail socket in, push arm out. Two elbows and the bar close it. Caster mounts on the front, caps on the back.'},
  {n:5, t:'Rails and crossbars', c:'Join the ends at 15″ and 41″. Bottom crossbar at 18″ with the mast-socket tee, branch up, 3″ stub glued in. Top crossbars at 15″ and 21″, one each side of the mast line. Square it, then one screw per socket.'},
  {n:6, t:'Axle and wheels', c:'Prop the back end level. Drill ⅝″ through both back stubs at the wheel’s radius. Nut both sides of each leg, then washer, wheel, washer, lock nut. Trim the rod.'},
  {n:7, t:'Shelves', c:'Four 19½″ pieces, bars spanning the rails, one each side of the mast, top and bottom. Zip-tie them down.'},
  {n:8, t:'Mast', c:'Drop the steel pipe into the socket. Drill ¼″ through both top crossbars and the mast in one line. Rod through crossbar, 1″ spacer, mast, spacer, crossbar; washer and lock nut each end.'},
  {n:9, t:'Swing arms', c:'Each collar: a tee with two 2″ sleeves glued in its run; slit one sleeve 1½″. Stop clamp, collar, lock clamp. Lower arm points back, upper arm forward. Arm, elbow, post and stud cap go on dry.'},
  {n:10, t:'Monitor and camera', c:'Monitor on the back stud, screen toward the push bar. Camera on the front stud, lens forward. Max 3⁄16″ of thread above the washer.'},
  {n:11, t:'Turntable and load out', c:'Zip-tie the bearing’s bottom plate to the front top shelf, centered 11″ from the mast. C300 and Pi below. Laptop on the back top shelf.'},
];
const grid = document.getElementById('steps');
for (const st of steps) {
  const fg = document.createElement('figure'); fg.className = 'sheet step'; fg.style.setProperty('--g', '12px');
  fg.innerHTML = `<header><b><span class="n">${st.n}</span>${st.t}</b></header><div class="draw"></div><figcaption>${st.c}</figcaption>`;
  grid.appendChild(fg);
  const el = fg.querySelector('.draw');
  if (st.n === 1) { el.innerHTML = stickSVG(); continue; }
  if (st.n === 2) {
    const tt = [ {t:'box', a:[0,0,3.5], b:[12,12,4.25], cls:'steel', step:2}, {t:'box', a:[0,0,7.5], b:[12,12,7.9], cls:'tile', step:2} ];
    scene(el, tt, ISO, {w: 360, hmax: 250, pl: 60, pr: 90, pt: 16, pb: 16, ground: false, hl: () => true, aria: 'Tile and bearing',
      extra: (T) => { const ar = (z0,z1) => { const a = T([6,6,z0]), b = T([6,6,z1]); return `<line class="arrow" x1="${f(a[0])}" y1="${f(a[1])}" x2="${f(b[0])}" y2="${f(b[1])}" marker-end="url(#arr)"/>`; };
        const t = (q,s) => { const p = T(q); return `<text class="lbl2" x="${f(p[0] + 10)}" y="${f(p[1] + 4)}">${s}</text>`; };
        return ar(7.1,4.6) + t([12,0,7.7],'12″ tile, glaze up') + t([12,0,3.9],'12″ bearing'); }});
    continue;
  }
  if (st.n === 3) {
    const cc = [];
    casterMount(0, 0, 3, o => cc.push(o));
    cc.push({t:'pipe', a:[0,0,13.6], b:[0,0,17.5], d:OD, cls:'pvc', step:0, bias:0});
    scene(el, cc, ISO, {w: 360, hmax: 250, pl: 100, pr: 120, pt: 14, pb: 10, hl: p => p.step === 3, aria: 'Caster mount',
      extra: (T) => { let last = -1e9; const r = (q,t) => { const p = T(q), y = Math.max(p[1] + 4, last + 14); last = y; return `<text class="lbl2" x="${f(p[0] + 40)}" y="${f(y)}">${t}</text>`; };
        return r([0,0,16],'leg stub') + r([0,0,11.4],'male adapter') + r([0,0,9.9],'floor flange') + r([0,0,8.6],'caster plate, 4 bolts') + r([0,0,3],'8″ air tire'); }});
    continue;
  }
  scene(el, M.filter(p => p.step <= st.n), ISO, {w: 360, hmax: 400, pl: 10, pr: 10, pt: 10, pb: 10, boundsPrims: M, hl: p => p.step === st.n, aria: 'Build step ' + st.n});
}
})();
</script>
'''


def write_html(out: Path) -> None:
    head = ('<!doctype html>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n')
    out.write_text(head + PAGE, encoding="utf-8")


def write_png(html: Path, png: Path) -> bool:
    browser = next((b for b in ("chromium", "chromium-browser", "google-chrome") if shutil.which(b)), None)
    if not browser:
        print("no Chromium-family browser on PATH; skipped the PNG", file=sys.stderr)
        return False
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--user-data-dir={profile}", f"--window-size={PNG_SIZE[0]},{PNG_SIZE[1]}",
                        "--virtual-time-budget=5000", f"--screenshot={png}", html.resolve().as_uri()],
                       check=False, capture_output=True)
    return png.exists()


def main() -> int:
    ap = argparse.ArgumentParser(description="Write the locked PVC media-cart plans.")
    ap.add_argument("-o", "--out", type=Path, default=Path("media_cart_plans.html"), help="HTML file to write")
    ap.add_argument("--png", action="store_true", help="also render a full-page PNG next to the HTML")
    args = ap.parse_args()

    if hashlib.sha256(PAGE.encode("utf-8")).hexdigest() != PAGE_SHA256:
        print("PAGE no longer matches the locked design; update PAGE_SHA256 and VERSION if the change is intended",
              file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    write_html(args.out)
    print(f"wrote {args.out} (media cart plans {VERSION})")
    if args.png:
        png = args.out.with_suffix(".png")
        if write_png(args.out, png):
            print(f"wrote {png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
