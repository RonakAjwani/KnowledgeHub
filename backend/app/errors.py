"""Error taxonomy, envelope, and the handlers that guarantee it.

One envelope on every error path::

    {"error": {"code": ..., "message": ..., "detail": {...}, "request_id": ...}}

The hard guarantee is the last line of the contract's §6: **no 500 with a stack
trace ever reaches the client.** Anything unhandled becomes a 503 carrying a
``request_id`` that correlates to the server log, so the operator can find the
trace and the caller cannot.

One deliberate deviation from framework defaults, because the taxonomy and FastAPI
disagree about 422: FastAPI returns 422 for request-body validation failures, but
this contract reserves 422 for ``document_not_ready`` — querying a document that
has not finished ingesting. Validation failures are 400 ``invalid_request`` here,
so ``RequestValidationError`` is remapped below. Leaving the default in place would
make "your JSON is malformed" and "your document is still embedding" indistinguishable
to a client, which is precisely the sort of thing this taxonomy exists to prevent.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def current_request_id() -> str:
    return _request_id.get()


class AppError(Exception):
    """Base for every error this application raises deliberately.

    Subclasses fix ``status_code`` and ``code``; the pair is the contract's §6
    table and should not be invented ad hoc at call sites.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class InvalidRequest(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "invalid_request"


class Unauthenticated(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthenticated"


class Forbidden(AppError):
    """Resource exists but is not owned by this user_id (I3)."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"


class NotFound(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class FileTooLarge(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    code = "file_too_large"


class UnsupportedMediaType(AppError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    code = "unsupported_media_type"


class DocumentNotReady(AppError):
    """Queried before status == ready. Not a validation failure — see module docstring."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "document_not_ready"


class RateLimited(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"

    def __init__(
        self,
        message: str,
        retry_after: int = 1,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, detail)
        self.retry_after = retry_after


class DependencyUnavailable(AppError):
    """Qdrant / Postgres / LLM is down. Always names the dependency."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "dependency_unavailable"

    def __init__(
        self,
        dependency: str,
        message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        merged = {"dependency": dependency, **(detail or {})}
        super().__init__(message or f"{dependency} is unavailable", merged)
        self.dependency = dependency


def error_body(
    code: str, message: str, detail: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "detail": detail or {},
            "request_id": current_request_id(),
        }
    }


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, exposes it on the response, and binds it for logging.

    Honours an inbound ``X-Request-ID`` so a correlation id set by a proxy or the
    frontend survives into our logs.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = _request_id.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id.reset(token)
        response.headers["X-Request-ID"] = rid
        return response


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {}
        if isinstance(exc, RateLimited):
            headers["Retry-After"] = str(exc.retry_after)
        # Deliberate errors are expected control flow, not incidents — log at
        # warning without a traceback so real faults stay visible in the noise.
        logger.warning(
            "app_error code=%s status=%s request_id=%s message=%s",
            exc.code,
            exc.status_code,
            current_request_id(),
            exc.message,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.detail),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Remapped from FastAPI's default 422 to 400 — see module docstring.
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=error_body(
                "invalid_request",
                "Request body or parameters failed validation.",
                {"errors": exc.errors()},
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Framework-raised HTTP errors (404 on an unknown route, 405, ...) still
        # have to come out in our envelope rather than Starlette's {"detail": ...}.
        code = {
            400: "invalid_request",
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            413: "file_too_large",
            415: "unsupported_media_type",
            429: "rate_limited",
        }.get(exc.status_code, "dependency_unavailable")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        if exc.status_code >= 500:
            # Never surface a 5xx as-is; collapse to 503 like any other fault.
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=error_body("dependency_unavailable", "Service unavailable."),
            )
        return JSONResponse(
            status_code=exc.status_code, content=error_body(code, message)
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # The whole traceback goes to the log, keyed by request_id. The client
        # gets the id and nothing else.
        logger.exception(
            "unhandled exception request_id=%s", current_request_id(), exc_info=exc
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_body(
                "dependency_unavailable",
                "The service encountered an unexpected error.",
            ),
        )
