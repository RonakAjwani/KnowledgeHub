"""Qdrant collection, upsert, delete, and the one hybrid search call.

One collection with two **named vectors** on every point — dense and sparse —
rather than two collections. Named vectors are what let a single request prefetch
both branches and fuse them server-side; two collections would force the merge
back into application code, which is the thing the design explicitly rejects.

**The fusion API is the sharpest trap in this module.** ``qdrant-client`` exposes
two shapes and they are not equivalent:

``FusionQuery(fusion=Fusion.RRF)``
    The form nearly every example shows. It takes **no weights and no k** — it is
    plain, unweighted RRF against an *unpublished* server default ``k``.

``RrfQuery(rrf=Rrf(k=..., weights=[...]))``  ← what this module uses
    The weighted form. Both terms of ``RRF_MAX = (w_dense + w_sparse) / (k + 1)``
    are then our own constants.

Using the first would look correct, return plausible results, and quietly make
every G2 threshold meaningless — because ``FLOOR_FUSED`` is derived from constants
the server would not actually be using. That is invariant I7's failure mode
exactly: reasonable-looking code, silently broken thresholds.

Every search carries ``user_id`` as a payload filter. There is no code path here
that omits it (I3).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.config import Settings, get_settings
from app.errors import DependencyUnavailable
from app.ingest.embed import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    EmbeddedChunk,
    get_embedder,
)
from app.models.schemas import Chunk

logger = logging.getLogger(__name__)


@dataclass
class ScoredPoint:
    chunk_id: str
    doc_id: str
    score: float
    payload: dict[str, Any]


class QdrantStore:
    def __init__(
        self,
        settings: Settings | None = None,
        client: AsyncQdrantClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client

    @property
    def client(self) -> AsyncQdrantClient:
        if self._client is None:
            self._client = AsyncQdrantClient(
                url=self.settings.qdrant_url,
                api_key=self.settings.qdrant_api_key or None,
                timeout=int(self.settings.timeout_qdrant_s),
            )
        return self._client

    @property
    def collection(self) -> str:
        return self.settings.qdrant_collection

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------- lifecycle

    async def ensure_collection(self, dense_dim: int | None = None) -> None:
        """Create the collection if absent. Safe to call on every startup."""
        if await self.client.collection_exists(self.collection):
            return

        dim = dense_dim or get_embedder().dense_dimension
        await self.client.create_collection(
            collection_name=self.collection,
            vectors_config={
                DENSE_VECTOR_NAME: models.VectorParams(
                    size=dim, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                SPARSE_VECTOR_NAME: models.SparseVectorParams(
                    # Server-side IDF is why there is no corpus bookkeeping in
                    # application code and no Elasticsearch in the stack.
                    modifier=models.Modifier.IDF
                )
            },
        )
        # Tenant scoping is on the hot path of every single query, so the field
        # it filters on is indexed rather than scanned.
        await self.client.create_payload_index(
            collection_name=self.collection,
            field_name="user_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        await self.client.create_payload_index(
            collection_name=self.collection,
            field_name="doc_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
        )
        logger.info("created collection %s (dense dim %d)", self.collection, dim)

    # ---------------------------------------------------------------- upsert

    @staticmethod
    def _payload(chunk: Chunk) -> dict[str, Any]:
        """Exactly the §3 payload — enough to filter and to resolve a citation
        without a Postgres round trip on the retrieval path."""
        return {
            "user_id": chunk.user_id,
            "doc_id": chunk.doc_id,
            "chunk_id": chunk.id,
            "chunk_index": chunk.chunk_index,
            "page": chunk.page,
            "section": chunk.section,
            "char_start": chunk.char_start,
            "char_end": chunk.char_end,
        }

    @staticmethod
    def _point_id(chunk_id: str) -> str:
        """Qdrant point IDs must be a UUID or an unsigned int.

        Our chunk ids are a 24-char sha256 prefix, so they are formatted as a
        UUID here — deterministically, from the same hex — which keeps re-ingest
        an idempotent overwrite rather than a duplicate insert.
        """
        padded = (chunk_id + "0" * 32)[:32]
        return f"{padded[:8]}-{padded[8:12]}-{padded[12:16]}-{padded[16:20]}-{padded[20:32]}"

    async def upsert_chunks(
        self, chunks: list[Chunk], embeddings: list[EmbeddedChunk]
    ) -> None:
        if not chunks:
            return
        points = [
            models.PointStruct(
                id=self._point_id(chunk.id),
                vector={
                    DENSE_VECTOR_NAME: emb.dense,
                    SPARSE_VECTOR_NAME: models.SparseVector(
                        indices=emb.sparse.indices, values=emb.sparse.values
                    ),
                },
                payload=self._payload(chunk),
            )
            for chunk, emb in zip(chunks, embeddings, strict=True)
        ]
        await self.client.upsert(
            collection_name=self.collection, points=points, wait=True
        )

    async def delete_document(self, user_id: str, doc_id: str) -> None:
        """Delete every point for a document. Scoped by user_id as well as doc_id.

        Filtering on ``doc_id`` alone would be sufficient in practice — ids are
        uuids — but writing an unscoped delete makes I3 a matter of the caller
        passing the right argument rather than of the query shape.
        """
        await self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(filter=_scope(user_id, [doc_id])),
            wait=True,
        )

    async def count(self, user_id: str) -> int:
        result = await self.client.count(
            collection_name=self.collection,
            count_filter=_scope(user_id, None),
            exact=True,
        )
        return int(result.count)

    # ---------------------------------------------------------------- search

    async def hybrid_search(
        self,
        query: EmbeddedChunk,
        *,
        user_id: str,
        doc_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> list[ScoredPoint]:
        """One call: prefetch dense + sparse, fuse server-side with weighted RRF.

        ``k`` and ``weights`` are passed explicitly on every query. Inheriting the
        server default for ``k`` would leave ``RRF_MAX`` — and therefore
        ``FLOOR_FUSED`` — derived from a number we do not know and that can change
        under a version bump.
        """
        cfg = self.settings
        top_k = limit or cfg.retrieve_top_k
        query_filter = _scope(user_id, doc_ids)

        # Each branch retrieves the full width; fusion then reorders the union.
        prefetch = [
            models.Prefetch(
                query=query.dense,
                using=DENSE_VECTOR_NAME,
                filter=query_filter,
                limit=top_k,
            ),
            models.Prefetch(
                query=models.SparseVector(
                    indices=query.sparse.indices, values=query.sparse.values
                ),
                using=SPARSE_VECTOR_NAME,
                filter=query_filter,
                limit=top_k,
            ),
        ]

        try:
            response = await self.client.query_points(
                collection_name=self.collection,
                prefetch=prefetch,
                # NOT FusionQuery(fusion=Fusion.RRF) — that form carries neither
                # weights nor k. See the module docstring.
                query=models.RrfQuery(
                    rrf=models.Rrf(k=cfg.rrf_k, weights=[cfg.w_dense, cfg.w_sparse])
                ),
                limit=top_k,
                with_payload=True,
                timeout=int(cfg.timeout_qdrant_s),
            )
        except Exception as exc:  # noqa: BLE001
            # Retrieval is the product; it has no fallback. 503 names the
            # dependency rather than degrading to a worse answer.
            logger.error("qdrant search failed: %s", exc)
            raise DependencyUnavailable("qdrant", "Vector search failed.") from exc

        return [
            ScoredPoint(
                chunk_id=str((p.payload or {}).get("chunk_id", "")),
                doc_id=str((p.payload or {}).get("doc_id", "")),
                score=float(p.score),
                payload=dict(p.payload or {}),
            )
            for p in response.points
        ]

    async def branch_search(
        self,
        query: EmbeddedChunk,
        *,
        user_id: str,
        branch: str,
        doc_ids: list[str] | None = None,
        limit: int | None = None,
    ) -> list[ScoredPoint]:
        """A single unfused branch — dense or sparse alone.

        Exists for the ablation table (dense-only and BM25-only rows) and for the
        empirical `RRF_MAX` check, which needs to know a chunk's rank *within*
        each branch. Not used on the request path.
        """
        cfg = self.settings
        top_k = limit or cfg.retrieve_top_k
        vector: Any = (
            query.dense
            if branch == DENSE_VECTOR_NAME
            else models.SparseVector(
                indices=query.sparse.indices, values=query.sparse.values
            )
        )
        response = await self.client.query_points(
            collection_name=self.collection,
            query=vector,
            using=branch,
            query_filter=_scope(user_id, doc_ids),
            limit=top_k,
            with_payload=True,
            timeout=int(cfg.timeout_qdrant_s),
        )
        return [
            ScoredPoint(
                chunk_id=str((p.payload or {}).get("chunk_id", "")),
                doc_id=str((p.payload or {}).get("doc_id", "")),
                score=float(p.score),
                payload=dict(p.payload or {}),
            )
            for p in response.points
        ]


def _scope(user_id: str, doc_ids: list[str] | None) -> models.Filter:
    """Every filter this module builds starts from user_id (I3).

    ``doc_ids`` narrows further when the user has scoped the conversation to a
    subset of their documents — the "Multi-" in the project title, and the reason
    per-document checkboxes in the UI are backed by a payload filter rather than
    post-filtering in application code.
    """
    conditions: list[models.Condition] = [
        models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))
    ]
    if doc_ids:
        conditions.append(
            models.FieldCondition(key="doc_id", match=models.MatchAny(any=doc_ids))
        )
    return models.Filter(must=conditions)


_store: QdrantStore | None = None


def get_store() -> QdrantStore:
    global _store
    if _store is None:
        _store = QdrantStore()
    return _store


async def close_store() -> None:
    global _store
    if _store is not None:
        await _store.aclose()
    _store = None
