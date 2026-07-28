"""``POST /chat`` — the query stream, plus conversations, messages, preferences.

``POST`` rather than ``GET`` because a turn carries a body, which means the
browser's native ``EventSource`` cannot consume it: that API is GET-only and
cannot attach an ``Authorization`` header. The frontend reads this with ``fetch``
and a ``ReadableStream`` reader. The contract's mention of ``EventSource`` is
intent, not an API.

Verification runs **after** the stream's terminal event, on the same connection.
It could be a poll or a second channel; the same stream is better because the
client already holds the connection open and the answer does not wait for it. The
tradeoff — a client that disconnects early misses the patch — is covered by
persisting verdicts and exposing ``GET /messages/{id}``.
"""

from __future__ import annotations

import functools
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sse import SSE_HEADERS, with_heartbeat
from app.api.turn import TurnRunner, run_verification
from app.auth import UserId
from app.db import models as db
from app.db.session import get_session
from app.errors import NotFound
from app.graph.nodes import Deps
from app.graph.state import initial_state
from app.memory.conversation import load_memory, update_memory
from app.models.schemas import Citation
from app.retrieval.hydrate import hydrate_candidates

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    # None means "every ready document". A list scopes retrieval to those
    # documents via a Qdrant payload filter — the per-document checkboxes in the
    # UI, and the "Multi-" in the project title.
    selected_doc_ids: list[str] | None = None


@router.post("/chat")
async def chat(
    request: ChatRequest,
    user_id: UserId,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    conversation = await _ensure_conversation(session, user_id, request.conversation_id)
    memory = await load_memory(
        session, conversation_id=conversation.id, user_id=user_id
    )

    session.add(
        db.Message(
            conversation_id=conversation.id,
            user_id=user_id,
            role="user",
            content=request.message,
        )
    )
    await session.commit()

    deps = Deps.default()
    # Hydration needs this request's session; without it candidates reach the
    # reranker and the model with empty text.
    deps.hydrate = functools.partial(hydrate_candidates, session)

    state = initial_state(
        user_id=user_id,
        conversation_id=conversation.id,
        raw_query=request.message,
        selected_doc_ids=request.selected_doc_ids,
        recent_turns=memory.recent_turns,
        rolling_summary=memory.rolling_summary,
        entity_ledger=memory.entity_ledger,
    )

    runner = TurnRunner(session, deps)

    async def source() -> AsyncIterator[str]:
        citations: list[Citation] = []

        async for frame in runner.run(state):
            yield frame

        # -- everything below is off the request path: the answer has landed.
        answer = state.get("answer", "")
        if not answer:
            return

        result = await session.execute(
            select(db.Message)
            .where(
                db.Message.conversation_id == conversation.id,
                db.Message.role == "assistant",
            )
            .order_by(db.Message.created_at.desc())
            .limit(1)
        )
        message = result.scalar_one_or_none()
        if message is None:
            return

        citation_rows = await session.execute(
            select(db.MessageCitation).where(
                db.MessageCitation.message_id == message.id
            )
        )
        markers = {row.marker: row.chunk_id for row in citation_rows.scalars().all()}
        sources = {
            marker: candidate.chunk.parent_text or candidate.chunk.text
            for marker, chunk_id in markers.items()
            for candidate in state.get("candidates", [])
            if candidate.chunk.id == chunk_id
        }

        if sources:
            payload = await run_verification(
                session,
                deps,
                message_id=message.id,
                answer=answer,
                citations=citations,
                sources=sources,
            )
            if payload:
                yield runner.stream.frame("verification.complete", payload)

        # Memory is folded in after the turn, never during — summarising mid-turn
        # would put an LLM call on the critical path of a streaming answer.
        try:
            await update_memory(
                session,
                conversation_id=conversation.id,
                user_id=user_id,
                turns=[
                    {"role": "user", "content": request.message},
                    {"role": "assistant", "content": answer},
                ],
                existing_summary=memory.rolling_summary,
                existing_ledger=memory.entity_ledger,
                llm=deps.llm,
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory update failed: %s", exc)

    return StreamingResponse(
        with_heartbeat(source()), media_type="text/event-stream", headers=SSE_HEADERS
    )


async def _ensure_conversation(
    session: AsyncSession, user_id: str, conversation_id: str | None
) -> db.Conversation:
    if conversation_id:
        conversation = await session.get(db.Conversation, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            raise NotFound("No such conversation.")
        return conversation

    conversation = db.Conversation(id=str(uuid.uuid4()), user_id=user_id)
    session.add(conversation)
    await session.commit()
    return conversation


# ------------------------------------------------------------- conversations


@router.get("/conversations")
async def list_conversations(
    user_id: UserId, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    result = await session.execute(
        select(db.Conversation)
        .where(db.Conversation.user_id == user_id)
        .order_by(db.Conversation.updated_at.desc())
    )
    return [
        {
            "id": c.id,
            "title": c.title,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in result.scalars().all()
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user_id: UserId,
    session: AsyncSession = Depends(get_session),
) -> dict:
    conversation = await session.get(db.Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise NotFound("No such conversation.")

    result = await session.execute(
        select(db.Message)
        .where(db.Message.conversation_id == conversation_id)
        .order_by(db.Message.created_at)
    )
    messages = result.scalars().all()
    return {
        "id": conversation.id,
        "title": conversation.title,
        "messages": [await _serialise_message(session, m) for m in messages],
    }


async def _serialise_message(session: AsyncSession, message: db.Message) -> dict:
    rows = await session.execute(
        select(db.MessageCitation)
        .where(db.MessageCitation.message_id == message.id)
        .order_by(db.MessageCitation.marker)
    )
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "degradations": message.degradations,
        "pipeline": message.pipeline,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "citations": [
            {
                "marker": row.marker,
                "chunk_id": row.chunk_id,
                "doc_id": row.doc_id,
                "rank": row.rank,
                "fused_score": row.fused_score,
                "rerank_score": row.rerank_score,
                # NULL stays null — "not checked" is not "unsupported" (I2).
                "verified": row.verified,
            }
            for row in rows.scalars().all()
        ],
    }


@router.get("/messages/{message_id}")
async def get_message(
    message_id: str, user_id: UserId, session: AsyncSession = Depends(get_session)
) -> dict:
    """The disconnect-recovery path.

    The stream is deliberately not resumable, so this is how a client that
    dropped mid-turn gets final state — including verification verdicts that
    arrived after it stopped listening.
    """
    message = await session.get(db.Message, message_id)
    if message is None or message.user_id != user_id:
        raise NotFound("No such message.")
    return await _serialise_message(session, message)


# -------------------------------------------------------------- preferences


class Preferences(BaseModel):
    preferences: dict = Field(default_factory=dict)


@router.get("/preferences")
async def get_preferences(
    user_id: UserId, session: AsyncSession = Depends(get_session)
) -> dict:
    row = await session.get(db.UserPreference, user_id)
    return {"preferences": row.preferences if row else {}}


@router.put("/preferences")
async def put_preferences(
    body: Preferences,
    user_id: UserId,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Stored per user, and **never read on a retrieval path**.

    Tone and default scoping are generation-time concerns. Letting them reach the
    query embedding would silently distort every search the user makes, which is
    why this is a third store rather than a field on conversation state.
    """
    row = await session.get(db.UserPreference, user_id)
    if row is None:
        row = db.UserPreference(user_id=user_id, preferences=body.preferences)
        session.add(row)
    else:
        row.preferences = body.preferences
    await session.commit()
    return {"preferences": row.preferences}
