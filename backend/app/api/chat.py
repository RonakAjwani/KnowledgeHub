"""``POST /chat`` - the query stream, plus conversations, messages, preferences.

``POST`` rather than ``GET`` because a turn carries a body, which means the
browser's native ``EventSource`` cannot consume it: that API is GET-only and
cannot attach an ``Authorization`` header. The frontend reads this with ``fetch``
and a ``ReadableStream`` reader. The contract's mention of ``EventSource`` is
intent, not an API.

Verification runs **after** the stream's terminal event, on the same connection.
It could be a poll or a second channel; the same stream is better because the
client already holds the connection open and the answer does not wait for it. The
tradeoff - a client that disconnects early misses the patch - is covered by
persisting verdicts and exposing ``GET /messages/{id}``.
"""

from __future__ import annotations

import functools
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.sse import SSE_HEADERS, with_heartbeat
from app.api.turn import TurnRunner, run_verification
from app.auth import UserId
from app.config import get_settings
from app.db import models as db
from app.db.session import get_session, get_sessionmaker
from app.errors import NotFound
from app.graph import prompts
from app.graph.nodes import Deps
from app.graph.state import initial_state
from app.llm.client import LLMError, Message, get_llm_client
from app.memory.conversation import load_memory, update_memory
from app.models.schemas import Citation
from app.retrieval.hydrate import (
    hydrate_candidates,
    load_filenames,
    load_ready_doc_ids,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    # A new conversation started from inside a workspace is tagged with it, so
    # it shows up under that workspace on reload without the client having to
    # re-send it on every later turn. Ignored once `conversation_id` is set -
    # the conversation's own workspace_id wins, since a chat cannot change which
    # workspace it belongs to mid-thread.
    workspace_id: str | None = None
    # None means "every ready document, or every document in the conversation's
    # workspace if it has one". A list scopes retrieval to those documents via a
    # Qdrant payload filter - the per-document checkboxes in the UI.
    selected_doc_ids: list[str] | None = None


@router.post("/chat")
async def chat(
    request: ChatRequest,
    user_id: UserId,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    conversation = await _ensure_conversation(
        session, user_id, request.conversation_id, request.workspace_id
    )
    # First turn on this conversation (brand-new, or an older one from before
    # this existed). The truncated title lands immediately - a real model
    # call on the request path would add latency to the one thing the user is
    # actually waiting on, for a chat name nobody is watching populate live.
    # `_generate_title` then runs after the stream closes and overwrites it
    # with a proper summary; if that call fails or never fires (the client
    # disconnected before the response finished sending, so `BackgroundTasks`
    # never ran), the truncated title is still there; nothing regresses to
    # "Untitled chat".
    is_first_turn = conversation.title is None
    if is_first_turn:
        conversation.title = _derive_title(request.message)
    selected_doc_ids = request.selected_doc_ids
    if selected_doc_ids is None and conversation.workspace_id is not None:
        # Scope to the workspace's own documents rather than every document the
        # user has ever uploaded - the whole point of a workspace is that a chat
        # inside it only ever searches what was put there.
        doc_rows = await session.execute(
            select(db.Document.id).where(
                db.Document.workspace_id == conversation.workspace_id
            )
        )
        selected_doc_ids = [row[0] for row in doc_rows.all()]
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
    # The overview route retrieves per document, so it needs the document list.
    deps.list_docs = functools.partial(load_ready_doc_ids, session)

    state = initial_state(
        user_id=user_id,
        conversation_id=conversation.id,
        raw_query=request.message,
        selected_doc_ids=selected_doc_ids,
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

        # Memory is folded in after the turn, never during - summarising mid-turn
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

    if is_first_turn:
        background.add_task(_generate_title, conversation.id, request.message)

    return StreamingResponse(
        with_heartbeat(source()), media_type="text/event-stream", headers=SSE_HEADERS
    )


TITLE_MAX_CHARS = 60


def _derive_title(message: str) -> str:
    """A chat's sidebar name, taken straight from its first message.

    Truncated on a word boundary rather than mid-word - a title sits in the
    sidebar and gets read on every visit, not skimmed once like a citation
    snippet, so "...three main types of pho" reads as broken in a way a
    clean cut at the last whole word before the limit does not.
    """
    text = " ".join(message.split())
    if len(text) <= TITLE_MAX_CHARS:
        return text
    truncated = text[:TITLE_MAX_CHARS]
    last_space = truncated.rfind(" ")
    if last_space > TITLE_MAX_CHARS // 2:
        truncated = truncated[:last_space]
    return truncated.rstrip() + "…"


async def _generate_title(conversation_id: str, message: str) -> None:
    """Upgrade the truncated title to a short, real summary - the same idea
    ChatGPT/Claude use for chat names, rather than just the question's own
    opening words.

    Runs after `POST /chat`'s response has finished sending (`BackgroundTasks`
    - see the call site), with its own DB session: the request's is closed by
    then. Uses `llm_model_route`, the fastest configured model - the same
    choice `route_node` makes for the same reason (contract: "latency-critical
    mechanical roles get the fastest model"), which fits a 3-6 word title
    better than it fits routing.

    Best-effort and silent on failure. This is a cosmetic upgrade over a title
    that already exists and is already reasonable (`_derive_title`'s own
    output), not a step anything else depends on - there is no turn left to
    degrade and no SSE connection left to tell.
    """
    settings = get_settings()
    llm = get_llm_client()
    try:
        result = await llm.complete_json(
            [
                Message(role="system", content=prompts.TITLE_SYSTEM),
                Message(role="user", content=prompts.build_title_message(message)),
            ],
            model=settings.llm_model_route,
            max_tokens=32,
            timeout=settings.timeout_llm_route_s,
        )
        title = str(result.get("title", "")).strip()
    except (LLMError, ValueError) as exc:
        logger.warning("title generation failed for %s: %s", conversation_id, exc)
        return
    if not title:
        return

    maker = get_sessionmaker()
    async with maker() as session:
        conversation = await session.get(db.Conversation, conversation_id)
        # Gone, or already renamed by something else in the meantime (the
        # user could in principle have already deleted this chat) - either
        # way, nothing here should resurrect or overwrite it.
        if conversation is None:
            return
        conversation.title = title[:TITLE_MAX_CHARS]
        await session.commit()


async def _ensure_conversation(
    session: AsyncSession,
    user_id: str,
    conversation_id: str | None,
    workspace_id: str | None,
) -> db.Conversation:
    if conversation_id:
        conversation = await session.get(db.Conversation, conversation_id)
        if conversation is None or conversation.user_id != user_id:
            raise NotFound("No such conversation.")
        return conversation

    if workspace_id is not None:
        workspace = await session.get(db.Workspace, workspace_id)
        if workspace is None or workspace.user_id != user_id:
            raise NotFound("No such workspace.")

    conversation = db.Conversation(
        id=str(uuid.uuid4()), user_id=user_id, workspace_id=workspace_id
    )
    session.add(conversation)
    await session.commit()
    return conversation


# ------------------------------------------------------------- conversations


def _serialise_conversation(c: db.Conversation) -> dict:
    return {
        "id": c.id,
        "title": c.title,
        "workspace_id": c.workspace_id,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


@router.get("/conversations")
async def list_conversations(
    user_id: UserId,
    workspace_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    query = select(db.Conversation).where(db.Conversation.user_id == user_id)
    if workspace_id is not None:
        query = query.where(db.Conversation.workspace_id == workspace_id)
    result = await session.execute(query.order_by(db.Conversation.updated_at.desc()))
    return [_serialise_conversation(c) for c in result.scalars().all()]


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
        **_serialise_conversation(conversation),
        "messages": [await _serialise_message(session, user_id, m) for m in messages],
    }


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    user_id: UserId,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete one conversation, the same shape as `delete_workspace`.

    No `IngestPipeline` step to mirror here - a conversation has no Qdrant
    footprint of its own (only documents do), so unlike a workspace's
    document-by-document cleanup, `messages` -> `message_citations` cascading
    in Postgres is the whole deletion and nothing is left orphaned in another
    store.
    """
    conversation = await session.get(db.Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise NotFound("No such conversation.")
    await session.delete(conversation)
    await session.commit()


async def _serialise_message(
    session: AsyncSession, user_id: str, message: db.Message
) -> dict:
    """Citations here carry the same span and filename info a live turn's
    `answer.complete` does - not just the evaluation-dataset columns.

    A reloaded conversation is how the frontend resumes a chat, and its
    citation chips have to resolve to a source span exactly like a fresh
    answer's do. `message_citations` alone cannot do that: it is the retrieval
    trace (rank, fused/rerank score, verified) and was never meant to duplicate
    the chunk mirror's own columns, so `char_start`/`char_end`/`section`/`page`
    and the filename are joined in from `chunks` and `documents` here.
    """
    rows = await session.execute(
        select(db.MessageCitation)
        .where(db.MessageCitation.message_id == message.id)
        .order_by(db.MessageCitation.marker)
    )
    citation_rows = rows.scalars().all()

    chunk_ids = {row.chunk_id for row in citation_rows}
    chunks: dict[str, db.Chunk] = {}
    if chunk_ids:
        # Scoped by user_id as well as id. The ids come from this user's own
        # message_citations, so the filter is redundant today - which is exactly
        # why it belongs here. I3 says there is no unscoped read path, and a
        # query that is safe only because of what a caller happens to pass is
        # safe by discipline rather than by construction. Chunk rows carry
        # document text; a future bug that let a foreign chunk_id into
        # message_citations would leak it.
        chunk_result = await session.execute(
            select(db.Chunk).where(
                db.Chunk.id.in_(chunk_ids), db.Chunk.user_id == user_id
            )
        )
        chunks = {c.id: c for c in chunk_result.scalars().all()}

    filenames = await load_filenames(
        session, user_id, [row.doc_id for row in citation_rows]
    )

    citations = []
    for row in citation_rows:
        chunk = chunks.get(row.chunk_id)
        citations.append(
            {
                "marker": row.marker,
                "chunk_id": row.chunk_id,
                "doc_id": row.doc_id,
                "filename": filenames.get(row.doc_id, ""),
                "section": chunk.section if chunk else None,
                "page": chunk.page if chunk else None,
                # A chunk can be gone (I3-scoped delete, or the recoverable half
                # of the Qdrant-then-Postgres write ordering) while its citation
                # row remains - the offsets fall back to 0 rather than crashing
                # the history load. The chip still shows; it just cannot scroll
                # anywhere useful, which is the honest outcome for a citation
                # whose source no longer exists.
                "char_start": chunk.char_start if chunk else 0,
                "char_end": chunk.char_end if chunk else 0,
                "rank": row.rank,
                "fused_score": row.fused_score,
                "rerank_score": row.rerank_score,
                # NULL stays null - "not checked" is not "unsupported" (I2).
                "verified": row.verified,
            }
        )

    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "degradations": message.degradations,
        "pipeline": message.pipeline,
        "created_at": message.created_at.isoformat() if message.created_at else None,
        "citations": citations,
    }


@router.get("/messages/{message_id}")
async def get_message(
    message_id: str, user_id: UserId, session: AsyncSession = Depends(get_session)
) -> dict:
    """The disconnect-recovery path.

    The stream is deliberately not resumable, so this is how a client that
    dropped mid-turn gets final state - including verification verdicts that
    arrived after it stopped listening.
    """
    message = await session.get(db.Message, message_id)
    if message is None or message.user_id != user_id:
        raise NotFound("No such message.")
    return await _serialise_message(session, user_id, message)


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
