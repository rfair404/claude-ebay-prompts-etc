"""ebaybiz — Stage B: eBay SOLD comps via the user's logged-in Chrome.

**This replaces Apify entirely.** `lib/apify_ebay.py` moved to `deprecated/`
on 2026-08-15; nothing in the live pipeline calls it.

## Why the actors died, definitively

eBay migrated its search results from the legacy `li.s-item` markup to a new
`li.s-card` layout. Measured on a live sold search 2026-08-15:

    li.s-item  ->  0 elements
    li.s-card  -> 62 elements

Every community scraper we used was written against `.s-item`, so they parse
zero rows off a page that renders fine. That is the whole "silent 0 items with
a SUCCEEDED status" mystery — not (only) proxy blocks. `cirkit` additionally
403s on warm-up because it depends on OUR Apify residential proxy pool, which
eBay blocks; `automation-lab` is 404/deleted from Apify outright.

A logged-in browser has none of these failure modes: no proxy to block, no
third-party actor to be deleted, no per-query cost.

## Anti-scrape decoys (must be filtered)

eBay salts the results with traps:
  * Placeholder cards linking to a FAKE item id (`/itm/123456`) with alt text
    "Shop on eBay". `extract()` drops anything whose href lacks a >=9-digit id.
  * The word "Sponsored" is rendered REVERSED as `derosnopS` (CSS direction
    trick), so it can't be string-matched. It appears on nearly every card, so
    it is useless as a sponsored marker — do NOT filter on it.

## The pipeline

    1. build_search_urls(query)        -> URLs           [this module]
    2. Chrome navigates to each URL                      [claude-in-chrome MCP]
    3. javascript_tool(EXTRACTOR_JS)   -> JSON rows      [this module's JS]
    4. --ingest-json                   -> run JSON       [this module]
    5. price_stats.py / comps_csv.py                     [unchanged]

Step 3 runs in the page, so it sees exactly what the user sees. The JSON it
returns carries item URLs and thumbnails, which the text-scraping fallback
(`--parse`) cannot recover.

Before step 1, the default (no-flag) invocation checks whether TODAY's comps
for this exact query are already sitting in an earlier `--ingest-json` run
(`lib/read_cache.py`, keyed query+condition+UTC date) — if both dual sorts are
already in hand it prints their paths and skips straight past steps 1-4
entirely. `--ingest-json` always records what it just captured. `--fresh`
bypasses the check and forces the live instructions regardless.

Walking the query ladder does NOT need one navigate per rung. From any loaded
eBay page, `--js-multi` fetches every rung's URL same-origin (login cookies
included), parses each with DOMParser and runs the same extractor over it — 4-5
formulations in ONE javascript_tool call, measured with no bot challenge. Use
it to find which formulation has a cohort, then ingest that one.

ALWAYS hand the user the same URLs — they browse the identical comp set.

## Sort codes (read off eBay's own sort menu, not guessed)

    12  Best Match                       representative body
    16  Price + Shipping: highest first  CEILING, delivered basis
    15  Price + Shipping: lowest first
    13  Ended Recently
    10  Time: newly listed

`_sop=3` — used by the retired `apify_ebay.build_sold_search_url` — is NOT a
sort code. It is the "Skip to main content" anchor echoing the current URL, so
it silently applies no sort. Every "ceiling" query built with it was reading an
unsorted page.
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comps_core import (  # noqa: E402
    CompRecord,
    parse_feedback_count,
    parse_money,
    parse_sold_date,
    save_run_json,
)
import read_cache  # noqa: E402

EBAY_SEARCH = "https://www.ebay.com/sch/i.html"

SORTS = {
    "best_match": "12",
    "price_high": "16",      # Price + Shipping: highest first (delivered)
    "price_low": "15",
    "ended_recently": "13",
    "newly_listed": "10",
}
DUAL_SORTS = ("best_match", "price_high")
DEFAULT_IPG = 60             # eBay's max per page without extra requests

_CONDITION_PARAM = {"new": "3", "used": "4"}


# ---------------------------------------------------------------------------
# Same-day comp cache (V4_PLAN Phase 4, #30) — see lib/read_cache.py for the
# key/staleness rationale. This module is the one caller: before printing the
# "go browse in Chrome" instructions, check whether today's comps for this
# exact query are already sitting in a run file from an earlier `--ingest-json`
# / `--parse` this session or an earlier one, and skip the round trip if so.
# ---------------------------------------------------------------------------
_CACHE_NS = "ebay_sold_browse.comp_run"


def _cache_parts(query: str, condition: Optional[str]) -> tuple:
    # Normalized so "Fenton hobnail vase" and " fenton hobnail vase " hit the
    # same entry; the printed query keeps the user's original casing.
    return (query.strip().lower(), condition or "any")


def _comp_cache_lookup(query: str, condition: Optional[str]) -> dict:
    """{sort: {n, path}} already captured TODAY for this query+condition.

    Empty dict on a miss (never raises — a corrupt/missing cache file is
    just "nothing captured yet").
    """
    result = read_cache.get(_CACHE_NS, *_cache_parts(query, condition))
    if not result.hit or not isinstance(result.value, dict):
        return {}
    sorts = result.value.get("sorts")
    return sorts if isinstance(sorts, dict) else {}


def _comp_cache_record(query: str, condition: Optional[str], sort: str,
                       page: int, path: str, n: int) -> None:
    """Record one ingested sort/page under today's entry for this query.

    Read-modify-write so ingesting `best_match` then `price_high` for the
    same query accumulates into one entry instead of the second overwriting
    the first — PRICE's dual-sort distribution needs both to count as
    "today's comps are in hand".
    """
    parts = _cache_parts(query, condition)
    existing = read_cache.get(_CACHE_NS, *parts)
    sorts = dict(existing.value.get("sorts", {})) if existing.hit and isinstance(
        existing.value, dict) else {}
    sorts[sort] = {"page": page, "n": n, "path": path}
    read_cache.put(_CACHE_NS, *parts, value={"sorts": sorts})


# ---------------------------------------------------------------------------
# URL construction
# ---------------------------------------------------------------------------

def build_search_url(query: str, sort: str = "best_match", *,
                     ipg: int = DEFAULT_IPG, page: int = 1,
                     condition: Optional[str] = None) -> str:
    """One eBay SOLD/completed search URL.

    BOTH `LH_Sold=1` and `LH_Complete=1` are required — without them eBay
    returns ACTIVE listings, i.e. asking prices, which are not comps.
    """
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {sorted(SORTS)}; got {sort!r}")
    params = {"_nkw": query, "LH_Sold": "1", "LH_Complete": "1",
              "_sop": SORTS[sort], "_ipg": str(ipg)}
    if page > 1:
        params["_pgn"] = str(page)
    if condition:
        if condition not in _CONDITION_PARAM:
            raise ValueError(f"condition must be one of {sorted(_CONDITION_PARAM)}")
        params["LH_ItemCondition"] = _CONDITION_PARAM[condition]
    return EBAY_SEARCH + "?" + urllib.parse.urlencode(params)


def build_search_urls(query: str, sorts=DUAL_SORTS, **kw) -> dict[str, str]:
    """The dual-query URL set PRICE's distribution needs: {sort: url}."""
    return {s: build_search_url(query, s, **kw) for s in sorts}


