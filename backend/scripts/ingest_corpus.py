"""Ingest the evaluation corpus into a named collection.

Separate from the eval runner so ingestion happens once and questions can be
re-run against it cheaply - VLM escalation on a 43-page 10-Q is minutes of work
and real token spend, and re-paying it on every threshold sweep would make
tuning unaffordable.

    docker compose up -d postgres qdrant
    PYTHONPATH=. poetry run python scripts/ingest_corpus.py
    PYTHONPATH=. poetry run python scripts/ingest_corpus.py --reset

``--reset`` deletes the eval user's documents first. It is required, not
optional, after any change to parsing or chunking: chunk ids are
``sha256(doc_id|chunk_index|text)``, so changed text produces changed ids, and a
plain re-run would upsert a second full set of points beside the first while the
originals stayed in Qdrant, unreachable and still matching queries.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db import models as db
from app.ingest.embed import Embedder
from app.ingest.pipeline import IngestPipeline, content_sha256, find_existing_document
from app.llm.client import LLMClient
from app.retrieval.qdrant_store import QdrantStore
from evals.corpus import CORPUS, EXCLUDED, FILENAMES

CORPUS_DIR = pathlib.Path(__file__).resolve().parents[2] / "document corpus"
EVAL_USER = "eval-user"
EVAL_COLLECTION = "eval_chunks"

MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


def eval_settings() -> Settings:
    return Settings(
        qdrant_collection=EVAL_COLLECTION,
        # The 10-Q flags 11 pages as having tables Tier 1 can see but not read.
        # The default cap of 10 would leave one of them locally-parsed, and the
        # question bank asks about exactly those financial tables.
        max_escalated_pages=16,
    )


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete the eval user's documents first (required after a parse or "
        "chunking change, since chunk ids are content-derived)",
    )
    args = parser.parse_args()

    settings = eval_settings()
    store = QdrantStore(settings)
    embedder = Embedder(settings)
    llm = LLMClient(settings)
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    await store.ensure_collection(dense_dim=embedder.dense_dimension)

    # The corpus is what ``evals.corpus`` declares, not whatever happens to sit
    # in the directory. Dropping a file into `document corpus/` used to silently
    # widen the evaluation set; now it is ignored until it is declared.
    files = sorted(p for p in CORPUS_DIR.iterdir() if p.name in FILENAMES)
    missing = FILENAMES - {p.name for p in files}
    if missing:
        print(f"declared but not on disk: {sorted(missing)}")
        return 1

    print(f"ingesting {len(files)} declared documents into '{EVAL_COLLECTION}'")
    for doc in CORPUS:
        print(f"  {doc.key:<10} {doc.filename}")
    for name in sorted(EXCLUDED):
        print(f"  {'(excluded)':<10} {name}")
    print()
    total_chunks = 0

    async with maker() as session:
        pipeline = IngestPipeline(
            store=store, embedder=embedder, llm=llm, settings=settings
        )

        if args.reset:
            rows = await session.execute(
                select(db.Document).where(db.Document.user_id == EVAL_USER)
            )
            stale = rows.scalars().all()
            for document in stale:
                await pipeline.delete(
                    session, document_id=document.id, user_id=EVAL_USER
                )
            await session.commit()
            print(f"reset: removed {len(stale)} documents\n")

        for path in files:
            data = path.read_bytes()
            mime = MIME_BY_SUFFIX[path.suffix]
            sha = content_sha256(data)

            existing = await find_existing_document(
                session, user_id=EVAL_USER, sha256=sha
            )
            if existing is not None and existing.status == "ready":
                print(f"  {path.name:<46} already ingested ({existing.chunk_count} chunks)")
                total_chunks += existing.chunk_count
                continue

            document = existing or db.Document(
                user_id=EVAL_USER,
                filename=path.name,
                mime=mime,
                content_sha256=sha,
                blob_ref=data,
                status="queued",
            )
            if existing is None:
                session.add(document)
                await session.commit()

            started = time.time()
            result = await pipeline.ingest(
                session,
                document_id=document.id,
                user_id=EVAL_USER,
                data=data,
                mime=mime,
            )
            elapsed = time.time() - started
            total_chunks += result.chunk_count

            extraction = result.extraction
            print(
                f"  {path.name:<46} {result.status:<8} "
                f"{result.chunk_count:>5} chunks  {elapsed:>6.1f}s  "
                f"pages={extraction.get('pages_total', '?')} "
                f"escalated={extraction.get('pages_escalated', 0)}/"
                f"{extraction.get('pages_flagged', 0)} "
                f"tables={extraction.get('tables_recovered', 0)} "
                f"conf={extraction.get('confidence', 0)}"
            )
            if result.error:
                print(f"      error: {result.error}")
            for degradation in result.degradations:
                print(f"      degraded [{degradation.reason}] {degradation.detail}")

        rows = await session.execute(
            select(db.Document).where(db.Document.user_id == EVAL_USER)
        )
        documents = rows.scalars().all()

    print(f"\n{len(documents)} documents, {total_chunks} chunks")
    print(f"vectors in Qdrant: {await store.count(EVAL_USER)}")

    await llm.aclose()
    await store.aclose()
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
