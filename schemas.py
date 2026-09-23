"""Request and response bodies for the HTTP API."""

from pydantic import BaseModel, Field


class IngestRequest(BaseModel):
    repo_url: str = Field(examples=["https://github.com/pallets/click"])


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
    valid: bool  # False means the answer cites lines the model was never shown


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
