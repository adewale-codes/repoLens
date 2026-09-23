"""Question -> relevant chunks.

1. Vector search: cosine similarity between the question and every chunk.
2. Identifier boost: if the question names a symbol ("what does `parse_args`
   do?"), chunks defining it get a bonus. Embeddings are good at meaning but
   can rank an exact name match below a semantically similar neighbour, and
   an exact name is the strongest signal a code question carries.
3. Graph expansion: for the files behind the top hits, look at the files they
   import and the files that import them, and pull in the best chunks from
   those neighbours. "Where is X handled" usually needs the caller and the
   callee, and they often live in different files that don't share
   vocabulary with the question. A neighbour chunk whose symbol is referenced
   by name in a hit gets priority, since that's a real caller/callee link.
"""

import re
from dataclasses import dataclass

import numpy as np

from config import settings

from .embed import get_embedder
from .parsers import Chunk
from .store import RepoIndex

SYMBOL_MATCH_BONUS = 0.20
REFERENCE_BONUS = 0.10
EXPANSION_MARGIN = 0.10  # a neighbour must score within this of the weakest vector hit

_WORD = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


@dataclass
class Retrieved:
    chunk: Chunk
    score: float
    source: str  # "vector" | "graph"
    reason: str


@dataclass
class Retrieval:
    results: list[Retrieved]
    top_similarity: float  # raw cosine of the best hit, before boosts


def _leaf(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1]


def _named(chunk: Chunk) -> bool:
    """A chunk with a distinctive name. `<module>` has none, and dunders like
    `__init__` appear in nearly every file, so a mention of one says nothing."""
    leaf = _leaf(chunk.symbol)
    return not leaf.startswith(("<", "__"))


def retrieve(index: RepoIndex, question: str) -> Retrieval:
    if not index.chunks:
        return Retrieval([], 0.0)
    query = get_embedder().embed_query(question)
    similarity = index.vectors @ query
    scores = similarity + _identifier_boost(index.chunks, question)

    top_k = min(settings.top_k, len(index.chunks))
    order = np.argsort(-scores)[:top_k]
    results = [Retrieved(index.chunks[i], float(scores[i]), "vector", f"similarity {similarity[i]:.3f}") for i in order]
    results += _expand(index, results, scores, set(int(i) for i in order))
    return Retrieval(results, float(similarity.max()))


def _identifier_boost(chunks: list[Chunk], question: str) -> np.ndarray:
    words = {w for w in _WORD.findall(question) if len(w) >= 3}
    lowered = {w.lower() for w in words}
    boost = np.zeros(len(chunks), dtype=np.float32)
    for i, chunk in enumerate(chunks):
        leaf = _leaf(chunk.symbol)
        if leaf in words:
            boost[i] = SYMBOL_MATCH_BONUS
        elif leaf.lower() in lowered and len(leaf) >= 4:
            boost[i] = SYMBOL_MATCH_BONUS / 2  # "Router" asked as "router"
    return boost


def _expand(index: RepoIndex, hits: list[Retrieved], scores: np.ndarray, taken: set[int]) -> list[Retrieved]:
    seed_files: list[str] = []
    for hit in hits:
        if hit.chunk.path not in seed_files:
            seed_files.append(hit.chunk.path)
    seed_files = seed_files[: settings.graph_expand_seeds]

    relation: dict[str, str] = {}  # neighbour file -> how it relates to a seed
    for seed in seed_files:
        for dst in sorted(index.imports.get(seed, ())):
            relation.setdefault(dst, f"imported by {seed}")
        for src in sorted(index.imported_by.get(seed, ())):
            relation.setdefault(src, f"imports {seed}")
    for seed in seed_files:
        relation.pop(seed, None)
    if not relation:
        return []

    hit_text_words = set()
    for hit in hits:
        if hit.chunk.path in seed_files:
            hit_text_words |= set(_WORD.findall(hit.chunk.text))

    floor = min(h.score for h in hits) - EXPANSION_MARGIN
    candidates: list[tuple[float, int, str]] = []
    for i, chunk in enumerate(index.chunks):
        if i in taken or chunk.path not in relation:
            continue
        referenced = _named(chunk) and _leaf(chunk.symbol) in hit_text_words
        score = float(scores[i]) + (REFERENCE_BONUS if referenced else 0.0)
        if referenced or score >= floor:
            why = relation[chunk.path] + (f"; {_leaf(chunk.symbol)} is referenced there" if referenced else "")
            candidates.append((score, i, why))

    candidates.sort(reverse=True)
    picked, per_file = [], {}
    for score, i, why in candidates:
        path = index.chunks[i].path
        if per_file.get(path, 0) >= 2:  # keep expansion broad, not deep in one file
            continue
        per_file[path] = per_file.get(path, 0) + 1
        picked.append(Retrieved(index.chunks[i], score, "graph", why))
        if len(picked) >= settings.graph_expand_max:
            break
    return picked
