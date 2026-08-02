"""End-to-end ingest against real Qdrant and Postgres.

Skipped automatically when the services are not up, so the suite still runs
offline - but the guarantees here (write ordering, the deletion cascade,
idempotency, tenant isolation) cannot be proven against a mock, because what they
assert is precisely that two *different* systems agree.

Bring the services up with:  docker compose up -d postgres qdrant
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete as sql_delete
from sqlalchemy import event, func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.workspaces import list_workspaces
from app.config import Settings
from app.db import models as db
from app.ingest.embed import Embedder
from app.ingest.pipeline import (
    IngestPipeline,
    _commit_chunk_batch,
    _mirror_chunks,
    content_sha256,
    find_existing_document,
)
from app.models.schemas import Chunk, DocumentStatus, chunk_id
from app.retrieval.qdrant_store import QdrantStore

DB_URL = "postgresql+asyncpg://knowledgehub:knowledgehub@localhost:5432/knowledgehub"
QDRANT_URL = "http://localhost:6333"

pytestmark = pytest.mark.asyncio


async def _services_up() -> bool:
    import httpx

    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.get(f"{QDRANT_URL}/")
        engine = create_async_engine(DB_URL)
        async with engine.connect():
            pass
        await engine.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest_asyncio.fixture(scope="module")
async def services():
    if not await _services_up():
        pytest.skip("postgres/qdrant not running - `docker compose up -d postgres qdrant`")
    return True


@pytest_asyncio.fixture
async def settings(services) -> Settings:
    # A throwaway collection per run, so a failed test cannot poison the next.
    return Settings(
        database_url=DB_URL,
        qdrant_url=QDRANT_URL,
        qdrant_collection=f"test_chunks_{uuid.uuid4().hex[:8]}",
        embed_batch_size=4,
        child_tokens=60,
        parent_tokens=200,
    )


@pytest_asyncio.fixture
async def store(settings: Settings):
    s = QdrantStore(settings)
    await s.ensure_collection(dense_dim=384)
    yield s
    try:
        await s.client.delete_collection(settings.qdrant_collection)
    finally:
        await s.aclose()


@pytest_asyncio.fixture
async def session(settings: Settings):
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def pipeline(settings: Settings, store: QdrantStore):
    return IngestPipeline(
        store=store, embedder=Embedder(settings), settings=settings
    )


@pytest_asyncio.fixture
async def document(session, settings: Settings):
    """A queued document row, cleaned up afterwards."""
    created: list[str] = []

    async def _make(user_id: str, data: bytes, filename: str = "doc.md") -> db.Document:
        doc = db.Document(
            user_id=user_id,
            filename=filename,
            mime="text/markdown",
            content_sha256=content_sha256(data),
            blob_ref=data,
            status="queued",
        )
        session.add(doc)
        await session.commit()
        created.append(doc.id)
        return doc

    yield _make

    for doc_id in created:
        existing = await session.get(db.Document, doc_id)
        if existing is not None:
            await session.delete(existing)
    await session.commit()


CORPUS = b"""# Quarterly Report

Revenue reached $8M in Q3, as Table 1 shows. Growth was driven by enterprise
contracts across the cloud segment.

Table 1: Quarterly revenue by segment

| quarter | revenue | segment |
| --- | --- | --- |
| Q1 | 5200000 | cloud |
| Q2 | 6400000 | cloud |
| Q3 | 8000000 | cloud |

## Outlook

