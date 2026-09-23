"""Chunk and query embeddings.

We run jina-embeddings-v2-base-code locally through fastembed (ONNX Runtime,
no PyTorch). It was trained on paired natural language and code across 30
programming languages, which is the match we need: English questions
against Python and JS/TS chunks. It needs no API key or network call per
chunk, and the model (~640 MB) is downloaded once and cached under data/models.

Anthropic doesn't offer an embeddings endpoint. The hosted option it recommends
is Voyage (voyage-code-3), which should retrieve better on code. Switching means
reimplementing `Embedder` against the Voyage client and re-ingesting, because
vectors from different models aren't comparable. The store records which model
built each repo's index.
"""

from functools import lru_cache

import numpy as np

from config import settings

from .parsers import Chunk

BATCH_SIZE = 16


def chunk_embedding_text(chunk: Chunk) -> str:
    """What gets embedded for a chunk: its location and name, then the code.

    The path and symbol give the vector something to match questions like
    "where is routing handled" against, even when the code itself never uses
    the word.
    """
    header = f"{chunk.path}\n{chunk.kind} {chunk.symbol}\n"
    if chunk.note:
        header += chunk.note + "\n"
    return (header + chunk.text)[: settings.embed_max_chars]


class Embedder:
    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(settings.data_dir / "models"))

    def embed_documents(self, texts: list[str]) -> np.ndarray:
        # Each batch is padded to its longest text, and attention cost grows
        # with length, so one long chunk makes the whole batch expensive. Sorting
        # by length keeps batches uniform; the original order is restored after.
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        embedded = np.array(list(self._model.embed([texts[i] for i in order], batch_size=BATCH_SIZE)), dtype=np.float32)
        vectors = np.empty_like(embedded)
        vectors[order] = embedded
        return _normalize(vectors)

    def embed_query(self, text: str) -> np.ndarray:
        return _normalize(np.array(list(self._model.query_embed(text)), dtype=np.float32))[0]


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder(settings.embed_model)


def _normalize(vectors: np.ndarray) -> np.ndarray:
    """L2-normalize rows so cosine similarity is a plain dot product."""
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return vectors / np.maximum(norms, 1e-12)
