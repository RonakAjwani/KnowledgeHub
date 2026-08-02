"""Rerank, cross-formulation fusion, and the RRF_MAX arithmetic.

The reranker tests are the ones that matter most here: its whole job is to be
*conditional*, and every branch it can take - skipped, cached, rate-limited,
quota-exhausted - has to be distinguishable from a healthy call afterwards, or
invariant I1 is decorative.
"""

from __future__ import annotations

import httpx
import pytest
from qdrant_client.http.exceptions import (
    ResponseHandlingException,
    UnexpectedResponse,
)

from app.config import Settings
from app.graph.nodes import relevance_score
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
from app.retrieval.qdrant_store import QdrantStore, _scope
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
    """One result is not evidence of confidence - let the floor judge it."""
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


def _cohere_ok(scored: list[tuple[int, float]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": i, "relevance_score": s} for i, s in scored
                ]
            },
        )

    return httpx.MockTransport(handler)


async def test_applied_rerank_reorders_and_scores() -> None:
    settings = Settings(cohere_api_key="k", decisive_ratio=99.0, rerank_top_n=3)
    reranker = Reranker(
        settings,
        client=httpx.AsyncClient(
            transport=_cohere_ok([(2, 0.64), (0, 0.02), (1, 0.01)])
        ),
    )
    candidates = [candidate(0, 1.0, 0, 5), candidate(1, 0.9), candidate(2, 0.8)]

    outcome = await reranker.rerank("q", candidates)

    assert outcome.status is RerankStatus.APPLIED
    assert [c.chunk.id for c in outcome.candidates] == ["c002", "c000", "c001"]
    assert outcome.degradations == []
    # Cohere's own judgement, verbatim.
    assert [c.rerank_score for c in outcome.candidates] == [0.64, 0.02, 0.01]


async def test_rerank_score_reflects_relevance_not_position() -> None:
    """Regression: the score must depend on what Cohere thought, not on how many
    results came back.

    An earlier ``_reorder`` assigned ``1.0 - position/len(order)``, so the blend
    G2 gates on was a fixed function of ``top_n`` - exactly 0.840 for every query
    at ``top_n = 5``, whether the documents were relevant or not. ``FLOOR_RERANK``
    was then a comparison against a constant and could never fire. Two result sets
    of identical length but opposite quality must not produce the same score.
    """
    settings = Settings(cohere_api_key="k", decisive_ratio=99.0, rerank_top_n=3)
    candidates = [candidate(0, 1.0, 0, 5), candidate(1, 0.9), candidate(2, 0.8)]

    async def scores_for(scored: list[tuple[int, float]]) -> list[float | None]:
        reranker = Reranker(
            settings, client=httpx.AsyncClient(transport=_cohere_ok(scored))
        )
        outcome = await reranker.rerank("q", candidates)
        return [c.rerank_score for c in outcome.candidates]

    strong = await scores_for([(0, 0.95), (1, 0.80), (2, 0.71)])
    weak = await scores_for([(0, 0.04), (1, 0.02), (2, 0.01)])

    assert strong != weak, "same ordering, opposite relevance, identical scores"
    assert relevance_score(
        [candidate(i, 0.5) for i in range(3)], str(RerankStatus.APPLIED)
    ) == 0.0, "unscored candidates must not be read as zeros (I2)"


