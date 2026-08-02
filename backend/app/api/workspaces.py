"""Workspaces - a named group of documents that many conversations share.

The point is upload-once: a user attaches documents to a workspace and every
chat opened inside it retrieves against that same set with no re-upload. This
is the frontend's primary organising unit (its own "project"), but it is
optional at the API level - `POST /chat` and `POST /documents` both still work
without a `workspace_id`, they just leave the row ungrouped. Nothing downstream
of ingest or retrieval reads `workspace_id`; it is purely a grouping label
resolved into `selected_doc_ids` before the query graph ever runs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import UserId
from app.db import models as db
from app.db.session import get_session
from app.errors import NotFound
from app.ingest.pipeline import IngestPipeline

router = APIRouter(tags=["workspaces"])


class WorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


def _serialise(workspace: db.Workspace, *, document_count: int, conversation_count: int) -> dict:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "document_count": document_count,
        "conversation_count": conversation_count,
        "created_at": workspace.created_at.isoformat() if workspace.created_at else None,
        "updated_at": workspace.updated_at.isoformat() if workspace.updated_at else None,
    }


async def _owned(session: AsyncSession, user_id: str, workspace_id: str) -> db.Workspace:
    """Fetch scoped by user_id (I3).

    404, not 403, for a workspace belonging to someone else - a 403 would
    confirm the id exists, which is an enumeration oracle.
    """
    workspace = await session.get(db.Workspace, workspace_id)
    if workspace is None or workspace.user_id != user_id:
        raise NotFound("No such workspace.")
    return workspace


async def _counts_for_all(
    session: AsyncSession, user_id: str
) -> tuple[dict[str, int], dict[str, int]]:
    """Every workspace's counts, in two queries rather than two per workspace.

    MEASURED, and the reason this exists: listing 18 workspaces issued **37**
    statements - one for the list, then a document count and a conversation
    count per row. Locally that is 51 ms and invisible. The deployment reaches
    Postgres over a network, and 37 *sequential* round trips is ~0.9 s at a
    25 ms RTT and ~2.2 s at 60 ms, on the first screen a user sees after
    signing in. The cost also grew with the account: every workspace added two
    more round trips, so the app got slower the more it was used.

    Counting is also done by the database now. `_counts` below selects every
    matching id and takes `len()` of the result in Python, which ships the
    whole id set over the wire to discard it.

    `user_id` is in both predicates for the reason `_counts` documents: I3
    holds by construction rather than by the caller having checked ownership
    first.
    """
    docs = await session.execute(
        select(db.Document.workspace_id, func.count())
        .where(
            db.Document.user_id == user_id,
            db.Document.workspace_id.is_not(None),
        )
        .group_by(db.Document.workspace_id)
    )
    convos = await session.execute(
        select(db.Conversation.workspace_id, func.count())
        .where(
            db.Conversation.user_id == user_id,
            db.Conversation.workspace_id.is_not(None),
        )
        .group_by(db.Conversation.workspace_id)
    )
    return (
        {row[0]: row[1] for row in docs.all()},
        {row[0]: row[1] for row in convos.all()},
    )


async def _counts(
    session: AsyncSession, user_id: str, workspace_id: str
) -> tuple[int, int]:
    """Both counts, scoped by user_id as well as workspace.

    Every caller reaches this after _owned_workspace has already rejected a
    foreign id, so the extra predicate changes no result today. It is here
    because I3 is meant to hold by construction: a helper that takes an id
    without the owner is one refactor away from being called before the
    ownership check, and it would then report a stranger's document count.
    """
    docs = await session.execute(
        select(db.Document.id).where(
            db.Document.workspace_id == workspace_id,
            db.Document.user_id == user_id,
        )
    )
    convos = await session.execute(
        select(db.Conversation.id).where(
            db.Conversation.workspace_id == workspace_id,
            db.Conversation.user_id == user_id,
        )
    )
    return len(docs.all()), len(convos.all())


@router.post("/workspaces", status_code=201)
async def create_workspace(
    request: WorkspaceRequest,
    user_id: UserId,
    session: AsyncSession = Depends(get_session),
) -> dict:
    workspace = db.Workspace(user_id=user_id, name=request.name)
    session.add(workspace)
    await session.commit()
    return _serialise(workspace, document_count=0, conversation_count=0)


@router.get("/workspaces")
async def list_workspaces(
    user_id: UserId, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    result = await session.execute(
        select(db.Workspace)
        .where(db.Workspace.user_id == user_id)
        .order_by(db.Workspace.updated_at.desc())
    )
    workspaces = result.scalars().all()
    doc_counts, convo_counts = await _counts_for_all(session, user_id)
    return [
        _serialise(
            workspace,
            document_count=doc_counts.get(workspace.id, 0),
            conversation_count=convo_counts.get(workspace.id, 0),
        )
        for workspace in workspaces
    ]


@router.get("/workspaces/{workspace_id}")
async def get_workspace(
    workspace_id: str, user_id: UserId, session: AsyncSession = Depends(get_session)
) -> dict:
    workspace = await _owned(session, user_id, workspace_id)
    doc_count, convo_count = await _counts(session, user_id, workspace_id)
    return _serialise(workspace, document_count=doc_count, conversation_count=convo_count)


@router.put("/workspaces/{workspace_id}")
async def rename_workspace(
    workspace_id: str,
    request: WorkspaceRequest,
    user_id: UserId,
    session: AsyncSession = Depends(get_session),
) -> dict:
    workspace = await _owned(session, user_id, workspace_id)
    workspace.name = request.name
    await session.commit()
    doc_count, convo_count = await _counts(session, user_id, workspace_id)
    return _serialise(workspace, document_count=doc_count, conversation_count=convo_count)


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: str, user_id: UserId, session: AsyncSession = Depends(get_session)
) -> None:
    """Delete a workspace and everything scoped to it.

    Documents are deleted through :meth:`IngestPipeline.delete` one at a time
    rather than left to the database's ``ON DELETE CASCADE`` - the cascade only
    knows about Postgres, and a document deleted that way would leave its
    vectors orphaned in Qdrant with nothing to ever clean them up. Conversations
    have no such external store, so their cascade (`conversations` ->
    `messages` -> `message_citations`) is left to the database.
    """
    await _owned(session, user_id, workspace_id)

    pipeline = IngestPipeline()
    docs = await session.execute(
        select(db.Document.id).where(db.Document.workspace_id == workspace_id)
    )
    for (doc_id,) in docs.all():
        await pipeline.delete(session, document_id=doc_id, user_id=user_id)

    workspace = await session.get(db.Workspace, workspace_id)
    if workspace is not None:
        await session.delete(workspace)
    await session.commit()
