"""The ingest pipeline: fetch -> filter -> parse -> embed -> store (plus the import graph)."""

import time
from collections import Counter
from dataclasses import dataclass

import numpy as np

from config import settings

from . import store
from .embed import chunk_embedding_text, get_embedder
from .fetch import fetch_repo
from .filter import filter_repo
from .graph import build_graph
from .parsers import Chunk, Import, parse_file


@dataclass
class IngestReport:
    repo_id: str
    commit: str
    files_indexed: int
    chunks: int
    chunks_by_kind: dict
    chunks_by_language: dict
    files_skipped: dict
    parse_errors: dict  # path -> error (for files that yielded nothing, or only partially parsed)
    graph: dict
    embed_model: str
    timings_s: dict


def ingest(repo_url: str) -> IngestReport:
    timings = {}
    t = time.perf_counter()
    fetched = fetch_repo(repo_url, settings.repos_dir)
    timings["fetch"] = time.perf_counter() - t

    t = time.perf_counter()
    filtered = filter_repo(fetched.root)
    chunks: list[Chunk] = []
    imports: dict[str, list[Import]] = {}
    errors: dict[str, str] = {}
    for file in filtered.kept:
        rel = file.relative_to(fetched.root).as_posix()
        source = file.read_text(encoding="utf-8", errors="replace")
        parsed = parse_file(rel, source)
        if parsed is None:
            continue
        if parsed.error:
            errors[rel] = parsed.error
        chunks.extend(parsed.chunks)
        imports[rel] = parsed.imports
    timings["parse"] = time.perf_counter() - t

    t = time.perf_counter()
    edges, graph_stats = build_graph(imports)
    timings["graph"] = time.perf_counter() - t

    t = time.perf_counter()
    embedder = get_embedder()
    vectors = (
        embedder.embed_documents([chunk_embedding_text(c) for c in chunks])
        if chunks else np.zeros((0, 0), dtype=np.float32)
    )
    timings["embed"] = time.perf_counter() - t

    report = IngestReport(
        repo_id=fetched.repo_id,
        commit=fetched.commit,
        files_indexed=len(imports),
        chunks=len(chunks),
        chunks_by_kind=dict(Counter(c.kind for c in chunks)),
        chunks_by_language=dict(Counter(c.language for c in chunks)),
        files_skipped=dict(filtered.skipped),
        parse_errors=errors,
        graph=graph_stats,
        embed_model=embedder.model_name,
        timings_s={k: round(v, 2) for k, v in timings.items()},
    )

    t = time.perf_counter()
    stats = {k: v for k, v in report.__dict__.items() if k not in ("repo_id", "commit", "embed_model")}
    store.replace_repo(fetched.repo_id, fetched.url, fetched.commit, embedder.model_name, chunks, vectors, edges, stats)
    report.timings_s["store"] = round(time.perf_counter() - t, 2)
    return report
