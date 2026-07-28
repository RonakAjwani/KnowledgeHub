"""Conditional Cohere rerank, with a cache and a fallback chain that never fails.

Reranking is the single biggest quality jump in the stack — larger than the
hybrid gain itself. It is also the most constrained resource in it: the trial key
allows **1,000 calls per month at 10 rpm**, so reranking unconditionally would cap
the entire deployment at roughly a thousand lifetime queries. A local
cross-encoder is not an option either — `bge-reranker-v2-m3` is ~568 M params and
the host has 512 MB.

Three responses, all of which have to hold at once:

1. **Skip when the fused result is already decisive.** The signal is *cross-branch
   agreement*, not margin alone: when dense and sparse independently rank the same
   chunk first, a cross-encoder is unlikely to overturn it, so the call buys
   nothing. Margin without agreement is a one-sided branch shouting confidently.
2. **Cache on ``(query, doc_set)``.** Re-asking the same question over the same
   documents — which is exactly what an eval sweep does — costs one call, not N.
3. **Fall back, never fail.** Cohere → cache → fused order. Every step below the
   first records a ``Degradation``, so a degraded ordering is never
   indistinguishable from a reranked one (I1).

**402 and 429 are different failures and must be handled differently.** Cohere
distinguishes them, so this client does too:

* **429** — per-minute limit. Transient; the next query may succeed. Fall back for
  this query only.
* **402** — monthly quota gone. Terminal, and every subsequent call is guaranteed
  to return it too. Trips a circuit breaker, so the remaining queries of the month
  do not each spend the full 2 s timeout budget rediscovering a known fact.

Neither code carries a documented ``Retry-After``, so the backoff is ours to
choose rather than obey.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import httpx

from app.config import Settings, get_settings
from app.llm.limiter import CircuitBreaker, RateLimiter
from app.models.schemas import (
    Degradation,
    DegradationReason,
    DegradationStage,
    RetrievedChunk,
)

logger = logging.getLogger(__name__)

COHERE_RERANK_URL = "https://api.cohere.com/v2/rerank"
COHERE_RPM = 10


class RerankStatus(StrEnum):
    APPLIED = "applied"
    SKIPPED_DECISIVE = "skipped_decisive"
    CACHED = "cached"
    FAILED = "failed"


@dataclass
class RerankOutcome:
    candidates: list[RetrievedChunk]
    status: RerankStatus
    degradations: list[Degradation]
    margin: float | None = None


def _cache_key(query: str, candidates: Sequence[RetrievedChunk]) -> str:
    """Keyed on the query *and* the exact candidate set.

    Including the candidate ids rather than just the selected document ids
    matters: the same question over the same documents can produce a different
    candidate set after a re-ingest, and serving the old ordering for a new set
    would silently rank chunks that are no longer there.
    """
    doc_set = "|".join(sorted(c.chunk.id for c in candidates))
    digest = hashlib.sha256(f"{query}\x00{doc_set}".encode()).hexdigest()
    return digest[:32]


def is_decisive(
    candidates: Sequence[RetrievedChunk], ratio_threshold: float
) -> tuple[bool, float | None]:
    """Whether fusion already settled it, plus the observed margin.

    Two conditions, both required:

    * the top fused score beats the runner-up by ``DECISIVE_RATIO``; and
    * the winner is top-3 in **both** branches.

    The second is the load-bearing one. A chunk can top the fused list on a large
    margin while being invisible to one branch entirely — that is one-sided
    evidence, and precisely the case where a cross-encoder earns its call.
    """
    if len(candidates) < 2:
        # Nothing to compare against; a single candidate is not evidence of
        # decisiveness, so let the reranker (or the floor) judge it.
        return False, None

    top, runner_up = candidates[0], candidates[1]
    if runner_up.fused_score <= 0:
        return False, None

    margin = top.fused_score / runner_up.fused_score
    if margin < ratio_threshold:
        return False, margin

    top_in_both = (
        top.dense_rank is not None
        and top.dense_rank < 3
        and top.sparse_rank is not None
        and top.sparse_rank < 3
    )
    return top_in_both, margin


class Reranker:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._cache: dict[str, list[str]] = {}
        self._limiter = RateLimiter(COHERE_RPM, name="cohere")
        self.breaker = CircuitBreaker("cohere")

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def rerank(
        self, query: str, candidates: list[RetrievedChunk]
    ) -> RerankOutcome:
        """Reorder candidates, or explain in a Degradation why they were not."""
        degradations: list[Degradation] = []

        if not candidates:
            return RerankOutcome([], RerankStatus.FAILED, degradations)

        decisive, margin = is_decisive(candidates, self.settings.decisive_ratio)
        if decisive:
            # Not a degradation: this is the design working, and `grade` knows to
            # score it against FLOOR_FUSED rather than FLOOR_RERANK.
            return RerankOutcome(
                candidates, RerankStatus.SKIPPED_DECISIVE, degradations, margin
            )

        key = _cache_key(query, candidates)
        if key in self._cache:
            return RerankOutcome(
                _reorder(candidates, self._cache[key]),
                RerankStatus.CACHED,
                degradations,
                margin,
            )

        if self.breaker.is_tripped:
            # Already known dead; no call, no timeout, no repeated degradation.
            return RerankOutcome(candidates, RerankStatus.FAILED, degradations, margin)

        if not self.settings.cohere_api_key:
            degradations.append(
                Degradation(
                    stage=DegradationStage.RERANK,
                    reason=DegradationReason.UNAVAILABLE,
                    fallback="fused order",
                    detail="No Cohere API key configured.",
                )
            )
            return RerankOutcome(candidates, RerankStatus.FAILED, degradations, margin)

        try:
            order = await self._call_cohere(query, candidates)
        except _RerankFailure as failure:
            degradations.append(failure.degradation)
            return RerankOutcome(candidates, RerankStatus.FAILED, degradations, margin)

        self._cache[key] = order
        return RerankOutcome(
            _reorder(candidates, order), RerankStatus.APPLIED, degradations, margin
        )

    async def _call_cohere(
        self, query: str, candidates: list[RetrievedChunk]
    ) -> list[str]:
        await self._limiter.acquire()

        try:
            response = await self.client.post(
                COHERE_RERANK_URL,
                headers={
                    "Authorization": f"Bearer {self.settings.cohere_api_key}",
                    "content-type": "application/json",
                },
                json={
                    "model": self.settings.cohere_rerank_model,
                    "query": query,
                    "documents": [c.chunk.text for c in candidates],
                    "top_n": min(self.settings.rerank_top_n, len(candidates)),
                },
                timeout=self.settings.timeout_cohere_s,
            )
        except httpx.TimeoutException as exc:
            raise _RerankFailure(
                DegradationReason.TIMEOUT,
                f"Cohere timed out after {self.settings.timeout_cohere_s}s",
            ) from exc
        except httpx.HTTPError as exc:
            raise _RerankFailure(
                DegradationReason.UNAVAILABLE, f"Cohere unreachable: {exc}"
            ) from exc

        if response.status_code == 402:
            # Terminal. Waiting never fixes a spent monthly quota.
            first = self.breaker.trip("Cohere returned 402 (quota exhausted)")
            raise _RerankFailure(
                DegradationReason.QUOTA_EXHAUSTED,
                "Cohere monthly quota exhausted; reranking disabled for the rest "
                "of this deployment."
                if first
                else "Cohere quota exhausted.",
            )

        if response.status_code == 429:
            # Transient. Do NOT trip the breaker — the next query may succeed.
            raise _RerankFailure(
                DegradationReason.RATE_LIMITED,
                "Cohere per-minute rate limit reached.",
            )

        if response.status_code >= 400:
            raise _RerankFailure(
                DegradationReason.UNAVAILABLE,
                f"Cohere returned {response.status_code}.",
            )

        results = response.json().get("results", [])
        # Cohere returns positions into the documents array we sent, so the
        # ordering is mapped back through our own candidate list rather than
        # trusting any id echoed by the upstream.
        return [
            candidates[item["index"]].chunk.id
            for item in results
            if 0 <= item.get("index", -1) < len(candidates)
        ]


class _RerankFailure(Exception):
    def __init__(self, reason: DegradationReason, detail: str) -> None:
        super().__init__(detail)
        self.degradation = Degradation(
            stage=DegradationStage.RERANK,
            reason=reason,
            fallback="fused order",
            detail=detail,
        )


def _reorder(
    candidates: list[RetrievedChunk], order: list[str]
) -> list[RetrievedChunk]:
    """Apply a reranked ordering, attaching scores.

    ``rerank_score`` descends from 1.0 by position rather than echoing Cohere's
    raw relevance, because ``top_n`` truncates the response: chunks Cohere did
    not return have no score at all, and inventing 0.0 for them would read as
    "judged irrelevant" instead of "not judged" (I2).
    """
    by_id = {c.chunk.id: c for c in candidates}
    ranked: list[RetrievedChunk] = []
    for position, chunk_id in enumerate(order):
        candidate = by_id.pop(chunk_id, None)
        if candidate is None:
            continue
        ranked.append(
            candidate.model_copy(
                update={"rerank_score": 1.0 - (position / max(len(order), 1))}
            )
        )
    # Anything Cohere did not rank keeps its fused position and a null score.
    ranked.extend(by_id[c.chunk.id] for c in candidates if c.chunk.id in by_id)
    return ranked


_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        _reranker = Reranker()
    return _reranker


async def close_reranker() -> None:
    global _reranker
    if _reranker is not None:
        await _reranker.aclose()
    _reranker = None
