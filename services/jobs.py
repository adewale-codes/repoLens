"""Background ingest jobs.

POST /ingest records a job and returns at once. The pipeline runs on a single
background worker thread that takes jobs from an in-process queue, in order.
Status and progress are written to the `jobs` table as each stage starts, so
GET /ingest/{job_id} reads them straight from SQLite.

Why one daemon thread and a queue.Queue, not a task queue, asyncio, or a pool:

- The pipeline is synchronous, CPU-bound code (git, parsing, ONNX embedding).
  Under asyncio it would still need `to_thread`, so asyncio adds a layer
  without removing the thread.
- One worker, not several: embedding already saturates every core (ONNX
  Runtime runs its own thread pool), so two ingests at once would each run
  at roughly half speed and double peak memory. A FIFO queue makes the
  "queued" status mean something.
- A daemon thread, not ThreadPoolExecutor: the executor's workers are joined
  at interpreter exit, so stopping the server mid-embed would hang for up to
  15 minutes. A daemon thread dies with the process.
- No Celery/RQ/Redis: those buy durability across restarts and multiple
  machines, at the cost of a broker to run. For a single-process portfolio
  service, the cheaper fix for restarts is the one below.

The trade-off: the queue lives in memory. If the process stops, queued and
running jobs are lost. `recover_interrupted_jobs()` runs at startup and marks
them failed with a clear error, so a client never polls a job that will never
move. Re-submitting is the recovery.
"""

import logging
import queue
import threading
import time
import uuid

from . import store
from .fetch import FetchError, parse_github_url
from .ingest import ingest

log = logging.getLogger(__name__)

STAGES = ("queued", "fetching", "parsing", "embedding", "storing", "complete", "failed")

# Embedding reports after every batch (~every few seconds). Throttle the
# database writes; stage changes are always written immediately.
_PROGRESS_MIN_INTERVAL_S = 1.0

_queue: "queue.Queue[str]" = queue.Queue()
_submit_lock = threading.Lock()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


class JobConflict(Exception):
    """A job for this repo is already queued or running."""

    def __init__(self, job_id: str):
        super().__init__(job_id)
        self.job_id = job_id


def submit(repo_url: str) -> str:
    """Record a queued ingest job and hand it to the worker. Returns the job id.

    Raises FetchError for a URL that isn't a GitHub repo URL (nothing to queue),
    and JobConflict if the same repo already has a job in flight.
    """
    owner, name = parse_github_url(repo_url)
    repo_id = f"{owner}/{name}"
    with _submit_lock:  # check-then-create must be atomic across request threads
        existing = store.active_job_for_repo(repo_id)
        if existing:
            raise JobConflict(existing)
        job_id = uuid.uuid4().hex
        store.create_job(job_id, repo_url, repo_id)
    _ensure_worker()
    _queue.put(job_id)
    return job_id


def recover_interrupted_jobs() -> int:
    """At startup: fail jobs left queued or running by a previous process."""
    count = store.fail_unfinished_jobs(
        "Interrupted: the server stopped before this job finished. Submit the ingest again."
    )
    if count:
        log.warning("Marked %d interrupted ingest job(s) as failed", count)
    return count


def run_job(job_id: str) -> None:
    """Run one job, recording each stage. Pipeline errors end the job as failed
    with a message; they're never raised to the caller."""
    job = store.get_job(job_id)
    if job is None:
        return
    store.update_job(job_id, started_at=time.time())
    last_stage, last_write = "queued", 0.0

    def on_progress(stage: str, detail: dict | None) -> None:
        nonlocal last_stage, last_write
        now = time.monotonic()
        if stage != last_stage or now - last_write >= _PROGRESS_MIN_INTERVAL_S:
            store.update_job(job_id, status=stage, progress=detail)
            last_stage, last_write = stage, now

    try:
        report = ingest(job["repo_url"], on_progress=on_progress)
    except FetchError as e:
        _fail(job_id, str(e))
    except Exception as e:  # anything else is a bug; keep the job honest and log the traceback
        log.exception("Ingest job %s failed", job_id)
        _fail(job_id, f"Internal error: {type(e).__name__}: {e}")
    else:
        store.update_job(
            job_id, status="complete", completed_at=time.time(), progress=None, result=report.__dict__
        )


def _fail(job_id: str, error: str) -> None:
    store.update_job(job_id, status="failed", completed_at=time.time(), error=error)


def _worker_loop() -> None:
    while True:
        job_id = _queue.get()
        try:
            run_job(job_id)
        except Exception:  # e.g. the database itself failing; keep serving the queue
            log.exception("Ingest worker error on job %s", job_id)
        finally:
            _queue.task_done()


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, name="ingest-worker", daemon=True)
            _worker.start()
