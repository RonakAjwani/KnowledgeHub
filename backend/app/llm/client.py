"""Provider-agnostic LLM adapter — contract §9.

The reference project's client is carried over in shape but not in substance: it
is text-only and synchronous, and two stages of this design quietly assume it is
neither.

* **``ImagePart`` is what makes Tier-2 VLM escalation free.** Page escalation is
  justified on "no new dependency, no GPU, no RAM — it goes through the existing
  LLM adapter", and that only holds if the message type can carry an image. A
  ``list[dict[str, str]]`` signature cannot express one.
* **``stream()`` is required by the SSE contract.** ``answer.delta`` has no other
  source.

Swapping providers is a config change, never a code change, so the provider
differences live here and nowhere else. Two families:

``openai_compat`` (Gemini, Groq)
    One shape for both. Gemini exposes an OpenAI-compatible endpoint, so a single
    code path covers the development provider and one of the two demo options.

``anthropic``
    The native Messages API, which differs in three ways that matter and are easy
    to get wrong: ``system`` is a **top-level parameter**, not a message role;
    images are ``{"type": "image", "source": {...}}`` rather than an
    ``image_url`` data URI; and **``temperature`` is rejected outright** on the
    current models (Opus 5 / 4.8 / 4.7 return a 400), so it is omitted rather
    than passed through.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import Settings, get_settings
from app.ingest.tokens import count_tokens
from app.llm.cache import get_cache
from app.llm.limiter import TokenBudgetLimiter

logger = logging.getLogger(__name__)

Role = Literal["system", "user", "assistant"]

# Anthropic rejects `temperature` on these; sending it is a 400 rather than a
# no-op, so the adapter drops it instead of hoping the caller knows.
_ANTHROPIC_NO_SAMPLING_PREFIXES = (
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-fable-5",
    "claude-sonnet-5",
)


class LLMError(Exception):
    """Any upstream failure. Callers map this to their stage's named fallback."""


class LLMTimeout(LLMError):
    pass


_MAX_RATE_LIMIT_RETRIES = 1
_DEFAULT_RATE_LIMIT_WAIT_S = 2.0


def _estimate_request_tokens(body: dict[str, Any], max_tokens: int) -> int:
    """What this request will cost against a per-minute allowance.

    Providers meter ``max_tokens`` as *reserved* output — it is spent whether or
    not the answer uses it — so the cost of a call is the prompt plus the full
    reservation, not the prompt plus the answer actually returned. Budgeting on
    the prompt alone under-counts by up to ``max_tokens`` per call, which is
    most of the request at these sizes.

    The heuristic counter is used deliberately: this is a pacing decision, and
    loading a tokenizer on the request path to refine an estimate that only has
    to be roughly right would cost more than the imprecision does.
    """
    text: list[str] = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            text.append(content)
        elif isinstance(content, list):
            text.extend(p.get("text", "") for p in content if isinstance(p, dict))
    system = body.get("system")
    if isinstance(system, str):
        text.append(system)
    return count_tokens("\n".join(text), exact=False) + max_tokens


def _retry_after_seconds(response: httpx.Response) -> float:
    """How long the provider says to wait, clamped to something sane.

    Both Gemini and Groq return the advice in ``Retry-After``; Gemini also
    repeats it in the body. Anything unparseable or absurd falls back to a short
    fixed wait — obeying a header that says 3600 would hang the request far past
    any caller's timeout.
    """
    raw = response.headers.get("retry-after", "")
    try:
        wait = float(raw)
    except ValueError:
        return _DEFAULT_RATE_LIMIT_WAIT_S
    return min(max(wait, 0.0), 30.0)


class LLMRateLimited(LLMError):
    """429 — the provider's quota or per-minute cap, not a malformed response.

    Distinguished so a degradation record names the real cause. Gemini's free
    tier is a per-*day* request cap on some models, so this is a routine
    condition during development rather than an exotic one, and a stream of
    degradations blaming "parse_error" or "timeout" for it sends whoever reads
    them to change the one thing that cannot help.
    """


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    mime: str
    data_b64: str


ContentPart = TextPart | ImagePart


@dataclass(frozen=True)
class Message:
    role: Role
    content: str | Sequence[ContentPart]


