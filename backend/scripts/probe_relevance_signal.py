"""Measure what the un-reranked relevance gate is actually gating on.

`grade` (G2) scores a candidate set with `0.6·max + 0.4·mean` and compares it to
a floor. Which numbers go into that blend depends on whether rerank ran:

* rerank applied  -> Cohere's calibrated `relevance_score`
* rerank skipped  -> `fused_score`, i.e. normalised RRF

The second is suspect by construction. RRF scores a chunk by its *rank*
(`w/(k+rank)`), not by how similar it is to the query - so the top hit of a
query with no good answer scores almost exactly the same as the top hit of a
query with a perfect one. If that is true, `FLOOR_FUSED` is comparing a
near-constant against a threshold and can never fire, which is the same class of
failure I7 exists to prevent.

The dense branch is already queried on every request (`_search` runs
`branch_search(branch="dense")` alongside the fused call and keeps only the
ordering, to attach `dense_rank`). Its cosine similarity is therefore available
at zero extra cost and is a real magnitude, not a rank artefact. This script
compares the two as *separators* between questions the corpus can answer and
questions it cannot.

Zero LLM calls - Qdrant and the local embedder only.

    PYTHONPATH=. poetry run python scripts/probe_relevance_signal.py
"""

from __future__ import annotations

import asyncio
import statistics
import sys
from dataclasses import dataclass

from app.config import Settings
from app.ingest.embed import Embedder
from app.retrieval.qdrant_store import DENSE_VECTOR_NAME, QdrantStore
from evals.questions import QUESTIONS, Expect

EVAL_USER = "eval-user"


def blend(scores: list[float]) -> float:
    """The same `0.6·max + 0.4·mean` G2 uses, so this measures the real gate."""
    if not scores:
        return 0.0
    return max(0.0, min(1.0, 0.6 * max(scores) + 0.4 * statistics.mean(scores)))


@dataclass
class Row:
    expect: str
    fused_all: float
    fused_top: float
    dense_all: float
    dense_top: float


def summarise(name: str, good: list[float], bad: list[float]) -> None:
    """Print the separation between the two populations, and the best threshold.

    `good` is answerable questions, `bad` is questions the corpus cannot answer.
    A usable signal keeps them apart; a useless one interleaves them, and the
    "best" threshold on an interleaved pair is a coin flip dressed as a number.
    """
    if not good or not bad:
        return
    g, b = sorted(good), sorted(bad)
    print(f"\n{name}")
    print(f"  answerable      min={g[0]:.3f} median={statistics.median(g):.3f} max={g[-1]:.3f}")
    print(f"  should-decline  min={b[0]:.3f} median={statistics.median(b):.3f} max={b[-1]:.3f}")

    # Separation = how far apart the two medians are, in units of the pooled
    # spread. Near zero means the populations sit on top of each other.
    spread = statistics.pstdev(g + b) or 1e-9
    sep = (statistics.median(g) - statistics.median(b)) / spread
    print(f"  separation      {sep:+.2f} (median gap / pooled sd)")

    best, best_correct = None, -1
    for step in range(0, 101):
        floor = step / 100
        correct = sum(v >= floor for v in good) + sum(v < floor for v in bad)
        if correct > best_correct:
            best, best_correct = floor, correct
    total = len(good) + len(bad)
    print(f"  best floor      {best:.2f} -> {best_correct}/{total} correct "
          f"({100 * best_correct / total:.0f}%)")

    # Max accuracy is the wrong thing to optimise here and the sweep is what
    # shows why: over-refusal is the worse failure (a user cannot tell a refusal
    # from a broken product), and the generator's own grounding prompt already
    # declines unanswerable questions. So the floor wants to sit *below* the
    # answerable population as a backstop against catastrophic retrieval, not at
    # whichever point happens to win by one question on 53 samples.
    print(f"  {'floor':>7} {'kept':>10} {'gated':>10}")
    for step in range(50, 96, 5):
        floor = step / 100
        print(f"  {floor:>7.2f} {sum(v >= floor for v in good):>5}/{len(good):<4} "
              f"{sum(v < floor for v in bad):>5}/{len(bad):<4}")


async def main() -> int:
    settings = Settings(qdrant_collection="eval_chunks")
    store = QdrantStore(settings)
    embedder = Embedder(settings)

    rows: list[Row] = []
    for question in QUESTIONS:
        embedded = await asyncio.to_thread(embedder.embed_query, question.text)

        fused, dense = await asyncio.gather(
            store.hybrid_search(embedded, user_id=EVAL_USER),
            store.branch_search(embedded, user_id=EVAL_USER, branch=DENSE_VECTOR_NAME),
        )
        if not fused or not dense:
            continue

        fused_scores = [
            p.score / settings.rrf_max if settings.rrf_max else 0.0 for p in fused
        ]
        dense_scores = [p.score for p in dense]

        rows.append(
            Row(
                expect="decline" if question.expect is Expect.DECLINE else "answer",
                fused_all=blend(fused_scores),
                fused_top=blend(fused_scores[: settings.rerank_top_n]),
                dense_all=blend(dense_scores),
                # Restricted to what actually reaches the model. The reranked
                # path blends over `rerank_top_n` scores because Cohere only
                # returns that many; blending the un-reranked path over all 40
                # candidates is not the same measurement, and the tail dominates
                # the mean.
                dense_top=blend(dense_scores[: settings.rerank_top_n]),
            )
        )

    await store.aclose()

    if not rows:
        print("no rows - is the eval corpus ingested? (scripts/ingest_corpus.py)")
        return 1

    print(f"{len(rows)} questions "
          f"({sum(r.expect == 'answer' for r in rows)} answerable, "
          f"{sum(r.expect == 'decline' for r in rows)} should-decline)")

    for name, get in (
        ("fused RRF over all candidates  [what FLOOR_FUSED gates on today]",
         lambda r: r.fused_all),
        (f"fused RRF over top {settings.rerank_top_n}  [like-for-like with the reranked path]",
         lambda r: r.fused_top),
        ("dense cosine over all candidates", lambda r: r.dense_all),
        (f"dense cosine over top {settings.rerank_top_n}  [proposed]",
         lambda r: r.dense_top),
    ):
        summarise(
            name,
            [get(r) for r in rows if r.expect == "answer"],
            [get(r) for r in rows if r.expect == "decline"],
        )

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
