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

Review-queue local writes (approve a PREP stage, edit a draft field —
the third bullet of Phase 2's "What") are left for a follow-up PR: this one
covers the live dashboard and the job queue itself, the two pieces every
later write-endpoint will sit on top of.

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

import tools.dashboard as _dashboard                 # noqa: E402
from webapp.jobs import JOB_HANDLERS                 # noqa: E402
from webapp.queue import Job, JobQueue                # noqa: E402

DEFAULT_DB_PATH = REPO / "reports" / "jobs.db"
DEFAULT_PORT = 8770

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