# --------------------------------------------------------------- serialisation


def _parts(content: str | Sequence[ContentPart]) -> list[ContentPart]:
    return [TextPart(content)] if isinstance(content, str) else list(content)


def _to_openai(messages: Sequence[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if isinstance(msg.content, str):
            out.append({"role": msg.role, "content": msg.content})
            continue
        blocks: list[dict[str, Any]] = []
        for part in msg.content:
            if isinstance(part, TextPart):
                blocks.append({"type": "text", "text": part.text})
            else:
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{part.mime};base64,{part.data_b64}"
                        },
                    }
                )
        out.append({"role": msg.role, "content": blocks})
    return out


def _to_anthropic(
    messages: Sequence[Message],
) -> tuple[str | None, list[dict[str, Any]]]:
    """Split the system prompt out — Anthropic takes it as a top-level field."""
    system_chunks: list[str] = []
    out: list[dict[str, Any]] = []

    for msg in messages:
        if msg.role == "system":
            system_chunks.extend(
                p.text for p in _parts(msg.content) if isinstance(p, TextPart)
            )
            continue

        blocks: list[dict[str, Any]] = []
        for part in _parts(msg.content):
            if isinstance(part, TextPart):
                blocks.append({"type": "text", "text": part.text})
            else:
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": part.mime,
                            "data": part.data_b64,
                        },
                    }
                )
        out.append({"role": msg.role, "content": blocks})

    return ("\n\n".join(system_chunks) or None), out


# ------------------------------------------------------------- tolerant JSON

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_tolerant(text: str, *, list_key: str | None = None) -> dict[str, Any]:
    """Extract a JSON object from a model response.

    Models wrap JSON in prose or code fences even under explicit instruction not
    to. Every caller of ``complete_json`` is on a fail-open path, so a parse
    failure means that stage degrades — being strict here converts a cosmetic
    formatting quirk into a lost rewrite or a lost route decision.

    ``list_key`` names the field a **bare top-level array** should be wrapped
    into. Asked for ``{"queries": [...]}``, models routinely answer with just
    ``[...]`` — which is a correct reading of the request and carries exactly the
    information wanted, but parsed to a list, failed the dict check, and left the
    brace-scan with no ``{`` to find. Rewrite then degraded to the raw query on
    every multi-part question, silently taking query decomposition with it. When
    the caller knows which key a list belongs under, that is a formatting quirk
    rather than a failure.
    """
    text = text.strip()
    if not text:
        raise LLMError("empty response")

    for candidate in (text, *(m.strip() for m in _FENCE_RE.findall(text))):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if list_key is not None and isinstance(parsed, list):
            return {list_key: parsed}

    # Last resort: the outermost balanced span, for responses that bury the JSON
    # in prose. Brackets are scanned as well as braces so a narrated bare array
    # ("Sure:\n[...]") reaches the same wrapping path as a fenced one.
    delimiters = [("{", "}")] + ([("[", "]")] if list_key is not None else [])
    for opener, closer in delimiters:
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        for i, ch in enumerate(text[start:], start):
            depth += ch == opener
            depth -= ch == closer
            if depth == 0:
                try:
                    parsed = json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    break
                if isinstance(parsed, dict):
                    return parsed
                if list_key is not None and isinstance(parsed, list):
                    return {list_key: parsed}
                break

    raise LLMError(f"could not parse JSON from response: {text[:200]!r}")


# ------------------------------------------------------------------- client


