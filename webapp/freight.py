"""webapp/freight.py — LTL freight in the local app: /freight.

The browser half of `ebz freight` (tools/freight.py). Both drive the same
lib/freight/service.py, so the rules are the same in either place:

  * quoting is free and runs on a click;
  * booking is two steps — a DRY RUN that names the carrier and the price,
    then a POST carrying confirm=true, which the page only sends after a
    browser confirm() that repeats the price. No single click spends money.

This is the first route in the app that calls a paid third-party API with a
stored key (a booking provider's). That is a deliberate step past Phase 2's
"secret-free" line in docs/webapp-architecture.md, taken because managing
freight from the app is the point; it stays safe for the same reason the
pick sheets do — the server binds 127.0.0.1 only. `freight.web_booking:
false` in config.yaml turns the book endpoint off (the page then shows the
CLI command instead), without touching quoting or tracking.

Shipments carry buyer names and addresses, so the pages send the same
no-store/noindex headers as /pick/{token}.
"""
from __future__ import annotations

import json
from typing import Optional

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import stores as _stores
from config import ConfigError, get_freight_config
from freight import http as _http
from freight import service as _svc
from freight.models import (ACCESSORIALS, FreightAddress, FreightQuote,
                            FreightRequest, HandlingUnit)
from freight.providers import ProviderNotReady
from freight.store import STATUSES, FreightStore, Shipment
from tools.sales_report import STYLE, _e, _stat

PII_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Referrer-Policy": "no-referrer",
}

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _local_only(request: Request, response: Response) -> None:
    """Every /freight route: the browser must be talking to this machine by
    a loopback name, and a write must come from a loopback page.

    Binding 127.0.0.1 stops other machines, not other web pages: a site the
    operator visits can point its own hostname at 127.0.0.1 (DNS rebinding)
    and then call these routes as same-origin — including the one that
    books freight. Checking Host (and Origin on writes) closes that. JSON
    answers carry buyer addresses, so they get the no-store headers too."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in LOCAL_HOSTS:
        raise HTTPException(403, f"served to localhost only, not {host!r}")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and (urlparse(origin).hostname or "") not in LOCAL_HOSTS:
            raise HTTPException(403, "cross-origin write refused")
    response.headers.update(PII_HEADERS)


router = APIRouter(dependencies=[Depends(_local_only)])

_store: Optional[FreightStore] = None


def get_store() -> FreightStore:
    global _store
    if _store is None:
        _store = _svc.open_store()
    return _store


def set_store(store: Optional[FreightStore]) -> None:
    """Test hook — a throwaway database instead of reports/freight.db."""
    global _store
    _store = store


def web_booking_enabled() -> bool:
    return get_freight_config().get("web_booking", True) is not False


# ------------------------------------------------------------------ models

class Address(BaseModel):
    zip: str = ""
    name: str = ""
    company: str = ""
    street1: str = ""
    street2: str = ""
    city: str = ""
    state: str = ""
    country: str = "US"
    phone: str = ""
    email: str = ""


class Unit(BaseModel):
    length_in: float
    width_in: float
    height_in: float
    weight_lbs: float
    count: int = 1
    unit_type: str = "pallet"
    freight_class: Optional[str] = None
    stackable: bool = False
    description: str = ""


class QuoteIn(BaseModel):
    store: Optional[str] = None
    origin: Address = Address()
    destination: Address
    pickup_date: Optional[str] = None
    units: list[Unit]
    accessorials: list[str] = []
    commodity: str = ""
    declared_value: Optional[float] = None
    providers: Optional[list[str]] = None


class ShipmentIn(QuoteIn):
    order_id: str = ""
    sku: str = ""
    title: str = ""
    notes: str = ""
    buyer_paid: Optional[float] = None
    quote: bool = True


class ManualQuoteIn(BaseModel):
    provider: str
    carrier: str
    price: float
    service: str = ""
    transit_days: Optional[int] = None
    quote_id: str = ""
    note: str = ""


class BookIn(BaseModel):
    quote: str          # the quote's ref; a row number is accepted for a dry run only
    confirm: bool = False


class MarkBookedIn(BaseModel):
    quote: str
    tracking: str = ""
    price: Optional[float] = None
    bol: str = ""
    note: str = ""


class StatusIn(BaseModel):
    status: str
    note: str = ""


class PatchIn(BaseModel):
    order_id: Optional[str] = None
    sku: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    buyer_paid: Optional[float] = None
    pickup_date: Optional[str] = None
    destination: Optional[Address] = None


# ------------------------------------------------------------------ helpers

def _store_name(name: Optional[str]) -> str:
    try:
        if name:
            _stores.validate_store_name(name)
        return _stores.resolve_store_name(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _request(body: QuoteIn) -> tuple[str, FreightRequest]:
    from tools.freight import next_business_day
    store = _store_name(body.store)
    try:
        origin = _svc.store_origin(store)
    except ConfigError as exc:      # a malformed ship_from is the operator's to fix
        raise HTTPException(400, str(exc)) from exc
    for k, v in body.origin.model_dump().items():
        if v:
            setattr(origin, k, v)
    req = FreightRequest(
        origin=origin, destination=FreightAddress(**body.destination.model_dump()),
        pickup_date=body.pickup_date or next_business_day(),
        units=[HandlingUnit(**u.model_dump()) for u in body.units],
        accessorials=list(body.accessorials), commodity=body.commodity,
        declared_value=body.declared_value,
        reference=getattr(body, "order_id", "") or "")
    try:
        req.validate()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return store, req


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, str(exc.args[0]))
    if isinstance(exc, ProviderNotReady):
        return HTTPException(409, str(exc))
    if isinstance(exc, (_http.FreightAPIError, RuntimeError)):
        return HTTPException(502, str(exc))
    return HTTPException(400, str(exc))


def _money(v) -> str:
    return "—" if v is None else f"${v:,.2f}"


def _js(value) -> str:
    """JSON safe to inline in a <script> block. json.dumps alone leaves
    `</script>` intact — a buyer address containing `</script><script>…`
    would run as code on the page that can book. In an HTML attribute
    (onclick), wrap it in _e() as well."""
    return (json.dumps(value).replace("&", "\\u0026").replace("<", "\\u003c")
            .replace(">", "\\u003e"))


def _safe_url(u) -> str:
    """Only http(s) links from provider data become hrefs — never
    javascript: or data: — so a provider response can't inject script."""
    from urllib.parse import urlparse
    u = str(u or "")
    return u if urlparse(u).scheme in ("http", "https") else ""


