"""Does skipping the reranker cost anything?

`probe_decisive_margin.py` answers "how often does the skip fire" (42% of
queries at DECISIVE_RATIO=1.02). It does not answer the question that actually
justifies the design: on those queries, would Cohere have *changed* the answer?

A skip is free only if the ordering the reranker would have produced agrees
with the fused ordering on the passages that reach the model. If it disagrees,
the conditional skip is trading answer quality for API budget on 42% of
traffic, and the threshold is wrong regardless of how much it saves.

So this forces a Cohere call on exactly the queries that *would* have skipped
and compares:

  * top-1 agreement — does the reranker keep the same passage first?
  * top-k overlap   — how much of the context set is the same either way?

Costs one Cohere call per decisive query (~22 of 53 here), against a
1,000/month trial budget. Zero LLM calls.

    PYTHONPATH=. poetry run python scripts/probe_skip_cost.py
"""

from __future__ import annotations

import asyncio
import functools
import statistics
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.graph.nodes import Deps, _search
from app.graph.state import initial_state
from app.ingest.embed import Embedder
from app.retrieval.hydrate import hydrate_candidates
from app.retrieval.qdrant_store import QdrantStore
from app.retrieval.rerank import Reranker, RerankStatus, is_decisive
from evals.questions import QUESTIONS

EVAL_USER = "eval-user"


async def main() -> int:
    settings = Settings(qdrant_collection="eval_chunks")
    store = QdrantStore(settings)
    embedder = Embedder(settings)
    engine = create_async_engine(settings.database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    # decisive_ratio=99 so this reranker never takes the skip itself — the whole
    # point is to see what the skipped queries were missing.
    forced = Reranker(settings.model_copy(update={"decisive_ratio": 99.0}))

    top_n = settings.rerank_top_n
    # (margin, top-1 agreed, top-k overlap) per eligible query.
    samples: list[tuple[float, bool, float]] = []
    total_queries = 0

    async with maker() as session:
        deps = Deps(
            llm=None,
            store=store,
            embedder=embedder,
            reranker=forced,
            settings=settings,
            hydrate=functools.partial(hydrate_candidates, session),
        )

        for question in QUESTIONS:
            state = dict(
                initial_state(
                    user_id=EVAL_USER,
                    conversation_id=f"skip-{question.id}",
                    raw_query=question.text,
                )
            )
            total_queries += 1
            candidates = await _search(state, deps, question.text)
            if len(candidates) < 2:
                continue

            # Measured for *every* query with cross-branch agreement, not just
            # those over the configured ratio — the goal is the agreement-vs-
            # margin curve, which is what lets the threshold be chosen on
            # whether the skip is safe rather than on how much it saves.
            agreed, margin = is_decisive(candidates, 1.0)
            if not agreed or margin is None:
                continue  # no cross-branch agreement: never eligible to skip

            # Cohere is sent chunk.text, which the Qdrant payload does not carry.
            candidates = await hydrate_candidates(session, EVAL_USER, candidates)
            outcome = await forced.rerank(question.text, candidates)
            if outcome.status is not RerankStatus.APPLIED:
                continue  # rate limited or unavailable — not a measurement

            fused_ids = [c.chunk.id for c in candidates[:top_n]]
            rerank_ids = [c.chunk.id for c in outcome.candidates[:top_n]]
            samples.append((
                margin,
                fused_ids[0] == rerank_ids[0],
                len(set(fused_ids) & set(rerank_ids)) / len(fused_ids),
            ))

    await forced.aclose()
    await store.aclose()
    await engine.dispose()

    if not samples:
        print("nothing measured — is the corpus ingested, and is COHERE_API_KEY set?")
        return 1

    print(f"{len(samples)} queries with cross-branch agreement, of "
          f"{total_queries} total\n")

    # For each candidate threshold: how many queries would skip, and — the
    # number that actually matters — how often the reranker would have kept the
    # same top passage on exactly those skipped queries. A skip is only free if
    # that agreement is high; otherwise the threshold buys API budget with
    # answer quality, which is the wrong trade at any saving.
    overlap_header = f"top-{top_n} overlap"
    print(f"  {'ratio':>6} {'skips':>13} {'top-1 agree':>14} {overlap_header:>15}")
    for ratio in (1.00, 1.01, 1.02, 1.03, 1.05, 1.10, 1.20, 1.50):
        skipped = [s for s in samples if s[0] >= ratio]
        if not skipped:
            print(f"  {ratio:>6.2f} {0:>6}/{total_queries:<6} {'—':>14} {'—':>15}")
            continue
        agree = sum(1 for _, ok, _ in skipped if ok)
        overlap = statistics.mean(o for _, _, o in skipped)
        print(f"  {ratio:>6.2f} {len(skipped):>6}/{total_queries:<6} "
              f"{agree:>3}/{len(skipped):<3} {100 * agree / len(skipped):>4.0f}% "
              f"{100 * overlap:>13.0f}%")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
