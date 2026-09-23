"""Persistence for chunks, their embeddings, and the import graph.

Phase 1 choice: SQLite (stdlib) for rows, with embeddings stored as float32
BLOBs. At query time a repo's vectors load into one numpy matrix and search is
an exact dot product.

Why not pgvector yet: it needs a running Postgres, and at this scale it buys
nothing. A 5k-chunk repo is a 5k x 768 matrix (~15 MB), and an exact top-k
over it takes about a millisecond. Exact search also means retrieval quality
can be judged without worrying about approximate-index recall. The limit is
memory: every queried repo's matrix stays resident. Past ~100k chunks per repo,
or with many repos queried concurrently, move to pgvector with an HNSW index.
Only this module needs to change; the rest of the code talks to it through
`replace_repo` / `load_index` / `get_repo`.
"""

import json
import sqlite3
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field

import numpy as np

from config import settings

from .parsers import Chunk

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    repo_id     TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    commit_sha  TEXT NOT NULL,
    embed_model TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    ingested_at REAL NOT NULL,
    stats       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    repo_id    TEXT NOT NULL,
    ordinal    INTEGER NOT NULL,
    path       TEXT NOT NULL,
    language   TEXT NOT NULL,
    kind       TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line   INTEGER NOT NULL,
    text       TEXT NOT NULL,
    note       TEXT NOT NULL,
    embedding  BLOB NOT NULL,
    PRIMARY KEY (repo_id, ordinal)
);
CREATE TABLE IF NOT EXISTS edges (
    repo_id TEXT NOT NULL,
    src     TEXT NOT NULL,
    dst     TEXT NOT NULL,
    spec    TEXT NOT NULL,
    line    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS edges_repo ON edges (repo_id);
"""


@dataclass
class Edge:
    src: str
    dst: str
    spec: str  # the import specifier as written, e.g. "./router" or "..utils"
    line: int


@dataclass
class RepoIndex:
    repo_id: str
    commit: str
    embed_model: str
    chunks: list[Chunk]
    vectors: np.ndarray  # row i is the embedding of chunks[i]
    edges: list[Edge]
    imports: dict[str, set[str]] = field(default_factory=dict)  # file -> files it imports
    imported_by: dict[str, set[str]] = field(default_factory=dict)  # file -> files importing it

    def __post_init__(self) -> None:
        imports, imported_by = defaultdict(set), defaultdict(set)
        for e in self.edges:
            imports[e.src].add(e.dst)
            imported_by[e.dst].add(e.src)
        self.imports, self.imported_by = dict(imports), dict(imported_by)


_cache: dict[str, RepoIndex] = {}
_cache_lock = threading.Lock()


@contextmanager
def _connect():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def replace_repo(
    repo_id: str,
    url: str,
    commit: str,
    embed_model: str,
    chunks: list[Chunk],
    vectors: np.ndarray,
    edges: list[Edge],
    stats: dict,
) -> None:
    """Swap a repo's index for a new one in a single transaction."""
    assert len(chunks) == len(vectors)
    dim = int(vectors.shape[1]) if len(vectors) else 0
    with _connect() as conn:
        for table in ("repos", "chunks", "edges"):
            conn.execute(f"DELETE FROM {table} WHERE repo_id = ?", (repo_id,))
        conn.execute(
            "INSERT INTO repos VALUES (?, ?, ?, ?, ?, ?, ?)",
            (repo_id, url, commit, embed_model, dim, time.time(), json.dumps(stats)),
        )
        conn.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (repo_id, i, c.path, c.language, c.kind, c.symbol, c.start_line, c.end_line, c.text, c.note,
                 vectors[i].astype(np.float32).tobytes())
                for i, c in enumerate(chunks)
            ],
        )
        conn.executemany(
            "INSERT INTO edges VALUES (?, ?, ?, ?, ?)",
            [(repo_id, e.src, e.dst, e.spec, e.line) for e in edges],
        )
    with _cache_lock:
        _cache.pop(repo_id, None)


def get_repo(repo_id: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT repo_id, url, commit_sha, embed_model, dim, ingested_at, stats FROM repos WHERE repo_id = ?",
            (repo_id,),
        ).fetchone()
    if row is None:
        return None
    keys = ("repo_id", "url", "commit", "embed_model", "dim", "ingested_at", "stats")
    record = dict(zip(keys, row))
    record["stats"] = json.loads(record["stats"])
    return record


def list_repos() -> list[dict]:
    with _connect() as conn:
        ids = [r[0] for r in conn.execute("SELECT repo_id FROM repos ORDER BY repo_id")]
    return [get_repo(i) for i in ids]


def load_index(repo_id: str) -> RepoIndex | None:
    """A repo's chunks, vectors, and graph, cached in memory after the first load."""
    with _cache_lock:
        if repo_id in _cache:
            return _cache[repo_id]
    repo = get_repo(repo_id)
    if repo is None:
        return None
    with _connect() as conn:
        rows = conn.execute(
            "SELECT path, language, kind, symbol, start_line, end_line, text, note, embedding "
            "FROM chunks WHERE repo_id = ? ORDER BY ordinal",
            (repo_id,),
        ).fetchall()
        edge_rows = conn.execute("SELECT src, dst, spec, line FROM edges WHERE repo_id = ?", (repo_id,)).fetchall()
    chunks = [Chunk(*r[:8]) for r in rows]
    vectors = (
        np.stack([np.frombuffer(r[8], dtype=np.float32) for r in rows])
        if rows else np.zeros((0, repo["dim"]), dtype=np.float32)
    )
    index = RepoIndex(repo_id, repo["commit"], repo["embed_model"], chunks, vectors, [Edge(*e) for e in edge_rows])
    with _cache_lock:
        _cache[repo_id] = index
    return index
