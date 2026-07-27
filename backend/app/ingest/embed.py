"""Dense and sparse embedding, in-process and batched.

Two vectors per chunk, because they fail on different things and that is the
whole argument for hybrid retrieval:

* **Dense** (``bge-small-en-v1.5``) handles paraphrase, synonymy and conceptual
  queries, and fails on exact identifiers, error codes, version numbers and rare
  proper nouns.
* **Sparse** (``Qdrant/bm25``) is the mirror image — and it is the hero for
  tables, which are dense with exactly the labels and numbers embeddings are
  worst at.

``bge-small`` rather than ``bge-base`` is forced by the 512 MB ceiling, and
``bge-m3`` is impossible in-process at all — fastembed ships no quantized variant,
only full fp32 ONNX at ~2.27 GB.

**Batch size is a real tuning parameter here, not a detail.** At 0.1 vCPU the
batch that keeps throughput reasonable and the batch that fits in the remaining
RAM are not obviously the same number, and the model itself is already ~130 MB
resident. It stays in config and gets set against measured RSS.

BM25 computes IDF **server-side** in Qdrant, so this module only produces term
frequencies — there is no corpus statistic to maintain in application code, and
no Elasticsearch in the stack.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config import Settings, get_settings

if TYPE_CHECKING:
    from fastembed import SparseTextEmbedding, TextEmbedding

logger = logging.getLogger(__name__)

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
SPARSE_MODEL = "Qdrant/bm25"


@dataclass(frozen=True)
class SparseVector:
    indices: list[int]
    values: list[float]


@dataclass(frozen=True)
class EmbeddedChunk:
    dense: list[float]
    sparse: SparseVector


class Embedder:
    """Process-wide embedding models, loaded lazily.

    One instance per process, deliberately. Two copies of the ONNX runtime and
    its weights would not fit beside each other inside 512 MB, and the second
    copy buys nothing — embedding is CPU-bound on a tenth of a core, so
    concurrency here is contention, not throughput.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        # Imported lazily so the 130 MB model is never a startup cost; typed
        # under TYPE_CHECKING so mypy still sees the real classes.
        self._dense: TextEmbedding | None = None
        self._sparse: SparseTextEmbedding | None = None
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._dense is not None and self._sparse is not None:
            return
        with self._lock:
            if self._dense is None:
                from fastembed import TextEmbedding

                self._dense = TextEmbedding(
                    model_name=self.settings.embed_model,
                    cache_dir=self.settings.fastembed_cache_dir,
                    threads=1,
                )
                logger.info("loaded dense model %s", self.settings.embed_model)
            if self._sparse is None:
                from fastembed import SparseTextEmbedding

                self._sparse = SparseTextEmbedding(
                    model_name=SPARSE_MODEL,
                    cache_dir=self.settings.fastembed_cache_dir,
                    threads=1,
                )
                logger.info("loaded sparse model %s", SPARSE_MODEL)

    @property
    def dense_dimension(self) -> int:
        """Vector width, needed to create the collection before any text exists."""
        self._load()
        assert self._dense is not None
        return int(self._dense.embedding_size)

    def embed_documents(self, texts: Sequence[str]) -> list[EmbeddedChunk]:
        """Embed a batch of chunk texts. Order matches the input exactly."""
        if not texts:
            return []
        self._load()
        assert self._dense is not None and self._sparse is not None

        batch = self.settings.embed_batch_size
        dense = list(self._dense.embed(list(texts), batch_size=batch))
        sparse = list(self._sparse.embed(list(texts), batch_size=batch))

        return [
            EmbeddedChunk(
                dense=[float(v) for v in d],
                sparse=SparseVector(
                    indices=[int(i) for i in s.indices],
                    values=[float(v) for v in s.values],
                ),
            )
            for d, s in zip(dense, sparse, strict=True)
        ]

    def embed_query(self, text: str) -> EmbeddedChunk:
        """Embed a single query.

        ``bge`` models are trained with an asymmetric query prefix, and fastembed
        applies it through ``query_embed``. Using the document path for a query
        would embed it into the wrong region of the space and quietly cost recall.
        """
        self._load()
        assert self._dense is not None and self._sparse is not None

        dense = next(iter(self._dense.query_embed(text)))
        sparse = next(iter(self._sparse.query_embed(text)))
        return EmbeddedChunk(
            dense=[float(v) for v in dense],
            sparse=SparseVector(
                indices=[int(i) for i in sparse.indices],
                values=[float(v) for v in sparse.values],
            ),
        )


def batched(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder


def reset_embedder_for_tests() -> None:
    global _embedder
    _embedder = None
