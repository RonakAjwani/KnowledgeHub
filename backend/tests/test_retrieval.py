"""Rerank, cross-formulation fusion, and the RRF_MAX arithmetic.

The reranker tests are the ones that matter most here: its whole job is to be
*conditional*, and every branch it can take — skipped, cached, rate-limited,
quota-exhausted — has to be distinguishable from a healthy call afterwards, or
invariant I1 is decorative.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings
from app.models.schemas import (
    Chunk,
    DegradationReason,
    DegradationStage,
    RetrievedChunk,
)
from app.retrieval.fuse import (
    attach_branch_ranks,
    fuse_formulations,
    nested_rrf_max,
)
from app.retrieval.rerank import Reranker, RerankStatus, is_decisive


def chunk(idx: int, text: str = "text") -> Chunk:
    return Chunk(
        id=f"c{idx:03d}",
        doc_id="d1",
        user_id="u1",
        chunk_index=idx,
        text=f"{text} {idx}",
        char_start=idx * 10,
        char_end=idx * 10 + 5,
        parent_text=f"{text} {idx}",
        parent_char_start=idx * 10,
        parent_char_end=idx * 10 + 5,
    )


def candidate(
    idx: int,
    fused: float = 1.0,
    dense: int | None = None,
    sparse: int | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=chunk(idx), fused_score=fused, dense_rank=dense, sparse_rank=sparse
    )


# ------------------------------------------------------------ decisive skip


def test_decisive_requires_agreement_not_just_margin() -> None:
    """Cross-branch agreement is the signal. A huge margin on one-sided evidence
    is exactly when a cross-encoder earns its call."""
    one_sided = [
        candidate(1, fused=1.0, dense=0, sparse=None),  # invisible to sparse
        candidate(2, fused=0.1, dense=1, sparse=0),
    ]
    decisive, margin = is_decisive(one_sided, ratio_threshold=1.5)
    assert margin == pytest.approx(10.0), "margin alone is enormous"
    assert decisive is False, "but one-sided evidence must still be reranked"


def test_decisive_when_both_branches_agree_and_margin_holds() -> None:
    agreed = [
        candidate(1, fused=1.0, dense=0, sparse=0),
        candidate(2, fused=0.4, dense=1, sparse=2),
    ]
    decisive, margin = is_decisive(agreed, ratio_threshold=1.5)
    assert decisive is True
    assert margin == pytest.approx(2.5)


def test_narrow_margin_is_never_decisive() -> None:
    close = [
        candidate(1, fused=1.0, dense=0, sparse=0),
        candidate(2, fused=0.9, dense=1, sparse=1),
    ]
    assert is_decisive(close, ratio_threshold=1.5)[0] is False


def test_single_candidate_is_not_decisive() -> None:
    """One result is not evidence of confidence — let the floor judge it."""
    assert is_decisive([candidate(1, fused=1.0, dense=0, sparse=0)], 1.5)[0] is False


async def test_skip_records_no_degradation() -> None:
    """Skipping deliberately is the design working, not a fallback."""
    reranker = Reranker(Settings(decisive_ratio=1.5, cohere_api_key="k"))
    outcome = await reranker.rerank(
        "q", [candidate(1, 1.0, 0, 0), candidate(2, 0.3, 1, 1)]
    )
    assert outcome.status is RerankStatus.SKIPPED_DECISIVE
    assert outcome.degradations == []


# --------------------------------------------------------------- happy path


def _cohere_ok(order: list[int]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"results": [{"index": i, "relevance_score": 0.9} for i in order]}
        )

    return httpx.MockTransport(handler)


async def test_applied_rerank_reorders_and_scores() -> None:
    settings = Settings(cohere_api_key="k", decisive_ratio=99.0, rerank_top_n=3)
    reranker = Reranker(
        settings, client=httpx.AsyncClient(transport=_cohere_ok([2, 0, 1]))
    )
    candidates = [candidate(0, 1.0, 0, 5), candidate(1, 0.9), candidate(2, 0.8)]

    outcome = await reranker.rerank("q", candidates)

    assert outcome.status is RerankStatus.APPLIED
    assert [c.chunk.id for c in outcome.candidates] == ["c002", "c000", "c001"]
    assert outcome.candidates[0].rerank_score == pytest.approx(1.0)
    assert outcome.candidates[1].rerank_score < outcome.candidates[0].rerank_score
    assert outcome.degradations == []


async def test_second_identical_query_is_served_from_cache() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"results": [{"index": 0}, {"index": 1}]})

    settings = Settings(cohere_api_key="k", decisive_ratio=99.0)
    reranker = Reranker(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    candidates = [candidate(0, 1.0), candidate(1, 0.9)]

    first = await reranker.rerank("same question", candidates)
    second = await reranker.rerank("same question", candidates)

    assert first.status is RerankStatus.APPLIED
    assert second.status is RerankStatus.CACHED
    assert calls["n"] == 1, "1000 calls/month is the budget an eval sweep would burn"


# ------------------------------------------------------------ failure modes


async def test_402_trips_the_breaker_and_stops_calling() -> None:
    """402 means the monthly quota is gone; every later call would also return it."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(402, json={"message": "quota"})

    settings = Settings(cohere_api_key="k", decisive_ratio=99.0)
    reranker = Reranker(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    first = await reranker.rerank("q1", [candidate(0, 1.0), candidate(1, 0.9)])
    assert first.status is RerankStatus.FAILED
    assert reranker.breaker.is_tripped
    assert first.degradations[0].reason is DegradationReason.QUOTA_EXHAUSTED
    assert first.degradations[0].fallback == "fused order"

    second = await reranker.rerank("q2", [candidate(2, 1.0), candidate(3, 0.9)])
    assert second.status is RerankStatus.FAILED
    assert calls["n"] == 1, "breaker must prevent the second call entirely"


async def test_429_does_not_trip_the_breaker() -> None:
    """Transient. Waiting fixes it, so the next query must still try."""
    settings = Settings(cohere_api_key="k", decisive_ratio=99.0)
    reranker = Reranker(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429))),
    )
    outcome = await reranker.rerank("q", [candidate(0, 1.0), candidate(1, 0.9)])

    assert outcome.status is RerankStatus.FAILED
    assert outcome.degradations[0].reason is DegradationReason.RATE_LIMITED
    assert reranker.breaker.is_tripped is False


