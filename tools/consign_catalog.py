"""consign catalog — a browsable HTML catalog of one consignment ledger.

    python -m lib.cli consign catalog CONSIGN-20261022

Writes reports/consign/<intake>-catalog.html and a <intake>-thumbs/ folder
beside it. Read-only on the ledger. Full-size photos are linked by file://
path, so the page works on this machine only. It shows the client code
(K01), never the client's name or contact.

Photos are split into individual shots (the item alone) and bin / group shots
(other items in frame). A frame in the row's `shots` column is individual. Any
other frame is a group shot when it sits in a bin-NN folder or _review/, or
is on two or more rows; otherwise it is individual. The hero is an
individual shot when there is one.
"""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path

import consign as C

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}       # what Pillow thumbnails here
SKIP_DIRS = {"_backups", ".history", "presets", ".prep"}
GALLERY_CAP = {"single": 16, "bin": 8}


def index_photos(inventory: Path, pid_re: re.Pattern) -> dict[str, Path]:
    """Photo id -> the best file for it under inventory/: the camera original
    (TG7_<id>) first, then any other copy, then a _review crop."""
    best: dict[str, tuple[int, Path]] = {}
    for root, dirs, files in os.walk(inventory, followlinks=True):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        review = "_review" in Path(root).parts
        for f in files:
            if Path(f).suffix.lower() not in IMG_EXT:
                continue
            for m in pid_re.finditer(f):
                pid = m.group(0)
                rank = 2 if review else 0 if f.upper().startswith("TG7_" + pid) else 1
                if pid not in best or rank < best[pid][0]:
                    best[pid] = (rank, Path(root) / f)
    return {k: v[1] for k, v in best.items()}


def draft_title(repo: Path, folder: str) -> str:
    d = repo / folder / "draft.md" if folder else None
    if not d or not d.is_file():
        return ""
    for line in d.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r'^title:\s*"?(.*?)"?\s*$', line)
        if m and m.group(1):
            return m.group(1)
    return ""


def is_group(pid: str, path: Path, rows: list[dict]) -> bool:
    if "_review" in path.parts or re.fullmatch(r"bin-\d+", path.parent.name):
        return True
    return sum(pid in r["photos"].split() for r in rows) > 1


def thumb(src: Path, dst_dir: Path, name: str, size: int = 900) -> str:
    """A JPEG thumbnail, remade only when the source is newer. '' if the
    source can't be read as an image."""
    from PIL import Image, ImageOps
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{name}.jpg"
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        try:
            with Image.open(src) as im:
                small = ImageOps.exif_transpose(im).convert("RGB")
                small.thumbnail((size, size))
        except OSError:
            return ""
        small.save(dst, "JPEG", quality=78)
    return f"{dst_dir.name}/{dst.name}"


def _num(v: str):
    try:
        return float(v) if v else None
    except ValueError:
        return None


