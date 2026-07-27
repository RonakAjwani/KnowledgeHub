"""Ingest orchestration: upload → parse → sanitize → chunk → embed → upsert.

Runs as an in-process background task. Not a separate worker service — that would
be a second Render service, and 750 free instance-hours per month fits exactly
one. So ingest shares the API process and its 512 MB ceiling, which is why the
embedding batch size matters and why escalation is paced rather than fanned out.

**Two orderings in here are load-bearing and both are easy to reverse by accident.**

*Qdrant before Postgres.* An orphaned vector is invisible to the user and
recoverable by re-running ingest. An orphaned Postgres chunk row is a citation
that resolves to a vector that does not exist — a broken link in the one feature
the whole design is built to make trustworthy.

*Deletion runs the other way:* Qdrant points → Postgres chunks → blob → document
row. Delete the document row first and the remaining rows are unreachable
garbage that nothing will ever clean up.

**Status is pushed, not polled.** Each transition publishes to the document's SSE
stream, because a 200-page PDF cannot block an HTTP request and a progress bar
that only moves on refresh is not a progress bar.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import models as db
from app.ingest.chunk import chunk_document
from app.ingest.crossref import resolve_cross_references
from app.ingest.embed import Embedder, get_embedder
from app.ingest.escalate import blocks_from_escalation, escalate_document
from app.ingest.normalize import build_normalized_text
from app.ingest.parse import UnsupportedDocument, parse_document
from app.llm.client import LLMClient, get_llm_client
from app.models.schemas import (
    Block,
    Chunk,
    Degradation,
    DocumentStatus,
    NormalizedDocument,
)
from app.retrieval.qdrant_store import QdrantStore, get_store

logger = logging.getLogger(__name__)

# Publishes a status update to the document's SSE stream.
StatusPublisher = Callable[[str, DocumentStatus, dict | None], Awaitable[None]]


async def _noop_publisher(
    doc_id: str, status: DocumentStatus, progress: dict | None
) -> None:
    return None


def content_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class IngestResult:
    document_id: str
    status: DocumentStatus
    chunk_count: int = 0
    degradations: list[Degradation] = field(default_factory=list)
    extraction: dict = field(default_factory=dict)
    error: str | None = None


class IngestPipeline:
    def __init__(
        self,
        *,
        store: QdrantStore | None = None,
        embedder: Embedder | None = None,
        llm: LLMClient | None = None,
        settings: Settings | None = None,
        publisher: StatusPublisher | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or get_store()
        self.embedder = embedder or get_embedder()
        self.llm = llm or get_llm_client()
        self.publish = publisher or _noop_publisher

    # ------------------------------------------------------------ the stages

    async def _parse_and_normalize(
        self, data: bytes, mime: str, doc_id: str
    ) -> tuple[NormalizedDocument, list[Degradation], dict]:
        """Tier 1, then Tier 2 for flagged pages, then the one builder."""
        await self.publish(doc_id, DocumentStatus.PARSING, None)

        parsed = parse_document(data, mime)
        degradations: list[Degradation] = []
        escalated_pages = 0

        if mime == "application/pdf" and parsed.complex_pages:
            recovered, escalation_degradations, attempted = await escalate_document(
                data,
                parsed.assessments,
                client=self.llm,
                settings=self.settings,
            )
            degradations.extend(escalation_degradations)
            escalated_pages = len(recovered)
            if recovered:
                parsed.blocks = _replace_pages(parsed.blocks, recovered)

        # Sanitisation happens *inside* this call, before offsets exist. Nothing
        # else in the codebase concatenates blocks.
        doc = build_normalized_text(parsed.blocks)

        extraction = {
            "pages_total": parsed.page_count,
            "pages_escalated": escalated_pages,
            "pages_flagged": len(parsed.complex_pages),
            "tables_recovered": parsed.tables_found,
            "figures_described": 0,
            "sanitization": doc.report.model_dump(),
            # A parser that fails loudly is more useful than one that fails
            # convincingly: this drives the quality indicator in the UI.
            "confidence": _extraction_confidence(parsed, escalated_pages),
        }
        return doc, degradations, extraction

    async def _chunk(
        self, doc: NormalizedDocument, doc_id: str, user_id: str
    ) -> list[Chunk]:
        await self.publish(doc_id, DocumentStatus.CHUNKING, None)
        references = resolve_cross_references(doc)
        return chunk_document(
            doc,
            doc_id=doc_id,
            user_id=user_id,
            references=references,
            settings=self.settings,
        )

    async def _embed_and_upsert(
        self, chunks: list[Chunk], doc_id: str, session: AsyncSession
    ) -> None:
        """Embed in batches, then write Qdrant first and Postgres second."""
        await self.publish(
            doc_id, DocumentStatus.EMBEDDING, {"done": 0, "total": len(chunks), "unit": "chunks"}
        )

        batch_size = self.settings.embed_batch_size
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            texts = [c.text for c in batch]

            # fastembed is synchronous and CPU-bound; run it off the event loop
            # so status events and other requests are not blocked behind it.
            embeddings = await asyncio.to_thread(self.embedder.embed_documents, texts)

            await self.store.upsert_chunks(batch, embeddings)
            await _mirror_chunks(session, batch)

            await self.publish(
                doc_id,
                DocumentStatus.EMBEDDING,
                {"done": start + len(batch), "total": len(chunks), "unit": "chunks"},
            )

        await session.flush()

    # ------------------------------------------------------------- entrypoint

    async def ingest(
        self,
        session: AsyncSession,
        *,
        document_id: str,
        user_id: str,
        data: bytes,
        mime: str,
    ) -> IngestResult:
        """Run the pipeline. Never raises — failure is recorded on the document.

        A partial index is the worst available outcome: the user believes their
        document is searchable and it silently is not. So any stage failure rolls
        the document to ``failed`` with a user-facing reason, and whatever vectors
        were written are removed.
        """
        try:
            doc, degradations, extraction = await self._parse_and_normalize(
                data, mime, document_id
            )
            chunks = await self._chunk(doc, document_id, user_id)
            await self._embed_and_upsert(chunks, document_id, session)

            await _update_document(
                session,
                document_id,
                status=DocumentStatus.READY,
                normalized_text=doc.text,
                chunk_count=len(chunks),
                sanitization_report=doc.report.model_dump(),
                extraction=extraction,
            )
            await session.commit()

            return IngestResult(
                document_id=document_id,
                status=DocumentStatus.READY,
                chunk_count=len(chunks),
                degradations=degradations,
                extraction=extraction,
            )

        except UnsupportedDocument as exc:
            return await self._fail(session, document_id, user_id, str(exc))
        except Exception as exc:  # noqa: BLE001
            logger.exception("ingest failed for %s", document_id)
            return await self._fail(
                session,
                document_id,
                user_id,
                f"Ingest failed while processing this document: {exc}",
            )

    async def _fail(
        self, session: AsyncSession, document_id: str, user_id: str, reason: str
    ) -> IngestResult:
        await session.rollback()
        # Remove any vectors written before the failure — otherwise a retry
        # would layer a second partial index on top of the first.
        try:
            await self.store.delete_document(user_id, document_id)
        except Exception:  # noqa: BLE001
            logger.warning("could not clean up vectors for failed doc %s", document_id)

        await _update_document(
            session, document_id, status=DocumentStatus.FAILED, error=reason
        )
        await session.commit()
        return IngestResult(
            document_id=document_id, status=DocumentStatus.FAILED, error=reason
        )

    # --------------------------------------------------------------- deletion

    async def delete(
        self, session: AsyncSession, *, document_id: str, user_id: str
    ) -> None:
        """Cascade in the order that never leaves an unreachable remainder.

        Qdrant points → Postgres chunks → blob → document row. Reversing this
        strands rows nothing will ever find again.
        """
        await self.store.delete_document(user_id, document_id)

        await session.execute(
            sql_delete(db.Chunk).where(
                db.Chunk.doc_id == document_id, db.Chunk.user_id == user_id
            )
        )
        document = await session.get(db.Document, document_id)
        if document is not None and document.user_id == user_id:
            document.blob_ref = None
            await session.delete(document)
        await session.commit()


# ----------------------------------------------------------------- helpers


def _replace_pages(blocks: list[Block], recovered: dict[int, str]) -> list[Block]:
    """Swap Tier-1 blocks for Tier-2 output on escalated pages, keeping order.

    Replacement rather than merge: if the VLM read the page, its transcription is
    the better one, and keeping both would double-index the same content and let
    the flattened Tier-1 version compete with the structured one in retrieval.
    """
    out: list[Block] = []
    inserted: set[int] = set()
    for block in blocks:
        if block.page in recovered:
            if block.page not in inserted:
                out.extend(blocks_from_escalation(block.page, recovered[block.page]))
                inserted.add(block.page)
            continue
        out.append(block)

    # A page whose Tier-1 pass produced no blocks at all — a pure scan — has
    # nothing to replace, so its recovered content is appended in page order.
    for page in sorted(set(recovered) - inserted):
        out.extend(blocks_from_escalation(page, recovered[page]))
    return out


def _extraction_confidence(parsed, escalated: int) -> float:
    """A blunt, honest signal — not a calibrated probability.

    Flagged pages that were escalated are counted as recovered; flagged pages
    that were not are counted against the score, which is what makes hitting the
    escalation cap visible in the document manager rather than only in a log.
    """
    if parsed.page_count == 0:
        return 0.0
    flagged = len(parsed.complex_pages)
    unresolved = max(0, flagged - escalated)
    return round(max(0.0, 1.0 - (unresolved / parsed.page_count)), 3)


async def _mirror_chunks(session: AsyncSession, chunks: list[Chunk]) -> None:
    """Postgres mirror of the Qdrant points, for citation resolution.

    **Upsert, not insert.** Deterministic chunk ids are what make re-ingest
    idempotent, and Qdrant's ``upsert`` honours that for free — but a plain
    ``session.add`` on the Postgres side turns the same determinism into a
    primary-key violation on the second run. The two stores have to agree about
    what "write this chunk again" means, or idempotency holds in one of them and
    fails the whole ingest in the other.
    """
    if not chunks:
        return

    rows = [
        {
            "id": chunk.id,
            "doc_id": chunk.doc_id,
            "user_id": chunk.user_id,
            "chunk_index": chunk.chunk_index,
            "text": chunk.text,
            "char_start": chunk.char_start,
            "char_end": chunk.char_end,
            "parent_text": chunk.parent_text,
            "parent_char_start": chunk.parent_char_start,
            "parent_char_end": chunk.parent_char_end,
            "section": chunk.section,
            "page": chunk.page,
            "token_count": chunk.token_count,
            "chunk_type": str(chunk.chunk_type),
            "is_derived": chunk.is_derived,
            "related_spans": [list(span) for span in chunk.related_spans],
        }
        for chunk in chunks
    ]

    statement = pg_insert(db.Chunk).values(rows)
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[db.Chunk.id],
            set_={
                key: getattr(statement.excluded, key)
                for key in rows[0]
                if key != "id"
            },
        )
    )


async def _update_document(
    session: AsyncSession, document_id: str, **fields
) -> None:
    document = await session.get(db.Document, document_id)
    if document is None:
        return
    for key, value in fields.items():
        setattr(document, key, str(value) if key == "status" else value)


async def find_existing_document(
    session: AsyncSession, *, user_id: str, sha256: str
) -> db.Document | None:
    """Idempotency, scoped per user.

    Re-uploading a file returns the existing document with HTTP 200 rather than
    reprocessing it. Scoped rather than global because two users uploading the
    same public PDF are two documents.
    """
    result = await session.execute(
        select(db.Document).where(
            db.Document.user_id == user_id, db.Document.content_sha256 == sha256
        )
    )
    return result.scalar_one_or_none()
