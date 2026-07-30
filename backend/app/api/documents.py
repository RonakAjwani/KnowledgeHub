"""Document upload, listing, deletion, and the ingest progress stream.

Upload returns immediately with ``status: queued`` and runs ingest as a
background task in the same process. A 200-page PDF cannot block an HTTP request,
and a separate worker service is not available - 750 free instance-hours per
month fits exactly one service, so ingest shares this process and its 512 MB.

**A duplicate upload is not an error.** The same bytes from the same user return
the existing document with **HTTP 200**, not a 409. Re-uploading a file you
already have is a reasonable thing to do by accident, and the useful response is
the document you already have rather than a failure the client must special-case.
Scoped per user: two people uploading the same public PDF have two documents.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.sse import SSE_HEADERS, EventStream, with_heartbeat
from app.auth import UserId
from app.config import get_settings
from app.db import models as db
from app.db.session import get_session, get_sessionmaker
from app.errors import FileTooLarge, NotFound, UnsupportedMediaType
from app.ingest.parse import SUPPORTED_MIMES
from app.ingest.pipeline import IngestPipeline, content_sha256, find_existing_document
from app.models.schemas import DocumentStatus

logger = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])

# doc_id -> subscribers. In-process because ingest runs in-process; a second
# instance would need a broker, and there is deliberately only ever one.
_subscribers: dict[str, list[asyncio.Queue]] = {}


def _publish(doc_id: str, event: str, payload: dict) -> None:
    for queue in _subscribers.get(doc_id, []):
        queue.put_nowait((event, payload))


def _serialise(document: db.Document) -> dict:
    return {
        "id": document.id,
        "filename": document.filename,
        "mime": document.mime,
        "status": document.status,
        "error": document.error,
        "chunk_count": document.chunk_count,
        "sanitization_report": document.sanitization_report,
        "extraction": document.extraction,
        "workspace_id": document.workspace_id,
        "created_at": document.created_at.isoformat() if document.created_at else None,
    }


def _guess_mime(upload: UploadFile) -> str:
    """Trust the extension over the browser's content type.

    Browsers report ``application/octet-stream`` for ``.md`` often enough that
    keying on the header alone rejects perfectly valid markdown.
    """
    name = (upload.filename or "").lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    if name.endswith((".md", ".markdown")):
        return "text/markdown"
    if name.endswith(".txt"):
        return "text/plain"
    return (upload.content_type or "").split(";")[0].strip()


@router.post("/documents", status_code=201)
async def upload_document(
    user_id: UserId,
    background: BackgroundTasks,
    response: Response,
    file: UploadFile = File(...),
    # None = ungrouped. The frontend always sends the active workspace, but the
    # field stays optional so a script or a future non-workspace caller isn't
    # forced to invent one.
    workspace_id: str | None = Form(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    settings = get_settings()
    data = await file.read()

    if len(data) > settings.max_upload_bytes:
        raise FileTooLarge(
            f"File exceeds the {settings.max_upload_bytes // (1024 * 1024)} MB limit.",
            {"size": len(data), "limit": settings.max_upload_bytes},
        )

    mime = _guess_mime(file)
    if mime not in SUPPORTED_MIMES:
        raise UnsupportedMediaType(
            f"Unsupported type '{mime or 'unknown'}'. Upload a PDF, .txt or .md file.",
            {"mime": mime},
        )

    if workspace_id is not None:
        await _owned_workspace(session, user_id, workspace_id)

    sha = content_sha256(data)
    existing = await find_existing_document(
        session, user_id=user_id, sha256=sha, workspace_id=workspace_id
    )
    if existing is not None:
        # Idempotent: return what they already have, with 200 rather than 201.
        response.status_code = 200
        return _serialise(existing)

    document = db.Document(
        user_id=user_id,
        workspace_id=workspace_id,
        filename=file.filename or "document",
        mime=mime,
        content_sha256=sha,
        blob_ref=data,
        status=str(DocumentStatus.QUEUED),
    )
    session.add(document)
    await session.commit()

    background.add_task(_run_ingest, document.id, user_id, data, mime)
    return _serialise(document)


async def _owned_workspace(session: AsyncSession, user_id: str, workspace_id: str) -> None:
    """A document cannot be filed under a workspace the caller does not own -
    otherwise a guessed id would silently attach someone else's upload to it."""
    workspace = await session.get(db.Workspace, workspace_id)
    if workspace is None or workspace.user_id != user_id:
        raise NotFound("No such workspace.")


