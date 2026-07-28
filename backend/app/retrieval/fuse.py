"""Nested RRF across *formulations* — the one place client-side fusion survives.

The project rule is that dense↔sparse fusion happens server-side, because Qdrant
does it in a single call with per-branch weights and doing it in application code
would be reimplementing a solved problem badly. That rule is about fusion *within*
a query.

This module fuses *across* queries — the raw formulation and the rewritten one —
and the server cannot do that at all, because the two were never in the same
request. Scoped precisely:

    Fusion within a query is server-side. Fusion across queries is client-side,
    and only here.

The alternative — waiting for the rewrite, then issuing all four branches as
prefetches in one call — is server-side throughout, but it puts the rewrite back
on the critical path, which is the entire thing the parallelism buys. So the one
surviving client-side merge is the one that is actually load-bearing.

**Same normalisation discipline as everywhere else (I7).** The fused score here is
scaled by an analytic maximum derived from constants, never by the observed top
score of the merged set. Self-normalising would force ``max == 1.0`` on every
query and make the downstream floor meaningless — the exact failure the reference
project shipped.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.models.schemas import RetrievedChunk

logger = logging.getLogger(__name__)


def nested_rrf_max(k: int, rank_base: int, formulations: int = 2) -> float:
    """Analytic ceiling for the cross-formulation merge.

    A chunk ranked first in every formulation scores ``formulations / (k + base)``.
    Same shape as the within-query ``RRF_MAX``, one level up.
    """
    return formulations / (k + rank_base)


def fuse_formulations(
    result_sets: Sequence[Sequence[RetrievedChunk]],
    *,
    k: int,
    rank_base: int,
    limit: int,
) -> list[RetrievedChunk]:
    """Merge result sets from independently executed queries by reciprocal rank.

    Rank-based rather than score-based on purpose: the two result sets come from
    separate Qdrant calls, and their fused scores are only comparable because both
    were produced under the same weights and ``k``. Ranks need no such assumption,
    which is the property that made RRF the right choice for the inner fusion too.

    Chunks carry their **best** rank from either branch, so a chunk found by the
    rewritten query alone is not penalised for being absent from the raw one — it
    simply scores the single contribution.
    """
    non_empty = [rs for rs in result_sets if rs]
    if not non_empty:
        return []
    if len(non_empty) == 1:
        # Nothing to merge. Returning the input untouched matters: re-scoring a
        # single set through the nested formula would rescale it against a
        # ceiling built for two, and every downstream threshold would shift.
        return list(non_empty[0][:limit])

    scores: dict[str, float] = {}
    best: dict[str, RetrievedChunk] = {}

    for result_set in non_empty:
        for rank, candidate in enumerate(result_set):
            chunk_id = candidate.chunk.id
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + rank_base)

            # Keep the richer record: whichever occurrence carries branch ranks,
            # since `is_decisive` needs them and only one formulation may have
            # surfaced the chunk in both branches.
            existing = best.get(chunk_id)
            if existing is None or _rank_signal(candidate) > _rank_signal(existing):
                best[chunk_id] = candidate

    ceiling = nested_rrf_max(k, rank_base, len(non_empty))
    merged = [
        best[chunk_id].model_copy(update={"fused_score": score / ceiling})
        for chunk_id, score in scores.items()
    ]
    merged.sort(key=lambda c: c.fused_score, reverse=True)
    return merged[:limit]


def _rank_signal(candidate: RetrievedChunk) -> int:
    """How much branch information this occurrence carries (0, 1 or 2)."""
    return (candidate.dense_rank is not None) + (candidate.sparse_rank is not None)


def attach_branch_ranks(
    fused: list[RetrievedChunk],
    dense_order: Sequence[str],
    sparse_order: Sequence[str],
) -> list[RetrievedChunk]:
    """Record each chunk's position within each branch.

    The conditional-rerank skip needs to know whether the fused winner is top-3 in
    *both* branches — cross-branch agreement, not margin, is the signal that a
    cross-encoder would not overturn the result. A single fused call does not
    report per-branch positions, so they are attached from the branch queries the
    ablation harness runs anyway.
    """
    dense_at = {cid: i for i, cid in enumerate(dense_order)}
    sparse_at = {cid: i for i, cid in enumerate(sparse_order)}
    return [
        candidate.model_copy(
            update={
                "dense_rank": dense_at.get(candidate.chunk.id),
                "sparse_rank": sparse_at.get(candidate.chunk.id),
            }
        )
        for candidate in fused
    ]