class LLMClient:
    """One client, three providers, one interface."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None
        # One budget per model, because the caps are per model and so are the
        # buckets. Sharing a limiter across models would pace the generation
        # model against the route model's spend and vice versa.
        self._tpm: dict[str, TokenBudgetLimiter] = {}

    async def _pace(self, model: str, body: dict[str, Any], max_tokens: int) -> None:
        """Wait until this request fits inside the model's per-minute budget.

        MEASURED: without this, a back-to-back run of the 22-question eval
        completed **2 of 22** — every other question died as
        ``DependencyUnavailable``. Generation reserves ~6k tokens against a
        12,000 TPM cap, so two calls exhaust the minute; the third 429s and
        falls back to the smaller model, which route and rewrite have already
        drained below *its* 6,000 cap, so the safety net fails at exactly the
        moment it is needed and the turn returns a 503.

        Waiting is the correct behaviour rather than a workaround: the request
        is going to be admissible in a few seconds, and a bounded wait is a
        better answer than an error the caller cannot act on. It is also not a
        degradation — nothing was lost, so no `Degradation` is recorded (I1
        governs fallbacks, not pacing).

        Unmetered models are not paced. Inventing a ceiling for a provider whose
        limits nobody measured would be guessing dressed as a safeguard.
        """
        budget = self.settings.tpm_limits.get(model, 0)
        if budget <= 0:
            return
        limiter = self._tpm.get(model)
        if limiter is None:
            limiter = self._tpm[model] = TokenBudgetLimiter(budget, name=model)
        waited = await limiter.acquire(_estimate_request_tokens(body, max_tokens))
        if waited:
            logger.info("paced %s for %.1fs to stay inside %d TPM", model, waited, budget)

    # -- provider wiring ---------------------------------------------------

    @property
    def provider(self) -> str:
        return self.settings.llm_provider

    @property
    def _family(self) -> Literal["openai_compat", "anthropic"]:
        return "anthropic" if self.provider == "anthropic" else "openai_compat"

    def _base_url(self) -> str:
        return {
            "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
            "groq": "https://api.groq.com/openai/v1",
            "anthropic": "https://api.anthropic.com/v1",
        }[self.provider]

    def _api_key(self) -> str:
        key = {
            "gemini": self.settings.gemini_api_key,
            "groq": self.settings.groq_api_key,
            "anthropic": self.settings.anthropic_api_key,
        }[self.provider]
        if not key:
            raise LLMError(
                f"No API key configured for provider '{self.provider}'. "
                f"Set {self.provider.upper()}_API_KEY."
            )
        return key

    def _headers(self) -> dict[str, str]:
        if self._family == "anthropic":
            return {
                "x-api-key": self._api_key(),
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self._api_key()}",
            "content-type": "application/json",
        }

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url())
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- request building --------------------------------------------------

    def _body(
        self,
        messages: Sequence[Message],
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        stream: bool,
    ) -> tuple[str, dict[str, Any]]:
        if self._family == "anthropic":
            system, msgs = _to_anthropic(messages)
            body: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": msgs,
            }
            if system:
                body["system"] = system
            # Sending temperature to a current Claude model is a 400, not a
            # no-op — the parameter was removed, not deprecated.
            if not model.startswith(_ANTHROPIC_NO_SAMPLING_PREFIXES):
                body["temperature"] = temperature
            if stream:
                body["stream"] = True
            return "/messages", body

        body = {
            "model": model,
            "messages": _to_openai(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if stream:
            body["stream"] = True
        return "/chat/completions", body

    @staticmethod
    def _extract_text(family: str, payload: dict[str, Any]) -> str:
        if family == "anthropic":
            return "".join(
                block.get("text", "")
                for block in payload.get("content", [])
                if block.get("type") == "text"
            )
        choices = payload.get("choices") or []
        if not choices:
            return ""
        return choices[0].get("message", {}).get("content") or ""

    # -- public interface --------------------------------------------------

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        timeout: float = 30.0,
        use_cache: bool = True,
    ) -> str:
        model = model or self.settings.llm_model_generate
        path, body = self._body(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )

        cache = get_cache()
        # Only deterministic calls are cacheable — a temperature above zero is a
        # request for variation, and serving it from cache defeats the point.
        cacheable = use_cache and temperature == 0.0
        cache_key = ""
        if cacheable:
            cache_key = cache.key(
                model=model,
                messages=body.get("messages", []),
                temperature=temperature,
                max_tokens=max_tokens,
            )
            hit = cache.get(cache_key)
            if hit is not None:
                return str(hit)

        # After the cache check: a hit costs the provider nothing and must not
        # consume budget. Before the retry loop: a retry is the same request,
        # already accounted for.
        await self._pace(model, body, max_tokens)

        deadline = time.monotonic() + timeout
        attempt = 0
        while True:
            try:
                response = await self._http().post(
                    path,
                    json=body,
                    headers=self._headers(),
                    timeout=max(0.1, deadline - time.monotonic()),
                )
            except httpx.TimeoutException as exc:
                raise LLMTimeout(f"{self.provider} timed out after {timeout}s") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"{self.provider} request failed: {exc}") from exc

            if response.status_code == 429:
                # Per-minute token and request caps are a routine condition on
                # every free tier here, and the largest prompts trip them first —
                # so the requests most worth completing are the ones that fail.
                # One short wait inside the caller's own deadline recovers them
                # without turning a transient cap into a 503. A cap whose advised
                # wait does not fit the budget is not retried: sleeping past the
                # deadline just fails later and more expensively.
                wait = _retry_after_seconds(response)
                remaining = deadline - time.monotonic()
                if attempt < _MAX_RATE_LIMIT_RETRIES and wait < remaining:
                    logger.warning(
                        "%s rate limited; retrying in %.1fs", self.provider, wait
                    )
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue
                raise LLMRateLimited(
                    f"{self.provider} returned 429: {response.text[:300]}"
                )

            if response.status_code >= 400:
                raise LLMError(
                    f"{self.provider} returned {response.status_code}: "
                    f"{response.text[:300]}"
                )
            break

        text = self._extract_text(self._family, response.json())
        if cacheable:
            cache.put(cache_key, text)
        return text

    async def complete_json(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        timeout: float = 30.0,
        list_key: str | None = None,
    ) -> dict[str, Any]:
        """``complete`` plus a tolerant parse. Raises ``LLMError`` if unparseable.

        ``list_key`` is forwarded to :func:`parse_json_tolerant` for callers whose
        schema is a single named list.
        """
        return parse_json_tolerant(
            await self.complete(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            ),
            list_key=list_key,
        )

    async def stream(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 30.0,
    ) -> AsyncIterator[str]:
        """Yield text deltas. Never cached — see the module docstring."""
        model = model or self.settings.llm_model_generate
        path, body = self._body(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        family = self._family
        await self._pace(model, body, max_tokens)
        deadline = time.monotonic() + timeout
        attempt = 0

        # The same bounded 429 retry as `complete`, and this is the path that
        # actually matters: every chat turn streams, so protecting only the
        # non-streaming path left the product's one hot route unguarded while
        # the tests looked green. Safe to retry because a 429 is known from the
        # response status before any delta has been yielded — once text has
        # reached the caller, re-requesting would duplicate it.
        while True:
            try:
                async with self._http().stream(
                    "POST",
                    path,
                    json=body,
                    headers=self._headers(),
                    timeout=max(0.1, deadline - time.monotonic()),
                ) as response:
                    if response.status_code == 429:
                        detail = (await response.aread()).decode(errors="replace")
                        wait = _retry_after_seconds(response)
                        remaining = deadline - time.monotonic()
                        if attempt < _MAX_RATE_LIMIT_RETRIES and wait < remaining:
                            logger.warning(
                                "%s stream rate limited; retrying in %.1fs",
                                self.provider,
                                wait,
                            )
                            await asyncio.sleep(wait)
                            attempt += 1
                            continue
                        raise LLMRateLimited(
                            f"{self.provider} returned 429: {detail[:300]}"
                        )

                    if response.status_code >= 400:
                        detail = (await response.aread()).decode(errors="replace")
                        raise LLMError(
                            f"{self.provider} returned {response.status_code}: "
                            f"{detail[:300]}"
                        )

                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            event = json.loads(data)
                        except json.JSONDecodeError:
                            continue

                        delta = _stream_delta(family, event)
                        if delta:
                            yield delta
                    return
            except httpx.TimeoutException as exc:
                raise LLMTimeout(f"{self.provider} stream timed out") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"{self.provider} stream failed: {exc}") from exc


def _stream_delta(family: str, event: dict[str, Any]) -> str:
    if family == "anthropic":
        if event.get("type") == "content_block_delta":
            return event.get("delta", {}).get("text", "")
        return ""
    choices = event.get("choices") or []
    if not choices:
        return ""
    return choices[0].get("delta", {}).get("content") or ""


_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


async def close_llm_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
