"""FastAPI application factory.

Phase 0 wires the cross-cutting concerns only — request ids, the error envelope,
CORS, and the auth-mode safety check. Routers, the database pool and the embedding
model arrive in later phases and are registered here as they land.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import assert_auth_mode_safe
from app.config import settings
from app.errors import RequestIDMiddleware, register_exception_handlers

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
    yield
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

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
