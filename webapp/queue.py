"""webapp/queue.py — the "boring" job runner docs/webapp-architecture.md's
Phase 2 asks for: a SQLite job table plus a small thread pool, no Celery, no
Redis, no message broker. Sized for the load this pipeline actually has
(one operator, a handful of items in flight at once) — the doc's own
"Job runner" answer.

`JobQueue` knows nothing about prep, pricing, or eBay: it takes a plain
`{job_type: (params: dict) -> dict}` handler map (see webapp/jobs.py for the
actual Phase-2-whitelisted handlers) and is responsible only for
persistence, dispatch, and status — the same split `tools/dashboard.py`
already draws between "gather the data" and "draw the page."
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Union

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
"""


@dataclass
class Job:
    id: int
    type: str
    params: dict
    status: str          # queued -> running -> done | error
    result: Optional[dict]
    error: Optional[str]
    created_at: float
    started_at: Optional[float]
    finished_at: Optional[float]


def _row_to_job(row) -> Job:
    return Job(
        id=row[0], type=row[1], params=json.loads(row[2]), status=row[3],
        result=json.loads(row[4]) if row[4] else None, error=row[5],
        created_at=row[6], started_at=row[7], finished_at=row[8],
    )


_COLUMNS = ("id", "type", "params", "status", "result", "error",
            "created_at", "started_at", "finished_at")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM jobs"


class JobQueue:
    """SQLite-backed job queue. One instance per process — `enqueue()`
    submits the job to an internal thread pool immediately (no separate
    polling loop to keep alive or crash silently); the DB row is the only
    thing that needs to survive a restart.

    A single `threading.Lock` serializes every write. At this queue's
    designed volume that costs nothing measurable and avoids "database is
    locked" errors from two workers finishing at once — the ledger's own
    single-writer rule (RUN.md) applied to this table instead.
    """

    def __init__(self, db_path: Union[str, Path],
                 handlers: dict[str, Callable[[dict], dict]],
                 max_workers: int = 2):
        self._handlers = handlers
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        with self._lock:
            self._conn.execute(SCHEMA)
            self._conn.commit()
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                             thread_name_prefix="job")

    def enqueue(self, job_type: str, params: dict) -> int:
        if job_type not in self._handlers:
            raise ValueError(
                f"unknown job type: {job_type!r} — one of: "
                f"{', '.join(sorted(self._handlers))}")
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO jobs (type, params, status, created_at) "
                "VALUES (?, ?, 'queued', ?)",
                (job_type, json.dumps(params), now),
            )
            self._conn.commit()
            job_id = cur.lastrowid
        self._executor.submit(self._run, job_id)
        return job_id

    def _run(self, job_id: int) -> None:
        job = self.get(job_id)
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status='running', started_at=? WHERE id=?",
                (time.time(), job_id))
            self._conn.commit()
        try:
            result = self._handlers[job.type](job.params)
            with self._lock:
                self._conn.execute(
                    "UPDATE jobs SET status='done', result=?, finished_at=? "
                    "WHERE id=?",
                    (json.dumps(result), time.time(), job_id))
                self._conn.commit()
        except Exception:
            # The traceback is diagnostic detail for the operator, not
            # secret — every Phase-2 handler is local-only by construction
            # (webapp/jobs.py), so nothing sensitive lands here.
            err = traceback.format_exc(limit=8)
            with self._lock:
                self._conn.execute(
                    "UPDATE jobs SET status='error', error=?, finished_at=? "
                    "WHERE id=?",
                    (err, time.time(), job_id))
                self._conn.commit()

    def get(self, job_id: int) -> Optional[Job]:
        with self._lock:
            row = self._conn.execute(f"{_SELECT} WHERE id=?",
                                      (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def list(self, limit: int = 50) -> list[Job]:
        with self._lock:
            rows = self._conn.execute(
                f"{_SELECT} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_row_to_job(r) for r in rows]

    def wait(self, job_id: int, timeout: float = 5.0) -> Optional[Job]:
        """Poll until a job leaves queued/running, or timeout. A test/CLI
        convenience — the HTTP API (webapp/server.py) never calls this, it
        always returns immediately after enqueueing."""
        deadline = time.time() + timeout
        job = self.get(job_id)
        while job and job.status in ("queued", "running") and time.time() < deadline:
            time.sleep(0.01)
            job = self.get(job_id)
        return job

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
        self._conn.close()
