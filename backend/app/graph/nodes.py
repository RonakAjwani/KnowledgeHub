"""The six pipeline nodes plus three terminals.

Node names match the SSE contract exactly — ``route | rewrite | retrieve |
rerank | grade | generate`` — because the client keys its progress UI on them.
Renaming one here silently breaks the frontend.

**Every node that can fail has a named direction of failure**, and they are not
all the same direction:

* ``route`` and ``rewrite`` **fail open** — a dead classifier must never cause a
  refusal, and a dead rewriter must never lose the turn. Both degrade to the raw
  query and record why.
* ``retrieve`` **cannot degrade as a whole** — retrieval is the product, so total
  failure is a 503. A *partial* failure is different: if the raw formulation
  succeeded and the rewritten one did not, the turn is answered from raw and
  recorded as degraded. Recall drops; the answer still happens.
* ``verify`` **fails to unknown**, never to false (I2), and runs off the request
  path entirely.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from statistics import mean

from app.config import Settings, get_settings
from app.graph import prompts
from app.graph.state import Grade, QueryState, Route
from app.ingest.embed import Embedder, get_embedder
from app.llm.client import LLMClient, LLMError, Message, get_llm_client
from app.models.schemas import (
    Chunk,
    Degradation,
    DegradationReason,
    DegradationStage,
    RetrievedChunk,
)
from app.retrieval.fuse import attach_branch_ranks, fuse_formulations
from app.retrieval.qdrant_store import QdrantStore, ScoredPoint, get_store
from app.retrieval.rerank import Reranker, RerankStatus, get_reranker

logger = logging.getLogger(__name__)

# (user_id, candidates) -> candidates with text filled in from Postgres.
Hydrator = Callable[[str, list[RetrievedChunk]], Awaitable[list[RetrievedChunk]]]


@dataclass
class Deps:
    """Everything the nodes need, injected rather than imported at call time.

    Explicit dependencies are what make the graph testable without a network:
    every node test swaps one of these for a stub.
    """

    llm: LLMClient
    store: QdrantStore
    embedder: Embedder
    reranker: Reranker
    settings: Settings
    # Refills retrieved candidates with their text from the Postgres mirror.
    # Required in practice: the Qdrant payload carries no text, so without this
    # the reranker scores blank documents and the model is handed empty DATA
    # blocks. Optional here only so node tests can pass pre-filled candidates.
    hydrate: Hydrator | None = None

    @classmethod
    def default(cls) -> Deps:
        return cls(
            llm=get_llm_client(),
            store=get_store(),
            embedder=get_embedder(),
            reranker=get_reranker(),
            settings=get_settings(),
        )


def _degrade(
    state: QueryState,
    stage: DegradationStage,
    reason: DegradationReason,
    fallback: str,
    detail: str | None = None,
) -> list[Degradation]:
    """Append a degradation. I1: every fallback leaves a trace."""
    return [
        *state.get("degradations", []),
        Degradation(stage=stage, reason=reason, fallback=fallback, detail=detail),
    ]


# --------------------------------------------------------------- route · G1


async def route_node(state: QueryState, deps: Deps) -> dict:
    """Classify: needs retrieval / answerable from history / out of scope.

    **Tuned loose on purpose.** Over-refusal on a benign question is a far worse
    failure than an unnecessary search — the user cannot tell a refusal from a
    broken product. G2's relevance floor catches what this lets through, so this
    gate biases hard toward ``retrieve``.
    """
    try:
        result = await deps.llm.complete_json(
            [
                Message(role="system", content=prompts.ROUTE_SYSTEM),
                Message(
                    role="user",
                    content=prompts.build_route_messages(
                        state["raw_query"], state.get("recent_turns", [])
                    ),
                ),
            ],
            model=deps.settings.llm_model_route,
            max_tokens=64,
            timeout=deps.settings.timeout_llm_route_s,
        )
        route = Route(result.get("route", Route.RETRIEVE))
        return {"route": route}
    except (LLMError, ValueError) as exc:
        # Fails open. Refusing because a classifier died is unacceptable.
        logger.warning("route failed, assuming retrieve: %s", exc)
        return {
            "route": Route.RETRIEVE,
            "degradations": _degrade(
                state,
                DegradationStage.ROUTE,
                DegradationReason.TIMEOUT
                if isinstance(exc, LLMError)
                else DegradationReason.PARSE_ERROR,
                "assumed retrieve",
                str(exc)[:200],
            ),
        }


# ------------------------------------------------------------------ rewrite


async def rewrite_node(state: QueryState, deps: Deps) -> dict:
    """Resolve coreference — and fire the raw-query retrieval alongside it.

    The parallelism is the whole point. Only ~60% of follow-up turns carry an
    unresolved reference, so on the rest the rewrite adds latency for nothing;
    running the raw retrieval concurrently means the rewrite's round trip is
    hidden behind a query that had to happen anyway.
    """
    raw_query = state["raw_query"]

    async def _rewrite() -> str:
        result = await deps.llm.complete_json(
            [
                Message(role="system", content=prompts.REWRITE_SYSTEM),
                Message(
                    role="user",
                    content=prompts.build_rewrite_messages(
                        raw_query,
                        state.get("recent_turns", []),
                        state.get("entity_ledger", {}),
                    ),
                ),
            ],
            model=deps.settings.llm_model_rewrite,
            max_tokens=256,
            timeout=deps.settings.timeout_llm_rewrite_s,
        )
        return str(result.get("query") or raw_query).strip() or raw_query

    rewritten_task = asyncio.create_task(_rewrite())
    raw_task = asyncio.create_task(_search(state, deps, raw_query))

    raw_candidates, rewrite_result = await asyncio.gather(
        raw_task, rewritten_task, return_exceptions=True
    )

    updates: dict = {}
    degradations = state.get("degradations", [])

    if isinstance(raw_candidates, BaseException):
        # Not fatal here — `retrieve` decides whether the turn can proceed.
        logger.warning("raw retrieval failed: %s", raw_candidates)
        updates["raw_candidates"] = []
    else:
        updates["raw_candidates"] = raw_candidates

    if isinstance(rewrite_result, BaseException):
        logger.warning("rewrite failed, using raw query: %s", rewrite_result)
        updates["effective_query"] = raw_query
        updates["rewritten"] = False
        degradations = _degrade(
            state,
            DegradationStage.REWRITE,
            DegradationReason.TIMEOUT,
            "raw query",
            str(rewrite_result)[:200],
        )
    else:
        updates["effective_query"] = rewrite_result
        updates["rewritten"] = rewrite_result != raw_query

    updates["degradations"] = degradations
    return updates


# ----------------------------------------------------------------- retrieve


async def _search(
    state: QueryState, deps: Deps, query: str
) -> list[RetrievedChunk]:
    """One Qdrant call: prefetch dense + sparse, fused server-side."""
    embedded = await asyncio.to_thread(deps.embedder.embed_query, query)
    settings = deps.settings

    fused, dense, sparse = await asyncio.gather(
        deps.store.hybrid_search(
            embedded,
            user_id=state["user_id"],
            doc_ids=state.get("selected_doc_ids"),
        ),
        deps.store.branch_search(
            embedded,
            user_id=state["user_id"],
            branch="dense",
            doc_ids=state.get("selected_doc_ids"),
        ),
        deps.store.branch_search(
            embedded,
            user_id=state["user_id"],
            branch="sparse",
            doc_ids=state.get("selected_doc_ids"),
        ),
    )

    candidates = [_to_candidate(point, settings) for point in fused]
    return attach_branch_ranks(
        candidates, [p.chunk_id for p in dense], [p.chunk_id for p in sparse]
    )


def _to_candidate(point: ScoredPoint, settings: Settings) -> RetrievedChunk:
    """Build a candidate from a Qdrant hit.

    ``fused_score`` is normalised against the **analytic** ``RRF_MAX`` here, once,
    at the boundary — so every consumer downstream is already on a scale where
    the G2 floor means the same thing on every query (I7). Normalising against
    the observed top score instead is the reference project's bug, and it looks
    identical in a diff.
    """
    payload = point.payload
    chunk = Chunk(
        id=str(payload.get("chunk_id", "")),
        doc_id=str(payload.get("doc_id", "")),
        user_id=str(payload.get("user_id", "")),
        chunk_index=int(payload.get("chunk_index", 0)),
        text="",
        char_start=int(payload.get("char_start", 0)),
        char_end=int(payload.get("char_end", 0)),
        parent_text="",
        parent_char_start=0,
        parent_char_end=0,
        section=payload.get("section"),
        page=payload.get("page"),
    )
    return RetrievedChunk(
        chunk=chunk, fused_score=point.score / settings.rrf_max if settings.rrf_max else 0.0
    )


async def _hydrate(
    state: QueryState, deps: Deps, candidates: list[RetrievedChunk]
) -> list[RetrievedChunk]:
    """Refill candidate text from the Postgres mirror.

    Must happen **before** rerank, not just before generation: Cohere is sent
    ``chunk.text``, and blank documents rerank into a confident-looking ordering
    of nothing.
    """
    if deps.hydrate is None or not candidates:
        return candidates
    return await deps.hydrate(state["user_id"], candidates)


async def retrieve_node(state: QueryState, deps: Deps) -> dict:
    """Combine the two formulations — or skip the second call entirely."""
    raw_candidates = state.get("raw_candidates", [])
    attempt = state.get("attempt", 0)

    # Nothing to resolve, or the rewrite degraded: the raw set is the answer.
    # Skipping here is the design working, not a shortcut — it avoids a Qdrant
    # call on every first turn of every conversation.
    if not state.get("rewritten", False):
        if not raw_candidates and attempt == 0:
            # Total failure. Retrieval is the product; it has no fallback.
            from app.errors import DependencyUnavailable

            raise DependencyUnavailable("qdrant", "Retrieval returned nothing.")
        return {
            "candidates": await _hydrate(state, deps, raw_candidates),
            "attempt": attempt,
        }

    try:
        rewritten_candidates = await _search(state, deps, state["effective_query"])
    except Exception as exc:  # noqa: BLE001
        if raw_candidates:
            # Partial failure is not a 503. Recall is reduced; the turn is not lost.
            logger.warning("rewritten retrieval failed, using raw only: %s", exc)
            return {
                "candidates": await _hydrate(state, deps, raw_candidates),
                "attempt": attempt,
                "degradations": _degrade(
                    state,
                    DegradationStage.RETRIEVE,
                    DegradationReason.UNAVAILABLE,
                    "raw formulation only",
                    str(exc)[:200],
                ),
            }
        raise

    merged = fuse_formulations(
        [raw_candidates, rewritten_candidates],
        k=deps.settings.rrf_k,
        rank_base=deps.settings.rrf_rank_base,
        limit=deps.settings.retrieve_top_k,
    )
    return {"candidates": await _hydrate(state, deps, merged), "attempt": attempt}


# ------------------------------------------------------------------- rerank


async def rerank_node(state: QueryState, deps: Deps) -> dict:
    outcome = await deps.reranker.rerank(
        state.get("effective_query", state["raw_query"]), state.get("candidates", [])
    )
    return {
        "candidates": outcome.candidates,
        "rerank_status": str(outcome.status),
        "rerank_margin": outcome.margin,
        "degradations": [*state.get("degradations", []), *outcome.degradations],
    }


# --------------------------------------------------------------- grade · G2


def relevance_score(candidates: list[RetrievedChunk], rerank_status: str) -> float:
    """``0.6·max + 0.4·mean`` over whichever score source applies.

    Top-weighted rather than a flat mean because a precise lookup is often
    answered by one strongly-relevant chunk while the rest of the top-k are
    tangential; a flat mean would trip the abstention gate on exactly the queries
    the system handles best.

    **Two score sources, therefore two scales.** Conditional reranking makes the
    un-reranked path the designed-for majority rather than a rare fallback, so
    its scale has to be as trustworthy as the reranked one. Neither is
    renormalised per query (I7).
    """
    if not candidates:
        return 0.0

    if rerank_status in (RerankStatus.APPLIED, RerankStatus.CACHED):
        scores = [c.rerank_score for c in candidates if c.rerank_score is not None]
    else:
        # Already divided by the analytic RRF_MAX at the retrieval boundary.
        scores = [c.fused_score for c in candidates]

    if not scores:
        return 0.0
    return max(0.0, min(1.0, 0.6 * max(scores) + 0.4 * mean(scores)))


def applicable_floor(rerank_status: str, settings: Settings) -> float:
    """Cohere relevance and normalised RRF are different distributions.

    One shared floor across both is a bug: a number that means "good enough" on a
    calibrated 0–1 cross-encoder scale means something else entirely on fused
    ranks. ``failed`` degrades the score *source*, never the check itself — which
    closes the reference project's hole, where the gate ran only when rerank
    succeeded and a dead reranker meant no gate at all.
    """
    if rerank_status in (RerankStatus.APPLIED, RerankStatus.CACHED):
        return settings.floor_rerank
    return settings.floor_fused


async def grade_node(state: QueryState, deps: Deps) -> dict:
    """No LLM call. The signal already exists; recomputing it with five serial
    grading calls is the mistake most CRAG implementations make."""
    candidates = state.get("candidates", [])
    status = state.get("rerank_status", str(RerankStatus.FAILED))

    relevance = relevance_score(candidates, status)
    floor = applicable_floor(status, deps.settings)
    attempt = state.get("attempt", 0)

    if relevance >= floor:
        grade = Grade.PASS
    elif attempt == 0:
        grade = Grade.RETRY
    else:
        # I6: hard cap at two attempts, enforced by the counter.
        grade = Grade.ABSTAIN

    return {
        "relevance": relevance,
        "grade": grade,
        "searched": {
            "doc_count": len({c.chunk.doc_id for c in candidates}),
            "candidate_count": len(candidates),
            "top_score": round(relevance, 4),
            "floor": floor,
        },
    }


async def retry_node(state: QueryState, deps: Deps) -> dict:
    """Bump the attempt counter before the corrective retrieval.

    A separate node rather than an increment inside ``retrieve`` so the cap is
    visible in the graph topology rather than buried in a branch.
    """
    return {"attempt": state.get("attempt", 0) + 1, "rewritten": True}


# ------------------------------------------------------------ generate · G3


async def generate_node(state: QueryState, deps: Deps) -> dict:
    """Assemble delimited DATA blocks and produce the answer.

    Non-streaming here; the streaming path used by the SSE endpoint is
    :func:`stream_answer`, which shares the same assembly so the two cannot
    drift apart in how they frame untrusted content.
    """
    messages, chunk_ids = build_generate_messages(state)
    try:
        answer = await deps.llm.complete(
            messages,
            model=deps.settings.llm_model_generate,
            max_tokens=2048,
            timeout=deps.settings.timeout_llm_generate_s,
        )
    except LLMError as exc:
        from app.errors import DependencyUnavailable

        raise DependencyUnavailable("llm", "Answer generation failed.") from exc

    return {"answer": answer, "citations": []}


def build_generate_messages(state: QueryState) -> tuple[list[Message], list[str]]:
    candidates = state.get("candidates", [])[: get_settings().rerank_top_n]
    user_message, chunk_ids = prompts.build_generate_user_message(
        state.get("effective_query", state["raw_query"]),
        candidates,
        recent_turns=state.get("recent_turns", []),
        rolling_summary=state.get("rolling_summary"),
    )
    return [
        Message(role="system", content=prompts.GENERATE_SYSTEM),
        Message(role="user", content=user_message),
    ], chunk_ids


# ----------------------------------------------------------------- terminals


async def history_node(state: QueryState, deps: Deps) -> dict:
    """Answer from conversation state — no retrieval round trip."""
    transcript = "\n".join(
        f"{t['role']}: {t['content']}" for t in state.get("recent_turns", [])
    )
    summary = state.get("rolling_summary") or ""
    try:
        answer = await deps.llm.complete(
            [
                Message(
                    role="system",
                    content=(
                        "Answer using only the conversation so far. If it does not "
                        "contain the answer, say so."
                    ),
                ),
                Message(
                    role="user",
                    content=f"{summary}\n\n{transcript}\n\nUser: {state['raw_query']}",
                ),
            ],
            model=deps.settings.llm_model_generate,
            max_tokens=1024,
            timeout=deps.settings.timeout_llm_generate_s,
        )
    except LLMError:
        answer = "I could not answer from the conversation history just now."
    return {"answer": answer, "citations": []}


async def refuse_node(state: QueryState, deps: Deps) -> dict:
    return {
        "answer": (
            "That is outside what I can help with here — I answer questions about "
            "the documents you have uploaded."
        ),
        "citations": [],
    }


async def abstain_node(state: QueryState, deps: Deps) -> dict:
    """A structured refusal naming what was searched and why nothing qualified.

    Never a guess. Hallucinating here is the worst available demo failure, and
    the honest version is more useful: it tells the user their corpus does not
    cover the question rather than inventing something that sounds like it does.
    """
    searched = state.get("searched", {})
    doc_count = searched.get("doc_count", 0)
    return {
        "answer": (
            f"I could not find anything in your documents that answers this. "
            f"I searched {doc_count} document(s) and the closest matches scored "
            f"below the relevance threshold, so I would be guessing."
        ),
        "citations": [],
        "abstain_reason": "relevance_below_floor",
    }