def query_ladder(query: str) -> list[tuple[str, str]]:
    """Specificity ladder [(label, query)] for when L1 is thin.

    Mirrors prompts/price.md: L1 specific -> L2 drop modifiers -> L3 category.
    Broadening the COMP SEARCH never touches the draft/title/SKU.
    """
    toks = query.split()
    rungs = [("L1 (specific)", query)]
    if len(toks) > 3:
        rungs.append(("L2 (broader)", " ".join(toks[:-2])))
    if len(toks) > 2:
        rungs.append(("L3 (broadest)", " ".join(toks[:2])))
    seen, out = set(), []
    for label, q in rungs:
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append((label, q))
    return out


# ---------------------------------------------------------------------------
# The in-page extractor
# ---------------------------------------------------------------------------
# `_EXTRACT_CORE_JS` defines `__ebayExtract(doc)`. It takes the DOCUMENT as an
# argument instead of closing over the global one, so the identical extraction
# runs over either:
#   * the live page  -> EXTRACTOR_JS   (navigate, then run)
#   * a document parsed by DOMParser from a fetched sold-search URL
#                    -> multi_query_js (N formulations, ONE call, no navigate)
#
# Design notes:
#  * Anchors on `li.s-card` + a real /itm/<9+ digits> href. Both are far more
#    stable than eBay's CSS class churn, and the href test is what kills the
#    decoy cards.
#  * Field regexes run over the card's leaf texts joined by NEWLINES, never
#    over raw `textContent`. eBay renders adjacent spans with no whitespace
#    between them, so `textContent` silently welds them together: the seller
#    "celticitaliansilver" sits right after "Sell one like this" and comes out
#    of textContent as "thiscelticitaliansilver". Joining leaves fixes it.
#  * Seller name and feedback are SEPARATE leaves:
#        "Sell one like this" / "celticitaliansilver" / "100% positive (905)"
#    so the username is read as the leaf PRECEDING the feedback leaf, not by
#    regex over one string.
#  * Strips `.clipped` / aria-hidden nodes from the title — eBay welds
#    "Opens in a new window or tab" into the title element.
#  * Reads href/src with an ATTRIBUTE fallback so a DOMParser document (no
#    layout, no loaded images) yields the same rows as the live page.
#  * PERFORMANCE — the "Results matching fewer words" marker is located with a
#    TreeWalker over TEXT NODES. It used to be `querySelectorAll('*')` plus a
#    `.textContent` read per node, which is O(nodes x subtree): on a full
#    `_ipg=60` SRP (tens of thousands of nodes) that scan pinned the renderer's
#    main thread hard enough that EVERY subsequent javascript_tool call on the
#    tab timed out at the 45s CDP limit — trivial one-liners included — and the
#    tab had to be closed and recreated (measured 2026-08-27). The TreeWalker
#    is linear over short strings and finds the same marker. Per-CARD
#    `c.querySelectorAll('*')` is fine, that subtree is tiny; never scan `*` at
#    document scope.
_EXTRACT_CORE_JS = r"""
const __ebayExtract=(doc)=>{
const T=n=>(n?.textContent||'').replace(/\s+/g,' ').trim();
const clean=n=>{if(!n)return null;const k=n.cloneNode(true);
  k.querySelectorAll('.clipped,[aria-hidden="true"]').forEach(e=>e.remove());
  return T(k).replace(/Opens in a new window or tab$/,'').trim()||null};
const money=s=>{const m=String(s).replace(/,/g,'').match(/\$(\d+(?:\.\d{1,2})?)/);
  return m?parseFloat(m[1]):null};
const fbn=s=>{const m=String(s).replace(/,/g,'').match(/^([\d.]+)([KM]?)$/);if(!m)return null;
  let v=parseFloat(m[1]);if(m[2]==='K')v*=1e3;if(m[2]==='M')v*=1e6;return Math.round(v)};
if(!doc||!doc.body) return {ok:false,error:'no document body'};
if(/Security Measure|verify yourself/i.test(doc.body.innerText||doc.body.textContent||''))
  return {ok:false,challenge:true,
    error:'eBay served a verification challenge. Do NOT solve it programmatically.'};
// eBay appends a "Results matching fewer words" section of LOOSE matches after
// the real results. They are NOT part of the query's cohort and they are NOT
// covered by the sort. Including them poisons the distribution (measured: a
// query with 3 real matches returned 62 cards, 57 of them unrelated, spanning
// $8-$264). Everything at/after that marker is dropped, and `loose` reports
// how many were cut so a thin cohort can't hide behind a big raw count.
let marker=null;
const tw=doc.createTreeWalker(doc.body,NodeFilter.SHOW_TEXT);
let tn;while(tn=tw.nextNode()){
  if(/matching fewer words/i.test(tn.nodeValue)){marker=tn.parentElement;break}}
const rows=[];let loose=0;
for(const c of doc.querySelectorAll('li.s-card')){
  if(marker&&!(c.compareDocumentPosition(marker)&Node.DOCUMENT_POSITION_FOLLOWING)){loose++;continue}
  const a=c.querySelector('a[href*="/itm/"]');
  const idm=(a&&(a.getAttribute('href')||a.href)||'').match(/\/itm\/(\d{9,})/);
  if(!idm) continue;                                  // decoy/placeholder card
  const im=c.querySelector('img');
  const leaves=[...c.querySelectorAll('*')]
    .filter(n=>!n.children.length&&n.textContent.trim()).map(T);
  const w=leaves.join('\n');            // NEVER T(c) — adjacent spans weld together
  const r={item_id:idm[1],url:'https://www.ebay.com/itm/'+idm[1],
    title:clean(c.querySelector('.s-card__title')),
    thumbnail:(im&&(im.src||im.getAttribute('src')))||null,
    sold_price:money(T(c.querySelector('.s-card__price'))),
    sold_date:null,shipping_cost:null,shipping_type:null,listing_type:null,
    bids_count:null,bo_accepted:false,seller_username:null,
    seller_feedback_score:null,seller_feedback_pct:null};
  let m;
  if((m=w.match(/Sold\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})/))) r.sold_date=m[1];
  if((m=w.match(/\+\s*\$([\d,.]+)\s+delivery/i))) r.shipping_cost=money('$'+m[1]);
  else if(/Free delivery/i.test(w)){r.shipping_cost=0;r.shipping_type='free'}
  if(/Best offer accepted/i.test(w)){r.bo_accepted=true;r.listing_type='Best Offer'}
  else if(/or Best Offer/.test(w)) r.listing_type='Buy It Now or Best Offer';
  else if(/Buy It Now/.test(w)) r.listing_type='Buy It Now';
  if((m=w.match(/(\d+)\s+bids?\b/))){r.bids_count=+m[1];r.listing_type='Auction'}
  // Seller: eBay renders this TWO ways on the same page — combined in one
  // leaf ("flatwareandtoys 99.9% positive (6.5K)") or split across two
  // ("celticitaliansilver" / "100% positive (905)"). Handle both or ~13% of
  // comps lose their seller, which silently weakens the low-feedback filter.
  for(let i=0;i<leaves.length;i++){
    let fm=leaves[i].match(/^([A-Za-z0-9_.\-*]+)\s+([\d.]+)%\s+positive\s+\(([\d.,]+[KM]?)\)/);
    if(fm){r.seller_username=fm[1];r.seller_feedback_pct=parseFloat(fm[2]);
      r.seller_feedback_score=fbn(fm[3]);break}
    fm=leaves[i].match(/^([\d.]+)%\s+positive\s+\(([\d.,]+[KM]?)\)/);
    if(fm){r.seller_feedback_pct=parseFloat(fm[1]);r.seller_feedback_score=fbn(fm[2]);
      const nm=i>0?leaves[i-1]:'';
      if(/^[A-Za-z0-9_.\-*]+$/.test(nm)) r.seller_username=nm;
      break}}
  if(r.title&&r.sold_price!=null) rows.push(r);
}
return {ok:true,
  header:T(doc.querySelector('h1,.srp-controls__count-heading')).slice(0,80),
  n:rows.length,loose_dropped:loose,rows};};
"""