# ------------------------------------------------------------------ JSON API

@router.get("/api/freight/providers")
def api_providers() -> list[dict]:
    return _svc.provider_status()


@router.post("/api/freight/quote")
def api_quote(body: QuoteIn) -> dict:
    """Ad-hoc comparison — the "what would freight to 30301 cost?" answer
    for a buyer's message. Nothing is saved."""
    _, req = _request(body)
    quotes, errors = _svc.quote_all(req, body.providers)
    return {"quotes": [q.to_dict() for q in quotes], "errors": errors,
            "html": _quotes_table(quotes, errors, None)}


@router.get("/api/freight/shipments")
def api_list(all: bool = False) -> list[dict]:
    return [s.to_dict() for s in get_store().list(open_only=not all)]


@router.post("/api/freight/shipments")
def api_create(body: ShipmentIn) -> dict:
    store, req = _request(body)
    fs = get_store()
    try:
        sid = fs.create(req, store=store, order_id=body.order_id, sku=body.sku,
                        title=body.title, notes=body.notes, buyer_paid=body.buyer_paid)
        if body.quote:
            _svc.quote_shipment(fs, sid, body.providers)
    except (ValueError, KeyError) as exc:
        raise _fail(exc) from exc
    return fs.require(sid).to_dict()


@router.get("/api/freight/shipments/{sid}")
def api_get(sid: int) -> dict:
    try:
        return get_store().require(sid).to_dict()
    except KeyError as exc:
        raise _fail(exc) from exc


@router.patch("/api/freight/shipments/{sid}")
def api_patch(sid: int, body: PatchIn) -> dict:
    fs = get_store()
    try:
        fields = {k: v for k, v in body.model_dump().items()
                  if v is not None and k not in ("destination", "pickup_date")}
        s = fs.set_fields(sid, **fields)
        if body.destination is not None or body.pickup_date:
            req = s.request
            if body.destination is not None:
                req.destination = FreightAddress(**body.destination.model_dump())
            req.pickup_date = body.pickup_date or req.pickup_date
            fs.update_request(sid, req)
        return fs.require(sid).to_dict()
    except (ValueError, KeyError) as exc:
        raise _fail(exc) from exc


@router.post("/api/freight/shipments/{sid}/requote")
def api_requote(sid: int) -> dict:
    try:
        return _svc.quote_shipment(get_store(), sid).to_dict()
    except (ValueError, KeyError) as exc:
        raise _fail(exc) from exc


@router.post("/api/freight/shipments/{sid}/quotes")
def api_add_quote(sid: int, body: ManualQuoteIn) -> dict:
    q = FreightQuote(provider=body.provider, carrier=body.carrier, price=body.price,
                     service=body.service, transit_days=body.transit_days,
                     quote_id=body.quote_id, warnings=[body.note] if body.note else [])
    try:
        return get_store().add_manual_quote(sid, q).to_dict()
    except (ValueError, KeyError) as exc:
        raise _fail(exc) from exc


