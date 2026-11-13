"""ebaybiz — Stage B secondary source: eBay SOLD comps from CompsAPI.

The logged-in Chrome path (`ebay_sold_browse.py`) stays PRICE's primary Stage
B source. CompsAPI runs AFTER it as the secondary source: `--with-chrome`
merges its rows into the Chrome best_match run, drops any item_id Chrome
already has, and cuts both sources to exact matches with price_stats' own
filters before any stats. A CompsAPI row with no condition can't be verified
and is dropped. It is a paid REST vendor with a Bearer key, NOT Apify: Apify
stays retired (lib/README.md).

    python lib/compsapi_sold.py "<query>" --with-chrome <bm.json> --condition used \\
        --require-tokens <tok>...                   # secondary source, merged run
    python lib/compsapi_sold.py "<query>"            # one /v1/sold page -> run JSON
    python lib/compsapi_sold.py --usage              # free quota read
    python lib/compsapi_sold.py --compare-recent 5   # vs the last 5 browser runs

API facts (read off https://compsapi.com/docs, 2026-11-12):
  * Base https://api.compsapi.com. `Authorization: Bearer <key>`. A
    `User-Agent` header is REQUIRED (missing -> 403).
  * GET /v1/sold?q=&days=(1-1095, default 90)&size=(10-240)&best_offer=1
    -> {ok, items:[{title,item_id,url,price,best_offer,last_sold,format,
    shipping_cost,...}], next_page}. Newest first; no price sort.
  * GET /v1/usage is free (doesn't count). It reports `used`/`daily_cap` for
    trial and legacy keys only; monthly plan totals are console-only.
  * Errors: {"ok": false, "error": ...}. 401 bad key, 403 expired/blocked,
    429 `quota_reached` (paused until the UTC month resets, no overage) or
    per-minute rate (Retry-After). Failed searches don't count.

Quota rule: check /v1/usage before every search and FAIL CLOSED — any error
raises CompsAPIError with a plain message and exits non-zero. In
`--with-chrome` mode the same errors (and a missing key) skip CompsAPI
instead: the merged run keeps the Chrome rows and the output says so.

Best Offer: the vendor says `price` on a `best_offer: true` row is the
ACCEPTED amount, unlike eBay's results page. That is unverified, so those rows
are flagged `bo_accepted` and price_stats drops them exactly as it does for
the browser path (#444). `--compare-recent` checks the claim against browser
runs that already hold accepted-offer rows.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from comps_core import (  # noqa: E402
    CompRecord,
    comp_to_dict,
    default_runs_dir,
    save_run_json,
)
import read_cache  # noqa: E402

BASE_URL = "https://api.compsapi.com"
USER_AGENT = "ebaybiz-price/1.0 (+https://github.com/rfair404/claude-ebay-prompts-etc)"
DEFAULT_DAYS = 90
MAX_DAYS = 1095
DEFAULT_SIZE = 240           # one page, the API maximum
TIMEOUT_S = 30
SOURCE = "compsapi:sold"
_CACHE_NS = "compsapi_sold.comp_run"
_FORMAT = {"AUCTION": "Auction", "FIXED_PRICE": "Buy It Now"}


class CompsAPIError(RuntimeError):
    """Any CompsAPI failure. The message is written for the operator."""


class CompsAPIQuotaError(CompsAPIError):
    """Quota used up (monthly or trial daily cap). Searches are paused."""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _default_opener(req: urllib.request.Request) -> tuple[int, dict, bytes]:
    """Perform one request -> (status, headers, body). Swapped out in tests."""
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CompsAPIError(f"CompsAPI unreachable: {e}") from e


class CompsAPIClient:
    def __init__(self, api_key: str, *,
                 opener: Optional[Callable] = None, base_url: str = BASE_URL):
        if not api_key:
            raise CompsAPIError("CompsAPI key is empty.")
        self._key = api_key
        self._open = opener or _default_opener
        self._base = base_url.rstrip("/")

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        url = self._base + path
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None})
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        status, headers, body = self._open(req)
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            data = {}
        if status == 200 and isinstance(data, dict) and data.get("ok", True):
            return data
        raise _error_for(status, headers, data)

    def usage(self) -> dict:
        return self._get("/v1/usage")

    def sold(self, query: str, *, days: int = DEFAULT_DAYS,
             size: int = DEFAULT_SIZE, condition: Optional[str] = None) -> dict:
        if not 1 <= days <= MAX_DAYS:
            raise CompsAPIError(f"days must be 1-{MAX_DAYS}, got {days}")
        return self._get("/v1/sold", {
            "q": query, "days": days, "size": size,
            "condition": condition, "best_offer": 1,
        })


def _error_for(status: int, headers: dict, data: dict) -> CompsAPIError:
    err = str((data or {}).get("error") or "").strip()
    if status == 429 and "quota" in err.lower():
        return CompsAPIQuotaError(
            "CompsAPI quota used up for this month — searches are paused until "
            "the UTC month resets (no overage is charged). Use the Chrome path: "
            "python lib/ebay_sold_browse.py \"<query>\" --urls")
    if status == 429:
        wait = {k.lower(): v for k, v in (headers or {}).items()}.get("retry-after")
        return CompsAPIError(
            "CompsAPI per-minute rate limit hit"
            + (f"; retry in {wait}s." if wait else "; wait a minute and retry."))
    if status == 401:
        return CompsAPIError("CompsAPI rejected the key (401). Check COMPSAPI_API_KEY "
                             "or compsapi.api_key in the config.")
    if status == 403:
        return CompsAPIError(f"CompsAPI refused the request (403: {err or 'key expired or blocked'}).")
    if status == 400:
        return CompsAPIError(f"CompsAPI rejected the query (400: {err or 'bad parameter'}).")
    return CompsAPIError(f"CompsAPI error {status}: {err or 'no detail'}")


def check_quota(client: CompsAPIClient) -> dict:
    """Read /v1/usage (free) and fail closed if a visible cap is used up.

    Paid monthly plans don't expose their monthly total here; for those the
    search's own 429 `quota_reached` is the stop, mapped to the same error.
    """
    u = client.usage()
    used, cap = u.get("used"), u.get("daily_cap")
    if isinstance(used, (int, float)) and isinstance(cap, (int, float)) and cap and used >= cap:
        raise CompsAPIQuotaError(
            f"CompsAPI cap reached ({int(used)}/{int(cap)} for {u.get('day_utc') or 'today'}). "
            "Use the Chrome path: python lib/ebay_sold_browse.py \"<query>\" --urls")
    return u


def usage_line(u: dict) -> str:
    if u.get("daily_cap"):
        return f"CompsAPI usage: {u.get('used')}/{u.get('daily_cap')} ({u.get('day_utc')})"
    return "CompsAPI usage: monthly total is on the console dashboard"


# ---------------------------------------------------------------------------
# Rows -> CompRecord
# ---------------------------------------------------------------------------

def items_to_comps(items: list[dict], query: Optional[str] = None) -> list[CompRecord]:
    """CompsAPI /v1/sold items -> CompRecords (delivered basis computed)."""
    comps: list[CompRecord] = []
    for r in items or []:
        price, title = r.get("price"), r.get("title")
        if price is None or not title:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        ship = r.get("shipping_cost")
        ship = float(ship) if isinstance(ship, (int, float)) else None
        bo = r.get("best_offer") is True          # null = couldn't check
        lt = "Best Offer" if bo else _FORMAT.get(str(r.get("format") or "").upper())
        last = r.get("last_sold")
        comps.append(CompRecord(
            title=title,
            sold_price=price,
            url=r.get("url") or "",
            item_id=str(r["item_id"]) if r.get("item_id") is not None else None,
            sold_date=str(last)[:10] if last else None,
            condition=r.get("condition"),
            shipping_cost=ship,
            shipping_type="free" if ship == 0 else None,
            total_price=round(price + ship, 2) if ship is not None else None,
            listing_type=lt,
            bo_accepted=bo,
            sold_currency="USD",
            keyword_tag=query,
            raw=r,
        ))
    return comps


def fetch_comps(client: CompsAPIClient, query: str, *, days: int = DEFAULT_DAYS,
                condition: Optional[str] = None) -> tuple[list[CompRecord], dict, dict]:
    """Quota check, then one /v1/sold page. -> (comps, response, usage)."""
    u = check_quota(client)
    data = client.sold(query, days=days, condition=condition)
    return items_to_comps(data.get("items") or [], query), data, u


def save_compsapi_run(comps: list[CompRecord], query: str, raw_items: list,
                      save_dir=None) -> str:
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    return save_run_json(f"compsapi-sold-{stamp}", SOURCE, [query], raw_items,
                         comps, save_dir)


# ---------------------------------------------------------------------------
# Compare against recent browser runs (live check once the key exists)
# ---------------------------------------------------------------------------

def recent_browser_runs(n: int, runs_dir=None) -> list[Path]:
    base = Path(runs_dir) if runs_dir else default_runs_dir()
    files = sorted(base.glob("apify_run_*_browse-*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    out, seen = [], set()
    for p in files:
        try:
            q = (json.loads(p.read_text(encoding="utf-8")).get("queries") or [""])[0]
        except (OSError, json.JSONDecodeError):
            continue
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(p)
        if len(out) >= n:
            break
    return out


def compare_run(browser_run: dict, comps: list[CompRecord]) -> dict:
    """Item-id overlap and price agreement between one browser run and CompsAPI."""
    api = {c.item_id: c for c in comps if c.item_id}
    matched, same, diff, bo_checks = 0, 0, [], []
    for b in browser_run.get("comps") or []:
        c = api.get(str(b.get("item_id") or ""))
        if not c:
            continue
        matched += 1
        if b.get("bo_accepted"):
            # Browser shows the ASK; CompsAPI claims the accepted price.
            bo_checks.append((c.item_id, b.get("sold_price"), c.sold_price, c.bo_accepted))
        elif abs(float(b.get("sold_price") or 0) - c.sold_price) < 0.01:
            same += 1
        else:
            diff.append((c.item_id, b.get("sold_price"), c.sold_price))
    return {"query": (browser_run.get("queries") or [""])[0],
            "browser_n": len(browser_run.get("comps") or []), "api_n": len(comps),
            "matched": matched, "same_price": same, "price_diff": diff,
            "best_offer": bo_checks}


# ---------------------------------------------------------------------------
# Secondary source: merge into a Chrome run, exact matches only
# ---------------------------------------------------------------------------

CHROME, COMPSAPI = "chrome", "compsapi"
NO_CONDITION_REASON = "CompsAPI row has no condition to verify"


def _stats_comp(d: dict):
    """A row dict -> price_stats.Comp, just enough for its match filters."""
    import price_stats
    sold = float(d.get("sold_price") or 0)
    return price_stats.Comp(
        title=str(d.get("title") or ""), price=sold, sold_price=sold,
        total_price=d.get("total_price"), url=str(d.get("url") or ""),
        condition=d.get("condition"),
        cohort=price_stats._condition_cohort(d.get("condition")))


def exact_match_reduce(chrome_rows: list[dict], api_rows: list[dict], *,
                       unit: str, require_tokens: list[str],
                       condition: Optional[str]) -> tuple[list[dict], list[dict]]:
    """Keep only exact matches from both sources -> (kept, dropped).

    Runs price_stats' own match filters (unit, core tokens, condition cohort);
    there is no second matcher here. Chrome rows get price_stats' usual
    condition rule (the search URL already filtered condition, so a missing
    per-row condition is pooled the same way price_stats pools it). CompsAPI
    rows are held to the stricter rule: the row must carry a condition in the
    target cohort, or it is dropped as unverifiable. The live test returned
    parts listings for a whole machine with `condition: null`.
    """
    import price_stats
    pairs = [(d, _stats_comp(d)) for d in chrome_rows + api_rows]
    comps = [c for _, c in pairs]
    price_stats.filter_unit(comps, unit)
    price_stats.filter_tokens(comps, require_tokens)

    n_chrome = len(chrome_rows)
    price_stats.filter_condition(comps[:n_chrome], condition)
    target = price_stats._condition_cohort(condition)
    for c in comps[n_chrome:]:
        if c.dropped:
            continue
        if c.cohort == "unknown":
            c.drop_reason = NO_CONDITION_REASON
        elif c.cohort != target:
            c.drop_reason = f"condition '{c.condition}' not in {target} cohort"

    kept, dropped = [], []
    for d, c in pairs:
        if c.dropped:
            dropped.append({"title": d.get("title"), "item_id": d.get("item_id"),
                            "comp_source": d.get("comp_source"),
                            "reason": c.drop_reason})
        else:
            kept.append(d)
    return kept, dropped


def merge_with_chrome(chrome_run: dict, api_comps: Optional[list[dict]], *,
                      unit: str, require_tokens: list[str], condition: Optional[str],
                      compsapi_note: Optional[str] = None) -> dict:
    """Chrome run + CompsAPI comp dicts (comp_to_dict shape) -> one
    exact-match run payload.

    `api_comps=None` means CompsAPI was skipped (quota, key, network); the
    Chrome rows still stand and `sources.compsapi.note` says why. CompsAPI
    rows whose item_id is already in the Chrome run are dropped as duplicates.
    Every kept row carries `comp_source`: "chrome" or "compsapi".
    """
    chrome_rows = [{**r, "comp_source": CHROME} for r in chrome_run.get("comps") or []]
    seen = {str(r["item_id"]) for r in chrome_rows if r.get("item_id")}
    api_rows, dupes = [], 0
    for c in api_comps or []:
        iid = str(c["item_id"]) if c.get("item_id") else None
        if iid and iid in seen:
            dupes += 1
            continue
        if iid:
            seen.add(iid)
        api_rows.append({**c, "comp_source": COMPSAPI})

    kept, dropped = exact_match_reduce(chrome_rows, api_rows, unit=unit,
                                       require_tokens=require_tokens,
                                       condition=condition)
    n_kept = {s: sum(1 for r in kept if r["comp_source"] == s) for s in (CHROME, COMPSAPI)}
    return {
        "source": "merged:chrome+compsapi",
        "queries": list(chrome_run.get("queries") or []),
        "n_comps": len(kept),
        "charm_price_share": chrome_run.get("charm_price_share"),
        "currency_leak_suspected": chrome_run.get("currency_leak_suspected"),
        "exact_match": {"unit": unit, "require_tokens": list(require_tokens),
                        "condition": condition,
                        "condition_cohort": _cohort(condition)},
        "sources": {
            CHROME: {"run_id": chrome_run.get("run_id"), "n_raw": len(chrome_rows),
                     "n_kept": n_kept[CHROME]},
            COMPSAPI: {"status": "skipped" if api_comps is None else "ok",
                       "note": compsapi_note, "n_raw": len(api_comps or []),
                       "n_duplicate": dupes, "n_kept": n_kept[COMPSAPI]},
        },
        "comps": kept,
        "dropped": dropped,
        "raw_items": [],
    }


def _cohort(condition: Optional[str]) -> str:
    import price_stats
    return price_stats._condition_cohort(condition)


def save_merged_run(payload: dict, save_dir=None) -> str:
    base = Path(save_dir) if save_dir else default_runs_dir()
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    run_id = f"merged-chrome-compsapi-{stamp}"
    path = base / f"apify_run_{stamp}_{run_id}.json"
    out = {"run_id": run_id, "actor": payload["source"], "saved_at_utc": stamp, **payload}
    path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return str(path)


def merge_line(payload: dict) -> str:
    s = payload["sources"]
    line = (f"Exact matches: {payload['n_comps']} "
            f"(chrome {s[CHROME]['n_kept']}/{s[CHROME]['n_raw']}, "
            f"compsapi {s[COMPSAPI]['n_kept']}/{s[COMPSAPI]['n_raw']}, "
            f"{s[COMPSAPI]['n_duplicate']} duplicate of chrome)")
    if s[COMPSAPI]["status"] == "skipped":
        line += f"\n  CompsAPI skipped: {s[COMPSAPI]['note']} Chrome results stand."
    else:
        unverified = sum(1 for d in payload["dropped"]
                         if d["reason"] == NO_CONDITION_REASON)
        if unverified:
            line += f"\n  {unverified} CompsAPI row(s) dropped: no condition to verify."
    return line


def _client_from_config() -> CompsAPIClient:
    from config import ConfigError, get_compsapi_key
    try:
        return CompsAPIClient(get_compsapi_key())
    except ConfigError as e:
        raise CompsAPIError(str(e)) from e


def _api_rows_for_merge(query: str, args) -> tuple[Optional[list[dict]], Optional[str]]:
    """Today's cached CompsAPI run, else one fresh search. Any CompsAPI
    failure -> (None, reason): the caller keeps the Chrome rows."""
    parts = (query.lower(), args.condition or "any", args.days)
    if not args.fresh:
        hit = read_cache.get(_CACHE_NS, *parts)
        if hit.hit and isinstance(hit.value, dict) and Path(hit.value.get("path", "")).exists():
            run = json.loads(Path(hit.value["path"]).read_text(encoding="utf-8"))
            return run.get("comps") or [], "today's cached run, no request spent"
    try:
        comps, data, u = fetch_comps(_client_from_config(), query,
                                     days=args.days, condition=args.condition)
    except CompsAPIError as e:
        return None, str(e).split(" Use the Chrome path")[0]
    path = save_compsapi_run(comps, query, data.get("items") or [], args.save_dir)
    read_cache.put(_CACHE_NS, *parts, value={"n": len(comps), "path": path})
    return [comp_to_dict(c) for c in comps], usage_line(u)


def _merge_cli(args, query: str) -> None:
    chrome_run = json.loads(Path(args.with_chrome).read_text(encoding="utf-8"))
    query = query or (chrome_run.get("queries") or [""])[0]
    if not query:
        raise CompsAPIError("No query given and the Chrome run has none.")
    api_rows, note = _api_rows_for_merge(query, args)
    payload = merge_with_chrome(chrome_run, api_rows, unit=args.unit,
                                require_tokens=args.require_tokens,
                                condition=args.condition, compsapi_note=note)
    path = save_merged_run(payload, args.save_dir)
    print(merge_line(payload))
    if api_rows is not None and note:
        print(f"  {note}")
    print(f"Saved results: {path}")
    print(f"Next: python lib/price_stats.py --best-match {path} --unit {args.unit} "
          f"--condition {args.condition} --price-field total")


def _cli() -> None:
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="Stage B secondary source: eBay sold comps from CompsAPI, "
                    "merged after the Chrome run (ebay_sold_browse.py) with --with-chrome.")
    ap.add_argument("query", nargs="*", help="Search keywords.")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"Look-back window, 1-{MAX_DAYS} (default {DEFAULT_DAYS}).")
    ap.add_argument("--condition", choices=["new", "used", "open_box", "refurbished", "parts"])
    ap.add_argument("--save-dir")
    ap.add_argument("--no-save", action="store_true", help="Print comps as JSON instead.")
    ap.add_argument("--fresh", action="store_true",
                    help="Bypass the same-day cache and spend a request anyway.")
    ap.add_argument("--usage", action="store_true", help="Print quota (free call) and exit.")
    ap.add_argument("--with-chrome", metavar="RUN_JSON",
                    help="Secondary-source mode: merge into this Chrome best_match run, "
                         "dedupe by item_id and keep exact matches only. Needs "
                         "--condition. A quota/key error leaves the Chrome rows.")
    ap.add_argument("--unit", default="single",
                    help="With --with-chrome: unit being priced (single/pair/set/lot).")
    ap.add_argument("--require-tokens", nargs="*", default=[],
                    help="With --with-chrome: core tokens every comp title must carry.")
    ap.add_argument("--compare-recent", type=int, metavar="N",
                    help="Re-run the last N browser-run queries through CompsAPI "
                         "and report item-id/price agreement (costs N requests).")
    args = ap.parse_args()
    query = " ".join(args.query).strip()

    try:
        if args.usage:
            print(usage_line(check_quota(_client_from_config())))
            return

        if args.compare_recent:
            runs = recent_browser_runs(args.compare_recent, args.save_dir)
            if not runs:
                raise CompsAPIError(f"No browser runs (apify_run_*_browse-*.json) in "
                                    f"{args.save_dir or default_runs_dir()} to compare.")
            client = _client_from_config()
            for p in runs:
                run = json.loads(p.read_text(encoding="utf-8"))
                comps, _, _ = fetch_comps(client, run["queries"][0], days=args.days)
                r = compare_run(run, comps)
                print(f"{r['query']!r}: browser {r['browser_n']} · api {r['api_n']} · "
                      f"same item {r['matched']} · same price {r['same_price']}")
                for iid, b, a in r["price_diff"]:
                    print(f"    price differs {iid}: browser ${b} vs api ${a}")
                for iid, ask, a, flagged in r["best_offer"]:
                    print(f"    Best Offer {iid}: browser ask ${ask} vs api ${a}"
                          f"{'' if flagged else '  (api did NOT flag it)'}")
            return

        if args.with_chrome:
            if not args.condition:
                ap.error("--with-chrome needs --condition (exact match includes condition)")
            _merge_cli(args, query)
            return

        if not query:
            ap.error("a query is required")

        parts = (query.lower(), args.condition or "any", args.days)
        if not args.fresh and not args.no_save:
            hit = read_cache.get(_CACHE_NS, *parts)
            if hit.hit and isinstance(hit.value, dict) and Path(hit.value.get("path", "")).exists():
                print(f"OK cache hit — today's CompsAPI comps for {query!r}: "
                      f"n={hit.value.get('n')}  {hit.value['path']}")
                print("No request spent. Use --fresh to pull again.")
                return

        comps, data, u = fetch_comps(_client_from_config(), query,
                                     days=args.days, condition=args.condition)
    except CompsAPIError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(3 if isinstance(e, CompsAPIQuotaError) else 2)

    if not comps:
        print(f"ERROR: CompsAPI returned no sold comps for {query!r} "
              f"in {args.days} days.", file=sys.stderr)
        sys.exit(1)
    if args.no_save:
        print(json.dumps([comp_to_dict(c) for c in comps], indent=2))
        return
    path = save_compsapi_run(comps, query, data.get("items") or [], args.save_dir)
    read_cache.put(_CACHE_NS, *parts, value={"n": len(comps), "path": path})
    n_bo = sum(1 for c in comps if c.bo_accepted)
    n_ship = sum(1 for c in comps if c.total_price is not None)
    print(f"CompsAPI: {len(comps)} sold comp(s) for {query!r} ({args.days}d, newest first)")
    print(f"  delivered {n_ship}/{len(comps)} · Best-Offer-accepted {n_bo} "
          f"(dropped by price_stats)")
    print(f"  {usage_line(u)}")
    print(f"Saved results: {path}")
    print(f"Next: python lib/price_stats.py --best-match {path} --price-field total")


if __name__ == "__main__":
    _cli()
