"""Phase 0 acceptance: the error envelope, request ids, auth modes, and RRF_MAX.

These are contract guarantees, not smoke tests. Each one guards something that is
easy to break with a reasonable-looking change:

* the envelope shape is what every client error path is parsed against;
* ``rrf_max`` being config-derived is invariant I7, and the test asserts it does
  not move when candidate scores would;
* dev auth reaching a deployed environment would merge every tenant into one.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import (
    DependencyUnavailable,
    DocumentNotReady,
    Forbidden,
    RateLimited,
    RequestIDMiddleware,
    register_exception_handlers,
)
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


# --------------------------------------------------------------------- envelope


@pytest.fixture
def error_app() -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)

    @app.get("/forbidden")
    async def _forbidden() -> None:
        raise Forbidden("Document is not yours.", {"document_id": "abc"})

    @app.get("/not-ready")
    async def _not_ready() -> None:
        raise DocumentNotReady("Still embedding.")

    @app.get("/limited")
    async def _limited() -> None:
        raise RateLimited("Slow down.", retry_after=7)

    @app.get("/dead")
    async def _dead() -> None:
        raise DependencyUnavailable("qdrant")

    @app.get("/boom")
    async def _boom() -> None:
        raise ValueError("this traceback must never reach a client")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/forbidden", 403, "forbidden"),
        ("/not-ready", 422, "document_not_ready"),
        ("/limited", 429, "rate_limited"),
        ("/dead", 503, "dependency_unavailable"),
    ],
)
def test_error_taxonomy_status_and_code(error_app, path, status, code) -> None:
    resp = error_app.get(path)
    assert resp.status_code == status
    body = resp.json()
    assert set(body["error"]) == {"code", "message", "detail", "request_id"}
    assert body["error"]["code"] == code
    assert body["error"]["request_id"]


def test_rate_limited_sets_retry_after(error_app) -> None:
    resp = error_app.get("/limited")
    assert resp.headers["Retry-After"] == "7"


def test_dependency_unavailable_names_the_dependency(error_app) -> None:
    # "Something is down" is not actionable; the contract requires naming which.
    assert error_app.get("/dead").json()["error"]["detail"]["dependency"] == "qdrant"


def test_unhandled_exception_becomes_503_without_a_traceback(error_app) -> None:
    """No 500 with a stack trace ever reaches the client."""
    resp = error_app.get("/boom")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "dependency_unavailable"
    assert "traceback" not in resp.text.lower()
    assert "ValueError" not in resp.text
    # The id is the operator's way back to the suppressed traceback.
    assert body["error"]["request_id"]


def test_validation_failure_is_400_not_422(error_app) -> None:
    """422 is reserved for document_not_ready, so validation must remap to 400.

    Left at FastAPI's default, "your JSON is malformed" and "your document is
    still embedding" would be indistinguishable to a client.
    """
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/typed")
    async def _typed(n: int) -> dict[str, int]:
        return {"n": n}

    resp = TestClient(app).get("/typed", params={"n": "not-an-int"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


# ------------------------------------------------------------------ request id


def test_request_id_is_returned_and_echoes_inbound(client: TestClient) -> None:
    assert client.get("/healthz").headers["X-Request-ID"]
    resp = client.get("/healthz", headers={"X-Request-ID": "trace-me"})
    assert resp.headers["X-Request-ID"] == "trace-me"


# ------------------------------------------------------------------ invariant I7


def test_rrf_max_is_analytic_and_config_derived() -> None:
    """I7: normalise against a fixed, analytically known maximum.

    A chunk ranked #1 in both branches scores (w_dense + w_sparse) / (k + base).
    The value must depend only on configuration - never on retrieved scores.

    ``rank_base`` defaults to 0: measured against Qdrant 1.18, not assumed. See
    ``scripts/probe_rrf_rank_base.py``.
    """
    assert Settings(w_dense=1.0, w_sparse=1.0, rrf_k=60).rrf_max == pytest.approx(2.0 / 60.0)

    # Weights move it; candidate data cannot, because none is an input.
    assert Settings(w_dense=0.7, w_sparse=1.3, rrf_k=60).rrf_max == pytest.approx(2.0 / 60.0)
    assert Settings(w_dense=1.0, w_sparse=1.0, rrf_k=10).rrf_max == pytest.approx(2.0 / 10.0)


def test_rrf_rank_base_switches_the_denominator() -> None:
    """Whether Qdrant ranks from 0 or 1 is settled empirically in the tuning pass.

    Wiring it as config rather than a literal is what lets that answer land
    without a code change.
    """
    assert Settings(rrf_k=60, rrf_rank_base=0).rrf_max == pytest.approx(2.0 / 60.0)
    assert Settings(rrf_k=60, rrf_rank_base=1).rrf_max == pytest.approx(2.0 / 61.0)


def test_two_floors_exist_separately() -> None:
    """One shared floor across both score sources is a bug: different distributions."""
    s = Settings(floor_rerank=0.42, floor_fused=0.17)
    assert s.floor_rerank != s.floor_fused


# ----------------------------------------------------------------------- auth


def test_dev_auth_is_refused_outside_local_environments() -> None:
    from app.auth import assert_auth_mode_safe

    assert_auth_mode_safe(Settings(auth_mode="dev", app_env="local"))
    assert_auth_mode_safe(Settings(auth_mode="dev", app_env="docker"))

    with pytest.raises(RuntimeError, match="not permitted"):
        assert_auth_mode_safe(Settings(auth_mode="dev", app_env="azure"))


def test_clerk_mode_requires_a_secret_key() -> None:
    from app.auth import assert_auth_mode_safe

    with pytest.raises(RuntimeError, match="CLERK_SECRET_KEY"):
        assert_auth_mode_safe(
            Settings(auth_mode="clerk", app_env="azure", clerk_secret_key="")
        )


async def test_dev_mode_yields_the_fixed_subject() -> None:
    from app.auth import get_user_id

    settings = Settings(auth_mode="dev", dev_user_id="dev-user")
    assert await get_user_id(request=None, settings=settings) == "dev-user"  # type: ignore[arg-type]