async def test_timeout_falls_back_without_tripping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    settings = Settings(cohere_api_key="k", decisive_ratio=99.0)
    reranker = Reranker(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    outcome = await reranker.rerank("q", [candidate(0, 1.0), candidate(1, 0.9)])

    assert outcome.degradations[0].reason is DegradationReason.TIMEOUT
    assert reranker.breaker.is_tripped is False


async def test_every_failure_preserves_fused_order() -> None:
    """The fallback chain ends at fused order — a query is never lost."""
    settings = Settings(cohere_api_key="k", decisive_ratio=99.0)
    reranker = Reranker(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    candidates = [candidate(0, 1.0), candidate(1, 0.9), candidate(2, 0.8)]
    outcome = await reranker.rerank("q", candidates)

    assert [c.chunk.id for c in outcome.candidates] == ["c000", "c001", "c002"]
    assert all(c.rerank_score is None for c in outcome.candidates), (
        "no rerank score is not the same as a score of zero (I2)"
    )


async def test_missing_key_degrades_rather_than_crashing() -> None:
    reranker = Reranker(Settings(cohere_api_key="", decisive_ratio=99.0))
    outcome = await reranker.rerank("q", [candidate(0, 1.0), candidate(1, 0.9)])
    assert outcome.status is RerankStatus.FAILED
    assert outcome.degradations[0].stage is DegradationStage.RERANK


# ------------------------------------------------------- nested fusion


def test_single_result_set_passes_through_untouched() -> None:
    """Re-scoring one set against a two-formulation ceiling would shift every
    downstream threshold."""
    only = [candidate(1, fused=0.5), candidate(2, fused=0.3)]
    out = fuse_formulations([only, []], k=60, rank_base=0, limit=10)
    assert [c.fused_score for c in out] == [0.5, 0.3]


def test_agreement_across_formulations_outranks_a_single_top_hit() -> None:
    """A chunk both formulations found beats one only the raw query surfaced."""
    raw = [candidate(1, 1.0), candidate(2, 0.9)]
    rewritten = [candidate(2, 1.0), candidate(3, 0.9)]

    out = fuse_formulations([raw, rewritten], k=60, rank_base=0, limit=10)

    assert out[0].chunk.id == "c002", "found by both formulations"
    assert {c.chunk.id for c in out} == {"c001", "c002", "c003"}


def test_nested_fusion_normalises_against_the_analytic_ceiling() -> None:
    """I7 one level up: a chunk first in both formulations scores exactly 1.0,
    because the ceiling comes from constants, not from the observed maximum."""
    both_first = [[candidate(1, 1.0)], [candidate(1, 1.0)]]
    out = fuse_formulations(both_first, k=60, rank_base=0, limit=10)
    assert out[0].fused_score == pytest.approx(1.0)


def test_nested_ceiling_matches_the_formula() -> None:
    assert nested_rrf_max(60, 0, 2) == pytest.approx(2 / 60)
    assert nested_rrf_max(60, 1, 2) == pytest.approx(2 / 61)


def test_fusion_keeps_the_occurrence_carrying_branch_ranks() -> None:
    """is_decisive needs branch ranks; only one formulation may have them."""
    without = [candidate(1, 1.0)]
    with_ranks = [candidate(1, 0.5, dense=0, sparse=1)]
    out = fuse_formulations([without, with_ranks], k=60, rank_base=0, limit=5)
    assert out[0].dense_rank == 0 and out[0].sparse_rank == 1


def test_attach_branch_ranks_marks_absence_as_none() -> None:
    """Absent from a branch is None, not a large rank — one-sided evidence has
    to stay visible to the decisive-skip check."""
    fused = [candidate(1), candidate(2)]
    out = attach_branch_ranks(fused, dense_order=["c001"], sparse_order=["c001", "c002"])

    assert out[0].dense_rank == 0 and out[0].sparse_rank == 0
    assert out[1].dense_rank is None, "absent from dense entirely"
    assert out[1].sparse_rank == 1


# --------------------------------------------------- RRF_MAX, settled value


def test_rrf_max_uses_the_measured_rank_base() -> None:
    """Settled empirically against Qdrant 1.18: it ranks from 0, so the
    denominator is k. See scripts/probe_rrf_rank_base.py."""
    assert Settings().rrf_rank_base == 0
    assert Settings(w_dense=1.0, w_sparse=1.0, rrf_k=60).rrf_max == pytest.approx(2 / 60)
