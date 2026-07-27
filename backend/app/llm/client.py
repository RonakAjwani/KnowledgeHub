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

import json
import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import Settings, get_settings
from app.llm.cache import get_cache

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


def parse_json_tolerant(text: str) -> dict[str, Any]:
    """Extract a JSON object from a model response.

    Models wrap JSON in prose or code fences even under explicit instruction not
    to. Every caller of ``complete_json`` is on a fail-open path, so a parse
    failure means that stage degrades — being strict here converts a cosmetic
    formatting quirk into a lost rewrite or a lost route decision.
    """
    text = text.strip()
    if not text:
        raise LLMError("empty response")

    for candidate in (text, *(m.strip() for m in _FENCE_RE.findall(text))):
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    # Last resort: the outermost brace-balanced span.
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            depth += ch == "{"
            depth -= ch == "}"
            if depth == 0:
                try:
                    parsed = json.loads(text[start : i + 1])
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    break

    raise LLMError(f"could not parse JSON from response: {text[:200]!r}")


# ------------------------------------------------------------------- client


class LLMClient:
    """One client, three providers, one interface."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._client: httpx.AsyncClient | None = None

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

        try:
            response = await self._http().post(
                path, json=body, headers=self._headers(), timeout=timeout
            )
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"{self.provider} timed out after {timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"{self.provider} request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMError(
                f"{self.provider} returned {response.status_code}: {response.text[:300]}"
            )

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
    ) -> dict[str, Any]:
        """``complete`` plus a tolerant parse. Raises ``LLMError`` if unparseable."""
        return parse_json_tolerant(
            await self.complete(
                messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
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

        try:
            async with self._http().stream(
                "POST", path, json=body, headers=self._headers(), timeout=timeout
            ) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode(errors="replace")
                    raise LLMError(
                        f"{self.provider} returned {response.status_code}: {detail[:300]}"
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
