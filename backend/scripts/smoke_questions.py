"""Run a handful of questions end to end and print enough to judge them by eye.

Deliberately not the eval harness. The harness answers "what fraction passed",
which is the wrong question before anyone has confirmed the system works at all -
and a pass rate computed over a broken pipeline is a precise number about
nothing. This prints the whole turn: how the query was decomposed, which
documents came back, the full answer, and what each citation resolves to.

Paced between cases, because free tiers meter tokens per minute and this is the
only place that fires six multi-chunk questions in a row. Override with
``SMOKE_PACE_S=0`` when the provider has headroom.

    PYTHONPATH=. poetry run python scripts/smoke_questions.py
"""

from __future__ import annotations

import asyncio
import functools
import os
import sys
import time

# Corpus text carries typographic dashes and accented names, and the Windows
# console default is cp1252. Printing a citation should never be what kills a
# diagnostic run.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.db import models as db
from app.graph.build import build_graph
from app.graph.nodes import Deps, context_budget
from app.graph.state import initial_state
from app.ingest.embed import Embedder
from app.llm.client import LLMClient
from app.retrieval.hydrate import hydrate_candidates
from app.retrieval.qdrant_store import QdrantStore
from app.retrieval.rerank import Reranker

EVAL_USER = "eval-user"

# Free tiers meter tokens per *minute*, and six multi-chunk questions fired
# back to back exhaust that budget in seconds - a throughput limit of the
# harness, not of the product, since a person asks one thing at a time.
# Pausing between cases keeps the diagnostic measuring the pipeline rather
# than the rate limiter.
PACE_S = float(os.environ.get("SMOKE_PACE_S", "20"))

# Six behaviours, one question each. Chosen to cover the things that would make
# the system worth measuring, not to sample the bank evenly.
CASES: list[tuple[str, str, list[str]]] = [
    (
        "single-document lookup",
        "What was India's 10-Year G-Sec yield?",
        [],
    ),
    (
        "the document whose spaces were broken until today",
        "Describe the three tools exposed by the MathModDB MCP server.",
        [],
    ),
    (
        "several questions in one message, spanning documents",
        "What is the 360 ONE Focused Fund's Net AUM? When was the 2025 CEO "
        "Interim Award forfeited? And what does langchain.md actually cover?",
        [],
    ),
    (
        "cross-document synthesis",
        "Which of these documents discuss knowledge graphs, and what does each "
        "one use them for?",
        [],
    ),
    (
        "not in the corpus - must decline",
        "According to the corpus, what are LangChain's core abstractions such "
        "as Chains, Agents, and Tools?",
        [],
    ),
    (
        "follow-up carrying a pronoun",
        "What is the 360 ONE Focused Fund's Net AUM?",
        ["What is its expense ratio?"],
    ),
]


async def main() -> int:
    settings = Settings(qdrant_collection="eval_chunks", max_escalated_pages=16)
    store = QdrantStore(settings)
    embedder = Embedder(settings)
    llm = LLMClient(settings)
    reranker = Reranker(settings)
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async with maker() as session:
        rows = await session.execute(
            select(db.Document).where(db.Document.user_id == EVAL_USER)
        )
        names = {d.id: d.filename for d in rows.scalars().all()}

        deps = Deps(
            llm=llm,
            store=store,
            embedder=embedder,
            reranker=reranker,
            settings=settings,
            hydrate=functools.partial(hydrate_candidates, session),
        )
        graph = build_graph(deps)

        for index, (label, question, follow_ups) in enumerate(CASES):
            if index:
                await asyncio.sleep(PACE_S)
            print("\n" + "=" * 100)
            print(f"{label.upper()}")
            print(f"Q: {question}")

            started = time.time()
            state = initial_state(
                user_id=EVAL_USER,
                conversation_id=f"smoke-{abs(hash(question)) % 10000}",
                raw_query=question,
            )
            try:
                result = await graph.ainvoke(state)
            except Exception as exc:  # noqa: BLE001
                print(f"   FAILED {type(exc).__name__}: {str(exc)[:200]}")
                continue

            _report(result, names, settings, time.time() - started)

            for follow_up in follow_ups:
                # A follow-up lands seconds after its parent turn, which is the
                # tightest burst in the whole script.
                await asyncio.sleep(PACE_S)
                print(f"\nQ (follow-up): {follow_up}")
                started = time.time()
                follow_state = initial_state(
                    user_id=EVAL_USER,
                    conversation_id=f"smoke-{abs(hash(question)) % 10000}",
                    raw_query=follow_up,
                    recent_turns=[
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": result.get("answer", "")},
                    ],
                )
                try:
                    result = await graph.ainvoke(follow_state)
                except Exception as exc:  # noqa: BLE001
                    print(f"   FAILED {type(exc).__name__}: {str(exc)[:200]}")
                    continue
                _report(result, names, settings, time.time() - started)

    await llm.aclose()
    await reranker.aclose()
    await store.aclose()
    await engine.dispose()
    return 0


def _report(result: dict, names: dict[str, str], settings: Settings, elapsed: float) -> None:
    queries = result.get("effective_queries") or []
    if len(queries) > 1 or (queries and queries[0] != result.get("raw_query")):
        print("   decomposed into:")
        for q in queries:
            print(f"     - {q}")

    used = result.get("candidates", [])[: context_budget(result, settings)]
    print(
        f"   grade={result.get('grade')}  relevance={result.get('relevance', 0):.3f}  "
        f"rerank={result.get('rerank_status')}  context={len(used)} chunks  "
        f"{elapsed:.1f}s"
    )

    for degradation in result.get("degradations", []):
        print(f"   ! degraded [{degradation.stage}/{degradation.reason}] {degradation.detail}")

    print("\n   ANSWER:")
    for line in (result.get("answer", "") or "(empty)").split("\n"):
        print(f"     {line}")

    if used:
        print("\n   CONTEXT GIVEN TO THE MODEL:")
        for i, candidate in enumerate(used, 1):
            chunk = candidate.chunk
            name = names.get(chunk.doc_id, chunk.doc_id[:8])
            snippet = " ".join(chunk.text.split())[:110]
            print(f"     [{i}] {name[:34]:<36} p{chunk.page or '?':<4} {snippet}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
