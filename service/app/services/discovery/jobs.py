"""A tiny in-process job registry for long-running discovery scans.

A LAN sweep can take a few seconds, longer than a browser wants to hold a
request open, so the scan runs in a background thread and the UI polls for its
state. Only one scan runs at a time (a second ``start`` while one is running
raises ``JobBusy`` so the router can answer 409): a home LAN sweep is cheap but
concurrent sweeps would just fight for the same sockets. Finished jobs linger
briefly so a poll right after completion still sees the result, then expire.

The registry is a plain module-level dict guarded by a lock, so it is pure and
testable: ``start`` a fast function, poll ``get`` until it is done, read the
results.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable, Optional

# How long a finished (done/error) job stays readable before it is dropped, so a
# late poll still sees the result but the dict does not grow without bound.
_JOB_TTL = 600.0

_JOBS: dict[str, dict] = {}
_LOCK = threading.Lock()


class JobBusy(Exception):
    """Raised by ``start`` when a scan is already running (router answers 409)."""


# A scan function is called with one argument: a progress reporter it may call as
# ``report(done, total)`` to update the job. It returns the results (any
# JSON-serialisable value) or raises.
ScanFn = Callable[[Callable[[int, int], None]], Any]


def _expire_locked() -> None:
    now = time.time()
    dead = [jid for jid, j in _JOBS.items()
            if j["status"] in ("done", "error")
            and j.get("finished_at") is not None
            and now - j["finished_at"] > _JOB_TTL]
    for jid in dead:
        _JOBS.pop(jid, None)


def _running_locked() -> bool:
    return any(j["status"] == "running" for j in _JOBS.values())


def start(fn: ScanFn) -> str:
    """Run ``fn`` in a background thread and return its job id.

    Raises ``JobBusy`` if another job is still running. ``fn`` receives a
    ``report(done, total)`` callback it may call to publish progress.
    """
    with _LOCK:
        _expire_locked()
        if _running_locked():
            raise JobBusy("A scan is already running. Wait for it to finish.")
        jid = uuid.uuid4().hex[:12]
        job = {
            "id": jid,
            "status": "running",
            "progress": None,
            "results": None,
            "error": None,
            "started_at": time.time(),
            "finished_at": None,
        }
        _JOBS[jid] = job

    def _report(done: int, total: int) -> None:
        with _LOCK:
            job["progress"] = {"done": int(done), "total": int(total)}

    def _run() -> None:
        try:
            results = fn(_report)
            with _LOCK:
                job["results"] = results
                job["status"] = "done"
                job["finished_at"] = time.time()
        except Exception as exc:  # noqa: BLE001 - captured into the job, never raised
            with _LOCK:
                job["error"] = str(exc) or exc.__class__.__name__
                job["status"] = "error"
                job["finished_at"] = time.time()

    threading.Thread(target=_run, daemon=True).start()
    return jid


def get(job_id: str) -> Optional[dict]:
    """A copy of the job's state, or None if the id is unknown/expired."""
    with _LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def public(job: Optional[dict]) -> Optional[dict]:
    """The client-facing view of a job: id, status, progress, results, error."""
    if not job:
        return None
    return {
        "id": job.get("id"),
        "status": job.get("status"),
        "progress": job.get("progress"),
        "results": job.get("results"),
        "error": job.get("error"),
    }


def reset() -> None:
    """Drop all jobs. For tests and a clean shutdown."""
    with _LOCK:
        _JOBS.clear()