async def _run_ingest(doc_id: str, user_id: str, data: bytes, mime: str) -> None:
    """Background ingest with its own session - the request's is long gone."""
    maker: async_sessionmaker[AsyncSession] = get_sessionmaker()

    async def publisher(document_id: str, status: DocumentStatus, progress) -> None:
        payload = {"document_id": document_id, "status": str(status)}
        if progress:
            payload["progress"] = progress
        _publish(document_id, "document.status", payload)

    async with maker() as session:
        pipeline = IngestPipeline(publisher=publisher)
        result = await pipeline.ingest(
            session, document_id=doc_id, user_id=user_id, data=data, mime=mime
        )

    # Exactly one terminal event per stream.
    if result.status is DocumentStatus.READY:
        _publish(
            doc_id,
            "document.complete",
            {
                "document_id": doc_id,
                "chunk_count": result.chunk_count,
                "extraction": result.extraction,
            },
        )
    else:
        _publish(
            doc_id,
            "document.error",
            {
                "document_id": doc_id,
                "code": "invalid_request",
                "message": result.error or "Ingest failed.",
            },
        )


@router.get("/documents")
async def list_documents(
    user_id: UserId,
    workspace_id: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    query = select(db.Document).where(db.Document.user_id == user_id)
    if workspace_id is not None:
        query = query.where(db.Document.workspace_id == workspace_id)
    result = await session.execute(query.order_by(db.Document.created_at.desc()))
    return [_serialise(d) for d in result.scalars().all()]


async def _owned(session: AsyncSession, user_id: str, doc_id: str) -> db.Document:
    """Fetch scoped by user_id (I3).

    A document belonging to someone else is reported as **404, not 403** - a 403
    would confirm that the id exists, which is an enumeration oracle. The
    taxonomy keeps 403 for resources the caller can legitimately know about.
    """
    document = await session.get(db.Document, doc_id)
    if document is None or document.user_id != user_id:
        raise NotFound("No such document.")
    return document


@router.get("/documents/{doc_id}")
async def get_document(
    doc_id: str, user_id: UserId, session: AsyncSession = Depends(get_session)
) -> dict:
    document = await _owned(session, user_id, doc_id)
    # normalized_text is included here and nowhere else: it is what the source
    # pane renders, and every citation offset indexes into it.
    return {**_serialise(document), "normalized_text": document.normalized_text}


@router.get("/documents/{doc_id}/blob")
async def download_document(
    doc_id: str, user_id: UserId, session: AsyncSession = Depends(get_session)
) -> Response:
    document = await _owned(session, user_id, doc_id)
    if document.blob_ref is None:
        raise NotFound("The original file is no longer stored.")
    return Response(
        content=document.blob_ref,
        media_type=document.mime,
        headers={
            "Content-Disposition": f'attachment; filename="{document.filename}"'
        },
    )


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str, user_id: UserId, session: AsyncSession = Depends(get_session)
) -> Response:
    await _owned(session, user_id, doc_id)
    await IngestPipeline().delete(session, document_id=doc_id, user_id=user_id)
    return Response(status_code=204)


@router.get("/documents/{doc_id}/events")
async def document_events(
    doc_id: str, user_id: UserId, session: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    """Progress for one document. Exactly one terminal event, then close."""
    document = await _owned(session, user_id, doc_id)
    stream = EventStream()

    async def source() -> AsyncIterator[str]:
        queue: asyncio.Queue = asyncio.Queue()
        _subscribers.setdefault(doc_id, []).append(queue)
        try:
            # Emit current state immediately: a client that connects after
            # ingest finished would otherwise wait forever for an event that
            # has already been published.
            if document.status == str(DocumentStatus.READY):
                yield stream.frame(
                    "document.complete",
                    {
                        "document_id": doc_id,
                        "chunk_count": document.chunk_count,
                        "extraction": document.extraction,
                    },
                )
                return
            if document.status == str(DocumentStatus.FAILED):
                yield stream.frame(
                    "document.error",
                    {
                        "document_id": doc_id,
                        "code": "invalid_request",
                        "message": document.error or "Ingest failed.",
                    },
                )
                return

            yield stream.frame(
                "document.status",
                {"document_id": doc_id, "status": document.status},
            )

            while True:
                event, payload = await queue.get()
                yield stream.frame(event, payload)
                if event in ("document.complete", "document.error"):
                    return
        finally:
            _subscribers.get(doc_id, []).remove(queue)
            if not _subscribers.get(doc_id):
                _subscribers.pop(doc_id, None)

    return StreamingResponse(
        with_heartbeat(source()), media_type="text/event-stream", headers=SSE_HEADERS
    )