async def test_second_identical_query_is_served_from_cache() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.71},
                    {"index": 1, "relevance_score": 0.12},
                ]
            },
        )

    settings = Settings(cohere_api_key="k", decisive_ratio=99.0)
    reranker = Reranker(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    candidates = [candidate(0, 1.0), candidate(1, 0.9)]

    first = await reranker.rerank("same question", candidates)
    second = await reranker.rerank("same question", candidates)

    assert first.status is RerankStatus.APPLIED
    assert second.status is RerankStatus.CACHED
    assert calls["n"] == 1, "1000 calls/month is the budget an eval sweep would burn"
    # A cache hit has to carry the scores too, not just the ordering - otherwise
    # the gate sees nothing to score and the cached path silently differs.
    assert [c.rerank_score for c in second.candidates] == [0.71, 0.12]


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
    """The fallback chain ends at fused order - a query is never lost."""
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


def test_fusion_carries_match_quality_rather_than_rank() -> None:
    """Regression, finding 7.1.

    Being top of the list says nothing about matching well. When the fused score
    was recomputed from rank, a barely-relevant chunk that happened to lead a
    weak result set scored 1.0 and the abstention floor could never fire.
    """
    weak = [[candidate(1, fused=0.30)], [candidate(1, fused=0.28)]]
    out = fuse_formulations(weak, k=60, rank_base=0, limit=10)
    assert out[0].fused_score == pytest.approx(0.30), "best of the two, not 1.0"


def test_self_fusion_leaves_the_score_untouched() -> None:
    """Regression, finding 7.1 - the path the CRAG retry actually takes.

    ``retry_node`` sets ``rewritten=True`` without changing the query, so the
    corrective attempt fuses a result set with an identical copy of itself. That
    must not move the score: the retry found nothing new, and the retry is the
    path every unanswerable question takes on its way to ``abstain``.
    """
    one = [candidate(1, fused=0.50), candidate(2, fused=0.49), candidate(3, fused=0.30)]
    out = fuse_formulations([one, list(one)], k=60, rank_base=0, limit=10)
    assert [c.fused_score for c in out] == pytest.approx([0.50, 0.49, 0.30])


def test_fusion_keeps_the_better_score_when_one_formulation_ranks_it_low() -> None:
    """Best-of, matching the best-rank rule: a chunk one formulation found
    strongly is not marked down because the other barely surfaced it."""
    strong = [candidate(1, fused=0.90)]
    weak = [candidate(2, fused=0.60), candidate(1, fused=0.05)]
    out = fuse_formulations([strong, weak], k=60, rank_base=0, limit=10)
    by_id = {c.chunk.id: c.fused_score for c in out}
    assert by_id["c001"] == pytest.approx(0.90)


def test_fusion_still_orders_by_reciprocal_rank_not_by_score() -> None:
    """Ordering and magnitude are separate on purpose. Agreement across
    formulations decides position even when a one-sided hit scores higher."""
    raw = [candidate(1, fused=0.99), candidate(2, fused=0.40)]
    rewritten = [candidate(3, fused=0.95), candidate(2, fused=0.40)]

    out = fuse_formulations([raw, rewritten], k=60, rank_base=0, limit=10)

    assert out[0].chunk.id == "c002", "found by both, so it leads on rank"
    assert out[0].fused_score == pytest.approx(0.40), "and keeps its own magnitude"


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
    """Absent from a branch is None, not a large rank - one-sided evidence has
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


# ------------------------------------------- transport retry on Qdrant calls


async def test_a_transport_blip_is_retried_once_and_recovers() -> None:
    """MEASURED against the managed cluster: DNS resolution failed on 1 of 12
    consecutive attempts, and a single miss surfaced as a 503 on
    DELETE /documents/{id} that succeeded immediately when repeated."""
    store = QdrantStore(Settings(), client=object())
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ResponseHandlingException(Exception("getaddrinfo failed"))
        return "recovered"

    assert await store._retrying("search", flaky) == "recovered"
    assert attempts == 2


async def test_a_dead_cluster_gives_up_rather_than_looping() -> None:
    """A retry loop with no ceiling is an unbounded latency budget, and a
    cluster that is genuinely down must surface as a named dependency failure
    rather than a hang (the reasoning behind I6, applied here)."""
    store = QdrantStore(Settings(), client=object())
    attempts = 0

    async def dead() -> str:
        nonlocal attempts
        attempts += 1
        raise ResponseHandlingException(Exception("down"))

    with pytest.raises(ResponseHandlingException):
        await store._retrying("search", dead)
    assert attempts == 2, "one retry, then give up"


async def test_an_http_error_is_not_retried() -> None:
    """Replaying a request the server actively rejected fails twice and hides
    the reason behind a delay."""
    store = QdrantStore(Settings(), client=object())
    attempts = 0

    async def rejected() -> str:
        nonlocal attempts
        attempts += 1
        raise UnexpectedResponse(404, "Not Found", b"", {})

    with pytest.raises(UnexpectedResponse):
        await store._retrying("count", rejected)
    assert attempts == 1


# ------------------------------------------------------------ scoping (I3 / workspaces)


def _keys(f) -> list[str]:
    return [c.key for c in f.must]


def test_an_empty_document_selection_is_not_an_unrestricted_search() -> None:
    """MEASURED, and the sharpest scoping bug found in the review: `_scope`
    tested `if doc_ids:`, which is falsy for both `None` and `[]`, so the two
    collapsed. `None` means "no document restriction"; `[]` means "restricted to
    no documents" and can only return nothing.

    `chat.py` builds that list from the conversation's workspace, so an **empty
    workspace produced `[]`** - and a chat there retrieved a document belonging
    to a different workspace, contradicting the promise written three lines
    above that query. Verified against a live Qdrant before and after: 2 hits,
    then 0.

    I3 was never at risk - `user_id` is unconditional - so this was workspace
    isolation, not tenant isolation. That distinction is why the bug survived: a
    tenant leak would have been caught by the isolation tests that already
    exist."""
    unrestricted = _scope("u1", None)
    empty = _scope("u1", [])
    listed = _scope("u1", ["doc-a"])

    assert _keys(unrestricted) == ["user_id"], "None must not narrow by document"
    assert _keys(empty) == ["user_id", "doc_id"], "[] must narrow, and to nothing"
    assert _keys(listed) == ["user_id", "doc_id"]
    assert empty != unrestricted


def test_every_scope_carries_user_id_whatever_the_selection(
) -> None:
    """I3 by construction: there is no argument to `_scope` that produces a
    filter without the tenant predicate."""
    for doc_ids in (None, [], ["a"], ["a", "b"]):
        assert _keys(_scope("u1", doc_ids))[0] == "user_id"


async def test_every_turn_after_the_breaker_trips_still_reports_degradation() -> None:
    """I1 is about each *answer*, not each breaker.

    MEASURED: the degradation was recorded only on the turn that tripped the
    breaker. Turns 2, 3 and 4 carried none, so every answer for the rest of the
    deployment was served on fused order while looking exactly like a reranked
    one - I1's failure condition stated literally.

    One record per answer is the correct rate, not spam. The breaker's job is to
    stop the *calls*, and it still does: exactly one HTTP request is made."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(402, json={"message": "quota"})

    settings = Settings(cohere_api_key="k", decisive_ratio=99.0)
    reranker = Reranker(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )

    for turn in range(4):
        outcome = await reranker.rerank(
            f"question {turn}", [candidate(0, 1.0), candidate(1, 0.9)]
        )
        assert outcome.status is RerankStatus.FAILED
        assert len(outcome.degradations) == 1, (
            f"turn {turn} was degraded silently - no record for the user to see"
        )
        assert outcome.degradations[0].reason is DegradationReason.QUOTA_EXHAUSTED

    assert calls["n"] == 1, "the breaker must still prevent every call after the first"


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("empty results", {"results": []}),
        ("renamed key", {"data": [{"index": 0, "relevance_score": 0.9}]}),
        ("every index out of range", {"results": [{"index": 99, "relevance_score": 0.9}]}),
        ("scores missing", {"results": [{"index": 0}]}),
    ],
)
async def test_a_200_with_nothing_usable_is_a_failure_not_a_rerank(
    label: str, body: dict
) -> None:
    """The quiet one. These all returned `status=APPLIED` with every
    `rerank_score` None - so `grade` read scores from the rerank source, found an
    empty list, scored the turn **0.0** and abstained. A good retrieval became "I
    could not find that", with no degradation anywhere to explain it, and an
    upstream response-shape change would have done that to every query at once.

    Now it falls back to fused order and says so."""
    settings = Settings(cohere_api_key="k", decisive_ratio=99.0)
    reranker = Reranker(
        settings,
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))
        ),
    )
    candidates = [candidate(0, 1.0), candidate(1, 0.9)]
    outcome = await reranker.rerank("q", candidates)

    assert outcome.status is RerankStatus.FAILED, label
    assert outcome.degradations, f"{label} degraded with no record"
    assert outcome.degradations[0].fallback == "fused order"
    # The fused ordering survives intact rather than being replaced by nothing.
    assert [c.chunk.id for c in outcome.candidates] == [c.chunk.id for c in candidates]


