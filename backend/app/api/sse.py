"""SSE frame construction - the wire format both sides are built against.

Every frame carries a monotonic ``seq`` and an ISO-8601 ``ts`` alongside its
payload, so a client can order and dedup without trusting arrival order.

Two decisions here are deliberate and worth not re-litigating:

**Heartbeat every 15 s as an SSE comment.** Idle SSE connections die silently
through proxies. Without a keepalive the client cannot distinguish "the model is
still thinking" from "the connection dropped ten seconds ago", and both look like
a frozen UI. A comment frame is invisible to ``EventSource``-style parsers and
costs nothing.

**No resumability.** ``Last-Event-ID`` is not honoured. On disconnect the client
does not resume the turn - it fetches ``GET /messages/{id}`` for final state.
A half-resumed token stream is worse than a refetch: the client would have to
reconcile a partial answer against a replayed one, and the failure mode is a
duplicated or truncated paragraph that looks like a model error.

The client-side counterpart is *not* the browser's native ``EventSource``: that
is GET-only and cannot carry an ``Authorization`` header, so the frontend reads
these frames with ``fetch`` + ``ReadableStream``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 15.0

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # Nginx buffers proxied responses by default, which would hold every frame
    # until the stream ends - turning a token stream into one late blob.
    "X-Accel-Buffering": "no",
}


class EventStream:
    """Serialises events for one stream, owning its own ``seq`` counter."""

    def __init__(self) -> None:
        self._seq = 0

    def frame(self, event: str, payload: dict[str, Any]) -> str:
        self._seq += 1
        body = {
            "seq": self._seq,
            "ts": datetime.now(UTC).isoformat(),
            **payload,
        }
        # default=str so a stray enum or datetime in a payload degrades to a
        # readable string rather than killing the stream mid-turn.
        return f"event: {event}\ndata: {json.dumps(body, default=str)}\n\n"

    @staticmethod
    def heartbeat() -> str:
        return ": keepalive\n\n"


async def with_heartbeat(
    source: AsyncIterator[str], interval: float = HEARTBEAT_SECONDS
) -> AsyncIterator[str]:
    """Interleave keepalives into a stream that may go quiet.

    The generator is pulled through a task so a slow producer does not also
    block the heartbeat - which is the entire point, since the quiet periods are
    exactly when the client needs proof the connection is alive.
    """
    iterator = source.__aiter__()
    pending: asyncio.Task[str] | None = None

    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(_next(iterator))

            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                yield EventStream.heartbeat()
                continue

            try:
                yield pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
    finally:
        if pending is not None and not pending.done():
            pending.cancel()


async def _next(iterator: AsyncIterator[str]) -> str:
    return await iterator.__anext__()
