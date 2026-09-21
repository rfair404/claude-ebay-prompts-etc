"""tests/test_webapp_queue.py — webapp/queue.py's SQLite job queue (#31
Phase 2): the "boring" job runner docs/webapp-architecture.md asks for.

No FastAPI, no real prep/price handlers here — those are webapp/jobs.py's
job and tests/test_webapp_jobs.py's coverage. This file locks down the
queue's own contract: enqueue -> dispatch -> status, independent of what a
handler actually does.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from webapp.queue import JobQueue  # noqa: E402


@pytest.fixture
def queue(tmp_path):
    q = JobQueue(tmp_path / "jobs.db", {
        "echo": lambda params: {"echoed": params},
        "boom": lambda params: (_ for _ in ()).throw(RuntimeError("kaboom")),
    })
    yield q
    q.shutdown()


def test_enqueue_unknown_type_raises(queue):
    with pytest.raises(ValueError, match="unknown job type"):
        queue.enqueue("no-such-job", {})


def test_enqueue_runs_and_reaches_done(queue):
    job_id = queue.enqueue("echo", {"x": 1})
    job = queue.wait(job_id, timeout=5)
    assert job.status == "done"
    assert job.result == {"echoed": {"x": 1}}
    assert job.error is None
    assert job.started_at is not None
    assert job.finished_at is not None
    assert job.started_at >= job.created_at


def test_a_raising_handler_lands_in_error_not_a_crash(queue):
    job_id = queue.enqueue("boom", {})
    job = queue.wait(job_id, timeout=5)
    assert job.status == "error"
    assert job.result is None
    assert "kaboom" in job.error


def test_get_missing_job_returns_none(queue):
    assert queue.get(999999) is None


def test_list_is_newest_first_and_respects_limit(queue):
    ids = [queue.enqueue("echo", {"n": i}) for i in range(5)]
    for i in ids:
        queue.wait(i, timeout=5)
    rows = queue.list(limit=3)
    assert [j.id for j in rows] == list(reversed(ids))[:3]


def test_params_round_trip_through_json(queue):
    params = {"a": [1, 2, 3], "b": {"nested": True}, "c": None}
    job_id = queue.enqueue("echo", params)
    job = queue.wait(job_id, timeout=5)
    assert job.params == params
    assert job.result == {"echoed": params}


def test_two_jobs_both_complete_under_the_default_pool(queue):
    # max_workers=2 by default — this is the "two operators' worth of
    # concurrent jobs" case the SQLite write-lock (JobQueue's own
    # threading.Lock) exists to serialize without either job losing its row.
    a = queue.enqueue("echo", {"job": "a"})
    b = queue.enqueue("echo", {"job": "b"})
    ja = queue.wait(a, timeout=5)
    jb = queue.wait(b, timeout=5)
    assert ja.status == jb.status == "done"
    assert ja.result == {"echoed": {"job": "a"}}
    assert jb.result == {"echoed": {"job": "b"}}
