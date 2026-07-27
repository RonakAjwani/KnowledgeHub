"""Prompt-hash cache for deterministic LLM calls.

Contract §9 draws the line by streaming, and the split falls out naturally —
every cacheable call in this system is a deterministic temperature-0 one:

===========================  ================================
Cached                       Not cached
===========================  ================================
route, rewrite, verify,      generate (streams)
VLM page parse, eval judges
===========================  ================================

The point is eval re-runs. A tuning pass sweeps thresholds over the same golden
set repeatedly; without a cache, every sweep re-pays for identical `route` and
`rewrite` calls that cannot have changed. With one, only the retrieval side
actually re-executes.

In-process and bounded rather than Redis-backed: the deployment is a single
process on 512 MB, a second network dependency for a latency optimisation would
be a poor trade, and the cache is pure performance — losing it on restart costs
nothing but time.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


class PromptCache:
    """Bounded LRU keyed on the full request shape.

    The key covers everything that could change the answer — model, temperature,
    max_tokens and the rendered messages. A cache keyed on the prompt alone
    would serve a `gemini-2.0-flash-lite` answer to a `gemini-2.0-flash` call and
    make an ablation between the two silently meaningless.
    """

    def __init__(self, max_entries: int = 512) -> None:
        self.max_entries = max_entries
        self._entries: OrderedDict[str, Any] = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        kind: str = "text",
    ) -> str:
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "kind": kind,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        if key in self._entries:
            self._entries.move_to_end(key)
            self.hits += 1
            return self._entries[key]
        self.misses += 1
        return None

    def put(self, key: str, value: Any) -> None:
        self._entries[key] = value
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()
        self.hits = 0
        self.misses = 0

    @property
    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "entries": len(self._entries),
            "hit_rate": (self.hits / total) if total else 0.0,
        }


_cache = PromptCache()


def get_cache() -> PromptCache:
    return _cache
