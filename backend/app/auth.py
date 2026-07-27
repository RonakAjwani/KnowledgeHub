"""Clerk JWT verification, reduced to the one thing the rest of the app needs: ``user_id``.

Invariant I3 says ``user_id`` scopes everything — every Postgres query and every
Qdrant search carries it, and there is no unscoped read path. That invariant is
only as good as its single source of truth, so this module is that source: nothing
else in the codebase reads an ``Authorization`` header or a JWT claim.

Two modes:

``dev``
    Returns a fixed subject. This exists so ``docker compose up`` brings the whole
    stack up on a clean clone with no Clerk account, which is what makes Compose a
    credible durable deliverable. It is refused at startup outside a local
    environment — see :func:`assert_auth_mode_safe`.

``clerk``
    Real verification via ``clerk-backend-api``.

API notes for this SDK version (3.3.1), confirmed against the installed package
rather than remembered — the helpers moved out of ``clerk_backend_api.jwks_helpers``,
which no longer exists:

* ``authenticate_request(request, options) -> RequestState`` lives at the package
  root and takes anything satisfying the ``Requestish`` protocol, which requires
  only a ``.headers`` mapping. A Starlette ``Request`` satisfies it structurally,
  so no adapter is needed.
* It is **synchronous** and performs a JWKS fetch (internally cached), so it is
  offloaded to a worker thread rather than blocking the event loop.
* ``RequestState.payload['sub']`` is the user id under both v1 and v2 session
  tokens, so reading ``sub`` directly is version-agnostic and avoids ``to_auth()``,
  whose return type varies by token type.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.errors import Unauthenticated

logger = logging.getLogger(__name__)


def assert_auth_mode_safe(settings: Settings) -> None:
    """Refuse to start in a deployed environment with authentication disabled.

    ``AUTH_MODE=dev`` hands every caller the same ``user_id``, which would collapse
    every tenant into one. That is fine on a laptop and catastrophic anywhere else,
    and the failure is silent — the app works, it just serves everyone the same
    documents. Fail loudly at boot instead.
    """
    if settings.auth_mode == "dev" and settings.app_env not in ("local", "docker"):
        raise RuntimeError(
            f"AUTH_MODE=dev is not permitted with APP_ENV={settings.app_env}. "
            "Dev auth assigns every caller the same user_id, which would merge all "
            "tenants into one. Set AUTH_MODE=clerk and CLERK_SECRET_KEY."
        )
    if settings.auth_mode == "clerk" and not settings.clerk_secret_key:
        raise RuntimeError("AUTH_MODE=clerk requires CLERK_SECRET_KEY to be set.")


async def _verify_with_clerk(request: Request, settings: Settings) -> str:
    from clerk_backend_api import AuthenticateRequestOptions, authenticate_request

    parties = [
        p.strip() for p in settings.clerk_authorized_parties.split(",") if p.strip()
    ]
    options = AuthenticateRequestOptions(
        secret_key=settings.clerk_secret_key,
        authorized_parties=parties or None,
    )

    try:
        # Sync + network-bound; keep it off the event loop.
        state = await run_in_threadpool(authenticate_request, request, options)
    except Exception as exc:  # noqa: BLE001 — any verification fault is a 401, not a 503
        logger.warning("clerk verification raised: %s", exc)
        raise Unauthenticated("Could not verify the session token.") from exc

    if not state.is_signed_in:
        raise Unauthenticated(state.message or "Invalid or expired session token.")

    subject = (state.payload or {}).get("sub")
    if not subject:
        # A signed-in state with no subject would silently produce an unscoped
        # query, so treat it as an auth failure rather than defaulting.
        raise Unauthenticated("Session token carried no subject claim.")

    return str(subject)


async def get_user_id(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> str:
    """FastAPI dependency yielding the authenticated ``user_id``.

    Every route that touches user data depends on this. A route that does not is a
    route with no tenant scoping, which is an I3 violation by construction.
    """
    if settings.auth_mode == "dev":
        return settings.dev_user_id
    return await _verify_with_clerk(request, settings)


UserId = Annotated[str, Depends(get_user_id)]