def build(d: Path) -> int:
    """Write the catalog for ledger folder `d`."""
    path = d / C.LEDGER_NAME
    if not path.exists():
        raise SystemExit(f"[X] no {C.LEDGER_NAME} in {d}")
    code = C.intake_client(d)
    rows, _ = C.read_ledger(path)
    C.migrate(rows, d, code)                     # in memory only: ids
    out_dir = C.REPO / "reports" / "consign"
    thumbs = out_dir / f"{d.name}-thumbs"
    out = out_dir / f"{d.name}-catalog.html"
    photos = index_photos(C.INVENTORY, C.PHOTO_ID_RE)
    items = []
    for r in rows:
        num = r["item_id"].split("-")[-1]
        ids = r["photos"].split()
        own = C.REPO / r["inventory_folder"] if r["inventory_folder"] else None
        if own and own.is_dir():                 # frames in its own folder count too
            for f in sorted(own.iterdir()):
                m = C.PHOTO_ID_RE.search(f.name)
                if m and f.suffix.lower() in IMG_EXT and m.group(0) not in ids:
                    ids.append(m.group(0))
                    photos.setdefault(m.group(0), f)
        shots = set(r["shots"].split())
        singles, group = [], []
        for pid in ids:
            if pid in photos:
                p = photos[pid]
                single = pid in shots or not is_group(pid, p, rows)
                (singles if single else group).append((pid, p))
        singles.sort(key=lambda t: t[0])
        gallery = []
        for kind, lst in (("single", singles), ("bin", group)):
            for pid, p in lst[:GALLERY_CAP[kind]]:
                t = thumb(p, thumbs, f"{num}-{pid}")
                if t:
                    gallery.append({"thumb": t, "full": p.resolve().as_uri(),
                                    "kind": kind, "pid": pid})
        items.append({
            "num": num, "id": r["item_id"],
            "title": draft_title(C.REPO, r["inventory_folder"]),
            "desc": r["description"], "lo": _num(r["est_low"]), "hi": _num(r["est_high"]),
            "ask": r["ask"], "state": r["state"] or "INTAKE",
            "note": C.hand_note(r["state_note"]),
            "bin": r["bin"], "cut": r["cut"], "why": r["why"], "qty": r["qty"],
            "lot": r["lot_id"], "store": r["store"], "src": r["est_source"],
            "photos": gallery, "nphotos": len(ids),
            "nsingle": len(singles), "ngroup": len(group),
        })
    data = json.dumps(items).replace("</", "<\\/")
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.replace("__INTAKE__", html.escape(d.name))
                   .replace("__CLIENT__", html.escape(code or "unassigned"))
                   .replace("__DATA__", data), encoding="utf-8")
    single = sum(1 for i in items if i["nsingle"])
    priced = sum(1 for i in items if i["hi"] is not None)
    print(f"[OK] wrote {out}")
    print(f"  {len(items)} item(s): {single} with individual shots, {len(items) - single} "
          f"bin shot only, {priced} with an estimate")
    return 0


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Consignment Catalog</title>
<style>
:root{--bg:#f6f4ef;--card:#fff;--ink:#1d1c1a;--muted:#6b675f;--line:#e2ddd2;--accent:#8a5a2b;--chip:#efe9dd}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161513;--card:#211f1c;--ink:#ece8e0;--muted:#a39d92;--line:#35322c;--accent:#d9a066;--chip:#2c2925}}
:root[data-theme="dark"]{--bg:#161513;--card:#211f1c;--ink:#ece8e0;--muted:#a39d92;--line:#35322c;--accent:#d9a066;--chip:#2c2925}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 system-ui,-apple-system,Segoe UI,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:12px 16px}
h1{font-size:18px;margin:0 0 8px}h1 small{color:var(--muted);font-weight:400}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.bar input,.bar select{font:inherit;padding:6px 8px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
.bar input{flex:1 1 220px}.stats{color:var(--muted);font-size:13px;margin-top:6px}
main{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px;padding:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;cursor:pointer;display:flex;flex-direction:column;text-align:left;font:inherit;color:inherit;padding:0}
.card:hover,.card:focus-visible{border-color:var(--accent)}
.thumb{aspect-ratio:1;background:var(--chip);display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:13px;position:relative;width:100%}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
.badge{position:absolute;left:6px;top:6px;font-size:11px;padding:2px 7px;border-radius:999px;background:rgba(0,0,0,.65);color:#fff}
.badge.bin{background:#8a4a12}
.dlg h3{font-size:14px;margin:14px 0 6px}.dlg h3 small,.none{color:var(--muted);font-weight:400;font-size:13px}
.body{padding:10px 12px;display:flex;flex-direction:column;gap:6px;flex:1;width:100%}
.top{display:flex;justify-content:space-between;align-items:baseline;gap:6px}
.num{font-weight:700;color:var(--accent);font-variant-numeric:tabular-nums}
.price{font-weight:600;font-variant-numeric:tabular-nums}.price.none{color:var(--muted);font-weight:400;font-size:13px}
.title{font-size:14px}.title.desc{color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin-top:auto}
.chip{background:var(--chip);border-radius:999px;padding:1px 8px;font-size:12px;color:var(--muted)}
.chip.st{color:var(--ink)}
dialog{border:none;border-radius:12px;padding:0;max-width:min(960px,96vw);width:100%;background:var(--card);color:var(--ink)}
dialog::backdrop{background:rgba(0,0,0,.6)}
.dlg{padding:16px}.dlg h2{margin:0 0 4px;font-size:18px}.close{float:right;font:inherit;background:none;border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:4px 10px;cursor:pointer}
.gal{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:12px 0}
.gal img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:6px;display:block}
table{border-collapse:collapse;width:100%;font-size:14px}td{padding:4px 6px;border-top:1px solid var(--line);vertical-align:top}td:first-child{color:var(--muted);width:120px}
@media (max-width:520px){main{grid-template-columns:repeat(2,1fr);gap:10px;padding:12px}.body{padding:8px}}
</style></head><body>
<header>
<h1>Consignment catalog <small>__INTAKE__ · client __CLIENT__</small></h1>
<div class="bar">
<input id="q" type="search" aria-label="Search" placeholder="Search item no, title, description…">
<select id="state" aria-label="State"><option value="">All states</option></select>
<select id="cut" aria-label="Cut"><option value="">All cuts</option></select>
<select id="shots" aria-label="Photos"><option value="">All photos</option><option value="single">Has individual shots</option><option value="bin">Bin / group shots only</option></select>
<select id="sort" aria-label="Sort"><option value="num">Sort: item no</option><option value="hi">Sort: estimate high → low</option><option value="lo">Sort: estimate low → high</option></select>
</div><div class="stats" id="stats" aria-live="polite"></div>
</header>
<main id="grid"></main>
<dialog id="dlg"><div class="dlg" id="dlgbody"></div></dialog>
<script>
const items=__DATA__;
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const money=v=>v==null?'':'$'+(Number.isInteger(v)?v:v.toFixed(2));
const est=i=>i.hi==null?'':(i.lo!=null&&i.lo!==i.hi?money(i.lo)+'–'+money(i.hi):money(i.hi));
function fill(sel,key){[...new Set(items.map(i=>i[key]).filter(Boolean))].sort().forEach(v=>sel.add(new Option(v,v)))}
fill($('#state'),'state');fill($('#cut'),'cut');
function render(){
  const q=$('#q').value.toLowerCase(),st=$('#state').value,ct=$('#cut').value,so=$('#sort').value,sh=$('#shots').value;
  let list=items.filter(i=>(!st||i.state===st)&&(!ct||i.cut===ct)&&(!sh||(sh==='single')===(i.nsingle>0))&&(!q||[i.num,i.id,i.title,i.desc,i.lot,i.bin].join(' ').toLowerCase().includes(q)));
  if(so==='hi')list.sort((a,b)=>(b.hi??-1)-(a.hi??-1));else if(so==='lo')list.sort((a,b)=>(a.lo??1e9)-(b.lo??1e9));
  const lo=list.reduce((s,i)=>s+(i.lo??0),0),hi=list.reduce((s,i)=>s+(i.hi??0),0),np=list.filter(i=>i.hi==null).length;
  $('#stats').textContent=`${list.length} of ${items.length} items · estimated ${money(lo)}–${money(hi)} · ${np} without an estimate`;
  $('#grid').innerHTML=list.map(i=>`<button type="button" class="card" data-id="${esc(i.id)}">
   <span class="thumb">${i.photos[0]?`<img loading="lazy" src="${esc(i.photos[0].thumb)}" alt="">`:'no photo yet'}
   <span class="badge ${i.nsingle?'one':'bin'}">${i.nsingle?i.nsingle+' individual':'bin shot only'}</span></span>
   <span class="body"><span class="top"><span class="num">${esc(i.num)}</span>
   <span class="price ${i.hi==null?'none':''}">${i.hi==null?'no estimate':esc(est(i))}</span></span>
   <span class="title ${i.title?'':'desc'}">${esc(i.title||i.desc)}</span>
   <span class="chips"><span class="chip st">${esc(i.state)}</span>${i.cut?`<span class="chip">${esc(i.cut)}</span>`:''}${i.lot?`<span class="chip">lot ${esc(i.lot)}</span>`:''}${i.bin?`<span class="chip">bin ${esc(i.bin)}</span>`:''}${i.ask?`<span class="chip">ask $${esc(i.ask)}</span>`:''}</span></span></button>`).join('');
}
$('#grid').addEventListener('click',e=>{const c=e.target.closest('.card');if(!c)return;const i=items.find(x=>x.id===c.dataset.id);
 const row=(k,v)=>v?`<tr><td>${k}</td><td>${esc(v)}</td></tr>`:'';
 $('#dlgbody').innerHTML=`<button class="close" onclick="dlg.close()">Close</button><h2>${esc(i.num)} · ${esc(i.title||i.desc)}</h2>
 ${[['single','Individual shots',i.nsingle],['bin','Bin / group shots (other items in frame)',i.ngroup]].map(([k,label,n])=>{const ps=i.photos.filter(p=>p.kind===k);
   return `<h3>${label} <small>${n}${ps.length<n?`, ${ps.length} shown`:''}</small></h3>`+(ps.length?`<div class="gal">${ps.map(p=>`<a href="${esc(p.full)}" target="_blank" rel="noopener" title="${esc(p.pid)}"><img loading="lazy" src="${esc(p.thumb)}" alt="${esc(p.pid)}"></a>`).join('')}</div>`:'<p class="none">none yet</p>')}).join('')}
 <table>${row('Estimate',est(i))}${row('Draft title',i.title)}${row('Description',i.desc)}${row('State',i.state)}${row('State note',i.note)}${row('Cut',i.cut)}${row('Why',i.why)}${row('Lot',i.lot)}${row('Bin',i.bin)}${row('Qty',i.qty)}${row('Store',i.store)}${row('Ask',i.ask&&'$'+i.ask)}${row('Estimate source',i.src)}${row('Photos',i.nphotos+' on file'+(i.photos.length<i.nphotos?`, ${i.photos.length} shown`:''))}</table>`;
 $('#dlg').showModal();});
$('#dlg').addEventListener('click',e=>{if(e.target.id==='dlg')e.target.close()});
['q','state','cut','shots','sort'].forEach(id=>$('#'+id).addEventListener('input',render));render();
</script></body></html>"""