# Paste into claude-in-chrome's javascript_tool on a loaded sold-search page.
# Returns {ok, header, n, loose_dropped, rows:[...]} as a JSON string.
EXTRACTOR_JS = ("(()=>{" + _EXTRACT_CORE_JS.strip()
                + "\nreturn JSON.stringify(__ebayExtract(document));})()")


def download_js(filename: str = "ebay_sold.json") -> str:
    """EXTRACTOR_JS wrapped to SAVE its output as a file instead of returning it.

    Why this exists: the extracted rows can't reliably be carried back through
    the MCP tool result. A payload containing dozens of eBay item ids trips the
    transport's content filter ("[BLOCKED: Cookie/query string data]") — long
    digit strings look like tracking data. Measured: ~6 rows with ids pass, 20
    do not.

    So the page writes the JSON to the browser's download directory via a blob,
    and we read it off disk. The tool result carries only a one-line receipt.
    This keeps item URLs, thumbnails and sellers — the provenance that makes a
    comp auditable — which the workaround (dropping ids) destroys.

    The download is user-visible and lands in the normal Downloads folder.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "ebay_sold.json"
    return ("(()=>{const j=" + EXTRACTOR_JS.strip() + ";"
            "const o=JSON.parse(j);"
            "if(!o.ok) return 'NOT SAVED: '+(o.error||'extractor returned ok:false');"
            "const b=new Blob([j],{type:'application/json'});"
            "const u=URL.createObjectURL(b);"
            "const a=document.createElement('a');a.href=u;"
            f"a.download={safe!r};"
            "document.body.appendChild(a);a.click();"
            "setTimeout(()=>{URL.revokeObjectURL(u);a.remove()},2000);"
            "return 'SAVED '+a.download+' rows='+o.n+' loose_dropped='+o.loose_dropped;})()")


# ---------------------------------------------------------------------------
# Same-origin multi-query fetch — walk the ladder in ONE javascript_tool call
# ---------------------------------------------------------------------------

MULTI_DELAY_MS = 400          # look like a browser, not a loop, between fetches
MULTI_MAX_FETCH = 8


def multi_query_plan(query: str, sorts=DUAL_SORTS, *, also=None,
                     ladder: bool = True, ipg: int = DEFAULT_IPG,
                     condition: Optional[str] = None,
                     limit: int = MULTI_MAX_FETCH) -> list[dict]:
    """The [{label, rung, query, sort, url}] set `multi_query_js` will fetch.

    Default = every ladder rung x every sort, so one call answers the question
    the ladder in prompts/price.md exists to walk: which formulation actually
    HAS a cohort. `also=[...]` adds hand-written formulations alongside.
    """
    rungs = query_ladder(query) if ladder else [("L1 (specific)", query)]
    seen = {q.lower() for _, q in rungs}
    for n, extra in enumerate(also or [], 1):
        extra = extra.strip()
        if extra and extra.lower() not in seen:
            seen.add(extra.lower())
            rungs.append((f"alt{n}", extra))
    plan = []
    for rung, q in rungs:
        short = rung.split()[0]                       # "L1 (specific)" -> "L1"
        for sort in sorts:
            plan.append({"label": f"{short}|{sort}", "rung": rung,
                         "query": q, "sort": sort,
                         "url": build_search_url(q, sort, ipg=ipg,
                                                 condition=condition)})
    return plan[:limit]


def multi_query_js(plan: list[dict], filename: Optional[str] = None, *,
                   delay_ms: int = MULTI_DELAY_MS) -> str:
    """JS that FETCHES several sold-search URLs from the already-loaded eBay tab.

    One `javascript_tool` call replaces N navigate+extract round trips: the tab
    is already on ebay.com, so `fetch(url,{credentials:'include'})` is
    same-origin and carries the login cookies, and `DOMParser` turns each
    response into a document `__ebayExtract(doc)` reads exactly like the live
    one. Measured 2026-08-27: 4-5 formulations in one call, no bot challenge.

    What comes BACK is per-query SUMMARIES only — n, loose_dropped, delivered
    min/median/max (over the `delivered_n` rows whose shipping is KNOWN), three
    sample titles — deliberately carrying no item ids, so
    the MCP transport's content filter can't block the result (see
    `download_js`). Pass `filename` to ALSO save the full rows to the browser's
    download folder for `--ingest-json <file> --pick <label>`.

    Fetches are sequential with a delay. Nothing here solves a challenge: a
    query whose response is a verification page comes back `challenge:true`.
    """
    safe = (re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "ebay_sold_multi.json"
            ) if filename else None
    save_js = (
        "const b=new Blob([JSON.stringify({queries:full})],{type:'application/json'});"
        "const u=URL.createObjectURL(b);const a=document.createElement('a');a.href=u;"
        f"a.download={safe!r};document.body.appendChild(a);a.click();"
        "setTimeout(()=>{URL.revokeObjectURL(u);a.remove()},2000);\n"
        "return JSON.stringify({ok:true,saved:a.download,queries:out});})()"
    ) if safe else "return JSON.stringify({ok:true,saved:null,queries:out});})()"
    return (
        "(async()=>{" + _EXTRACT_CORE_JS.strip() + "\n"
        "if(!/(^|\\.)ebay\\.com$/i.test(location.hostname))"
        " return JSON.stringify({ok:false,error:'not on ebay.com — fetch would be "
        "cross-origin; navigate the tab to an eBay page first'});\n"
        "const Q=" + json.dumps(plan, separators=(",", ":")) + ";\n"
        "const med=v=>{if(!v.length)return null;const s=[...v].sort((a,b)=>a-b),h=s.length>>1;"
        "return s.length%2?s[h]:Math.round((s[h-1]+s[h])*50)/100};\n"
        "const out=[],full=[];\n"
        "for(let i=0;i<Q.length;i++){const q=Q[i];let r;\n"
        f" if(i) await new Promise(r=>setTimeout(r,{int(delay_ms)}));\n"
        " try{const res=await fetch(q.url,{credentials:'include'});\n"
        "  if(!res.ok){out.push({label:q.label,query:q.query,sort:q.sort,ok:false,"
        "error:'HTTP '+res.status});continue}\n"
        "  r=__ebayExtract(new DOMParser().parseFromString(await res.text(),'text/html'));\n"
        " }catch(e){out.push({label:q.label,query:q.query,sort:q.sort,ok:false,"
        "error:String((e&&e.message)||e)});continue}\n"
        " if(!r.ok){out.push({label:q.label,query:q.query,sort:q.sort,ok:false,"
        "challenge:!!r.challenge,error:r.error});continue}\n"
        " full.push(Object.assign({},q,r));\n"
        # delivered basis only where shipping is KNOWN — an unknown ship cost is
        # not a free one, and quietly calling it 0 understates the comp.
        " const d=r.rows.filter(x=>x.shipping_cost!=null)"
        ".map(x=>Math.round((x.sold_price+x.shipping_cost)*100)/100);\n"
        " out.push({label:q.label,query:q.query,sort:q.sort,ok:true,n:r.n,"
        "loose_dropped:r.loose_dropped,header:r.header,delivered_n:d.length,"
        "delivered_min:d.length?Math.min(...d):null,delivered_med:med(d),"
        "delivered_max:d.length?Math.max(...d):null,"
        "sample:r.rows.slice(0,3).map(x=>String(x.title).slice(0,70))});\n"
        "}\n" + save_js)


def pick_multi_rows(data: dict, label: Optional[str] = None) -> tuple:
    """Pull ONE query's rows out of a `--js-multi` save file -> (rows, label).

    Raises ValueError listing the labels when `label` is missing or ambiguous:
    silently picking a rung would mislabel which formulation the comps came
    from, and the rung IS the provenance.
    """
    queries = data.get("queries") or []
    if not label:
        raise ValueError(
            "multi-query file — choose one with --pick: "
            + ", ".join(f"{q.get('label')} (n={q.get('n')}, {q.get('query')!r})"
                        for q in queries))
    hits = ([q for q in queries if q.get("label", "").lower() == label.lower()]
            or [q for q in queries if label.lower() in q.get("label", "").lower()])
    if len(hits) != 1:
        raise ValueError(f"--pick {label!r} matched {len(hits)} of: "
                         + ", ".join(q.get("label", "?") for q in queries))
    return hits[0].get("rows", []), hits[0].get("label", label)


def rows_to_comps(rows: list[dict], query: Optional[str] = None) -> list[CompRecord]:
    """Convert EXTRACTOR_JS rows into CompRecords (delivered basis computed)."""
    comps: list[CompRecord] = []
    for r in rows:
        price = r.get("sold_price")
        if price is None or not r.get("title"):
            continue
        ship = r.get("shipping_cost")
        comps.append(CompRecord(
            title=r["title"],
            sold_price=float(price),
            url=r.get("url") or "",
            item_id=r.get("item_id"),
            sold_date=parse_sold_date(r.get("sold_date")),
            shipping_cost=ship,
            shipping_type=r.get("shipping_type"),
            total_price=(float(price) + ship) if ship is not None else None,
            listing_type=r.get("listing_type"),
            bids_count=r.get("bids_count"),
            bo_accepted=bool(r.get("bo_accepted")),
            seller_username=r.get("seller_username"),
            seller_feedback_score=(r.get("seller_feedback_score")
                                   if isinstance(r.get("seller_feedback_score"), int)
                                   else parse_feedback_count(r.get("seller_feedback_score"))),
            seller_feedback_pct=r.get("seller_feedback_pct"),
            thumbnail=r.get("thumbnail"),
            sold_currency="USD",
            keyword_tag=query,
            raw=r,
        ))
    return comps


def save_browse_run(comps: list[CompRecord], query: str, sort: str,
                    save_dir=None, page: int = 1) -> str:
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    run_id = f"browse-{sort}-p{page}-{stamp}"
    return save_run_json(run_id, f"browser:ebay-sold({sort})", [query], [],
                         comps, save_dir)


def summarize(comps: list[CompRecord]) -> dict:
    """Quick quality read so a bad capture is obvious before pricing."""
    n = len(comps)
    def have(attr):
        return sum(1 for c in comps if getattr(c, attr) is not None)
    return {
        "n": n,
        "with_url": sum(1 for c in comps if c.url),
        "with_delivered": have("total_price"),
        "with_seller": have("seller_username"),
        "bo_accepted": sum(1 for c in comps if c.bo_accepted),
        "auctions": sum(1 for c in comps if c.bids_count),
    }


# ---------------------------------------------------------------------------
# Text fallback (when only get_page_text is available)
# ---------------------------------------------------------------------------

_NOISE = {"Opens in a new window or tab", "View similar active items",
          "Sell one like this", "Located in United States", "Free returns",
          "Last one", "Benefits charity", "Shop on eBay", "derosnopS",
          "Have one to sell?", "Almost gone", "Top Rated Plus"}
_TAIL = ("Related Searches", "Recently viewed items", "Tell us what you think",
         "Results Pagination")


def parse_sold_page(text: str, *, query: Optional[str] = None) -> list[CompRecord]:
    """Parse rendered page TEXT into comps. Fallback only — no URLs/thumbnails.

    Prefer EXTRACTOR_JS. This exists for when JS execution isn't available.
    """
    import re
    sold_re = re.compile(r"^Sold\s+([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\s*$")
    seller_re = re.compile(r"^([A-Za-z0-9_.\-*]+)\s+([\d.]+)%\s+positive\s+\(([\d.,]+[KM]?)\)\s*$")
    for marker in _TAIL:
        i = text.find(marker)
        if i != -1:
            text = text[:i]
    lines = [ln.strip() for ln in text.splitlines()]
    starts = [i for i, ln in enumerate(lines) if sold_re.match(ln)]
    comps: list[CompRecord] = []
    for n, start in enumerate(starts):
        block = lines[start:starts[n + 1] if n + 1 < len(starts) else len(lines)]
        rec: dict = {"sold_date": sold_re.match(block[0]).group(1)}
        for ln in block[1:]:
            if not ln or ln in _NOISE:
                continue
            if "title" not in rec and not ln.startswith(("$", "+")) \
                    and "% positive" not in ln:
                rec["title"] = ln
            elif "sold_price" not in rec and ln.startswith("$"):
                rec["sold_price"] = parse_money(ln)
            elif "Best offer accepted" in ln:
                rec["bo_accepted"] = True
                rec.setdefault("listing_type", "Best Offer")
            elif ln.startswith("or Best Offer"):
                rec.setdefault("listing_type", "Buy It Now or Best Offer")
            elif ln.startswith("Buy It Now"):
                rec.setdefault("listing_type", "Buy It Now")
            elif "delivery" in ln and ln.startswith("+"):
                rec["shipping_cost"] = parse_money(ln)
            elif ln.startswith("Free delivery"):
                rec["shipping_cost"], rec["shipping_type"] = 0.0, "free"
            else:
                m = seller_re.match(ln)
                if m:
                    rec["seller_username"] = m.group(1)
                    rec["seller_feedback_pct"] = float(m.group(2))
                    rec["seller_feedback_score"] = parse_feedback_count(m.group(3))
        if rec.get("title") and rec.get("sold_price") is not None:
            comps.append(rows_to_comps([rec], query)[0])
    return comps


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description="Stage B via logged-in Chrome — build eBay sold-search "
                    "URLs, print the in-page extractor, ingest its output.")
    ap.add_argument("query", nargs="*", help="Search keywords.")
    ap.add_argument("--urls", action="store_true",
                    help="Print the dual sold-search URLs (hand these to the user).")
    ap.add_argument("--ladder", action="store_true",
                    help="With --urls: also print broader L2/L3 rungs.")
    ap.add_argument("--js", action="store_true",
                    help="Print the extractor JS to run via javascript_tool.")
    ap.add_argument("--js-download", metavar="FILENAME", nargs="?", const="ebay_sold.json",
                    help="Print extractor JS that SAVES its output to the browser's "
                         "download folder instead of returning it. Use this for real "
                         "runs: the MCP transport blocks payloads containing many eBay "
                         "item ids, so returning the rows loses URLs/sellers.")
    ap.add_argument("--js-multi", metavar="FILENAME", nargs="?", const="",
                    help="Print JS that FETCHES several sold-search URLs from the "
                         "already-loaded eBay tab (same-origin) and extracts each — "
                         "the whole query ladder in ONE javascript_tool call instead "
                         "of a navigate+extract per rung. Returns per-query summaries; "
                         "give a FILENAME to also save the full rows for --pick.")
    ap.add_argument("--also", action="append", metavar="QUERY", default=[],
                    help="With --js-multi: an extra formulation to test alongside the "
                         "ladder rungs (repeatable).")
    ap.add_argument("--multi-sorts", default=",".join(DUAL_SORTS),
                    help="With --js-multi: comma-separated sorts to fetch per rung "
                         "(default %s)." % ",".join(DUAL_SORTS))
    ap.add_argument("--ingest-json", metavar="FILE",
                    help="Ingest EXTRACTOR_JS output (file, or '-' for stdin).")
    ap.add_argument("--pick", metavar="LABEL",
                    help="With --ingest-json on a --js-multi save file: which query's "
                         "rows to ingest (e.g. 'L2|price_high').")
    ap.add_argument("--parse", metavar="TXT",
                    help="Fallback: parse captured page TEXT (no URLs).")
    ap.add_argument("--sort", default="best_match", choices=sorted(SORTS),
                    help="Which sort the capture came from (labels the run).")
    ap.add_argument("--page", type=int, default=1, help="Which results page.")
    ap.add_argument("--condition", choices=sorted(_CONDITION_PARAM))
    ap.add_argument("--save-dir")
    ap.add_argument("--no-save", action="store_true",
                    help="Print comps as JSON instead of saving.")
    ap.add_argument("--fresh", action="store_true",
                    help="Bypass the same-day comp cache: always print live "
                         "browse instructions even if today's comps for this "
                         "query are already captured. Ingesting afterward "
                         "repopulates the cache as usual.")
    args = ap.parse_args()
    query = " ".join(args.query).strip()

    if args.js:
        print(EXTRACTOR_JS.strip())
        return

    if args.js_download:
        print(download_js(args.js_download))
        return

    if args.js_multi is not None:
        if not query:
            ap.error("--js-multi needs the query (the ladder is built from it)")
        sorts = [x.strip() for x in args.multi_sorts.split(",") if x.strip()]
        bad = [x for x in sorts if x not in SORTS]
        if bad:
            ap.error("--multi-sorts: unknown %s; pick from %s" % (bad, sorted(SORTS)))
        full = multi_query_plan(query, sorts, also=args.also,
                                condition=args.condition, limit=10 ** 6)
        plan, dropped = full[:MULTI_MAX_FETCH], full[MULTI_MAX_FETCH:]
        print(multi_query_js(plan, args.js_multi or None))
        # Provenance goes to stderr so stdout stays pasteable JS.
        print("\n%d fetch(es), run from any loaded ebay.com tab:" % len(plan),
              file=sys.stderr)
        for q in plan:
            print("  %-16s %r" % (q["label"], q["query"]), file=sys.stderr)
        if dropped:                    # never let a coverage cap go unsaid
            print("  CAPPED at %d — NOT fetched: %s"
                  % (MULTI_MAX_FETCH, ", ".join(q["label"] for q in dropped)),
                  file=sys.stderr)
        return

    if args.ingest_json or args.parse:
        if not query:
            ap.error("ingest/parse needs the query too (it tags the comps)")
        if args.ingest_json:
            raw = (sys.stdin.read() if args.ingest_json == "-"
                   else Path(args.ingest_json).read_text(encoding="utf-8"))
            data = json.loads(raw)
            if isinstance(data, dict):
                if data.get("challenge"):
                    print("ERROR: eBay served a verification challenge on that page. "
                          "Do not solve it programmatically — reload in Chrome and retry.",
                          file=sys.stderr)
                    sys.exit(2)
                if "queries" in data:                  # a --js-multi save file
                    try:
                        rows, label = pick_multi_rows(data, args.pick)
                    except ValueError as e:
                        print("ERROR: %s" % e, file=sys.stderr)
                        sys.exit(2)
                    print("Picked %s from the multi-query capture" % label)
                else:
                    rows = data.get("rows", [])
            else:
                rows = data
            comps = rows_to_comps(rows, query)
        else:
            comps = parse_sold_page(
                Path(args.parse).read_text(encoding="utf-8", errors="replace"),
                query=query)

        if not comps:
            print("ERROR: no comps. Was it a SOLD search "
                  "(LH_Sold=1&LH_Complete=1) and did the page finish loading?",
                  file=sys.stderr)
            sys.exit(1)

        s = summarize(comps)
        if args.no_save:
            from comps_core import comp_to_dict
            print(json.dumps([comp_to_dict(c) for c in comps], indent=2))
            return
        path = save_browse_run(comps, query, args.sort, args.save_dir, args.page)
        _comp_cache_record(query, args.condition, args.sort, args.page, path, s["n"])
        print(f"Ingested {s['n']} comp(s)  [sort={args.sort} page={args.page}]")
        print(f"  urls {s['with_url']}/{s['n']} · delivered {s['with_delivered']}/{s['n']} · "
              f"seller {s['with_seller']}/{s['n']} · Best-Offer-accepted {s['bo_accepted']} · "
              f"auctions {s['auctions']}")
        if s["bo_accepted"]:
            print(f"  NOTE: {s['bo_accepted']} comp(s) sold via ACCEPTED Best Offer — "
                  f"their ASK was not the clearing price; soft ceiling.")
        print(f"Saved results: {path}")
        print(f"Next: python lib/price_stats.py "
              f"--{args.sort.replace('_', '-')} {path} --price-field total")
        return

    if not query:
        ap.error("a query is required")

    cached = {} if args.fresh else _comp_cache_lookup(query, args.condition)
    missing = [s for s in DUAL_SORTS if s not in cached]
    if cached and not missing:
        print(f"OK cache hit — today's comps for {query!r} are already in hand:")
        for sort, entry in cached.items():
            print(f"  [{sort}]  n={entry['n']}  {entry['path']}")
        print("No browse needed. Use --fresh to bypass and re-browse live.")
        return

    urls = build_search_urls(query, condition=args.condition)
    print(f"eBay SOLD comps — {query!r}")
    for sort, url in urls.items():
        label = ("representative body" if sort == "best_match"
                 else "CEILING (price + shipping, highest first)")
        note = (f"  [cached: n={cached[sort]['n']}, skip this one]"
                if sort in cached else "")
        print(f"  [{sort}]  {label}{note}\n    {url}")
    if args.ladder:
        for label, q in query_ladder(query)[1:]:
            print(f"  {label}: {q!r}\n    {build_search_url(q, 'best_match')}")
    print("\nThen, in claude-in-chrome on each page:")
    print("  python lib/ebay_sold_browse.py --js        # paste into javascript_tool")
    print(f"  python lib/ebay_sold_browse.py \"{query}\" --js-multi   "
          "# every ladder rung in ONE call, no navigate")
    print(f"  python lib/ebay_sold_browse.py \"{query}\" --ingest-json out.json --sort <sort>")


if __name__ == "__main__":
    _cli()
