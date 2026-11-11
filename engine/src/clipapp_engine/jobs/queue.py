"""Serial background queue for heavy work (prepare, renders, transcription).

One worker thread runs jobs one at a time so the GPU is never shared. Every job is a row of
``export_jobs``; its state changes and (throttled) progress are pushed through ``emit``.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any, Literal

from clipapp_engine.db.database import Database
from clipapp_engine.media.errors import FFmpegCancelledError

log = logging.getLogger(__name__)

JobKind = Literal["prepare", "full", "clip", "transcribe"]
JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
ProgressFn = Callable[[float], None]
# A handler runs one job: (target_id, on_progress, cancel_event) -> output path or None.
Handler = Callable[[int, ProgressFn, threading.Event], str | None]
Emit = Callable[[dict[str, Any]], None]

INTERRUPTED = "interrupted"
PROGRESS_INTERVAL_S = 0.2  # at most 5 job_progress messages per second per job
_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


class JobCancelledError(Exception):
    """Raised by a handler that noticed its cancel event between steps."""


class JobQueue:
    """Single-worker job queue persisted in ``export_jobs``.

    Creating it marks jobs left ``queued`` or ``running`` by a previous process as ``failed``
    with the reason ``interrupted``.

    Args:
        db: Database holding ``export_jobs``.
        handlers: Function running each job kind.
        emit: Receives ``job_progress`` and ``job_status`` events (called from any thread).
    """

    def __init__(self, db: Database, handlers: dict[str, Handler], emit: Emit) -> None:
        """Recover interrupted jobs; the worker starts with ``start()``."""
        self.db, self.handlers, self.emit = db, handlers, emit
        self._pending: queue.Queue[int | None] = queue.Queue()
        self._cancels: dict[int, threading.Event] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        db.execute(
            f"UPDATE export_jobs SET status = 'failed', error = ?, finished_at = {_NOW} "
            "WHERE status IN ('queued', 'running')",
            (INTERRUPTED,),
        )

    def start(self) -> None:
        """Start the worker thread."""
        self._thread = threading.Thread(target=self._work, name="clipapp-jobs", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Cancel the running job and stop the worker (queued jobs stay queued)."""
        with self._lock:
            for event in self._cancels.values():
                event.set()
        self._pending.put(None)
        if self._thread is not None:
            self._thread.join()

    def submit(self, kind: JobKind, target_id: int) -> int:
        """Queue a job and return its id."""
        cur = self.db.execute(
            "INSERT INTO export_jobs (kind, target_id) VALUES (?, ?)", (kind, target_id)
        )
        job_id = int(cur.lastrowid or 0)
        self._enqueue(job_id)
        return job_id

    def get(self, job_id: int) -> dict[str, Any] | None:
        """Return a job row, or None."""
        return self.db.one("SELECT * FROM export_jobs WHERE id = ?", (job_id,))

    def cancel(self, job_id: int) -> bool:
        """Cancel a queued job at once or signal a running one; False if already finished."""
        with self._lock:
            event = self._cancels.get(job_id)
            if event is None:
                return False
            event.set()
        cur = self.db.execute(
            f"UPDATE export_jobs SET status = 'cancelled', finished_at = {_NOW} "
            "WHERE id = ? AND status = 'queued'",
            (job_id,),
        )
        if cur.rowcount:
            self._status(job_id)
        return True

    def retry(self, job_id: int) -> bool:
        """Queue a failed or cancelled job again; False if it is not in one of those states."""
        cur = self.db.execute(
            "UPDATE export_jobs SET status = 'queued', progress = 0, error = NULL, "
            "output_path = NULL, finished_at = NULL "
            "WHERE id = ? AND status IN ('failed', 'cancelled')",
            (job_id,),
        )
        if not cur.rowcount:
            return False
        self._enqueue(job_id)
        return True

    def _enqueue(self, job_id: int) -> None:
        with self._lock:
            self._cancels[job_id] = threading.Event()
        self._status(job_id)
        self._pending.put(job_id)

    def _status(self, job_id: int) -> None:
        job = self.get(job_id)
        if job is not None:
            self.emit(
                {
                    "type": "job_status",
                    "id": job_id,
                    "status": job["status"],
                    "error": job["error"],
                    "output_path": job["output_path"],
                }
            )

    def _work(self) -> None:
        while (job_id := self._pending.get()) is not None:
            with self._lock:
                cancel = self._cancels.get(job_id)
            job = self.get(job_id)
            if cancel is None or job is None or job["status"] != "queued":
                continue
            try:
                self._run(job, cancel)
            finally:
                with self._lock:
                    if self._cancels.get(job_id) is cancel:  # a retry may have re-armed it
                        del self._cancels[job_id]

    def _run(self, job: dict[str, Any], cancel: threading.Event) -> None:
        job_id = job["id"]
        self.db.execute("UPDATE export_jobs SET status = 'running' WHERE id = ?", (job_id,))
        self._status(job_id)
        last = 0.0

        def on_progress(value: float) -> None:
            nonlocal last
            now = time.monotonic()
            if now - last < PROGRESS_INTERVAL_S:
                return
            last = now
            self.db.execute("UPDATE export_jobs SET progress = ? WHERE id = ?", (value, job_id))
            self.emit({"type": "job_progress", "id": job_id, "progress": value})

        status, output, error = "done", None, None
        try:
            output = self.handlers[job["kind"]](job["target_id"], on_progress, cancel)
        except (FFmpegCancelledError, JobCancelledError):
            status = "cancelled"
        except Exception as exc:  # any handler failure becomes the job's error message
            log.exception("job %s failed", job_id)
            status, error = "failed", str(exc).strip() or type(exc).__name__
        self.db.execute(
            f"UPDATE export_jobs SET status = ?, error = ?, output_path = ?, "
            f"progress = CASE WHEN ? = 'done' THEN 1 ELSE progress END, finished_at = {_NOW} "
            "WHERE id = ?",
            (status, error, output, status, job_id),
        )
        self._status(job_id)
