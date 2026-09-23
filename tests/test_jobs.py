import time

import pytest
from fastapi.testclient import TestClient

from config import settings
from services import jobs, store
from services.fetch import FetchError
from services.ingest import IngestReport

URL = "https://github.com/octo/demo"


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)


def fake_report() -> IngestReport:
    return IngestReport(
        repo_id="octo/demo", commit="abc", files_indexed=1, chunks=2, chunks_by_kind={"function": 2},
        chunks_by_language={"python": 2}, files_skipped={}, parse_errors={}, graph={"edges": 0},
        embed_model="m", timings_s={"fetch": 0.1},
    )


def fake_pipeline(repo_url, on_progress):
    """Reports stages the way services.ingest.ingest does, without doing the work."""
    on_progress("fetching", None)
    on_progress("parsing", {"repo_id": "octo/demo", "commit": "abc"})
    for done in (0, 16, 32):
        on_progress("embedding", {"chunks_embedded": done, "chunks_total": 32})
    on_progress("storing", None)
    return fake_report()


def record_statuses(monkeypatch) -> list[str]:
    seen, real_update = [], store.update_job

    def spy(job_id, **fields):
        if "status" in fields:
            seen.append(fields["status"])
        real_update(job_id, **fields)

    monkeypatch.setattr(store, "update_job", spy)
    return seen


def test_job_moves_through_every_real_stage(monkeypatch):
    monkeypatch.setattr(jobs, "ingest", fake_pipeline)
    seen = record_statuses(monkeypatch)
    store.create_job("j1", URL, "octo/demo")
    jobs.run_job("j1")

    assert seen == ["fetching", "parsing", "embedding", "storing", "complete"]
    job = store.get_job("j1")
    assert job["result"]["chunks"] == 2 and job["error"] is None
    assert job["started_at"] <= job["completed_at"]


def test_embedding_progress_is_recorded(monkeypatch):
    snapshots = []

    def pipeline(repo_url, on_progress):
        on_progress("embedding", {"chunks_embedded": 0, "chunks_total": 32})
        snapshots.append(store.get_job("j1")["progress"])
        return fake_report()

    monkeypatch.setattr(jobs, "ingest", pipeline)
    store.create_job("j1", URL, "octo/demo")
    jobs.run_job("j1")
    assert snapshots == [{"chunks_embedded": 0, "chunks_total": 32}]
    assert store.get_job("j1")["progress"] is None  # cleared once complete


def test_fetch_failure_ends_the_job_as_failed_with_the_git_error(monkeypatch):
    def pipeline(repo_url, on_progress):
        on_progress("fetching", None)
        raise FetchError("git clone failed: remote: Repository not found.")

    monkeypatch.setattr(jobs, "ingest", pipeline)
    store.create_job("j1", URL, "octo/demo")
    jobs.run_job("j1")
    job = store.get_job("j1")
    assert job["status"] == "failed"
    assert job["error"] == "git clone failed: remote: Repository not found."
    assert job["completed_at"] is not None


def test_unexpected_error_fails_the_job_instead_of_leaving_it_running(monkeypatch):
    def pipeline(repo_url, on_progress):
        on_progress("parsing", None)
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs, "ingest", pipeline)
    store.create_job("j1", URL, "octo/demo")
    jobs.run_job("j1")
    job = store.get_job("j1")
    assert (job["status"], job["error"]) == ("failed", "Internal error: RuntimeError: boom")


def test_second_submit_for_the_same_repo_conflicts_until_the_first_finishes(monkeypatch):
    monkeypatch.setattr(jobs, "_ensure_worker", lambda: None)  # keep the job queued
    first = jobs.submit(URL)
    with pytest.raises(jobs.JobConflict) as conflict:
        jobs.submit(URL + ".git")
    assert conflict.value.job_id == first

    store.update_job(first, status="complete")
    assert jobs.submit(URL) != first  # re-ingest is allowed once the earlier job is done


def test_restart_fails_jobs_that_can_never_finish():
    for job_id, status in (("q", "queued"), ("e", "embedding"), ("c", "complete")):
        store.create_job(job_id, URL, "octo/" + job_id)
        store.update_job(job_id, status=status)
    assert jobs.recover_interrupted_jobs() == 2
    assert store.get_job("q")["status"] == store.get_job("e")["status"] == "failed"
    assert "Interrupted" in store.get_job("e")["error"]
    assert store.get_job("c")["status"] == "complete"


def test_http_flow_returns_202_then_polls_to_complete(monkeypatch):
    import main

    def slow_pipeline(repo_url, on_progress):
        time.sleep(0.3)  # the request must not wait for this
        return fake_pipeline(repo_url, on_progress)

    monkeypatch.setattr(jobs, "ingest", slow_pipeline)
    with TestClient(main.app) as client:
        t = time.perf_counter()
        resp = client.post("/ingest", json={"repo_url": URL})
        assert resp.status_code == 202 and time.perf_counter() - t < 0.3
        job_id = resp.json()["job_id"]
        assert resp.json()["status_url"] == f"/ingest/{job_id}"

        for _ in range(100):
            body = client.get(f"/ingest/{job_id}").json()
            if body["status"] in ("complete", "failed"):
                break
            time.sleep(0.05)
        assert body["status"] == "complete"
        assert body["result"]["repo_id"] == "octo/demo"

        assert client.post("/ingest", json={"repo_url": "not a url"}).status_code == 422
        assert client.get("/ingest/does-not-exist").status_code == 404
