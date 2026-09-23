"""Request and response bodies for the HTTP API."""

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    repo_url: str = Field(examples=["https://github.com/pallets/click"])


class IngestAccepted(BaseModel):
    job_id: str
    status: str  # always "queued" at submission
    status_url: str  # poll this with GET


class IngestJob(BaseModel):
    job_id: str
    repo_url: str
    repo_id: str
    # queued -> fetching -> parsing -> embedding -> storing -> complete, or failed at any point
    status: str
    created_at: str  # ISO 8601, UTC
    started_at: str | None
    completed_at: str | None
    updated_at: str
    progress: dict | None  # e.g. {"chunks_embedded": 640, "chunks_total": 1290} while embedding
    error: str | None  # set when status is "failed"
    result: dict | None  # the ingest report, set when status is "complete"


class AskRequest(BaseModel):
    repo_id: str = Field(description='"owner/name", as returned by /ingest', examples=["pallets/click"])
    question: str = Field(min_length=3, max_length=2000)


class ChunkOut(BaseModel):
    ref: int
    file: str
    start_line: int
    end_line: int
    shown_through_line: int  # less than end_line if the chunk was truncated to fit the context budget
    symbol: str
    kind: str
    retrieved_by: str  # "vector" or "graph"
    reason: str
    score: float


class CitationOut(BaseModel):
    file: str
    start: int
    end: int
    valid: bool  # False: points at code the model wasn't shown (marked [unverified] in the answer)
    basis: str | None  # "excerpt" (code was shown) | "class_index" (exact location from a class index) | None


class AskResponse(BaseModel):
    repo_id: str
    commit: str
    question: str
    answer: str
    citations: list[CitationOut]
    files_consulted: list[str]
    chunks_used: list[ChunkOut]
    top_similarity: float
    model: str | None
    stop_reason: str | None
    usage: dict | None


class GraphEdgeOut(BaseModel):
    src: str
    dst: str
    spec: str
    line: int


class GraphResponse(BaseModel):
    repo_id: str
    edge_count: int
    edges: list[GraphEdgeOut]
