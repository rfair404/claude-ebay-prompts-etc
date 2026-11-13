"""ebaybiz — Stage B fallback: eBay SOLD comps from CompsAPI (compsapi.com).

The logged-in Chrome path (`ebay_sold_browse.py`) stays PRICE's primary Stage
B source. This module is the selectable fallback for when Chrome isn't
available (a cloud session, a challenge page, a headless run). It is a paid
REST vendor with a Bearer key, NOT Apify: Apify stays retired (lib/README.md).

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
raises CompsAPIError with a plain message and exits non-zero. Nothing here
falls through to another source silently; switching sources is PRICE's call.

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


def _client_from_config() -> CompsAPIClient:
    from config import ConfigError, get_compsapi_key
    try:
        return CompsAPIClient(get_compsapi_key())
    except ConfigError as e:
        raise CompsAPIError(str(e)) from e


def _cli() -> None:
    import argparse
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="Stage B fallback: eBay sold comps from CompsAPI -> run JSON "
                    "for price_stats.py. Chrome (ebay_sold_browse.py) stays primary.")
    ap.add_argument("query", nargs="*", help="Search keywords.")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"Look-back window, 1-{MAX_DAYS} (default {DEFAULT_DAYS}).")
    ap.add_argument("--condition", choices=["new", "used", "open_box", "refurbished", "parts"])
    ap.add_argument("--save-dir")
    ap.add_argument("--no-save", action="store_true", help="Print comps as JSON instead.")
    ap.add_argument("--fresh", action="store_true",
                    help="Bypass the same-day cache and spend a request anyway.")
    ap.add_argument("--usage", action="store_true", help="Print quota (free call) and exit.")
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