@router.post("/api/freight/shipments/{sid}/book")
def api_book(sid: int, body: BookIn) -> dict:
    """DRY RUN unless confirm=true. With freight.web_booking: false a confirmed
    book is refused here and the page shows the CLI command instead."""
    if body.confirm and not web_booking_enabled():
        raise HTTPException(403, "booking from the app is off (freight.web_booking: false) "
                                 f"— run: python -m lib.cli freight book {sid} "
                                 f"--quote {body.quote} --confirm")
    try:
        r = _svc.book(get_store(), sid, body.quote, confirm=body.confirm)
    except (ValueError, KeyError, ProviderNotReady, _http.FreightAPIError,
            RuntimeError) as exc:
        raise _fail(exc) from exc
    from dataclasses import asdict
    out = asdict(r)
    out.pop("raw", None)
    return out


@router.post("/api/freight/shipments/{sid}/mark-booked")
def api_mark_booked(sid: int, body: MarkBookedIn) -> dict:
    try:
        return _svc.mark_booked(get_store(), sid, body.quote, tracking=body.tracking,
                                price=body.price, bol_url=body.bol,
                                note=body.note).to_dict()
    except (ValueError, KeyError) as exc:
        raise _fail(exc) from exc


@router.post("/api/freight/shipments/{sid}/track")
def api_track(sid: int) -> dict:
    try:
        t = _svc.track(get_store(), sid)
    except (ValueError, KeyError, ProviderNotReady, _http.FreightAPIError) as exc:
        raise _fail(exc) from exc
    return {"status": t.status, "delivered": t.delivered,
            "events": [e.__dict__ for e in t.events],
            "shipment_status": get_store().require(sid).status}


@router.get("/api/freight/shipments/{sid}/documents")
def api_documents(sid: int) -> list[dict]:
    try:
        return _svc.documents(get_store(), sid)
    except (ValueError, KeyError, ProviderNotReady, _http.FreightAPIError) as exc:
        raise _fail(exc) from exc


@router.post("/api/freight/shipments/{sid}/status")
def api_status(sid: int, body: StatusIn) -> dict:
    try:
        return get_store().set_status(sid, body.status, note=body.note).to_dict()
    except (ValueError, KeyError) as exc:
        raise _fail(exc) from exc


# ------------------------------------------------------------------ pages

PAGE_CSS = """
form.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px 14px}
label{display:flex;flex-direction:column;gap:4px;font-size:11px;letter-spacing:.04em;
  color:var(--muted)}
label.chk{flex-direction:row;align-items:center;gap:7px;font-size:13px;color:var(--ink);
  letter-spacing:0}
input,select,textarea{font:14px/1.3 "IBM Plex Sans",system-ui,sans-serif;color:var(--ink);
  background:var(--ground);border:1px solid var(--rule);border-radius:3px;padding:7px 8px;
  min-width:0}
fieldset{border:1px solid var(--rule);border-radius:3px;margin:0;padding:12px 14px 14px;
  display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px 14px;
  grid-column:1/-1}
legend{font:600 10.5px/1 "IBM Plex Sans",sans-serif;letter-spacing:.12em;
  text-transform:uppercase;color:var(--muted);padding:0 6px}
.actions{grid-column:1/-1;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
button{font:600 13px/1 "IBM Plex Sans",system-ui,sans-serif;padding:9px 14px;border-radius:3px;
  border:1px solid var(--accent);background:var(--accent);color:var(--surface);cursor:pointer}
button.ghost{background:transparent;color:var(--accent)}
button.small{padding:6px 10px;font-size:12px}
button:disabled{opacity:.5;cursor:progress}
.msg{font-size:13px;min-height:1.2em}
.msg.err{color:var(--warn)}
td .w{display:block;font-size:11.5px;color:var(--warn);margin-top:3px}
td .inc{display:block;font-size:11.5px;color:var(--muted);margin-top:3px}
tr.best td{background:color-mix(in srgb,var(--ok) 9%,transparent)}
.kv{display:grid;grid-template-columns:max-content 1fr;gap:6px 18px;font-size:13.5px}
.kv dt{color:var(--muted)}
.kv dd{margin:0}
details summary{cursor:pointer;color:var(--accent);font-size:13px;margin-top:10px}
@media (max-width:600px){body{padding:16px 16px 40px}.hdr,.pad{padding-left:16px;padding-right:16px}}
"""