We expect continued expansion in the next fiscal year.
"""


# ------------------------------------------------------------------ happy path


async def test_ingest_reaches_ready_and_writes_both_stores(
    pipeline, session, document, store, settings
) -> None:
    user = f"u-{uuid.uuid4().hex[:8]}"
    doc = await document(user, CORPUS)

    result = await pipeline.ingest(
        session, document_id=doc.id, user_id=user, data=CORPUS, mime="text/markdown"
    )

    assert result.status is DocumentStatus.READY, result.error
    assert result.chunk_count > 0

    # Postgres side
    await session.refresh(doc)
    assert doc.status == "ready"
    assert doc.normalized_text, "the offset referent must be persisted"
    assert doc.chunk_count == result.chunk_count

    rows = (
        await session.execute(
            select(func.count()).select_from(db.Chunk).where(db.Chunk.doc_id == doc.id)
        )
    ).scalar_one()
    assert rows == result.chunk_count

    # Qdrant side - the two stores must agree, which is the whole point of
    # mirroring rather than deriving one from the other.
    assert await store.count(user) == result.chunk_count


async def test_offsets_survive_into_postgres(pipeline, session, document) -> None:
    """The citation chain, across the process boundary.

    Everything upstream is verified in-memory; this proves the offsets still
    slice correctly out of the persisted normalized_text.
    """
    user = f"u-{uuid.uuid4().hex[:8]}"
    doc = await document(user, CORPUS)
    await pipeline.ingest(
        session, document_id=doc.id, user_id=user, data=CORPUS, mime="text/markdown"
    )
    await session.refresh(doc)

    chunks = (
        await session.execute(select(db.Chunk).where(db.Chunk.doc_id == doc.id))
    ).scalars().all()

    assert chunks
    for chunk in chunks:
        sliced = doc.normalized_text[chunk.char_start : chunk.char_end]
        assert sliced.strip(), f"chunk {chunk.id} points at empty text"
        assert (
            doc.normalized_text[chunk.parent_char_start : chunk.parent_char_end]
            == chunk.parent_text
        )


async def test_search_finds_an_exact_identifier(pipeline, session, document, store) -> None:
    """BM25's job. A revenue figure is exactly where dense retrieval is weakest,
    and exactly what the sparse branch is for."""
    user = f"u-{uuid.uuid4().hex[:8]}"
    doc = await document(user, CORPUS)
    await pipeline.ingest(
        session, document_id=doc.id, user_id=user, data=CORPUS, mime="text/markdown"
    )

    query = Embedder(store.settings).embed_query("8000000 cloud segment revenue")
    hits = await store.hybrid_search(query, user_id=user)

    assert hits, "hybrid search returned nothing"
    assert all(h.payload["user_id"] == user for h in hits)
    assert all(h.payload["doc_id"] == doc.id for h in hits)


# ------------------------------------------------------------------ scoping


async def test_one_users_documents_are_invisible_to_another(
    pipeline, session, document, store
) -> None:
    """I3, proven rather than asserted: the filter is on the query, so a second
    user's search cannot reach the first user's vectors."""
    user_a = f"a-{uuid.uuid4().hex[:8]}"
    user_b = f"b-{uuid.uuid4().hex[:8]}"

    doc_a = await document(user_a, CORPUS)
    await pipeline.ingest(
        session, document_id=doc_a.id, user_id=user_a, data=CORPUS, mime="text/markdown"
    )

    assert await store.count(user_a) > 0
    assert await store.count(user_b) == 0

    query = Embedder(store.settings).embed_query("revenue")
    assert await store.hybrid_search(query, user_id=user_b) == []


async def test_doc_scoping_filters_to_the_selected_documents(
    pipeline, session, document, store
) -> None:
    """The 'Multi-' in the project title - backed by a payload filter, not by
    post-filtering in application code."""
    user = f"u-{uuid.uuid4().hex[:8]}"
    doc_one = await document(user, CORPUS, "one.md")
    other = b"# Unrelated\n\nCompletely different subject matter about penguins.\n"
    doc_two = await document(user, other, "two.md")

    await pipeline.ingest(
        session, document_id=doc_one.id, user_id=user, data=CORPUS, mime="text/markdown"
    )
    await pipeline.ingest(
        session, document_id=doc_two.id, user_id=user, data=other, mime="text/markdown"
    )

    query = Embedder(store.settings).embed_query("revenue")
    scoped = await store.hybrid_search(query, user_id=user, doc_ids=[doc_two.id])

    assert scoped, "scoped search should still return the selected document"
    assert {h.doc_id for h in scoped} == {doc_two.id}


# --------------------------------------------------------------- idempotency


async def test_reingest_does_not_duplicate_vectors(
    pipeline, session, document, store
) -> None:
    """Deterministic chunk ids make re-ingest an overwrite, not a second copy."""
    user = f"u-{uuid.uuid4().hex[:8]}"
    doc = await document(user, CORPUS)

    first = await pipeline.ingest(
        session, document_id=doc.id, user_id=user, data=CORPUS, mime="text/markdown"
    )
    count_after_first = await store.count(user)

    second = await pipeline.ingest(
        session, document_id=doc.id, user_id=user, data=CORPUS, mime="text/markdown"
    )

    assert second.chunk_count == first.chunk_count
    assert await store.count(user) == count_after_first


async def test_duplicate_upload_is_found_by_hash(session, document) -> None:
    user = f"u-{uuid.uuid4().hex[:8]}"
    doc = await document(user, CORPUS)

    found = await find_existing_document(
        session, user_id=user, sha256=content_sha256(CORPUS)
    )
    assert found is not None and found.id == doc.id

    # Scoped per user: the same bytes from someone else is a different document.
    assert (
        await find_existing_document(
            session, user_id="someone-else", sha256=content_sha256(CORPUS)
        )
        is None
    )


# ------------------------------------------------------------------ deletion


async def test_delete_cascades_to_both_stores(
    pipeline, session, document, store
) -> None:
    """Deletion must actually delete - in both systems, not just the one the UI
    reads from."""
    user = f"u-{uuid.uuid4().hex[:8]}"
    doc = await document(user, CORPUS)
    doc_id = doc.id

    await pipeline.ingest(
        session, document_id=doc_id, user_id=user, data=CORPUS, mime="text/markdown"
    )
    assert await store.count(user) > 0

    await pipeline.delete(session, document_id=doc_id, user_id=user)

    assert await store.count(user) == 0
    remaining = (
        await session.execute(
            select(func.count()).select_from(db.Chunk).where(db.Chunk.doc_id == doc_id)
        )
    ).scalar_one()
    assert remaining == 0
    assert await session.get(db.Document, doc_id) is None


# -------------------------------------------------------------------- failure


async def test_unparseable_document_fails_loudly_and_leaves_nothing_behind(
    pipeline, session, document, store
) -> None:
    """A partial index is the worst available outcome: the user believes the
    document is searchable and it silently is not."""
    user = f"u-{uuid.uuid4().hex[:8]}"
    bad = b"%PDF-1.4 this is not actually a pdf"
    doc = await document(user, bad, "broken.pdf")

    result = await pipeline.ingest(
        session, document_id=doc.id, user_id=user, data=bad, mime="application/pdf"
    )

    assert result.status is DocumentStatus.FAILED
    assert result.error
    await session.refresh(doc)
    assert doc.status == "failed"
    assert doc.error
    assert await store.count(user) == 0


# ------------------------------------------------------- concurrent ingest


async def test_two_documents_ingest_concurrently_without_deadlocking(
    settings, store, session, document
) -> None:
    """Two ingests running at once against real Postgres, each in its own
    session - the shape "upload two large PDFs into one workspace" produces.

    Reported as a Postgres deadlock on the chunk upsert. Measured here so the
    claim is testable rather than remembered: chunk ids are
    sha256(doc_id|chunk_index|text) and the second unique key is
    (doc_id, chunk_index), so two *different* documents share no row and no
    cycle is available to them. What this guards is the write path staying
    that way - a chunk id that stopped including doc_id, or a shared row
    introduced into the loop, would make the reported deadlock real.
    """
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    user = f"u-{uuid.uuid4().hex[:8]}"
    doc_a = await document(user, CORPUS + b"\nAlpha segment.\n", "a.md")
    doc_b = await document(user, CORPUS + b"\nBeta segment.\n", "b.md")

    async def run(doc_id: str, data: bytes):
        pipeline = IngestPipeline(
            store=store, embedder=Embedder(settings), settings=settings
        )
        async with maker() as own_session:
            return await pipeline.ingest(
                own_session,
                document_id=doc_id,
                user_id=user,
                data=data,
                mime="text/markdown",
            )

    try:
        results = await asyncio.gather(
            run(doc_a.id, CORPUS + b"\nAlpha segment.\n"),
            run(doc_b.id, CORPUS + b"\nBeta segment.\n"),
        )
    finally:
        await engine.dispose()

    assert [r.status for r in results] == [DocumentStatus.READY, DocumentStatus.READY]
    assert all(r.chunk_count > 0 for r in results)


async def test_chunk_rows_are_written_in_id_order(session, document) -> None:
    """The circular-wait defence, and the only test that can catch its loss.

    Postgres locks rows in the order the VALUES list presents them, so two
    writers whose key sets overlap deadlock if they present those keys in
    opposite orders. Sorting by id gives every writer one global order, which
    makes the cycle unconstructible - including across replicas, where the
    in-process ingest semaphore does nothing. Nothing else fails if a refactor
    drops the sort: the rows written are identical, and the deadlock it
    prevents only appears under concurrency that no unit test reproduces.
    """
    captured: list[list[str]] = []

    class Recorder:
        async def execute(self, statement):
            compiled = statement.compile()
            ids = [
                value
                for key, value in compiled.params.items()
                if key.startswith("id")
            ]
            captured.append(ids)

    chunks = [
        Chunk(
            id=chunk_id("doc-1", i, f"body {i}"),
            doc_id="doc-1",
            user_id="u",
            chunk_index=i,
            text=f"body {i}",
            char_start=i,
            char_end=i + 1,
            parent_text=f"body {i}",
            parent_char_start=i,
            parent_char_end=i + 1,
        )
        for i in range(12)
    ]
    # Deliberately not already sorted: chunk_index order is arbitrary with
    # respect to a sha256 digest, which is exactly the starting condition.
    assert [c.id for c in chunks] != sorted(c.id for c in chunks)

    await _mirror_chunks(Recorder(), chunks)  # type: ignore[arg-type]

    assert captured, "the upsert statement should have been executed"
    assert captured[0] == sorted(captured[0])


async def test_a_deadlocked_chunk_batch_is_retried_once_then_surfaces() -> None:
    """Postgres breaks a deadlock by killing one side, so the victim's re-run
    finds no contention - retrying is the correct response, not a paper-over.
    Exactly once: a second deadlock is not a race, and retrying forever would
    turn a visible failure into a hang."""
    attempts = 0

    class Deadlocking:
        def __init__(self, fail_times: int) -> None:
            self.fail_times = fail_times

        async def execute(self, statement):
            nonlocal attempts
            attempts += 1
            if attempts <= self.fail_times:
                orig = Exception("deadlock detected")
                orig.sqlstate = "40P01"  # type: ignore[attr-defined]
                raise DBAPIError("INSERT INTO chunks", {}, orig)

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    batch = [
        Chunk(
            id=chunk_id("doc-1", 0, "body"),
            doc_id="doc-1",
            user_id="u",
            chunk_index=0,
            text="body",
            char_start=0,
            char_end=4,
            parent_text="body",
            parent_char_start=0,
            parent_char_end=4,
        )
    ]

    attempts = 0
    await _commit_chunk_batch(Deadlocking(1), batch)  # type: ignore[arg-type]
    assert attempts == 2, "one deadlock should cost exactly one retry"

    attempts = 0
    with pytest.raises(DBAPIError):
        await _commit_chunk_batch(Deadlocking(2), batch)  # type: ignore[arg-type]
    assert attempts == 2, "a second deadlock must surface, not retry again"


async def test_a_non_deadlock_error_is_not_retried() -> None:
    """Re-running a statement that failed on its merits just fails again, and
    the same distinction §5 draws for connection errors applies here."""
    attempts = 0

    class Failing:
        async def execute(self, statement):
            nonlocal attempts
            attempts += 1
            orig = Exception("duplicate key")
            orig.sqlstate = "23505"  # type: ignore[attr-defined]
            raise DBAPIError("INSERT INTO chunks", {}, orig)

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    batch = [
        Chunk(
            id=chunk_id("doc-1", 0, "body"),
            doc_id="doc-1",
            user_id="u",
            chunk_index=0,
            text="body",
            char_start=0,
            char_end=4,
            parent_text="body",
            parent_char_start=0,
            parent_char_end=4,
        )
    ]

    with pytest.raises(DBAPIError):
        await _commit_chunk_batch(Failing(), batch)  # type: ignore[arg-type]
    assert attempts == 1


# --------------------------------------------------------- workspace listing


async def test_listing_workspaces_costs_the_same_queries_at_any_size(
    settings, session
) -> None:
    """MEASURED: this endpoint used to issue 1 + 2N statements - 37 for 18
    workspaces - because it looped and counted per row. Locally that is 51 ms
    and invisible, which is exactly why it survived review; against Postgres
    over a network it is 37 *sequential* round trips on the first screen after
    sign-in, and it got slower the more the account was used.

    Asserting on the statement count rather than on latency: the response body
    is identical either way, and a local timing would pass at any N. Counting
    statements at two sizes is the only thing that distinguishes "constant" from
    "linear" without a network to measure over."""
    engine = create_async_engine(settings.database_url)
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    user = f"u-{uuid.uuid4().hex[:8]}"
    counts: dict[int, int] = {}
    try:
        async with maker() as own:
            for size in (2, 9):
                own.add_all(
                    [db.Workspace(user_id=user, name=f"w{i}") for i in range(size)]
                )
                await own.commit()

                statements.clear()
                listed = await list_workspaces(user_id=user, session=own)
                counts[size] = len(statements)
                assert len(listed) == size

                # Wipe so the next size is exact rather than cumulative.
                await own.execute(
                    sql_delete(db.Workspace).where(db.Workspace.user_id == user)
                )
                await own.commit()
    finally:
        await engine.dispose()

    assert counts[2] == counts[9], (
        f"query count must not grow with the number of workspaces, "
        f"got {counts[2]} at 2 and {counts[9]} at 9"
    )
    assert counts[9] <= 3, f"expected the list plus two grouped counts, got {counts[9]}"


# ------------------------------------------------------------- status events


async def test_status_transitions_are_published_in_order(
    session, document, store, settings
) -> None:
    """A 200-page PDF cannot block a request, so progress is pushed."""
    seen: list[DocumentStatus] = []

    async def publisher(doc_id: str, status: DocumentStatus, progress: dict | None):
        seen.append(status)

    user = f"u-{uuid.uuid4().hex[:8]}"
    doc = await document(user, CORPUS)
    pipeline = IngestPipeline(
        store=store,
        embedder=Embedder(settings),
        settings=settings,
        publisher=publisher,
    )
    await pipeline.ingest(
        session, document_id=doc.id, user_id=user, data=CORPUS, mime="text/markdown"
    )

    assert seen[0] is DocumentStatus.PARSING
    assert DocumentStatus.CHUNKING in seen
    assert DocumentStatus.EMBEDDING in seen
    assert seen.index(DocumentStatus.CHUNKING) < seen.index(DocumentStatus.EMBEDDING)
