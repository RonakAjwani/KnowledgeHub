"""Engine, pool, and the one-reconnect rule.

Managed Postgres free tiers (this one is Azure Database for PostgreSQL Flexible
Server) ship no guaranteed connection pooling and may restart without notice, so
a dropped connection is an *expected event* rather than an outage. The
contract's §5 response is precise about the shape of the retry, and the
precision is the point:

* retry **once**, not with a backoff ladder - a second failure means the database
  is genuinely down, and spending the request's whole timeout budget rediscovering
  that helps nobody;
* retry **only on connection-level errors** - never on a query that failed on its
  merits. Re-running a statement that raised an integrity error just raises it
  again, and re-running one with side effects is worse than useless.

After the retry, a failure is a 503 that names Postgres. Postgres cannot degrade:
there is no answer to serve without it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.errors import DependencyUnavailable

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            # Recycle below any sensible idle timeout so we hand out connections
            # the server has not already closed underneath us.
            pool_recycle=300,
            # Cheap liveness check on checkout. This is what turns most restarts
            # into a transparent reconnect instead of a failed request.
            pool_pre_ping=True,
            echo=False,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def is_connection_error(exc: BaseException) -> bool:
    """True only for faults where reconnecting could plausibly help.

    ``DBAPIError.connection_invalidated`` is SQLAlchemy's own verdict that the
    connection - not the statement - is what went wrong, which is exactly the
    distinction the contract draws.
    """
    if isinstance(exc, InterfaceError):
        return True
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    if isinstance(exc, OperationalError):
        text = str(exc).lower()
        return any(
            marker in text
            for marker in (
                "server closed the connection",
                "connection was closed",
                "connection is closed",
                "terminating connection",
                "cannot connect",
                "connection refused",
                "connection reset",
            )
        )
    return False


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session, mapping DB faults to a named 503."""
    maker = get_sessionmaker()
    try:
        async with maker() as session:
            yield session
    except Exception as exc:
        if is_connection_error(exc):
            logger.warning("postgres connection error: %s", exc)
            raise DependencyUnavailable("postgres", "Database connection failed.") from exc
        raise
