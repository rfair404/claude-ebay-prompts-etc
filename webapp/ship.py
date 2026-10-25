"""webapp/ship.py — /ship/box: each box's size and weight, typed once at the
shipping station (green) and read back by the laptop. The seed of #311 slice
10's /ship; the rest of /ship grows in this file, not in a second router.

    GET  /ship/box   one row per open box from reports/shipping/ship_queue.json
                     (tools/pick_list.py writes it after each poll; the laptop
                     pushes it to green), inputs prefilled from the drafts
    POST /ship/box   {"store", "order_ids", "length_in", "width_in", "depth_in",
                      "weight_lb", "weight_oz", "confirm": true}: appends one
                     row to reports/shipping/boxes.csv (BOXES_COLUMNS)

There is no label API (pick_list.buy_shipping_label), so the shipper types the
same numbers into Seller Hub. The fields come in Seller Hub's order: length,
width, height (our depth), then lb and oz. The last row for a box wins, so a
typo is fixed by saving the box again.

No config, no eBay and no keys: green has none of them. The queue shows the
buyer only as "First L." plus city and state (#311's PII rule). A write
needs a loopback Origin and `confirm: true`; `_local_only` alone still lets a
request with no Origin through until #311 slice 1.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from tools.sales_report import STYLE, _e
from webapp.freight import LOCAL_HOSTS, PAGE_CSS, PII_HEADERS, _js, _local_only

REPO = Path(__file__).resolve().parent.parent
SHIP_DIR = REPO / "reports" / "shipping"
QUEUE_NAME = "ship_queue.json"     # tools/pick_list.py SHIP_QUEUE
BOXES_NAME = "boxes.csv"
BOXES_COLUMNS = ["recorded_at", "store", "order_ids", "length_in", "width_in",
                 "depth_in", "weight_lb", "weight_oz"]
FIELDS = BOXES_COLUMNS[3:]

router = APIRouter(dependencies=[Depends(_local_only)])

_ship_dir: Optional[Path] = None


def ship_dir() -> Path:
    return _ship_dir or SHIP_DIR


def set_ship_dir(path: Optional[Path]) -> None:
    """Test hook — a throwaway folder instead of reports/shipping/."""
    global _ship_dir
    _ship_dir = path


def _loopback_origin(request: Request) -> None:
    """A write must name a loopback page as its Origin. A browser's fetch()
    POST always sends one; a request without it is refused, not trusted."""
    if (urlparse(request.headers.get("origin") or "").hostname or "") not in LOCAL_HOSTS:
        raise HTTPException(403, "write refused: no loopback Origin")


def load_queue() -> list[dict]:
    try:
        return json.loads((ship_dir() / QUEUE_NAME).read_text(encoding="utf-8"))["boxes"]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def _key(store: str, order_ids) -> tuple[str, str]:
    return store, "+".join(order_ids)


def load_recorded() -> dict[tuple[str, str], dict]:
    """The last boxes.csv row for each box."""
    try:
        with (ship_dir() / BOXES_NAME).open(encoding="utf-8", newline="") as fh:
            return {(r["store"], r["order_ids"]): r for r in csv.DictReader(fh)}
    except (OSError, KeyError):
        return {}


def _num(v: float) -> str:
    return f"{v:g}"


class BoxIn(BaseModel):
    store: str
    order_ids: list[str]
    length_in: float = Field(gt=0, allow_inf_nan=False)
    width_in: float = Field(gt=0, allow_inf_nan=False)
    depth_in: float = Field(gt=0, allow_inf_nan=False)
    weight_lb: int = Field(ge=0)
    weight_oz: float = Field(ge=0, le=15.99, allow_inf_nan=False)
    confirm: bool = False


@router.post("/ship/box", dependencies=[Depends(_loopback_origin)])
def record_box(body: BoxIn) -> dict:
    if not body.confirm:
        raise HTTPException(400, "confirm required: nothing was written")
    if body.weight_lb == 0 and body.weight_oz == 0:
        raise HTTPException(400, "weight must be more than 0 lb 0 oz")
    store, order_ids = _key(body.store, body.order_ids)
    if not any(_key(b.get("store", ""), b.get("order_ids") or []) == (store, order_ids)
               for b in load_queue()):
        raise HTTPException(404, "no such box in the ship queue")
    row = [datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), store, order_ids,
           _num(body.length_in), _num(body.width_in), _num(body.depth_in),
           str(body.weight_lb), _num(body.weight_oz)]
    path = ship_dir() / BOXES_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(BOXES_COLUMNS)
        w.writerow(row)
    return {"recorded": dict(zip(BOXES_COLUMNS, row))}


SHIP_CSS = """
td input{width:5.5em}
tr.done td{background:color-mix(in srgb,var(--ok) 9%,transparent)}
"""

SHIP_JS = """
function say(el, text, err){ el.textContent=text; el.className='msg'+(err?' err':''); }
async function save(btn, i){
  const tr=btn.closest('tr'), m=tr.querySelector('.msg'), b=BOXES[i];
  const body={store:b.store, order_ids:b.order_ids, confirm:true};
  for(const f of FIELDS){ const v=tr.querySelector('[name='+f+']').value; body[f]=v===''?null:Number(v); }
  btn.disabled=true; say(m,'Saving…');
  try{
    const r=await fetch('/ship/box',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)});
    const j=await r.json().catch(()=>({}));
    if(!r.ok){ const d=j.detail; throw new Error(typeof d==='string'? d : Array.isArray(d)?
      d.map(e=>(e.loc||[]).slice(-1)[0]+': '+e.msg).join('; ') : r.statusText); }
    tr.classList.add('done'); say(m,'Recorded '+j.recorded.recorded_at.slice(11,16)+' UTC');
  }catch(e){ say(m,e.message,true); } finally{ btn.disabled=false; }
}
"""

HEADS = [("length_in", "Length (in)", "0.1"), ("width_in", "Width (in)", "0.1"),
         ("depth_in", "Height (in)", "0.1"), ("weight_lb", "lb", "1"),
         ("weight_oz", "oz", "0.01")]


def _row(i: int, box: dict, rec: Optional[dict]) -> str:
    vals = rec or (box.get("prefill") or {})
    who = ", ".join(p for p in (box.get("city"), box.get("state")) if p)
    cells = "".join(
        f'<td><input type="number" name="{f}" min="0" step="{step}" inputmode="decimal" '
        f'value="{_e(vals.get(f) if vals.get(f) is not None else "")}" '
        f'aria-label="{_e(label)}, box {i + 1}"></td>'
        for f, label, step in HEADS)
    status = f"Recorded {_e(rec['recorded_at'][:16].replace('T', ' '))} UTC" if rec else ""
    return (f'<tr class="{"done" if rec else ""}"><td>{_e(box.get("store"))}</td>'
            f'<td class="num">{"<br>".join(_e(o) for o in box.get("order_ids") or [])}</td>'
            f'<td>{_e(box.get("buyer"))}<br><span class="dim">{_e(who)}</span></td>{cells}'
            f'<td><button type="button" onclick="save(this,{i})">Save</button>'
            f'<div class="msg">{status}</div></td></tr>')


@router.get("/ship/box", response_class=HTMLResponse)
def box_page() -> HTMLResponse:
    boxes = load_queue()
    recorded = load_recorded()
    recs = [recorded.get(_key(b.get("store", ""), b.get("order_ids") or [])) for b in boxes]
    rows = [_row(i, b, rec) for i, (b, rec) in enumerate(zip(boxes, recs))]
    done = sum(1 for rec in recs if rec)
    if rows:
        heads = "".join(f"<th>{_e(label)}</th>" for _f, label, _s in HEADS)
        table = ('<div class="scroll"><table><thead><tr><th>Store</th><th>Orders</th>'
                 f'<th>Buyer</th>{heads}<th></th></tr></thead><tbody>'
                 + "".join(rows) + "</tbody></table></div>")
    else:
        table = ('<p class="dim">No boxes in the queue. The laptop writes '
                 f'reports/shipping/{QUEUE_NAME} after each pick-list poll.</p>')
    html = ('<!doctype html><html lang="en"><meta charset="utf-8">\n<title>Box sizes</title>\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<meta name="robots" content="noindex,nofollow">\n'
            f'<style>{STYLE}{PAGE_CSS}{SHIP_CSS}</style>\n<div class="wrap"><div class="card">'
            '<div class="hdr"><p class="eyebrow">Shipping station</p><h1>Box size and weight</h1>'
            f'<div class="ct">{done} of {len(rows)} recorded. Type the same numbers into '
            'Seller Hub, in this order.</div></div>'
            f'<div class="pad">{table}</div></div></div>\n'
            f'<script>const BOXES={_js(boxes)}, FIELDS={_js(FIELDS)};{SHIP_JS}</script>\n')
    return HTMLResponse(html, headers=PII_HEADERS)
