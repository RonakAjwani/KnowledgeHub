"""Measure the fused-margin distribution that DECISIVE_RATIO is thresholded on.

``is_decisive`` skips the Cohere call when the top candidate beats the runner-up
by ``DECISIVE_RATIO`` *and* is top-3 in both branches. The placeholder was 1.5,
carried over from a scale that does not exist here: RRF scores are
``w/(k + rank)``, so a top candidate ranked 0 in both branches scores
``2/60 = 0.03333`` against a runner-up ranked 1 in both at ``2/61 = 0.03279`` —
a ratio of 1.017. A 1.5 threshold therefore only fires when the runner-up is
missing from a branch entirely, and on the eval corpus it fired zero times in 53
questions, meaning every query paid a Cohere call against a 1,000/month budget.

This prints the observed margins so the constant is set from the distribution
rather than from an unexamined default. Qdrant only — no Cohere, no LLM.

    PYTHONPATH=. poetry run python scripts/probe_decisive_margin.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys

from app.config import Settings
from app.graph.nodes import Deps, _search
from app.graph.state import initial_state
from app.ingest.embed import Embedder
from app.retrieval.qdrant_store import QdrantStore
from evals.questions import QUESTIONS

EVAL_USER = "eval-user"


async def main() -> int:
    settings = Settings(qdrant_collection="eval_chunks")
    store = QdrantStore(settings)
    embedder = Embedder(settings)
    deps = Deps(
        llm=None, store=store, embedder=embedder, reranker=None, settings=settings
    )

    margins: list[float] = []
    agreed_margins: list[float] = []

    for question in QUESTIONS:
        state = dict(
            initial_state(
                user_id=EVAL_USER,
                conversation_id=f"probe-{question.id}",
                raw_query=question.text,
            )
        )
        candidates = await _search(state, deps, question.text)
        if len(candidates) < 2:
            continue
        top, runner_up = candidates[0], candidates[1]
        if runner_up.fused_score <= 0:
            continue

        margin = top.fused_score / runner_up.fused_score
        margins.append(margin)
        top_in_both = (
            top.dense_rank is not None
            and top.dense_rank < 3
            and top.sparse_rank is not None
            and top.sparse_rank < 3
        )
        if top_in_both:
            agreed_margins.append(margin)

    await store.aclose()

    if not margins:
        print("no margins collected")
        return 1

    ordered = sorted(margins)
    print(f"observed margins over {len(margins)} queries")
    print(f"  min={ordered[0]:.4f}  median={statistics.median(ordered):.4f}  "
          f"max={ordered[-1]:.4f}")
    print(f"  cross-branch agreement (top-3 in both): {len(agreed_margins)}/{len(margins)}")
    if agreed_margins:
        agreed = sorted(agreed_margins)
        print(f"  agreed margins  min={agreed[0]:.4f}  "
              f"median={statistics.median(agreed):.4f}  max={agreed[-1]:.4f}")

    print("\n  ratio   would skip (of queries with agreement)")
    for ratio in (1.00, 1.01, 1.02, 1.03, 1.05, 1.10, 1.20, 1.50, 2.00):
        skipped = sum(m >= ratio for m in agreed_margins)
        share = 100 * skipped / len(margins)
        print(
            f"  {ratio:>5.2f}   {skipped:>3}/{len(margins)}  "
            f"({share:.0f}% of Cohere calls saved)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
