"""webapp/server.py — Phase 2 of #31 (docs/webapp-architecture.md): a small,
localhost-only FastAPI app that

  (a) serves Phase 1's dashboard (tools/dashboard.py's `gather()`/`draw()`)
      as a live page instead of a generated file, and
  (b) wraps the secret-free half of the pipeline as background jobs on a
      SQLite-backed queue (webapp/queue.py, webapp/jobs.py), so routine
      work doesn't need a chat turn.

Binds to 127.0.0.1 ONLY — see the architecture doc's "Hosting" answer: no
phase before Phase 3 puts a process on a network-reachable interface at
all. Nothing here touches eBay or Apify credentials; every job type comes
from webapp/jobs.py's Phase-2 whitelist. Publish, offers, policy sweep, and
Apify comp pulls stay chat-only until Phase 3's secrets story lands.

The review page (`/review/{shoot}`) is read-only — it renders the same
secret-free page `tools/review_card_html.py` always has, live, from a
shoot's already-written `draft.md`/`price.txt`/`review_card.md`. It is the
default review link: `review_card_html.py` prints it, and review pages are
no longer uploaded to the offsite bucket. `{shoot}` is the shoot's path
under inventory/, nested or not (`fr/arquilt`, `estate-lots/lot-3`). It calls
no eBay/Apify API (those live behind `preflight_listing()`, which this
route never touches). Review-queue *writes* (approve a PREP stage, edit a
draft field — the third bullet of Phase 2's "What") are still left for a
follow-up PR.

`/pick/{token}` (#151) serves a published pick sheet so the pack step gets
a link instead of a file path. Unlike every other route here it serves
buyer PII, so it is the one route with rules of its own: an unguessable
token, expiry enforced on read, and no-store/noindex headers. Those rules,
and the store behind them, live in lib/pick_store.py. Localhost-only is
what currently keeps this honest — the link is clickable from this
machine's browser and nowhere else.

`/live/{show}` (host page), `/live/{show}/overlay` (OBS browser source) and
`/live/{show}/state` serve an eBay Live show from inventory/_live/<show>/
(lib/live_show.py). The lineup is built by tools/live_lineup.py, which does
the eBay read; these routes only read that file and write which lot is on
screen, so they stay eBay-free like the rest of the app.

`/api/pager` (webapp/pager.py) switches the show's OBS page: intro, item
(the camera view) or next. It is tools/live/show_flow.py's pager over
HTTP, for a control page or another agent; it talks to OBS on this
machine and to the camera over USB, never to eBay.

Stores (#156 §5). `GET /` covers every configured store — one drift panel
per store, each against its own ledger, never pooled — and `GET /?store=NAME`
narrows it to one (a malformed name is a 400, an unconfigured one a 404).
`/review/{shoot}` shows which store the draft will publish to, because since
#150 that is a property the reviewer is approving. `/api/jobs` stays
store-free: every whitelisted job is local and eBay-free (webapp/jobs.py).

`/freight` (webapp/freight.py) manages LTL freight: compare broker quotes,
book one (dry run, then an explicit confirm), track it. It is the one area
that calls a paid third-party API with a stored key — see that module's
docstring for why, and `freight.web_booking: false` to switch booking off.

    python -m lib.cli serve                 # -> http://127.0.0.1:8770
    python -m lib.cli serve --port 8080
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
for _p in (str(REPO), str(REPO / "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import Depends, FastAPI, HTTPException # noqa: E402
from fastapi.responses import HTMLResponse          # noqa: E402
from pydantic import BaseModel                       # noqa: E402

import live_show as _live                            # noqa: E402
import pick_store as _pick_store                     # noqa: E402
import stores as _stores                             # noqa: E402
import tools.dashboard as _dashboard                 # noqa: E402
import tools.review_card_html as _review_card_html   # noqa: E402
import webapp.freight as _freight                      # noqa: E402
import webapp.jobs as _jobs                            # noqa: E402
import webapp.pager as _pager                          # noqa: E402
from webapp.jobs import JOB_HANDLERS                  # noqa: E402
from webapp.queue import Job, JobQueue                # noqa: E402
from webapp.security import local_only                # noqa: E402

DEFAULT_DB_PATH = REPO / "reports" / "jobs.db"
DEFAULT_PORT = _pick_store.DEFAULT_PORT   # one value; pick sheet links embed it

# Every route, app-wide (#311 slice 1): a loopback Host, a loopback Origin on
# writes, and no-store headers. Until this, only /freight and /api/pager had
# it, so POST /api/jobs and POST /live/{show}/state were unguarded.
app = FastAPI(title="ebay-ops (local, #31 Phase 2)",
              dependencies=[Depends(local_only)])
app.include_router(_freight.router)   # /freight — LTL quotes, booking, tracking
app.include_router(_pager.router)     # /api/pager — which page of the live show is on air
_queue: Optional[JobQueue] = None


def get_queue() -> JobQueue:
    """Lazy singleton so importing this module (e.g. for tests) never opens
    the SQLite file — only actually handling a request does."""
    global _queue
    if _queue is None:
        _queue = make_queue(DEFAULT_DB_PATH)
    return _queue


def make_queue(db_path: Path) -> JobQueue:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return JobQueue(db_path, JOB_HANDLERS)


_pick_store_dir: Optional[Path] = None


def pick_store_dir() -> Path:
    return _pick_store_dir or _pick_store.STORE_DIR


def set_pick_store_dir(path: Optional[Path]) -> None:
    """Test hook — points /pick/{token} at a throwaway store instead of the
    real pick_lists/.served/, so no test can read or delete a live sheet."""
    global _pick_store_dir
    _pick_store_dir = path


def set_queue(queue: Optional[JobQueue]) -> None:
    """Test hook — points the app at a throwaway queue instead of the
    default reports/jobs.db, and lets a test reset to the lazy default."""
    global _queue
    _queue = queue


_live_base: Optional[Path] = None


def set_live_base(path: Optional[Path]) -> None:
    """Test hook — points /live/* at a throwaway folder instead of the real
    inventory/_live/."""
    global _live_base
    _live_base = path


class LiveStateRequest(BaseModel):
    lot: Optional[int] = None
    note: str = ""


class EnqueueRequest(BaseModel):
    # Deliberately store-free (#156): no job handler touches eBay. The day
    # one does, it names its store in `params` — see webapp/jobs.py.
    type: str
    params: dict = {}


# `ebz serve` runs this file through runpy as "__main__" without replacing
# sys.modules["__main__"], so pydantic can't find this module to resolve the
# postponed (string) annotations and every POST body 500s. Rebuilding here,
# at module level, resolves them against this namespace instead.
LiveStateRequest.model_rebuild()
EnqueueRequest.model_rebuild()


def _job_dict(job: Job) -> dict:
    return {
        "id": job.id, "type": job.type, "params": job.params,
        "status": job.status, "result": job.result, "error": job.error,
        "created_at": job.created_at, "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard(store: Optional[str] = None) -> str:
    """The dashboard, live. No `store`: every configured store, a drift panel
    each. `?store=NAME`: that store's drift and drafts only. The name is
    validated before it goes anywhere near a filename (stores.paths builds
    `listings_ledger-<store>.csv` from it)."""
    if store is None:
        return _dashboard.draw(_dashboard.gather())
    try:
        _stores.validate_store_name(store)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    known = _dashboard._store_names()
    if store not in known:
        raise HTTPException(
            status_code=404,
            detail=f"no store {store!r} configured (known: {', '.join(known)})")
    return _dashboard.draw(_dashboard.gather([store], only_store=store))


def _resolve_review_dir(shoot: str) -> Path:
    """A shoot path relative to inventory/, at any depth (`fr/arquilt`). One
    guard for pages and jobs alike: webapp/jobs.py's `_resolve_shoot_dir`."""
    return _jobs._resolve_shoot_dir(shoot)


@app.get("/review/{shoot:path}", response_class=HTMLResponse)
def review(shoot: str) -> str:
    """The REVIEW gate page, live — same secret-free render
    tools/review_card_html.py has always produced, from a shoot's own
    draft.md/price.txt/review_card.md. No eBay/Apify call, no write: this
    is strictly a faster way to look at what a chat session already
    produced, not a new way to decide anything (see _shared.md's Publish
    firewall — nothing here approves or publishes)."""
    try:
        shoot_dir = _resolve_review_dir(shoot)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not (shoot_dir / "draft.md").exists():
        raise HTTPException(
            status_code=404,
            detail=f"{shoot!r} has no draft.md yet — DRAFT hasn't run")
    return _review_card_html.render(shoot_dir)


def _live_lineup(show: str) -> dict:
    try:
        lineup = _live.load_lineup(show, _live_base)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if lineup is None:
        raise HTTPException(
            status_code=404,
            detail=f"show {show!r} has no lineup — run tools/live_lineup.py")
    return lineup


@app.get("/live/{show}", response_class=HTMLResponse)
def live_host(show: str) -> str:
    """eBay Live host page: the show's lineup, which lot is on screen, and a
    Copy-ID button per lot for Stream manager's "Add by Item ID". Reads
    inventory/_live/<show>/ only — the lineup itself is built (with the
    eBay read) by tools/live_lineup.py, so this route makes no eBay call."""
    lineup = _live_lineup(show)
    return _live.render_host(show, lineup, _live.load_state(show, _live_base))


@app.get("/live/{show}/lineup")
def live_lineup(show: str) -> dict:
    """The lineup as JSON, for the pager's lineup panel. No eBay call."""
    return _live_lineup(show)


@app.get("/live/{show}/overlay", response_class=HTMLResponse)
def live_overlay(show: str) -> str:
    """Transparent page for an OBS browser source over the camera feed."""
    _live_lineup(show)
    return _live.render_overlay(show)


@app.get("/live/{show}/state")
def live_state(show: str) -> dict:
    _live_lineup(show)
    return _live.current(show, _live_base)


@app.post("/live/{show}/state")
def set_live_state(show: str, req: LiveStateRequest) -> dict:
    _live_lineup(show)
    try:
        _live.save_state(show, req.lot, req.note, _live_base)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _live.current(show, _live_base)


@app.get("/healthz")
def healthz() -> dict:
    """Is the app up? Deliberately cheap and secret-free: tools that hand out
    a `/pick/...` link call this first so they can say "start the server"
    instead of printing a URL that answers nothing."""
    return {"ok": True}


@app.get("/pick/{token}", response_class=HTMLResponse)
def pick_sheet(token: str) -> HTMLResponse:
    """One published pick sheet, by its unguessable token (#151).

    This is the route that turns a pick sheet from a file path into a link.
    The page is exactly what tools/pick_list_html.py renders — self-contained
    HTML with the thumbnail inlined, so it prints from the browser the same
    way the local file always did.

    It serves buyer name and street address, which is why the token is 256
    random bits, why the sheet expires on its own (lib/pick_store.fetch()
    deletes an expired one as it answers), and why the response is
    no-store/noindex. There is no route that lists the store: a token is the
    only way in, and a sheet nobody holds a token for is unreachable until it
    expires. The app binds to 127.0.0.1 only — see the module docstring.

    404 = no such sheet, 410 = there was one and it has expired. The
    difference matters when someone re-opens yesterday's link and needs to
    know whether to re-run the tool or check the id."""
    html, status = _pick_store.fetch(token, store_dir=pick_store_dir())
    if status == "expired":
        raise HTTPException(
            status_code=410,
            detail="this pick sheet has expired and was deleted — re-run "
                   "`python -m lib.cli pick-list --poll` (or pick_list_html.py "
                   "for one order) to publish a fresh link")
    if status != "ok" or html is None:
        raise HTTPException(status_code=404, detail="no such pick sheet")
    return HTMLResponse(content=html, headers={
        # Buyer PII: never cached by an intermediary, never indexed, never
        # kept in a shared history. The page itself also carries a noindex
        # meta (tools/pick_list_html.py) for whatever ignores headers.
        "Cache-Control": "no-store, no-cache, must-revalidate, private",
        "X-Robots-Tag": "noindex, nofollow, noarchive",
        "Referrer-Policy": "no-referrer",
    })


@app.post("/api/jobs")
def enqueue_job(req: EnqueueRequest) -> dict:
    try:
        job_id = get_queue().enqueue(req.type, req.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs")
def list_jobs(limit: int = 50) -> list[dict]:
    return [_job_dict(j) for j in get_queue().list(limit=limit)]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: int) -> dict:
    job = get_queue().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return _job_dict(job)


def main(argv: Optional[list[str]] = None) -> int:
    import uvicorn
    ap = argparse.ArgumentParser(prog="ebz serve")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)
    # access_log off: URLs carry pick-sheet tokens today and OAuth codes later.
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
