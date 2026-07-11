"""The in-process discovery job registry."""
import threading
import time

import pytest

from app.services.discovery import jobs


@pytest.fixture(autouse=True)
def _clean_jobs():
    jobs.reset()
    yield
    jobs.reset()


def _wait_done(job_id, timeout=2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = jobs.get(job_id)
        if job and job["status"] in ("done", "error"):
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish in time")


def test_job_runs_and_returns_results():
    def _fn(report):
        report(1, 2)
        report(2, 2)
        return {"cameras": [1, 2, 3]}

    jid = jobs.start(_fn)
    job = _wait_done(jid)
    assert job["status"] == "done"
    assert job["results"] == {"cameras": [1, 2, 3]}
    assert job["progress"] == {"done": 2, "total": 2}
    assert job["error"] is None


def test_job_captures_error_not_raised():
    def _fn(report):
        raise RuntimeError("boom")

    jid = jobs.start(_fn)
    job = _wait_done(jid)
    assert job["status"] == "error"
    assert "boom" in job["error"]
    assert job["results"] is None


def test_only_one_scan_at_a_time():
    release = threading.Event()

    def _blocking(report):
        release.wait(2.0)
        return "done"

    jid = jobs.start(_blocking)
    with pytest.raises(jobs.JobBusy):
        jobs.start(lambda report: "second")
    release.set()
    job = _wait_done(jid)
    assert job["results"] == "done"


def test_start_allowed_again_after_completion():
    jid1 = jobs.start(lambda report: "a")
    _wait_done(jid1)
    jid2 = jobs.start(lambda report: "b")
    assert jid2 != jid1
    assert _wait_done(jid2)["results"] == "b"


def test_get_unknown_job_is_none():
    assert jobs.get("nope") is None
    assert jobs.public(None) is None


def test_public_view_shape():
    jid = jobs.start(lambda report: 42)
    _wait_done(jid)
    view = jobs.public(jobs.get(jid))
    assert set(view) == {"id", "status", "progress", "results", "error"}
    assert view["results"] == 42
