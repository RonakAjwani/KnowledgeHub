"""``QueryState`` - the one object every node reads and writes.

A ``TypedDict`` rather than a Pydantic model because LangGraph merges the partial
dict a node returns into the running state; a model would be revalidated and
rebuilt on every node transition for no benefit.

The field worth pausing on is ``attempt``. Invariant I6 caps retrieval at two
attempts - initial plus one corrective retry - and the cap is enforced **by this
counter**, not by asking the model nicely in a prompt. A bounded CRAG loop whose
bound lives in an instruction is not bounded.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, TypedDict

from app.models.schemas import Citation, Degradation, RetrievedChunk


class Route(StrEnum):
    RETRIEVE = "retrieve"
    HISTORY = "history"
    REFUSE = "refuse"


class Grade(StrEnum):
    PASS = "pass"
    RETRY = "retry"
    ABSTAIN = "abstain"


class Turn(TypedDict):
    role: str
    content: str


class QueryState(TypedDict, total=False):
    # -- input
    user_id: str
    conversation_id: str
    raw_query: str
    selected_doc_ids: list[str] | None

    # -- conversation memory (three stores, deliberately separate; user
    #    preferences are NOT here and never reach a retrieval query)
    recent_turns: list[Turn]
    rolling_summary: str | None
    entity_ledger: dict[str, str]

    # -- routing
    route: Route

    # -- rewriting
    #
    # `effective_queries` is the list actually retrieved with - one entry for an
    # ordinary question, several when the user asked distinct things in one
    # message ("Who is Ronak? What are his qualifications?"). Retrieving once for
    # a multi-intent message embeds a blend of every intent and tends to surface
    # passages answering only the loudest one.
    #
    # `effective_query` stays a single string for the things that need exactly
    # one - Cohere's rerank input and the rerank cache key.
    effective_queries: list[str]
    effective_query: str
    rewritten: bool
    # The raw-query result set, fetched in parallel with the rewrite call so the
    # rewrite's latency is hidden rather than serial.
    raw_candidates: list[RetrievedChunk]

    # -- retrieval
    candidates: list[RetrievedChunk]
    attempt: int
    rerank_status: str
    rerank_margin: float | None

    # -- grading
    relevance: float
    grade: Grade

    # -- output
    answer: str
    citations: list[Citation]
    degradations: list[Degradation]
    abstain_reason: str | None
    searched: dict[str, Any]


def initial_state(
    *,
    user_id: str,
    conversation_id: str,
    raw_query: str,
    selected_doc_ids: list[str] | None = None,
    recent_turns: list[Turn] | None = None,
    rolling_summary: str | None = None,
    entity_ledger: dict[str, str] | None = None,
) -> QueryState:
    return QueryState(
        user_id=user_id,
        conversation_id=conversation_id,
        raw_query=raw_query,
        selected_doc_ids=selected_doc_ids,
        recent_turns=recent_turns or [],
        rolling_summary=rolling_summary,
        entity_ledger=entity_ledger or {},
        effective_queries=[raw_query],
        effective_query=raw_query,
        rewritten=False,
        raw_candidates=[],
        candidates=[],
        attempt=0,
        rerank_status="failed",
        rerank_margin=None,
        relevance=0.0,
        answer="",
        citations=[],
        degradations=[],
        abstain_reason=None,
        searched={},
    )
