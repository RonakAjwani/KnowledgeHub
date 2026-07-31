"""Drives one chat turn: graph -> SSE events -> persistence.

The node functions are called directly rather than through ``graph.ainvoke``.
LangGraph would run the same sequence, but this loop needs to emit a
``pipeline.stage`` frame around each node *as it happens* and to stream tokens
out of ``generate`` - and the value of LangGraph here was always checkpointing
and node-level observability, never autonomy. Driving the nodes explicitly keeps
the event contract exact and the control flow readable; the compiled graph
remains the tested description of that same topology.

Ordering is the contract (§8) and is enforced structurally:

* ``turn.start`` is emitted before anything can fail;
* exactly one of ``answer.complete`` / ``abstain`` / ``error`` closes the stream;
* ``answer.delta`` only appears after ``pipeline.stage{generate, started}``;
* ``pipeline.stage`` repeats on retry with a different ``attempt``, which is why
  the client keys on ``(node, attempt)`` rather than ``node``;
* ``verification.complete`` may follow ``answer.complete`` - or never arrive.

**Degradations are emitted as they appear, not collected at the end.** A user
watching a slow turn should learn that the reranker fell back at the moment it
happens, not after the answer lands (I1).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sse import EventStream
from app.config import get_settings
from app.db import models as db
from app.errors import AppError, DependencyUnavailable, current_request_id
from app.graph import nodes
from app.graph.nodes import Deps
from app.graph.state import Grade, QueryState, Route
from app.graph.verify import Verifier
from app.llm.client import LLMError, LLMRateLimited
from app.models.schemas import (
    Citation,
    Degradation,
    DegradationReason,
    DegradationStage,
)
from app.retrieval.hydrate import load_filenames

logger = logging.getLogger(__name__)


class TurnRunner:
    def __init__(self, session: AsyncSession, deps: Deps) -> None:
        self.session = session
        self.deps = deps
        self.stream = EventStream()
        self._emitted_degradations = 0

    async def run(self, state: QueryState) -> AsyncIterator[str]:
        message_id = str(uuid.uuid4())
        turn_id = str(uuid.uuid4())

        # Always first, and before anything that can fail: the client keys its
        # optimistic UI to these ids.
        #
        # `conversation_id` is what makes multi-turn memory reachable. A client
        # that starts a fresh conversation has no id to send, so the server mints
        # one - and without it on the wire the client can never learn what it
        # was, meaning every turn silently starts a new conversation and
        # follow-up questions lose their history.
        yield self.stream.frame(
            "turn.start",
            {
                "turn_id": turn_id,
                "message_id": message_id,
                "conversation_id": state["conversation_id"],
            },
        )

        try:
            async for frame in self._pipeline(state, message_id):
                yield frame
        except AppError as exc:
            # A stream that fails must say so. Closing silently leaves the client
            # unable to distinguish a crash from a very slow answer.
            yield self.stream.frame(
                "error",
                {
                    "code": exc.code,
                    "message": exc.message,
                    "request_id": current_request_id(),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("unhandled error in turn %s", turn_id)
            yield self.stream.frame(
                "error",
                {
                    "code": "dependency_unavailable",
                    "message": "The turn failed unexpectedly.",
                    "request_id": current_request_id(),
                },
            )
            del exc

    # ------------------------------------------------------------- internals

    def _drain_degradations(self, state: QueryState) -> list[str]:
        """Emit any degradation recorded since the last check."""
        recorded: list[Degradation] = state.get("degradations", [])
        frames = [
            self.stream.frame("degradation", d.model_dump())
            for d in recorded[self._emitted_degradations :]
        ]
        self._emitted_degradations = len(recorded)
        return frames

    async def _stage(
        self, node: str, state: QueryState, fn, detail_fn=None
    ) -> AsyncIterator[str]:
        """Run one node, bracketed by its stage events."""
        attempt = state.get("attempt", 0)
        yield self.stream.frame(
            "pipeline.stage", {"node": node, "state": "started", "attempt": attempt}
        )

        update = await fn(state, self.deps)
        state.update(cast(Any, update))

        for frame in self._drain_degradations(state):
            yield frame

        payload = {"node": node, "state": "done", "attempt": attempt}
        if detail_fn is not None:
            payload["detail"] = detail_fn(state)
        yield self.stream.frame("pipeline.stage", payload)

    async def _pipeline(
        self, state: QueryState, message_id: str
    ) -> AsyncIterator[str]:
        # -- route · G1
        async for frame in self._stage(
            "route", state, nodes.route_node, lambda s: {"route": str(s.get("route"))}
        ):
            yield frame

        route = state.get("route", Route.RETRIEVE)
        if route in (Route.HISTORY, Route.REFUSE):
            node = nodes.history_node if route == Route.HISTORY else nodes.refuse_node
            state.update(cast(Any, await node(state, self.deps)))
            async for frame in self._emit_answer(state, message_id, []):
                yield frame
            return

        # -- rewrite (raw retrieval fires concurrently inside this node)
        async for frame in self._stage(
            "rewrite",
            state,
            nodes.rewrite_node,
            lambda s: {
                "rewritten": s.get("rewritten", False),
                # >1 means the message asked distinct things and was split, so
                # the UI can say "searching for 3 things" rather than implying
                # one search happened.
                "sub_queries": len(s.get("effective_queries") or [1]),
            },
        ):
            yield frame

        # -- retrieve -> rerank -> grade, with at most one corrective retry (I6)
        while True:
            async for frame in self._stage("retrieve", state, nodes.retrieve_node):
                yield frame

            yield await self._retrieval_result(state)

            async for frame in self._stage(
                "rerank",
                state,
                nodes.rerank_node,
                lambda s: {
                    "status": s.get("rerank_status"),
                    "margin": s.get("rerank_margin"),
                },
            ):
                yield frame

            async for frame in self._stage(
                "grade",
                state,
                nodes.grade_node,
                lambda s: {
                    "relevance": round(s.get("relevance", 0.0), 4),
                    "decision": str(s.get("grade")),
                },
            ):
                yield frame

            if state.get("grade") != Grade.RETRY:
                break

            # The retry node bumps `attempt`; `grade` returns ABSTAIN rather than
            # RETRY once it is spent, so this loop runs at most twice.
            state.update(cast(Any, await nodes.retry_node(state, self.deps)))

        if state.get("grade") == Grade.ABSTAIN:
            state.update(cast(Any, await nodes.abstain_node(state, self.deps)))
            await self._persist(state, message_id, [])
            searched = state.get("searched", {})
            yield self.stream.frame(
                "abstain",
                {
                    "message_id": message_id,
                    "reason": state.get("abstain_reason", "relevance_below_floor"),
                    "searched": {
                        "doc_count": searched.get("doc_count", 0),
                        "top_score": searched.get("top_score", 0.0),
                    },
                },
            )
            return

        # -- generate · G3, streaming
        async for frame in self._generate(state, message_id):
            yield frame

    async def _retrieval_result(self, state: QueryState) -> str:
        """The one stage event with a bespoke shape - the UI renders it directly."""
        candidates = state.get("candidates", [])
        by_doc: dict[str, int] = {}
        for candidate in candidates:
            by_doc[candidate.chunk.doc_id] = by_doc.get(candidate.chunk.doc_id, 0) + 1

        filenames = await load_filenames(self.session, state["user_id"], list(by_doc))
        return self.stream.frame(
            "retrieval.result",
            {
                "candidate_count": len(candidates),
                "attempt": state.get("attempt", 0),
                "documents": [
                    {
                        "doc_id": doc_id,
                        "filename": filenames.get(doc_id, "(unknown)"),
                        "hits": hits,
                    }
                    for doc_id, hits in sorted(
                        by_doc.items(), key=lambda kv: kv[1], reverse=True
                    )
                ],
            },
        )

    async def _generate(
        self, state: QueryState, message_id: str
    ) -> AsyncIterator[str]:
        settings = get_settings()
        messages, chunk_ids = nodes.build_generate_messages(state)
        # Derived from what the prompt actually contained, rather than recomputed
        # from the same inputs. Two independent computations agreed only for as
        # long as nothing else trimmed the context - the token budget now can,
        # and a citation list built from a different set than the DATA blocks
        # would silently point [n] at the wrong chunk.
        by_id = {c.chunk.id: c for c in state.get("candidates", [])}
        top = [by_id[cid] for cid in chunk_ids if cid in by_id]

        attempt = state.get("attempt", 0)
        yield self.stream.frame(
            "pipeline.stage", {"node": "generate", "state": "started", "attempt": attempt}
        )

        parts: list[str] = []
        try:
            async for delta in self.deps.llm.stream(
                messages,
                model=settings.llm_model_generate,
                max_tokens=settings.max_answer_tokens,
                timeout=settings.timeout_llm_generate_s,
            ):
                parts.append(delta)
                yield self.stream.frame("answer.delta", {"text": delta})
        except LLMRateLimited as exc:
            fallback = settings.llm_model_generate_fallback
            # Free tiers meter the strongest model per *day*, and no amount of
            # waiting inside one request recovers that - so the choice is a
            # smaller model or no answer at all. Safe to restart because a 429 is
            # known from the response status before any delta is yielded; once
            # text has reached the client, re-requesting would duplicate it.
            if not fallback or parts:
                raise DependencyUnavailable(
                    "llm", "Answer generation failed."
                ) from exc

            logger.warning("generate rate limited; falling back to %s", fallback)
            degradation = Degradation(
                stage=DegradationStage.GENERATE,
                reason=DegradationReason.RATE_LIMITED,
                fallback=fallback,
                detail=(
                    f"{settings.llm_model_generate} is rate limited or out of "
                    f"quota; answered with {fallback} instead."
                ),
            )
            state["degradations"] = [*state.get("degradations", []), degradation]
            yield self.stream.frame("degradation", degradation.model_dump(mode="json"))

            # Rebuilt, not replayed. The fallback model's per-minute ceiling is
            # half the primary's (measured: 6,000 vs 12,000), so re-sending the
            # primary's full-size prompt returns 413 - and a 413 is not a 429, so
            # nothing retries it. Fewer DATA blocks reach the model, which is why
            # the citation list is re-derived from the *new* chunk_ids below
            # rather than kept from the first attempt: a citation list built from
            # a prompt the model never saw would point [n] at the wrong chunk.
            messages, chunk_ids = nodes.build_generate_messages(
                state, context_tokens=settings.max_context_tokens_fallback
            )
            top = [by_id[cid] for cid in chunk_ids if cid in by_id]

            try:
                async for delta in self.deps.llm.stream(
                    messages,
                    model=fallback,
                    max_tokens=settings.max_answer_tokens,
                    timeout=settings.timeout_llm_generate_s,
                ):
                    parts.append(delta)
                    yield self.stream.frame("answer.delta", {"text": delta})
            except LLMError as inner:
                raise DependencyUnavailable(
                    "llm", "Answer generation failed."
                ) from inner
        except LLMError as exc:
            # A partial stream is closed with an explicit error frame, never
            # truncated silently - the caller's handler emits it.
            raise DependencyUnavailable("llm", "Answer generation failed.") from exc

        state["answer"] = "".join(parts)
        yield self.stream.frame(
            "pipeline.stage", {"node": "generate", "state": "done", "attempt": attempt}
        )

        citations = await self._build_citations(state, top, chunk_ids)
        async for frame in self._emit_answer(state, message_id, citations):
            yield frame

    async def _build_citations(
        self, state: QueryState, top, chunk_ids: list[str]
    ) -> list[Citation]:
        """Resolve ``[n]`` markers to chunks by position, not by trusting the model.

        The marker number is the block's position in the prompt, which this side
        assigned - so a hallucinated identifier cannot produce a citation, and a
        marker past the end simply resolves to nothing.
        """
        filenames = await load_filenames(
            self.session, state["user_id"], [c.chunk.doc_id for c in top]
        )
        citations: list[Citation] = []
        for position, candidate in enumerate(top, start=1):
            chunk = candidate.chunk
            citations.append(
                Citation(
                    marker=position,
                    chunk_id=chunk.id,
                    doc_id=chunk.doc_id,
                    filename=filenames.get(chunk.doc_id, "(unknown)"),
                    section=chunk.section,
                    page=chunk.page,
                    char_start=chunk.parent_char_start,
                    char_end=chunk.parent_char_end,
                    verified=None,  # I2 - not yet checked, not "unsupported"
                )
            )
        return citations

    async def _emit_answer(
        self, state: QueryState, message_id: str, citations: list[Citation]
    ) -> AsyncIterator[str]:
        await self._persist(state, message_id, citations)
        yield self.stream.frame(
            "answer.complete",
            {
                "message_id": message_id,
                "citations": [c.model_dump() for c in citations],
            },
        )

    async def _persist(
        self, state: QueryState, message_id: str, citations: list[Citation]
    ) -> None:
        """Write the turn. ``message_citations`` is the evaluation dataset and the
        retrieval trace, not a UI join."""
        candidates = {c.chunk.id: c for c in state.get("candidates", [])}

        self.session.add(
            db.Message(
                id=message_id,
                conversation_id=state["conversation_id"],
                user_id=state["user_id"],
                role="assistant",
                content=state.get("answer", ""),
                degradations=[d.model_dump() for d in state.get("degradations", [])],
                pipeline={
                    "route": str(state.get("route", "")),
                    "rewritten": state.get("rewritten", False),
                    "effective_query": state.get("effective_query", ""),
                    "rerank_status": state.get("rerank_status", ""),
                    "relevance": state.get("relevance", 0.0),
                    "grade": str(state.get("grade", "")),
                    "attempt": state.get("attempt", 0),
                },
            )
        )

        for rank, citation in enumerate(citations):
            candidate = candidates.get(citation.chunk_id)
            self.session.add(
                db.MessageCitation(
                    message_id=message_id,
                    user_id=state["user_id"],
                    chunk_id=citation.chunk_id,
                    doc_id=citation.doc_id,
                    marker=citation.marker,
                    rank=rank,
                    fused_score=candidate.fused_score if candidate else None,
                    rerank_score=candidate.rerank_score if candidate else None,
                    verified=None,
                )
            )

        await self.session.commit()


async def run_verification(
    session: AsyncSession,
    deps: Deps,
    *,
    message_id: str,
    answer: str,
    citations: list[Citation],
    sources: dict[int, str],
) -> dict:
    """G4, off the request path. Persists verdicts and returns the SSE payload.

    Runs after the answer has streamed, so its latency is never charged to the
    user. Persisting matters as much as emitting: a client that disconnected
    early still sees the verdicts via ``GET /messages/{id}``.
    """
    verifier = Verifier(deps.llm)
    try:
        result = await verifier.verify(answer, sources)
    except Exception as exc:  # noqa: BLE001
        logger.warning("verification failed for %s: %s", message_id, exc)
        return {}

    rows = await session.execute(
        select(db.MessageCitation).where(db.MessageCitation.message_id == message_id)
    )
    for row in rows.scalars().all():
        verdict = result.verdicts.get(row.marker)
        if verdict is None:
            continue  # I2 - leave NULL rather than writing a guess
        await session.execute(
            update(db.MessageCitation)
            .where(db.MessageCitation.id == row.id)
            .values(verified=verdict)
        )
    await session.commit()

    return {
        "message_id": message_id,
        "citations": [
            {"marker": marker, "verified": verdict}
            for marker, verdict in result.verdicts.items()
        ],
        "coverage": result.coverage,
    }
