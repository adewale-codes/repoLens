"""RepoLens API.

POST /ingest is synchronous: it returns once the repo is fully indexed. Fetch
and parse take seconds; CPU embedding dominates at roughly 0.3-0.7 s per chunk
(7-15 minutes for the 450-1300-chunk repos tested). Phase 2 should make it
return a job id right away and run the pipeline in a worker (GET /jobs/{id} to
poll), since that already exceeds typical HTTP client and proxy timeouts.
"""

import threading
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()  # before anything reads ANTHROPIC_API_KEY or REPOLENS_* settings

import anthropic  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402

from schemas import (  # noqa: E402
    AskRequest, AskResponse, ChunkOut, CitationOut, GraphEdgeOut, GraphResponse, IngestRequest,
)
from services import store  # noqa: E402
from services.answer import MissingCredentials, answer_question  # noqa: E402
from services.fetch import FetchError, parse_github_url  # noqa: E402
from services.ingest import IngestReport, ingest  # noqa: E402
from services.retrieve import retrieve  # noqa: E402

app = FastAPI(title="RepoLens", version="0.1.0")

_ingest_locks: dict[str, threading.Lock] = defaultdict(threading.Lock)


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
    return {"status": "ok"}


@app.post("/ingest")
def ingest_repo(body: IngestRequest) -> IngestReport:
    try:
        owner, name = parse_github_url(body.repo_url)
    except FetchError as e:
        raise HTTPException(422, str(e)) from e
    lock = _ingest_locks[f"{owner}/{name}"]
    if not lock.acquire(blocking=False):
        raise HTTPException(409, f"{owner}/{name} is already being ingested.")
    try:
        return ingest(body.repo_url)
    except FetchError as e:
        raise HTTPException(400, str(e)) from e
    finally:
        lock.release()


@app.post("/ask")
def ask(body: AskRequest) -> AskResponse:
    repo_id = _normalize_repo_id(body.repo_id)
    index = _index_or_404(repo_id)
    retrieval = retrieve(index, body.question)
    try:
        result = answer_question(repo_id, index.commit, body.question, retrieval)
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
