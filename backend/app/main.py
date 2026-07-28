"""FastAPI application factory.

The lifespan does three things and deliberately not a fourth: it refuses to start
misconfigured, ensures the Qdrant collection exists, and closes the pools on the
way out. It does **not** eagerly load the embedding model — that is ~130 MB of
ONNX weights, and paying for it at import time would make every cold start on a
512 MB box slower for no benefit. The first ingest or query loads it lazily.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, documents, workspaces
from app.auth import assert_auth_mode_safe
from app.config import settings
from app.db.session import dispose_engine
from app.errors import RequestIDMiddleware, register_exception_handlers
from app.llm.client import close_llm_client
from app.retrieval.qdrant_store import close_store, get_store
from app.retrieval.rerank import close_reranker

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Refuse to serve a misconfigured deployment rather than silently merging
    # every tenant into one dev user.
    assert_auth_mode_safe(settings)
    logger.info(
        "starting env=%s auth_mode=%s provider=%s rrf_max=%.6f",
        settings.app_env,
        settings.auth_mode,
        settings.llm_provider,
        settings.rrf_max,
    )

    try:
        await get_store().ensure_collection()
    except Exception as exc:  # noqa: BLE001
        # Not fatal at boot: Qdrant may still be coming up under Compose, and a
        # failed healthcheck is more useful than a crash loop. Every search
        # already raises a named 503 if it is genuinely unreachable.
        logger.warning("could not ensure Qdrant collection at startup: %s", exc)

    yield

    await close_llm_client()
    await close_reranker()
    await close_store()
    await dispose_engine()
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="KnowledgeHub",
        description="Multi-document RAG assistant with chat memory",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # The frontend reads this to correlate a failed request with a server log.
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)

    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(workspaces.router)

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
