"""Workspaces — a named group of documents that many conversations share.

The point is upload-once: a user attaches documents to a workspace and every
chat opened inside it retrieves against that same set with no re-upload. This
is the frontend's primary organising unit (its own "project"), but it is
optional at the API level — `POST /chat` and `POST /documents` both still work
without a `workspace_id`, they just leave the row ungrouped. Nothing downstream
of ingest or retrieval reads `workspace_id`; it is purely a grouping label
resolved into `selected_doc_ids` before the query graph ever runs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
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

    404, not 403, for a workspace belonging to someone else — a 403 would
    confirm the id exists, which is an enumeration oracle.
    """
    workspace = await session.get(db.Workspace, workspace_id)
    if workspace is None or workspace.user_id != user_id:
        raise NotFound("No such workspace.")
    return workspace


async def _counts(session: AsyncSession, workspace_id: str) -> tuple[int, int]:
    docs = await session.execute(
        select(db.Document.id).where(db.Document.workspace_id == workspace_id)
    )
    convos = await session.execute(
        select(db.Conversation.id).where(db.Conversation.workspace_id == workspace_id)
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
    out = []
    for workspace in workspaces:
        doc_count, convo_count = await _counts(session, workspace.id)
        out.append(_serialise(workspace, document_count=doc_count, conversation_count=convo_count))
    return out


@router.get("/workspaces/{workspace_id}")
async def get_workspace(
    workspace_id: str, user_id: UserId, session: AsyncSession = Depends(get_session)
) -> dict:
    workspace = await _owned(session, user_id, workspace_id)
    doc_count, convo_count = await _counts(session, workspace_id)
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
    doc_count, convo_count = await _counts(session, workspace_id)
    return _serialise(workspace, document_count=doc_count, conversation_count=convo_count)


@router.delete("/workspaces/{workspace_id}", status_code=204)
async def delete_workspace(
    workspace_id: str, user_id: UserId, session: AsyncSession = Depends(get_session)
) -> None:
    """Delete a workspace and everything scoped to it.

    Documents are deleted through :meth:`IngestPipeline.delete` one at a time
    rather than left to the database's ``ON DELETE CASCADE`` — the cascade only
    knows about Postgres, and a document deleted that way would leave its
    vectors orphaned in Qdrant with nothing to ever clean them up. Conversations
    have no such external store, so their cascade (`conversations` →
    `messages` → `message_citations`) is left to the database.
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