PAGE_JS = """
async function api(method, url, body){
  const r = await fetch(url,{method,headers:{'Content-Type':'application/json'},
    body: body===undefined?undefined:JSON.stringify(body)});
  const j = await r.json().catch(()=>({}));
  if(!r.ok){
    const d=j.detail;
    // FastAPI validation errors are a list; name the field instead of dumping JSON.
    const text = typeof d==='string'? d : Array.isArray(d)?
      d.map(e=>(e.loc||[]).filter(x=>x!=='body'&&typeof x!=='number').join('.')+': '+e.msg).join('; ')
      : r.statusText;
    throw new Error(text);
  }
  return j;
}
function say(el, text, err){ el.textContent=text; el.className='msg'+(err?' err':''); }
function num(v){ return v===''||v==null? null : Number(v); }
function loadForm(f){
  const g = n => (f.elements.namedItem(n)||{}).value || '';
  const acc = [...f.querySelectorAll('input[name=acc]:checked')].map(x=>x.value);
  const addr = p => ({zip:g(p+'zip'),name:g(p+'name'),company:g(p+'company'),
    street1:g(p+'street1'),street2:g(p+'street2'),city:g(p+'city'),state:g(p+'state'),
    phone:g(p+'phone'),email:g(p+'email')});
  return {store:g('store')||null, origin:addr('from_'), destination:addr('to_'),
    pickup_date:g('pickup_date')||null, accessorials:acc, commodity:g('commodity'),
    declared_value:num(g('declared_value')),
    units:[{length_in:num(g('len_in')),width_in:num(g('wid_in')),height_in:num(g('hgt_in')),
      weight_lbs:num(g('wt_lb')),count:num(g('count'))||1,unit_type:g('unit_type')||'pallet',
      freight_class:g('freight_class')||null,stackable:!!(f.elements.stackable||{}).checked,
      description:g('commodity')}],
    order_id:g('order_id'),title:g('title'),sku:g('sku'),notes:g('notes'),
    buyer_paid:num(g('buyer_paid'))};
}
async function quoteOnly(btn){
  const f=btn.form, out=document.getElementById('adhoc'), m=document.getElementById('newmsg');
  btn.disabled=true; say(m,'Asking every provider… (the market spread can take ~45 s)');
  try{ const j=await api('POST','/api/freight/quote',loadForm(f)); out.innerHTML=j.html; say(m,'Nothing saved.'); }
  catch(e){ say(m,e.message,true); } finally{ btn.disabled=false; }
}
async function saveShipment(btn){
  const f=btn.form, m=document.getElementById('newmsg');
  btn.disabled=true; say(m,'Saving and quoting…');
  try{ const j=await api('POST','/api/freight/shipments',loadForm(f)); location.href='/freight/'+j.id; }
  catch(e){ say(m,e.message,true); btn.disabled=false; }
}
async function act(btn, method, url, body, reload){
  const m=document.getElementById('msg'); btn.disabled=true; say(m,'Working…');
  try{ const j=await api(method,url,body); if(reload!==false) location.reload(); return j; }
  catch(e){ say(m,e.message,true); btn.disabled=false; }
}
async function book(btn, sid, ref){
  const m=document.getElementById('msg'); btn.disabled=true; say(m,'Checking (dry run)…');
  let dry;
  try{ dry=await api('POST',`/api/freight/shipments/${sid}/book`,{quote:ref,confirm:false}); }
  catch(e){ say(m,e.message,true); btn.disabled=false; return; }
  const warn = (dry.warnings||[]).length? '\\n\\n'+dry.warnings.join('\\n') : '';
  if(!confirm(`Book ${dry.carrier} via ${dry.provider} for $${dry.price.toFixed(2)}?\\n\\nThis dispatches a pickup and charges the account on file.`+warn)){
    say(m,'Not booked.'); btn.disabled=false; return; }
  say(m,'Booking…');
  try{ await api('POST',`/api/freight/shipments/${sid}/book`,{quote:ref,confirm:true}); location.reload(); }
  catch(e){ say(m,e.message,true); btn.disabled=false; }
}
async function showDocs(btn, sid){
  const m=document.getElementById('msg'); btn.disabled=true; say(m,'Fetching documents…');
  try{
    const docs=await api('GET',`/api/freight/shipments/${sid}/documents`);
    m.className='msg'; m.textContent='';
    if(!docs.length){ m.textContent='No documents yet — the BOL can lag the booking.'; }
    for(const d of docs){ const a=document.createElement('a'); a.href=d.url; a.target='_blank';
      a.rel='noreferrer'; a.textContent=(d.type||'doc')+(d.podType?' / '+d.podType:'');
      m.append(a,' '); }
  }catch(e){ say(m,e.message,true); } finally{ btn.disabled=false; }
}
function formJSON(f){ const o={}; for(const el of f.elements){ if(!el.name) continue;
  o[el.name]= el.type==='number'? num(el.value) : el.value; } return o; }
"""


