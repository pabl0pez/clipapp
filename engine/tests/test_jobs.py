import threading
import time
from pathlib import Path
from typing import Any

import pytest

from clipapp_engine.db.database import MIGRATIONS, Database
from clipapp_engine.jobs.queue import INTERRUPTED, Handler, JobQueue, ProgressFn
from clipapp_engine.media.errors import FFmpegCancelledError

FINAL = ("done", "failed", "cancelled")


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "jobs.db")


def wait(q: JobQueue, job_id: int, timeout: float = 10) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = q.get(job_id)
        assert job is not None
        if job["status"] in FINAL:
            return job
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish")


def make_queue(db: Database, handler: Handler) -> tuple[JobQueue, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    q = JobQueue(db, {"clip": handler}, events.append)
    q.start()
    return q, events


def test_migrations_seed_once(tmp_path: Path) -> None:
    path = tmp_path / "x.db"
    Database(path).close()
    db = Database(path)
    assert db.version == len(MIGRATIONS)
    styles = db.all("SELECT name, builtin FROM subtitle_styles")
    assert [s["builtin"] for s in styles] == [1, 1, 1]
    assert db.one("SELECT count(*) AS n FROM app_settings") == {"n": 1}


def test_lifecycle_done_and_progress_throttle(db: Database) -> None:
    def handler(_target: int, progress: ProgressFn, _cancel: threading.Event) -> str:
        for i in range(200):
            progress(i / 200)
        return "out.mp4"

    q, events = make_queue(db, handler)
    job_id = q.submit("clip", 7)
    job = wait(q, job_id)
    q.stop()
    assert job["status"] == "done"
    assert job["output_path"] == "out.mp4"
    assert job["progress"] == 1
    statuses = [e["status"] for e in events if e["type"] == "job_status"]
    assert statuses == ["queued", "running", "done"]
    assert len([e for e in events if e["type"] == "job_progress"]) <= 2


def test_failure_keeps_message(db: Database) -> None:
    def handler(_t: int, _p: ProgressFn, _c: threading.Event) -> None:
        raise ValueError("boom: bad clip")

    q, events = make_queue(db, handler)
    job = wait(q, q.submit("clip", 1))
    q.stop()
    assert job["status"] == "failed"
    assert job["error"] == "boom: bad clip"
    assert events[-1]["error"] == "boom: bad clip"


def test_cancel_running_and_queued(db: Database) -> None:
    started = threading.Event()
    ran: list[int] = []

    def handler(target: int, _p: ProgressFn, cancel: threading.Event) -> None:
        ran.append(target)
        started.set()
        cancel.wait(5)
        raise FFmpegCancelledError("ffmpeg was cancelled")

    q, _ = make_queue(db, handler)
    first, second = q.submit("clip", 1), q.submit("clip", 2)
    assert started.wait(5)
    assert q.cancel(second)
    assert q.get(second)["status"] == "cancelled"  # type: ignore[index]
    assert q.cancel(first)
    assert wait(q, first)["status"] == "cancelled"
    assert not q.cancel(first)  # already finished
    time.sleep(0.1)
    q.stop()
    assert ran == [1]


def test_retry(db: Database) -> None:
    attempts: list[int] = []

    def handler(_t: int, _p: ProgressFn, _c: threading.Event) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("first try fails")

    q, _ = make_queue(db, handler)
    job_id = q.submit("clip", 1)
    assert wait(q, job_id)["status"] == "failed"
    assert q.retry(job_id)
    job = wait(q, job_id)
    assert not q.retry(job_id)  # done jobs cannot be retried
    q.stop()
    assert job["status"] == "done"
    assert job["error"] is None


def test_restart_marks_unfinished_jobs_interrupted(db: Database) -> None:
    for status in ("queued", "running", "done"):
        db.execute(
            "INSERT INTO export_jobs (kind, target_id, status) VALUES ('clip', 1, ?)", (status,)
        )
    JobQueue(db, {}, lambda _e: None)
    rows = db.all("SELECT status, error FROM export_jobs ORDER BY id")
    assert rows == [
        {"status": "failed", "error": INTERRUPTED},
        {"status": "failed", "error": INTERRUPTED},
        {"status": "done", "error": None},
    ]
