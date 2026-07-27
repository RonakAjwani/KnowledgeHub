"""Pacing limiters — two of them, because two ceilings are denominated differently.

The reference project has a single RPM limiter, and reusing it for both paths
would measure the wrong quantity on one of them:

* **Cohere rerank is bounded by requests** — 10 per minute on the trial key. Every
  call is roughly the same size (top-40 candidates), so counting calls is exactly
  right.
* **VLM page escalation is bounded by tokens.** Gemini publishes no separate image
  allowance; page images draw on the same TPM budget as text and drain it far
  faster. Ten prose pages and ten dense scanned tables are ten requests either
  way and wildly different token loads, so an RPM limiter would happily wave
  through a burst that blows the minute's budget.

Both are sliding windows rather than fixed buckets: a fixed window lets a caller
spend the whole allowance in the last second of one window and again in the first
second of the next, which is the burst the ceiling exists to prevent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60.0


class RateLimiter:
    """Requests per minute. Used for Cohere's 10 rpm trial ceiling."""

    def __init__(self, max_per_minute: int, name: str = "rpm") -> None:
        self.max_per_minute = max_per_minute
        self.name = name
        self._events: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Block until a slot is free. Returns how long it waited, in seconds."""
        waited = 0.0
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._events and now - self._events[0] >= _WINDOW_SECONDS:
                    self._events.popleft()

                if len(self._events) < self.max_per_minute:
                    self._events.append(now)
                    return waited

                sleep_for = _WINDOW_SECONDS - (now - self._events[0]) + 0.01
                logger.debug("%s limiter sleeping %.2fs", self.name, sleep_for)
                await asyncio.sleep(sleep_for)
                waited += sleep_for


class TokenBudgetLimiter:
    """Tokens per minute. Used for the VLM escalation queue.

    ``estimate`` is deliberately the caller's job: only the caller knows a page
    image's rendered dimensions, and image cost is a step function of those (a
    flat 258 tokens when both sides are within 384 px, tiled and climbing above).
    """

    def __init__(self, max_tokens_per_minute: int, name: str = "tpm") -> None:
        self.max_tokens_per_minute = max_tokens_per_minute
        self.name = name
        self._events: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    @property
    def _in_window(self) -> int:
        return sum(tokens for _, tokens in self._events)

    async def acquire(self, tokens: int) -> float:
        """Block until ``tokens`` fit in the current minute. Returns wait time."""
        waited = 0.0
        # A single request larger than the whole budget would wait forever;
        # let it through and let the upstream reject it rather than deadlocking.
        tokens = min(tokens, self.max_tokens_per_minute)

        async with self._lock:
            while True:
                now = time.monotonic()
                while self._events and now - self._events[0][0] >= _WINDOW_SECONDS:
                    self._events.popleft()

                if self._in_window + tokens <= self.max_tokens_per_minute:
                    self._events.append((now, tokens))
                    return waited

                sleep_for = _WINDOW_SECONDS - (now - self._events[0][0]) + 0.01
                logger.debug(
                    "%s limiter sleeping %.2fs (%d/%d tokens in window)",
                    self.name,
                    sleep_for,
                    self._in_window,
                    self.max_tokens_per_minute,
                )
                await asyncio.sleep(sleep_for)
                waited += sleep_for


class CircuitBreaker:
    """Latches open on a terminal upstream condition and stays there.

    Cohere returns 402 when the monthly quota is gone. That guarantees every
    subsequent call returns 402 too, so retrying spends the full 2 s timeout
    budget on every remaining query of the month to re-learn a known fact. One
    degradation is recorded when it trips; after that the fallback path is taken
    immediately.

    Deliberately has no half-open state and no reset timer: the condition it
    models is a monthly quota, not a flaky dependency.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._tripped = False
        self._reason: str | None = None

    @property
    def is_tripped(self) -> bool:
        return self._tripped

    @property
    def reason(self) -> str | None:
        return self._reason

    def trip(self, reason: str) -> bool:
        """Trip the breaker. Returns True the first time only."""
        if self._tripped:
            return False
        self._tripped = True
        self._reason = reason
        logger.warning("circuit breaker %s tripped: %s", self.name, reason)
        return True

    def reset(self) -> None:
        """Only for tests and process restart — never called on a live 402."""
        self._tripped = False
        self._reason = None
