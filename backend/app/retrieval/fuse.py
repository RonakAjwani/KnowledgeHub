"""Nested RRF across *formulations* - the one place client-side fusion survives.

The project rule is that dense↔sparse fusion happens server-side, because Qdrant
does it in a single call with per-branch weights and doing it in application code
would be reimplementing a solved problem badly. That rule is about fusion *within*
a query.

This module fuses *across* queries - the raw formulation and the rewritten one -
and the server cannot do that at all, because the two were never in the same
request. Scoped precisely:

    Fusion within a query is server-side. Fusion across queries is client-side,
    and only here.

The alternative - waiting for the rewrite, then issuing all four branches as
prefetches in one call - is server-side throughout, but it puts the rewrite back
on the critical path, which is the entire thing the parallelism buys. So the one
surviving client-side merge is the one that is actually load-bearing.

**Same normalisation discipline as everywhere else (I7).** The fused score here is
scaled by an analytic maximum derived from constants, never by the observed top
score of the merged set. Self-normalising would force ``max == 1.0`` on every
query and make the downstream floor meaningless - the exact failure the reference
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
    rewritten query alone is not penalised for being absent from the raw one - it
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

    # Rank orders; score measures. Keeping the two apart is the whole trick here,
    # and collapsing them was a real bug (finding 7.1, fixed 2026-08-02).
    #
    # Reciprocal rank is the right basis for *ordering* - two separate Qdrant
    # calls produce comparable ranks and incomparable raw scores, which is this
    # function's docstring and it still holds. It is the wrong basis for a
    # *magnitude*, because a rank says nothing about how well anything matched:
    # the top chunk is rank 0 by definition, so a rank-derived score hands it 1.0
    # whatever it is. Writing that number into `fused_score` fed it straight to
    # `relevance_score`, which is what G2's abstention floor is compared against.
    #
    # MEASURED before the fix, varying only how well the chunk actually matched:
    #
    #     match quality                     at boundary   after fusion   relevance
    #     excellent (top of one branch)          0.5000         1.0000      0.9967
    #     terrible  (rank 39, last of top_k)     0.3030         1.0000      0.9967
    #
    # It reached production traffic through the CRAG retry, which is what makes
    # it worse than it first looks. `retry_node` sets `rewritten = True`, so the
    # corrective attempt always lands here - and the corrective attempt is, by
    # construction, the path every question the corpus cannot answer takes.
    # Measured end to end on an unanswerable question: attempt 0 scored 0.4543
    # against a floor of 0.5 and correctly asked for a retry; the retry re-ran
    # the identical query, fused the identical result set with itself, and scored
    # 0.9085 - exactly double, since two identical contributions over a ceiling
    # built for two leaves `k / (k + rank)`. It then answered from chunks about
    # railway signalling. The `abstain` terminal was unreachable.
    #
    # That is `config.py`'s own documented I7 failure mode ("forces
    # max(score) == 1.0 ... makes the abstention gate meaningless") reached by a
    # different route: I7 was defended against observed-max normalisation, and
    # rank-only recomputation was not.
    #
    # So: sort by reciprocal rank, and carry the magnitude across from the
    # incoming scores. Every score arriving here was already divided by the
    # analytic `RRF_MAX` at the retrieval boundary, so scores from different
    # formulations are on one scale and directly comparable - no renormalisation
    # happens here, and none may (I7). The **best** occurrence wins, matching the
    # best-rank rule above: a chunk one formulation found strongly is not marked
    # down because the other missed it.
    scores: dict[str, float] = {}
    magnitude: dict[str, float] = {}
    best: dict[str, RetrievedChunk] = {}

    for result_set in non_empty:
        for rank, candidate in enumerate(result_set):
            chunk_id = candidate.chunk.id
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + rank_base)
            magnitude[chunk_id] = max(
                magnitude.get(chunk_id, 0.0), candidate.fused_score
            )

            # Keep the richer record: whichever occurrence carries branch ranks,
            # since `is_decisive` needs them and only one formulation may have
            # surfaced the chunk in both branches. Deliberately independent of
            # the magnitude above - the occurrence with the ranks is not
            # necessarily the occurrence with the better score.
            existing = best.get(chunk_id)
            if existing is None or _rank_signal(candidate) > _rank_signal(existing):
                best[chunk_id] = candidate

    # Normalised for interpretability only; dividing by a positive constant
    # cannot reorder anything.
    ceiling = nested_rrf_max(k, rank_base, len(non_empty))
    order = sorted(scores, key=lambda cid: scores[cid] / ceiling, reverse=True)
    return [
        best[chunk_id].model_copy(update={"fused_score": magnitude[chunk_id]})
        for chunk_id in order[:limit]
    ]


def interleave_intents(
    result_sets: Sequence[Sequence[RetrievedChunk]],
    *,
    limit: int,
    tail: Sequence[RetrievedChunk] = (),
) -> list[RetrievedChunk]:
    """Merge result sets that answer **different questions**, by allocation.

    RRF is the wrong operator here, and quietly so. It rewards a chunk for
    appearing in many result sets, which is exactly right when the sets are
    rephrasings of one intent - agreement is evidence. When the sets are distinct
    sub-questions, agreement means almost nothing and *disagreement is expected*:
    the passage answering the third sub-question appears in the third result set
    and nowhere else, so RRF scores it once against rivals scoring two and three
    times, and it lands below the context cut.

    Observed exactly that way: asked three things at once, the pipeline
    decomposed correctly, retrieved for each, then filled all twelve context
    slots from the two documents the first two sub-questions shared and gave the
    third zero. The answer said the corpus contained no information about a
    document that was sitting in it.

    So each intent takes turns instead. Round-robin over the sets by rank means a
    sub-question's best passage is only ever beaten by another sub-question's
    best passage, and every intent reaches the model as long as the budget has
    slots for it.

    ``tail`` is appended after the interleave, deduplicated - normally the raw
    whole-message results, which stay available without being allowed to crowd
    out any single intent.

    Scores are carried through untouched. Each was already normalised against the
    analytic ceiling by its own search; rescoring the union here would renormalise
    across queries and shift every downstream threshold (I7).
    """
    non_empty = [rs for rs in result_sets if rs]
    if not non_empty:
        return list(tail[:limit])
    if len(non_empty) == 1:
        merged = list(non_empty[0])
    else:
        merged = []
        seen: set[str] = set()
        for rank in range(max(len(rs) for rs in non_empty)):
            for result_set in non_empty:
                if rank >= len(result_set):
                    continue
                candidate = result_set[rank]
                if candidate.chunk.id in seen:
                    continue
                seen.add(candidate.chunk.id)
                merged.append(candidate)
            if len(merged) >= limit:
                break

    present = {c.chunk.id for c in merged}
    merged.extend(c for c in tail if c.chunk.id not in present)
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
    *both* branches - cross-branch agreement, not margin, is the signal that a
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