def _page(title: str, body: str) -> HTMLResponse:
    html = ('<!doctype html><meta charset="utf-8">\n'
            f'<title>{_e(title)}</title>\n'
            '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<meta name="robots" content="noindex,nofollow">\n'
            f'<style>{STYLE}{PAGE_CSS}</style>\n<div class="wrap">\n{body}\n</div>\n'
            f'<script>{PAGE_JS}</script>\n')
    return HTMLResponse(html, headers=PII_HEADERS)


def _header(eyebrow: str, h1: str, sub: str, stats: str = "") -> str:
    return (f'<div class="card"><div class="hdr"><p class="eyebrow">{_e(eyebrow)}</p>'
            f'<h1>{h1}</h1><div class="ct">{sub}</div></div>'
            + (f'<div class="stats">{stats}</div>' if stats else "") + '</div>')


def _quotes_table(quotes: list[FreightQuote], errors: dict, sid: Optional[int],
                  booked: bool = False) -> str:
    if not quotes and not errors:
        return '<p class="dim">No quotes yet.</p>'
    rows = []
    best = next((i for i, q in enumerate(quotes) if q.bookable or q.tier == "manual"), None)
    for i, q in enumerate(quotes):
        n = i + 1
        notes = "".join(f'<span class="w">! {_e(w)}</span>' for w in q.warnings)
        inc = (q.extra or {}).get("included")
        if inc:
            notes += f'<span class="inc">includes {_e(", ".join(map(str, inc)))}</span>'
        charges = (q.extra or {}).get("charges")
        if charges:
            notes += '<span class="inc">' + _e(" · ".join(
                f"{c.get('description')} {_money(c.get('amount'))}" for c in charges)) + '</span>'
        expired = q.expired()
        if expired:
            notes += f'<span class="w">EXPIRED {_e(q.expires_at)} — re-quote</span>'
        if sid is None or booked:
            action = ""
        elif expired and q.tier != "manual":
            action = '<span class="dim">expired</span>'
        elif q.bookable:
            action = (f'<button class="small" onclick="book(this,{sid},{_e(_js(q.ref))})">'
                      'Book…</button>')
        elif q.tier == "manual":
            action = (f'<button class="small ghost" onclick="markBooked(this,{sid},{_e(_js(q.ref))})">'
                      'Mark booked…</button>')
        else:
            action = '<span class="dim">compare only</span>'
        days = "?" if q.transit_days is None else q.transit_days
        rows.append(
            f'<tr class="{"best" if i == best else ""}"><td class="num">{n}</td>'
            f'<td>{_e(q.carrier)}<span class="inc">{_e(q.provider)}'
            f'{" · " + _e(q.service) if q.service else ""}</span>{notes}</td>'
            f'<td class="num">{_money(q.price)}</td><td class="num">{_e(days)}</td>'
            f'<td><span class="pill{" on" if q.bookable else ""}">{_e(q.tier or "—")}</span></td>'
            f'<td>{action}</td></tr>')
    errs = "".join(f'<li><b>{_e(k)}</b> — {_e(v)}</li>' for k, v in errors.items())
    return ('<div class="scroll"><table><tr><th class="num">#</th><th>Carrier</th>'
            '<th class="num">Price</th><th class="num">Days</th><th>Tier</th><th></th></tr>'
            + "".join(rows) + '</table></div>'
            + (f'<p class="note">Not quoting:</p><ul class="dim" style="font-size:12.5px">'
               f'{errs}</ul>' if errs else ""))


def _acc_boxes(selected: list[str] = ()) -> str:
    return "".join(
        f'<label class="chk"><input type="checkbox" name="acc" value="{k}"'
        f'{" checked" if k in selected else ""}>{_e(v)}</label>'
        for k, v in ACCESSORIALS.items())


def _addr_fields(prefix: str, a: Optional[FreightAddress] = None, zip_only: bool = False) -> str:
    fields = [("zip", "ZIP")] if zip_only else [
        ("zip", "ZIP"), ("name", "Contact name"), ("company", "Company"),
        ("street1", "Street"), ("street2", "Street 2"), ("city", "City"),
        ("state", "State"), ("phone", "Phone"), ("email", "Email")]
    return "".join(
        f'<label>{label}<input name="{prefix}{k}" value="{_e(getattr(a, k, "") if a else "")}"'
        f'{" inputmode=numeric" if k == "zip" else ""}></label>' for k, label in fields)


