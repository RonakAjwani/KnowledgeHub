"""Is the golden set actually answerable from the parsed corpus?

Run this before spending a single generation token. Every expected substring in
``evals.questions`` is checked against the ``normalized_text`` the pipeline
actually produced — the same string chunk offsets index into (I5), so this is
the text the model can possibly see, not the PDF a human reads.

The distinction it buys is the one that matters when a score comes back at 60%:

    string absent from every document   the *question* is wrong, or parsing
                                        dropped the fact. Retrieval cannot be
                                        blamed and tuning it is wasted effort.
    string present, question failed     a real pipeline failure — retrieval,
                                        ranking or generation.

Without this, both look identical in the results table, and the first one reads
as a retrieval bug that no amount of tuning will fix. It also catches the
inverse: an expected substring for a *negative* control that turns out to be
present is a mislabelled question, and the pipeline would be marked wrong for
being right.

    docker compose up -d postgres qdrant
    PYTHONPATH=. poetry run python scripts/probe_golden_set.py
"""

from __future__ import annotations

import asyncio
import re
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db import models as db
from evals.corpus import BY_FILENAME
from evals.questions import QUESTIONS, Expect
from evals.run import EVAL_USER, present


def _windows(needle: str, text: str, *, width: int = 60, limit: int = 2) -> list[str]:
    """Where in the document the string occurs, for eyeballing a false match."""
    found: list[str] = []
    for match in re.finditer(re.escape(needle), text, flags=re.IGNORECASE):
        start = max(0, match.start() - width)
        found.append(" ".join(text[start : match.end() + width].split()))
        if len(found) == limit:
            break
    return found


async def main() -> int:
    settings = Settings(qdrant_collection="eval_chunks")
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        rows = await session.execute(
            select(db.Document).where(
                db.Document.user_id == EVAL_USER, db.Document.status == "ready"
            )
        )
        documents = rows.scalars().all()

    # The filename is part of what the model can see: every DATA block carries a
    # `source: <filename>` header, and the system prompt tells the model to refer
    # to documents by filename. A question whose answer *is* a document name is
    # therefore answerable even though the name appears nowhere in the text, so
    # searching normalized_text alone reports a false BROKEN.
    text_by_key = {
        BY_FILENAME[d.filename].key: f"{d.filename}\n{d.normalized_text}"
        for d in documents
        if d.filename in BY_FILENAME
    }
    if not text_by_key:
        print("no corpus ingested — run scripts/ingest_corpus.py --reset")
        return 1

    sizes = ", ".join(f"{k} ({len(v):,} chars)" for k, v in sorted(text_by_key.items()))
    print(f"corpus: {sizes}\n")

    broken: list[str] = []
    misplaced: list[str] = []
    for question in QUESTIONS:
        if question.expect is Expect.DECLINE:
            continue
        expected = question.must_include_all or question.must_include
        conj = "ALL" if question.must_include_all else "ANY"
        hits: dict[str, list[str]] = {}
        for needle in expected:
            where = [k for k, text in text_by_key.items() if present(needle, text.lower())]
            hits[needle] = where

        absent = [n for n, where in hits.items() if not where]
        # A conjunctive question needs every part somewhere; a disjunctive one
        # needs at least one.
        if question.derived:
            # The answer is computed from retrieved values, so it is *expected*
            # to be absent. Nothing here can be checked automatically.
            dead, outside = [], []
        else:
            if question.must_include_all:
                dead = absent
            else:
                dead = absent if len(absent) == len(expected) else []
            outside = [
                (n, where)
                for n, where in hits.items()
                if where and question.docs and not (set(where) & set(question.docs))
            ]

        if question.derived:
            status = "derived"
        elif dead:
            status = "BROKEN"
        elif outside:
            status = "elsewhere"
        else:
            status = "ok"
        detail = "  ".join(
            f"{n}={'/'.join(where) if where else 'ABSENT'}" for n, where in hits.items()
        )
        print(f"{question.id:<5} {conj:<3} {status:<9} {detail}")
        for needle in dead:
            broken.append(f"{question.id}:{needle}")
            print(f"      no document contains {needle!r}")
        for needle, where in outside:
            misplaced.append(f"{question.id}:{needle}")
            print(f"      {needle!r} is in {where}, question declares {list(question.docs)}")
            for window in _windows(needle, text_by_key[where[0]]):
                print(f"        … {window} …")

    print()
    for question in QUESTIONS:
        if question.expect is not Expect.DECLINE:
            continue
        print(f"{question.id:<5} decline   {question.note}")

    print("\n" + "=" * 60)
    if broken:
        print(f"{len(broken)} expected fact(s) absent from the corpus: {broken}")
        print("These questions cannot be passed. Fix the question or the parser —")
        print("do not tune retrieval against them.")
    else:
        print("every expected fact is present in normalized_text")
    if misplaced:
        print(f"{len(misplaced)} fact(s) found outside the declared document: {misplaced}")

    await engine.dispose()
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
