"""RepoLens API.

POST /ingest queues a background job and returns 202 with its id right away.
Poll GET /ingest/{job_id} for its stage and, once complete, its report. An
ingest takes 7-15 minutes on CPU, mostly embedding, far beyond an HTTP
timeout. See services/jobs.py for how jobs run.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()  # before anything reads ANTHROPIC_API_KEY or REPOLENS_* settings

import anthropic  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402

from config import storage_status  # noqa: E402
from schemas import (  # noqa: E402
    AskRequest, AskResponse, ChunkOut, CitationOut, GraphEdgeOut, GraphResponse, IngestAccepted, IngestJob,
    IngestRequest,
)
from services import jobs, store  # noqa: E402
from services.answer import MissingCredentials, answer_question  # noqa: E402
from services.fetch import FetchError, parse_github_url  # noqa: E402
from services.retrieve import retrieve  # noqa: E402


log = logging.getLogger("repolens")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    storage = storage_status()
    if storage["persistent"] is False:
        # Running on Railway without a Volume at REPOLENS_DATA_DIR: every
        # indexed repo would be lost on the next redeploy.
        log.warning(
            "REPOLENS_DATA_DIR=%s is NOT on a Railway Volume (volume mount: %s). Indexed repos and "
            "ingest jobs will be wiped on every redeploy. Attach a Volume mounted at %s.",
            storage["data_dir"], storage["volume_mount"], storage["data_dir"],
        )
    # Jobs left queued or running by a previous process can never finish now.
    jobs.recover_interrupted_jobs()
    yield


app = FastAPI(title="RepoLens", version="0.2.0", lifespan=lifespan)


def _normalize_repo_id(value: str) -> str:
    """Accept "owner/name" or a full GitHub URL."""
    if "github.com" in value:
        owner, name = parse_github_url(value)
        return f"{owner}/{name}"
    return value.strip().strip("/")


def _index_or_404(repo_id: str):
    index = store.load_index(repo_id)
    if index is None:
        raise HTTPException(404, f"Repository {repo_id!r} has not been ingested. POST /ingest first.")
    return index


@app.get("/health")
def health() -> dict:
    # "storage.persistent" is false on Railway without a Volume: a quick
    # post-deploy check that indexed repos will survive the next redeploy.
    return {"status": "ok", "storage": storage_status()}


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts is not None else None


@app.post("/ingest", status_code=202)
def ingest_repo(body: IngestRequest) -> IngestAccepted:
    """Queue an ingest (or a re-ingest, to pick up new commits) and return at once."""
    try:
        job_id = jobs.submit(body.repo_url)
    except FetchError as e:  # malformed URL: nothing to queue
        raise HTTPException(422, str(e)) from e
    except jobs.JobConflict as e:
        raise HTTPException(
            409, {"message": "This repo already has an ingest queued or running.", "job_id": e.job_id}
        ) from e
    return IngestAccepted(job_id=job_id, status="queued", status_url=f"/ingest/{job_id}")


@app.get("/ingest/{job_id}")
def ingest_status(job_id: str) -> IngestJob:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, f"No ingest job {job_id!r}.")
    return IngestJob(
        job_id=job["id"],
        repo_url=job["repo_url"],
        repo_id=job["repo_id"],
        status=job["status"],
        created_at=_iso(job["created_at"]),
        started_at=_iso(job["started_at"]),
        completed_at=_iso(job["completed_at"]),
        updated_at=_iso(job["updated_at"]),
        progress=job["progress"],
        error=job["error"],
        result=job["result"],
    )


@app.post("/ask")
def ask(body: AskRequest) -> AskResponse:
    repo_id = _normalize_repo_id(body.repo_id)
    index = _index_or_404(repo_id)
    retrieval = retrieve(index, body.question)
    try:
        result = answer_question(index, body.question, retrieval)
    except (MissingCredentials, anthropic.AuthenticationError) as e:
        raise HTTPException(503, "Claude API credentials are missing or invalid. Set ANTHROPIC_API_KEY (e.g. in .env).") from e
    except anthropic.RateLimitError as e:
        raise HTTPException(429, "Claude API rate limit hit; retry shortly.") from e
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise HTTPException(502, "Could not reach the Claude API.") from e

    chunks = [
        ChunkOut(
            ref=cc.ref,
            file=cc.item.chunk.path,
            start_line=cc.item.chunk.start_line,
            end_line=cc.item.chunk.end_line,
            shown_through_line=cc.shown_end,
            symbol=cc.item.chunk.symbol,
            kind=cc.item.chunk.kind,
            retrieved_by=cc.item.source,
            reason=cc.item.reason,
            score=round(cc.item.score, 4),
        )
        for cc in result.context
    ]
    return AskResponse(
        repo_id=repo_id,
        commit=index.commit,
        question=body.question,
        answer=result.text,
        answer_status=result.status,
        citations=[CitationOut(**c.__dict__) for c in result.citations],
        files_consulted=sorted({c.file for c in chunks}),
        chunks_used=chunks,
        top_similarity=round(retrieval.top_similarity, 4),
        model=result.model,
        stop_reason=result.stop_reason,
        usage=result.usage,
    )


@app.get("/repos")
def list_repos() -> list[dict]:
    return store.list_repos()


@app.get("/repos/{repo_id:path}/graph")
def repo_graph(repo_id: str, file: str | None = None) -> GraphResponse:
    index = _index_or_404(_normalize_repo_id(repo_id))
    edges = [e for e in index.edges if file is None or file in (e.src, e.dst)]
    return GraphResponse(
        repo_id=index.repo_id,
        edge_count=len(edges),
        edges=[GraphEdgeOut(**e.__dict__) for e in sorted(edges, key=lambda e: (e.src, e.dst))],
    )


@app.get("/repos/{repo_id:path}/chunks")
def repo_chunks(repo_id: str, file: str | None = None, include_text: bool = False) -> list[dict]:
    """List chunks, optionally for one file, for checking chunk boundaries."""
    index = _index_or_404(_normalize_repo_id(repo_id))
    return [
        {
            "file": c.path, "symbol": c.symbol, "kind": c.kind,
            "start_line": c.start_line, "end_line": c.end_line,
            **({"text": c.text} if include_text else {}),
        }
        for c in index.chunks
        if file is None or c.path == file
    ]


@app.get("/repos/{repo_id:path}")
def get_repo(repo_id: str) -> dict:
    """One indexed repo: its commit, embedding model, and ingest report stats.

    Case-insensitive, like GitHub; the response carries the canonical repo_id.
    Declared after /graph and /chunks so those more specific paths match first.
    """
    repo = store.find_repo(_normalize_repo_id(repo_id))
    if repo is None:
        raise HTTPException(404, f"Repository {repo_id!r} has not been ingested.")
    return repo