def _new_form() -> str:
    try:
        origin = _svc.store_origin(None)
    except ConfigError:
        origin = None
    names = []
    try:
        names = _stores.configured_stores()
    except Exception:  # noqa: BLE001 — no config still renders the page
        names = []
    store_sel = ("<label>Store<select name=store>" + "".join(
        f'<option value="{_e(n)}">{_e(n)}</option>' for n in names) + "</select></label>"
        if len(names) > 1 else "")
    return f'''<div class="card"><div class="pad"><h2>New freight quote</h2>
<form class="grid" onsubmit="return false">
{store_sel}
<fieldset><legend>Load — per unit, packed</legend>
<label>Length in<input name=len_in type=number step=any required></label>
<label>Width in<input name=wid_in type=number step=any required></label>
<label>Height in<input name=hgt_in type=number step=any required></label>
<label>Weight lb<input name=wt_lb type=number step=any required></label>
<label>Count<input name=count type=number value=1 min=1></label>
<label>Unit<select name=unit_type><option>pallet</option><option>crate</option>
<option>box</option><option>piece</option></select></label>
<label>Freight class<input name=freight_class placeholder="estimated if blank"></label>
<label>Pickup date<input name=pickup_date type=date></label>
<label>Commodity<input name=commodity placeholder="antique sewing machine"></label>
<label>Declared value $<input name=declared_value type=number step=any></label>
<label class="chk"><input type=checkbox name=stackable>Stackable</label>
</fieldset>
<fieldset><legend>From (default: store ship_from)</legend>
{_addr_fields("from_", origin, zip_only=True)}</fieldset>
<fieldset><legend>To — ZIP to quote; the rest to book</legend>{_addr_fields("to_")}</fieldset>
<fieldset><legend>Accessorials</legend>{_acc_boxes()}</fieldset>
<fieldset><legend>Saving as a shipment (optional)</legend>
<label>eBay order id<input name=order_id></label><label>Title<input name=title></label>
<label>SKU<input name=sku></label><label>Buyer paid for freight $<input name=buyer_paid type=number step=any></label>
<label style="grid-column:1/-1">Notes<input name=notes></label></fieldset>
<div class="actions"><button type=button onclick="quoteOnly(this)">Quote only</button>
<button type=button class="ghost" onclick="saveShipment(this)">Save &amp; quote</button>
<span id=newmsg class="msg"></span></div>
</form><div id=adhoc style="margin-top:16px"></div></div></div>'''


def _list_table(rows: list[Shipment]) -> str:
    if not rows:
        return '<p class="dim">No shipments yet.</p>'
    out = []
    for s in rows:
        best = s.cheapest
        price = s.cost if s.booking else (best.price if best else None)
        carrier = (s.booking or {}).get("carrier") or (best.carrier if best else "")
        st = s.status
        cls = "on" if st == "delivered" else ("off" if st in ("draft", "quoted", "booking") else "")
        flag = ""
        if st == "booking":
            flag = '<span class="w">outcome unknown — check the provider</span>'
        elif (s.booking or {}).get("payment_link") and st == "booked":
            flag = '<span class="w">UNPAID — on hold</span>'
        out.append(
            f'<tr><td class="num"><a href="/freight/{s.id}">#{s.id}</a></td>'
            f'<td><span class="pill {cls}">{_e(st)}</span></td>'
            f'<td><a href="/freight/{s.id}">{_e(s.title or "(untitled)")}</a>'
            f'<span class="inc">{_e(s.order_id or "")}</span>{flag}</td>'
            f'<td class="num">{_e(s.request.origin.zip)} → {_e(s.request.destination.zip)}</td>'
            f'<td>{_e(carrier)}</td><td class="num">{_money(price)}</td>'
            f'<td class="num">{_money(s.margin)}</td></tr>')
    return ('<div class="scroll"><table><tr><th class="num">#</th><th>Status</th><th>Item</th>'
            '<th class="num">Lane</th><th>Carrier</th><th class="num">Cost / best</th>'
            '<th class="num">Margin</th></tr>' + "".join(out) + '</table></div>')


