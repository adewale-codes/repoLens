"""Acceptance run against real repos.

For each question, prints the chunks retrieval would send to Claude, with
their scores and why each was retrieved. With --ask, it also calls the running
API's POST /ask and prints the answer plus any citations that point outside
the lines Claude was shown.

Each repo has factual questions with a clear answer in the code, plus one
question the repo doesn't contain (PostgreSQL / WebSockets / GraphQL). For
those, the answer should say it can't find relevant code instead of
inventing one.

With --ingest, each repo is first (re-)ingested through the job API: POST
/ingest returns a job id at once, and the script polls GET /ingest/{job_id},
printing each stage and embedding progress, until the job completes or fails.

Usage (from anywhere; without --ingest the repos must already be in data/):
    .venv/Scripts/python scratchpad/acceptance.py                     # retrieval only, no API key needed
    .venv/Scripts/python scratchpad/acceptance.py --ask               # also call /ask (server must be running)
    .venv/Scripts/python scratchpad/acceptance.py pallets/click --ask # one repo
    .venv/Scripts/python scratchpad/acceptance.py pallets/click --ingest --ask  # re-ingest first, then ask
"""

import json
import os
import time
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)  # config's data_dir ("data") is relative to the repo root
sys.path.insert(0, str(REPO_ROOT))

from services import store  # noqa: E402
from services.answer import build_context  # noqa: E402
from services.retrieve import retrieve  # noqa: E402

# 127.0.0.1, not localhost: on Windows "localhost" tries IPv6 first and the
# refused connection costs ~2 s per request before falling back to IPv4.
API_URL = os.environ.get("REPOLENS_API_URL", "http://127.0.0.1:8765")

QUESTIONS = {
    "pallets/click": [
        "What is the main entry point when a click command is invoked from the command line?",
        "What does the make_context method do?",
        "How does click connect to a PostgreSQL database?",  # not in the repo
    ],
    "expressjs/express": [
        "Where is the application object created when you call express()?",
        "How does app.listen start the HTTP server?",
        "Where does Express implement WebSocket support?",  # not in the repo
    ],
    "sindresorhus/ky": [
        "How does ky decide whether to retry a failed request and how long to wait?",
        "Where is the GraphQL query builder implemented?",  # not in the repo
    ],
}


POLL_SECONDS = 3


def ingest_via_job(repo: str) -> dict:
    """Submit an ingest job and poll it to the end. Returns the final job record."""
    resp = httpx.post(f"{API_URL}/ingest", json={"repo_url": f"https://github.com/{repo}"}, timeout=30)
    if resp.status_code == 409:  # already running: follow that job instead
        job_id = resp.json()["detail"]["job_id"]
        print(f"   {repo} already has a job in flight; following {job_id}")
    elif resp.status_code == 202:
        job_id = resp.json()["job_id"]
        print(f"   submitted {repo}: job {job_id} (202 in {resp.elapsed.total_seconds():.2f}s)")
    else:
        raise SystemExit(f"POST /ingest -> {resp.status_code}: {resp.text[:300]}")

    started, last = time.monotonic(), None
    while True:
        job = httpx.get(f"{API_URL}/ingest/{job_id}", timeout=30).json()
        progress = job["progress"] or {}
        tenth = progress.get("chunks_embedded", 0) * 10 // max(progress.get("chunks_total", 1), 1)
        if (job["status"], tenth) != last:  # print stage changes, and embedding progress every ~10%
            detail = f" {progress['chunks_embedded']}/{progress['chunks_total']} chunks" if "chunks_total" in progress else ""
            print(f"   [{time.monotonic() - started:6.0f}s] {job['status']}{detail}", flush=True)
            last = (job["status"], tenth)
        if job["status"] in ("complete", "failed"):
            break
        time.sleep(POLL_SECONDS)

    if job["status"] == "failed":
        print(f"   FAILED: {job['error']}\n")
    else:
        r = job["result"]
        print(f"   commit {r['commit'][:12]}: {r['files_indexed']} files, {r['chunks']} chunks, "
              f"graph {json.dumps(r['graph'])}, timings {json.dumps(r['timings_s'])}\n")
    return job


def main() -> None:
    call_api = "--ask" in sys.argv
    repos = [a for a in sys.argv[1:] if "/" in a] or list(QUESTIONS)
    for repo in repos:
        if repo not in QUESTIONS:
            print(f"## {repo}: no questions defined (choose from {', '.join(QUESTIONS)})\n")
            continue
        if "--ingest" in sys.argv:
            print(f"## [{repo}] ingest via job API")
            if ingest_via_job(repo)["status"] != "complete":
                continue
        index = store.load_index(repo)
        if index is None:
            print(f"## {repo}: not ingested. POST /ingest with https://github.com/{repo} first.\n")
            continue
        for question in QUESTIONS[repo]:
            run_question(repo, index, question, call_api)


def run_question(repo: str, index, question: str, call_api: bool) -> None:
    retrieval = retrieve(index, question)
    _, context = build_context(retrieval, 60_000)
    print(f"## [{repo}] {question}")
    print(f"   top raw similarity: {retrieval.top_similarity:.3f}")
    for cc in context:
        c = cc.item.chunk
        print(
            f"   {cc.ref:>2}. {cc.item.source:6} {cc.item.score:.3f}  "
            f"{c.path}:{c.start_line}-{c.end_line}  {c.symbol}  ({cc.item.reason})"
        )

    if call_api:
        try:
            resp = httpx.post(f"{API_URL}/ask", json={"repo_id": repo, "question": question}, timeout=600)
        except httpx.HTTPError as e:
            print(f"   /ask failed: {e!r} (is the server running at {API_URL}?)\n")
            return
        if resp.status_code != 200:
            print(f"   /ask -> {resp.status_code}: {resp.text[:500]}\n")
            return
        body = resp.json()
        print("   --- answer ---")
        print("\n".join("   " + line for line in body["answer"].splitlines()))
        invalid = [c for c in body["citations"] if not c["valid"]]
        from_index = sum(1 for c in body["citations"] if c.get("basis") == "class_index")
        print(
            f"   citations: {len(body['citations'])} ({from_index} exact locations from a class index), "
            f"pointing outside shown lines: {json.dumps(invalid)}"
        )
        print(f"   answer_status: {body.get('answer_status')}")
        print(f"   model: {body['model']}, stop_reason: {body['stop_reason']}, usage: {body['usage']}")
    print()


if __name__ == "__main__":
    main()
