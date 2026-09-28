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
shoot's already-written `draft.md`/`price.txt`/`review_card.md`. It calls
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

from fastapi import FastAPI, HTTPException          # noqa: E402
from fastapi.responses import HTMLResponse          # noqa: E402
from pydantic import BaseModel                       # noqa: E402

import pick_store as _pick_store                     # noqa: E402
import tools.dashboard as _dashboard                 # noqa: E402
import tools.review_card_html as _review_card_html   # noqa: E402
from webapp.jobs import JOB_HANDLERS, _resolve_shoot_dir  # noqa: E402
from webapp.queue import Job, JobQueue                # noqa: E402

DEFAULT_DB_PATH = REPO / "reports" / "jobs.db"
DEFAULT_PORT = _pick_store.DEFAULT_PORT   # one value; pick sheet links embed it

app = FastAPI(title="ebay-ops (local, #31 Phase 2)")
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


class EnqueueRequest(BaseModel):
    type: str
    params: dict = {}


def _job_dict(job: Job) -> dict:
    return {
        "id": job.id, "type": job.type, "params": job.params,
        "status": job.status, "result": job.result, "error": job.error,
        "created_at": job.created_at, "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return _dashboard.draw(_dashboard.gather())


@app.get("/review/{shoot}", response_class=HTMLResponse)
def review(shoot: str) -> str:
    """The REVIEW gate page, live — same secret-free render
    tools/review_card_html.py has always produced, from a shoot's own
    draft.md/price.txt/review_card.md. No eBay/Apify call, no write: this
    is strictly a faster way to look at what a chat session already
    produced, not a new way to decide anything (see _shared.md's Publish
    firewall — nothing here approves or publishes)."""
    try:
        shoot_dir = _resolve_shoot_dir(shoot)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not (shoot_dir / "draft.md").exists():
        raise HTTPException(
            status_code=404,
            detail=f"{shoot!r} has no draft.md yet — DRAFT hasn't run")
    return _review_card_html.render(shoot_dir)


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
    uvicorn.run(app, host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