@router.get("/freight", response_class=HTMLResponse)
def freight_index(all: bool = False) -> HTMLResponse:
    fs = get_store()
    rows = fs.list(open_only=not all)
    every = fs.list() if not all else rows
    booked = [s for s in every if s.booking]
    spent = sum(s.cost or 0 for s in booked)
    margins = [s.margin for s in booked if s.margin is not None]
    prov = " · ".join(f'{p["provider"]} {"✓" if p["ready"] else "—"}'
                      for p in _svc.provider_status())
    head = _header(
        "ebaybiz · freight", "LTL freight",
        f'{_e(prov)} · <a href="/">dashboard</a>',
        _stat(str(len([s for s in every if s.status not in ("delivered", "cancelled")])),
              "open shipments")
        + _stat(_money(spent), "freight booked, all time")
        + _stat(_money(sum(margins)) if margins else "—", "freight margin",
                "buyer paid − cost, where both known"))
    toggle = ('<a href="/freight">open only</a>' if all else
              '<a href="/freight?all=1">show delivered &amp; cancelled</a>')
    body = (head + _new_form()
            + f'<div class="card"><div class="pad"><h2>Shipments</h2>{_list_table(rows)}'
              f'<p class="note">{toggle}</p></div></div>')
    return _page("Freight", body)


