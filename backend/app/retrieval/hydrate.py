"""Refill retrieved candidates with their text from the Postgres mirror.

The Qdrant payload deliberately carries only what is needed to *filter* and to
*locate* a chunk - ``user_id``, ``doc_id``, ``chunk_id``, offsets, section, page.
It does not carry the chunk text, because duplicating every document into the
vector store's payload would inflate a 1 GB free tier for no retrieval benefit.

Which means a candidate arrives from search with empty ``text`` and
``parent_text``, and two stages downstream silently produce nonsense if it stays
that way:

* **rerank** sends ``chunk.text`` to Cohere - blank documents rerank into noise
  while still returning a confident-looking ordering;
* **generate** wraps ``parent_text`` in DATA blocks - the model receives empty
  documents and correctly reports that it has no information, which reads to a
  user as "the system cannot find things that are obviously there".

Neither failure raises. This module is what makes the mirror load-bearing rather
than decorative, and it runs *before* rerank, not just before generation.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models as db
from app.models.schemas import DocumentStatus, RetrievedChunk

logger = logging.getLogger(__name__)


async def hydrate_candidates(
    session: AsyncSession, user_id: str, candidates: Sequence[RetrievedChunk]
) -> list[RetrievedChunk]:
    """Fill in text, parent text and metadata from Postgres.

    Scoped by ``user_id`` as well as chunk id (I3): the ids came from a
    user-filtered search, but a lookup that does not re-assert the filter is a
    lookup that could be reached from somewhere that did not.
    """
    if not candidates:
        return []

    chunk_ids = [c.chunk.id for c in candidates]
    result = await session.execute(
        select(db.Chunk, db.Document.filename)
        .join(db.Document, db.Document.id == db.Chunk.doc_id)
        .where(db.Chunk.id.in_(chunk_ids), db.Chunk.user_id == user_id)
    )
    # The filename rides along on the join rather than costing a second query.
    # It is what lets the model attribute a passage to a document, so a citation
    # can name its source and a "which document covers X" question is answerable.
    fetched = result.all()
    rows = {row[0].id: row[0] for row in fetched}
    names = {row[0].id: row[1] for row in fetched}

    hydrated: list[RetrievedChunk] = []
    for candidate in candidates:
        row = rows.get(candidate.chunk.id)
        if row is None:
            # A vector whose mirror row is gone - the recoverable half of the
            # write ordering. Drop it rather than send an empty document
            # downstream, and say so, because a citation pointing at a missing
            # chunk is exactly what the ordering exists to prevent.
            logger.warning("no Postgres mirror for chunk %s; dropping", candidate.chunk.id)
            continue

        hydrated.append(
            candidate.model_copy(
                update={
                    "chunk": candidate.chunk.model_copy(
                        update={
                            "text": row.text,
                            "source_name": names.get(candidate.chunk.id),
                            "parent_text": row.parent_text,
                            "parent_char_start": row.parent_char_start,
                            "parent_char_end": row.parent_char_end,
                            "token_count": row.token_count,
                            "chunk_type": row.chunk_type,
                            "is_derived": row.is_derived,
                            "related_spans": tuple(
                                tuple(span) for span in (row.related_spans or [])
                            ),
                        }
                    )
                }
            )
        )

    return hydrated


async def load_ready_doc_ids(session: AsyncSession, user_id: str) -> list[str]:
    """Every document this user can be asked about, scoped by ``user_id`` (I3).

    Overview questions are answered on coverage, and coverage cannot be obtained
    from a ranked search: one global query returns whatever is topically closest
    and clusters on a couple of documents, so "which documents do I have" names
    the two it happened to hit. Retrieving per document needs the list of
    documents, and this is it.

    Only ``ready`` documents. A document still parsing has no vectors to search,
    and one that failed has nothing to say - including either would advertise a
    document the user cannot actually query.
    """
    result = await session.execute(
        select(db.Document.id).where(
            db.Document.user_id == user_id,
            db.Document.status == str(DocumentStatus.READY),
        )
    )
    return [row[0] for row in result.all()]


async def load_filenames(
    session: AsyncSession, user_id: str, doc_ids: Sequence[str]
) -> dict[str, str]:
    """doc_id -> filename, for citation chips."""
    if not doc_ids:
        return {}
    result = await session.execute(
        select(db.Document.id, db.Document.filename).where(
            db.Document.id.in_(list(doc_ids)), db.Document.user_id == user_id
        )
    )
    return {doc_id: filename for doc_id, filename in result.all()}