async def test_an_unusable_response_is_not_cached() -> None:
    """One malformed response used to poison that (query, doc-set) permanently:
    the empty order was written to the cache, so every later identical query was
    served from it as `CACHED` with no scores at all."""
    settings = Settings(cohere_api_key="k", decisive_ratio=99.0)
    bodies = [{"results": []}, {"results": [{"index": 0, "relevance_score": 0.8}]}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=bodies.pop(0) if bodies else {"results": []})

    reranker = Reranker(
        settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    candidates = [candidate(0, 1.0), candidate(1, 0.9)]

    first = await reranker.rerank("q", candidates)
    assert first.status is RerankStatus.FAILED
    assert reranker._cache == {}, "an unusable ordering must not be remembered"

    # The identical query must reach Cohere again rather than replay the failure.
    second = await reranker.rerank("q", candidates)
    assert second.status is RerankStatus.APPLIED
    assert second.candidates[0].rerank_score == 0.8


def test_the_rerank_cache_is_bounded() -> None:
    """It was unbounded - 50 distinct queries left 50 entries and nothing evicted.
    Small entries make it a slow leak rather than a fast one, but unbounded inside
    a 512 MB ceiling is still a leak, and the sibling prompt cache in
    `app/llm/cache.py` is already bounded at the same size.

    Exercised through `_remember` rather than through 500+ `rerank()` calls: the
    reranker is rate limited to 10 rpm, so driving eviction end to end would take
    the better part of an hour to assert one dict length."""
    from app.retrieval.rerank import _CACHE_MAX_ENTRIES

    reranker = Reranker(Settings(cohere_api_key="k"))
    for i in range(_CACHE_MAX_ENTRIES + 25):
        reranker._remember(f"key-{i}", [(f"c{i}", 0.9)])

    assert len(reranker._cache) == _CACHE_MAX_ENTRIES
    assert "key-0" not in reranker._cache, "the oldest entry must be the one evicted"
    assert f"key-{_CACHE_MAX_ENTRIES + 24}" in reranker._cache, "the newest must survive"