def _detail(s: Shipment) -> str:
    r = s.request
    booked = s.booking is not None
    units = "<br>".join(
        f'{u.count} × {_e(u.unit_type)} {u.length_in:g}×{u.width_in:g}×{u.height_in:g} in, '
        f'{u.weight_lbs:g} lb each · {u.density_pcf:.1f} pcf · class '
        f'{_e(u.effective_class())}{"" if u.freight_class else " (est.)"}' for u in r.units)
    d = r.destination
    to = ", ".join(x for x in (d.name, d.company, d.street1, d.street2, d.city,
                               d.state, d.zip, d.phone) if x)
    kv = [("Lane", f'{_e(r.origin.zip)} {_e(r.origin.city)} → {_e(r.destination.zip)} '
                   f'{_e(r.destination.city)}'),
          ("Pickup", _e(r.pickup_date)), ("Load", units),
          ("Deliver to", _e(to) or '<span class="warn">ZIP only — add the address to book</span>'),
          ("Accessorials", _e(", ".join(ACCESSORIALS[a] for a in r.accessorials)) or "none"),
          ("Store", _e(s.store or "default")), ("eBay order", _e(s.order_id or "—")),
          ("Buyer paid", _money(s.buyer_paid)), ("Margin", _money(s.margin)),
          ("Notes", _e(s.notes or "—"))]
    if booked:
        b = s.booking
        links = " ".join(f'<a href="{_e(_safe_url(b[k]))}" target=_blank rel=noreferrer>{lbl}</a>'
                         for k, lbl in (("tracking_url", "tracking"), ("bol_url", "BOL"))
                         if _safe_url(b.get(k)))
        pay = _safe_url(b.get("payment_link"))
        kv.insert(0, ("Booked", f'{_e(b.get("provider"))} / {_e(b.get("carrier"))} · '
                                f'{_money(b.get("price"))} · tracking '
                                f'{_e(b.get("tracking_number") or "—")} {links}'
                                + (f'<span class="w">UNPAID — on hold until paid: '
                                   f'<a href="{_e(pay)}" target=_blank '
                                   f'rel=noreferrer>pay</a></span>'
                                   if pay else "")
                                + "".join(f'<span class="w">! {_e(w)}</span>'
                                          for w in b.get("warnings") or [])))
    if s.status == "booking":
        last = next((e for e in reversed(s.events) if e.get("status") == "book_unknown"), {})
        kv.insert(0, ("Booking", '<span class="warn">OUTCOME UNKNOWN — the booking may have '
                                 'gone through. Check the provider, then mark it booked or set '
                                 'the status back to quoted.</span>'
                                 + (f'<span class="w">{_e(last.get("note"))}</span>'
                                    if last.get("note") else "")))
    info = '<dl class="kv">' + "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in kv) + "</dl>"

    status_opts = "".join(f'<option{" selected" if x == s.status else ""}>{x}</option>'
                          for x in STATUSES)
    base = f"/api/freight/shipments/{s.id}"
    requote_btn = ("" if booked else
                   f'<button class="ghost small" onclick="act(this,\'POST\',\'{base}/requote\')">'
                   'Re-quote</button>')
    track_btn = (f'<button class="ghost small" onclick="act(this,\'POST\',\'{base}/track\')">'
                 'Track</button>' if booked else "")
    set_status = ("act(this,'POST','" + base + "/status',"
                  "{status:document.getElementById('st').value})")
    docs_btn = (f'<button class="ghost small" onclick="showDocs(this,{s.id})">Documents</button>'
                if booked else "")
    controls = (f'<div class="actions" style="margin-top:16px">{requote_btn}{track_btn}{docs_btn}'
                f'<select id=st>{status_opts}</select>'
                f'<button class="ghost small" onclick="{set_status}">Set status</button>'
                '<span id=msg class="msg"></span></div>')
    web_note = "" if web_booking_enabled() else (
        '<p class="note">Booking from the app is off (freight.web_booking: false). Book with '
        f'<code>python -m lib.cli freight book {s.id} --quote N --confirm</code>.</p>')

    add_quote = ("act(this.querySelector('button'),'POST','" + base + "/quotes',"
                 "formJSON(this));return false")
    manual = "" if booked else (
        '<details><summary>Add a quote got by phone or web (uShip, Freightquote, a carrier)'
        f'</summary><form class="grid" style="margin-top:10px" onsubmit="{add_quote}">'
        '<label>Source<input name=provider required placeholder="uship"></label>'
        '<label>Carrier<input name=carrier required></label>'
        '<label>Price $<input name=price type=number step=any required></label>'
        '<label>Transit days<input name=transit_days type=number></label>'
        '<label>Quote / ref #<input name=quote_id></label>'
        '<label>Note<input name=note></label>'
        '<div class="actions"><button class="small">Add quote</button></div></form></details>')

    addr_keys = ("zip", "name", "company", "street1", "street2", "city", "state", "phone", "email")
    dest_fields = "" if booked else (
        '<fieldset><legend>Destination (changing it clears quotes)</legend>'
        + _addr_fields("to_", d) + '</fieldset>')
    dest_js = "" if booked else (
        "const dest={}; for(const k of " + _js(list(addr_keys)) + "){dest[k]=o['to_'+k]||'';}"
        " if(JSON.stringify(dest)!==JSON.stringify(" + _js({k: getattr(d, k) for k in addr_keys})
        + ")) body.destination=dest;")
    paid = "" if s.buyer_paid is None else s.buyer_paid
    edit = (
        f'<details><summary>Edit order, notes, buyer-paid{"" if booked else ", pickup date, destination"}'
        '</summary><form class="grid" style="margin-top:10px" onsubmit="saveEdit(this);return false">'
        f'<label>eBay order id<input name=order_id value="{_e(s.order_id)}"></label>'
        f'<label>Title<input name=title value="{_e(s.title)}"></label>'
        f'<label>Buyer paid $<input name=buyer_paid type=number step=any value="{paid}"></label>'
        f'<label style="grid-column:1/-1">Notes<input name=notes value="{_e(s.notes)}"></label>'
        + ("" if booked else f'<label>Pickup date (changing it clears quotes)<input '
                             f'name=pickup_date type=date value="{_e(s.request.pickup_date)}"></label>')
        + f'{dest_fields}<div class="actions"><button class="small">Save</button></div></form></details>'
        '<script>\nasync function saveEdit(f){\n'
        '  const o=formJSON(f), body={order_id:o.order_id,title:o.title,notes:o.notes,'
        'buyer_paid:o.buyer_paid};\n'
        + ("" if booked else "  if(o.pickup_date && o.pickup_date!==" + _js(s.request.pickup_date)
           + ") body.pickup_date=o.pickup_date;\n")
        + '  ' + dest_js + '\n'
        "  await act(f.querySelector('button'),'PATCH','" + base + "',body);\n}\n"
        'async function markBooked(btn,sid,n){\n'
        "  const pro=prompt('PRO / tracking number for quote '+n+' (blank if not yet known):');\n"
        '  if(pro===null) return;\n'
        "  await act(btn,'POST','/api/freight/shipments/'+sid+'/mark-booked',{quote:n,tracking:pro});\n"
        '}\n</script>')
    events = "".join(f'<li><span class="dim">{_e(e.get("when"))}</span> {_e(e.get("status"))} '
                      f'{_e(e.get("note") or "")}</li>' for e in reversed(s.events))
    quotes_h = (f'Quotes · {_e(s.quoted_at)}' if s.quoted_at else "Quotes")
    return (_header(f"ebaybiz · freight #{s.id}", _e(s.title or f"Shipment #{s.id}"),
                    f'<span class="pill">{_e(s.status)}</span> · <a href="/freight">all freight</a>')
            + f'<div class="card"><div class="pad">{info}{controls}</div></div>'
            + f'<div class="card"><div class="pad"><h2>{quotes_h}</h2>'
              f'{_quotes_table(s.quotes, s.quote_errors, s.id, booked)}{web_note}{manual}'
              f'{edit}</div></div>'
            + (f'<div class="card"><div class="pad"><h2>History</h2><ul>{events}</ul></div></div>'
               if events else ""))


@router.get("/freight/{sid}", response_class=HTMLResponse)
def freight_detail(sid: int) -> HTMLResponse:
    s = get_store().get(sid)
    if s is None:
        raise HTTPException(404, f"no freight shipment #{sid}")
    return _page(f"Freight #{sid}", _detail(s))
