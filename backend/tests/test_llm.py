"""LLM adapter, limiters, cache, and Tier-2 escalation.

No network. Provider request shapes are asserted against captured bodies, because
the shapes differ in ways that fail loudly at runtime and silently in review —
Anthropic rejects `temperature` outright on current models, and takes `system` as
a top-level field rather than a message role.
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from app.config import Settings
from app.ingest.escalate import (
    blocks_from_escalation,
    escalate_document,
    estimate_image_tokens,
)
from app.ingest.parse import PageAssessment
from app.llm.cache import PromptCache, get_cache
from app.llm.client import (
    ImagePart,
    LLMClient,
    LLMError,
    LLMRateLimited,
    Message,
    TextPart,
    parse_json_tolerant,
)
from app.llm.limiter import CircuitBreaker, RateLimiter, TokenBudgetLimiter
from app.models.schemas import BlockType, DegradationReason, DegradationStage


def make_client(settings: Settings, handler) -> LLMClient:
    """An LLMClient wired to a mock transport that records request bodies."""
    client = LLMClient(settings)
    client._client = httpx.AsyncClient(
        base_url=client._base_url(), transport=httpx.MockTransport(handler)
    )
    return client


@pytest.fixture(autouse=True)
def _clear_cache():
    get_cache().clear()


# ------------------------------------------------------------- request shape


async def test_openai_compatible_shape_for_gemini() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        captured["_auth"] = request.headers.get("authorization")
        captured["_path"] = request.url.path
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "hello"}}]}
        )

    client = make_client(
        Settings(llm_provider="gemini", gemini_api_key="k"), handler
    )
    result = await client.complete(
        [Message(role="system", content="sys"), Message(role="user", content="hi")],
        model="gemini-2.0-flash",
    )

    assert result == "hello"
    assert captured["_path"].endswith("/chat/completions")
    assert captured["_auth"] == "Bearer k"
    # System stays a message in the OpenAI shape.
    assert captured["messages"][0] == {"role": "system", "content": "sys"}
    assert captured["temperature"] == 0.0


async def test_anthropic_lifts_system_out_of_messages() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        captured["_key"] = request.headers.get("x-api-key")
        captured["_version"] = request.headers.get("anthropic-version")
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": "hello"}]}
        )

    client = make_client(
        Settings(llm_provider="anthropic", anthropic_api_key="k"), handler
    )
    result = await client.complete(
        [Message(role="system", content="sys"), Message(role="user", content="hi")],
        model="claude-opus-5",
    )

    assert result == "hello"
    assert captured["_key"] == "k"
    assert captured["_version"] == "2023-06-01"
    # system is a top-level field, and must not remain in messages.
    assert captured["system"] == "sys"
    assert all(m["role"] != "system" for m in captured["messages"])


async def test_anthropic_omits_temperature_on_current_models() -> None:
    """Sending temperature to a current Claude model is a 400, not a no-op."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "x"}]})

    settings = Settings(llm_provider="anthropic", anthropic_api_key="k")

    await make_client(settings, handler).complete(
        [Message(role="user", content="hi")], model="claude-opus-5", temperature=0.7
    )
    assert "temperature" not in captured

    captured.clear()
    await make_client(settings, handler).complete(
        [Message(role="user", content="hi")], model="claude-haiku-4-5", temperature=0.7
    )
    assert captured["temperature"] == 0.7


# ----------------------------------------------------------------- images


async def test_image_part_uses_data_uri_on_openai_compatible() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = make_client(Settings(llm_provider="gemini", gemini_api_key="k"), handler)
    await client.complete(
        [
            Message(
                role="user",
                content=[TextPart("read this"), ImagePart("image/png", "QUJD")],
            )
        ]
    )

    blocks = captured["messages"][0]["content"]
    assert blocks[0] == {"type": "text", "text": "read this"}
    assert blocks[1]["image_url"]["url"] == "data:image/png;base64,QUJD"


async def test_image_part_uses_source_block_on_anthropic() -> None:
    """The one thing that makes Tier-2 escalation possible, in each dialect."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = make_client(
        Settings(llm_provider="anthropic", anthropic_api_key="k"), handler
    )
    await client.complete(
        [Message(role="user", content=[ImagePart("image/png", "QUJD")])],
        model="claude-opus-5",
    )

    block = captured["messages"][0]["content"][0]
    assert block == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
    }


# ---------------------------------------------------------------- streaming


async def test_streaming_yields_deltas_openai() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    client = make_client(Settings(llm_provider="gemini", gemini_api_key="k"), handler)
    chunks = [c async for c in client.stream([Message(role="user", content="hi")])]
    assert "".join(chunks) == "Hello"


async def test_streaming_yields_deltas_anthropic() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}\n\n'
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"lo"}}\n\n'
            'data: {"type":"message_stop"}\n\n'
        )
        return httpx.Response(200, text=body)

    client = make_client(
        Settings(llm_provider="anthropic", anthropic_api_key="k"), handler
    )
    chunks = [
        c
        async for c in client.stream(
            [Message(role="user", content="hi")], model="claude-opus-5"
        )
    ]
    assert "".join(chunks) == "Hello"


async def test_upstream_error_raises_llm_error() -> None:
    client = make_client(
        Settings(llm_provider="gemini", gemini_api_key="k"),
        lambda r: httpx.Response(500, text="upstream exploded"),
    )
    with pytest.raises(LLMError, match="500"):
        await client.complete([Message(role="user", content="hi")])


async def test_missing_api_key_is_a_clear_error() -> None:
    client = make_client(
        Settings(llm_provider="gemini", gemini_api_key=""),
        lambda r: httpx.Response(200, json={}),
    )
    with pytest.raises(LLMError, match="No API key"):
        await client.complete([Message(role="user", content="hi")])


# -------------------------------------------------------------- JSON parsing


@pytest.mark.parametrize(
    "raw",
    [
        '{"route": "retrieve"}',
        '```json\n{"route": "retrieve"}\n```',
        '```\n{"route": "retrieve"}\n```',
        'Here is the answer:\n{"route": "retrieve"}\nHope that helps.',
    ],
)
def test_tolerant_json_survives_the_ways_models_wrap_output(raw: str) -> None:
    """Every caller of complete_json is on a fail-open path — being strict here
    turns a formatting quirk into a lost routing decision."""
    assert parse_json_tolerant(raw) == {"route": "retrieve"}


def test_unparseable_json_raises() -> None:
    with pytest.raises(LLMError):
        parse_json_tolerant("no json here at all")


@pytest.mark.parametrize(
    "raw",
    [
        '["a", "b"]',
        '```json\n["a", "b"]\n```',
        'Sure:\n["a", "b"]',
    ],
)
def test_a_bare_array_is_wrapped_when_the_caller_names_its_key(raw: str) -> None:
    """Asked for ``{"queries": [...]}``, models routinely answer ``[...]``.

    That is a correct reading of the request carrying exactly the information
    wanted, but it parsed to a list, failed the dict check, and left the
    brace-scan with no ``{`` to find — so rewrite degraded to the raw query on
    every multi-part question and took query decomposition with it, silently.
    """
    assert parse_json_tolerant(raw, list_key="queries") == {"queries": ["a", "b"]}


def test_a_bare_array_still_raises_when_no_key_is_named() -> None:
    """Callers expecting an object must not silently receive a list under a
    guessed key."""
    with pytest.raises(LLMError):
        parse_json_tolerant('["a", "b"]')


# ------------------------------------------------------------------- cache


async def test_deterministic_calls_are_cached() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    client = make_client(Settings(llm_provider="gemini", gemini_api_key="k"), handler)
    msgs = [Message(role="user", content="same")]

    await client.complete(msgs, temperature=0.0)
    await client.complete(msgs, temperature=0.0)
    assert calls["n"] == 1, "identical temp-0 call should be served from cache"


async def test_nondeterministic_calls_are_not_cached() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    client = make_client(Settings(llm_provider="gemini", gemini_api_key="k"), handler)
    msgs = [Message(role="user", content="same")]

    await client.complete(msgs, temperature=0.7)
    await client.complete(msgs, temperature=0.7)
    assert calls["n"] == 2, "temperature > 0 asks for variation; cache would defeat it"


def test_cache_key_separates_models() -> None:
    """Serving one model's answer for another would make an ablation meaningless."""
    msgs = [{"role": "user", "content": "hi"}]
    a = PromptCache.key(model="flash", messages=msgs, temperature=0.0, max_tokens=10)
    b = PromptCache.key(model="pro", messages=msgs, temperature=0.0, max_tokens=10)
    assert a != b


def test_cache_evicts_least_recently_used() -> None:
    cache = PromptCache(max_entries=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # refresh a
    cache.put("c", 3)
    assert cache.get("a") == 1
    assert cache.get("b") is None


# ---------------------------------------------------------------- limiters


async def test_rate_limiter_admits_up_to_the_ceiling_without_waiting() -> None:
    limiter = RateLimiter(max_per_minute=3)
    for _ in range(3):
        assert await limiter.acquire() == 0.0


async def test_token_limiter_paces_on_tokens_not_calls() -> None:
    """Ten prose pages and ten scanned tables are ten calls either way and
    wildly different token loads — which is why this limiter exists."""
    limiter = TokenBudgetLimiter(max_tokens_per_minute=1000)
    assert await limiter.acquire(600) == 0.0
    assert await limiter.acquire(300) == 0.0
    assert limiter._in_window == 900


async def test_token_limiter_never_deadlocks_on_an_oversized_request() -> None:
    limiter = TokenBudgetLimiter(max_tokens_per_minute=100)
    assert await limiter.acquire(5000) == 0.0


def test_circuit_breaker_latches_and_reports_once() -> None:
    """402 means the monthly quota is gone — retrying spends 2s per query to
    re-learn a known fact."""
    breaker = CircuitBreaker("cohere")
    assert breaker.is_tripped is False
    assert breaker.trip("402 quota exhausted") is True
    assert breaker.trip("402 again") is False, "only the first trip is reported"
    assert breaker.is_tripped is True
    assert breaker.reason == "402 quota exhausted"


# ------------------------------------------------------------- image tokens


def test_small_images_are_a_flat_cost() -> None:
    assert estimate_image_tokens(384, 384) == 258
    assert estimate_image_tokens(100, 100) == 258


def test_large_images_tile_and_cost_more() -> None:
    """Why render DPI is a tuning constant and not an implementation detail."""
    small = estimate_image_tokens(384, 384)
    large = estimate_image_tokens(1700, 2200)
    assert large > small * 8


# ------------------------------------------------------------- escalation


class _FakeClient:
    def __init__(self, reply: str | None = "| a | b |\n| --- | --- |\n| 1 | 2 |"):
        self.reply = reply
        self.calls = 0

    async def complete(self, messages, **kwargs):
        self.calls += 1
        if self.reply is None:
            raise LLMError("model unavailable")
        return self.reply


@pytest.fixture
def pdf_bytes() -> bytes:
    pytest.importorskip("reportlab")
    import io as _io

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = _io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(6):
        c.drawString(72, 720, f"Page {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


async def test_escalation_cap_emits_a_visible_degradation(pdf_bytes: bytes) -> None:
    """I1: hitting the cap must be visible. Silently truncating would produce
    exactly the fails-convincingly outcome the design argues against."""
    assessments = [
        PageAssessment(page=i, reasons=["degenerate_table"]) for i in range(1, 7)
    ]
    settings = Settings(llm_provider="gemini", max_escalated_pages=2, vlm_render_dpi=72)
    fake = _FakeClient()

    recovered, degradations, attempted = await escalate_document(
        pdf_bytes, assessments, client=fake, settings=settings
    )

    assert attempted == 2, "cap must bound the work actually done"
    assert len(recovered) == 2
    cap = next(d for d in degradations if d.reason is DegradationReason.CAP_REACHED)
    assert cap.stage is DegradationStage.PARSE
    assert "4 page(s)" in cap.detail
    assert "3, 4, 5, 6" in cap.detail, "the user must learn *which* pages were skipped"


async def test_unflagged_pages_are_never_escalated(pdf_bytes: bytes) -> None:
    """The heuristic is the whole reason Tier 2 costs nothing extra."""
    assessments = [PageAssessment(page=i, reasons=[]) for i in range(1, 7)]
    fake = _FakeClient()

    recovered, degradations, attempted = await escalate_document(
        pdf_bytes,
        assessments,
        client=fake,
        settings=Settings(llm_provider="gemini", vlm_render_dpi=72),
    )

    assert attempted == 0
    assert fake.calls == 0
    assert recovered == {}
    assert degradations == []


async def test_a_failed_page_degrades_rather_than_failing_ingest(
    pdf_bytes: bytes,
) -> None:
    assessments = [PageAssessment(page=1, reasons=["sparse_text"])]
    fake = _FakeClient(reply=None)

    recovered, degradations, _ = await escalate_document(
        pdf_bytes,
        assessments,
        client=fake,
        settings=Settings(llm_provider="gemini", vlm_render_dpi=72),
    )

    assert recovered == {}
    assert degradations[0].reason is DegradationReason.UNAVAILABLE
    assert "page 1" in degradations[0].detail


async def test_escalation_sends_an_image_part(pdf_bytes: bytes) -> None:
    captured: list = []

    class Capturing(_FakeClient):
        async def complete(self, messages, **kwargs):
            captured.append(messages)
            return "text"

    await escalate_document(
        pdf_bytes,
        [PageAssessment(page=1, reasons=["large_figure"])],
        client=Capturing(),
        settings=Settings(llm_provider="gemini", vlm_render_dpi=72),
    )

    parts = captured[0][1].content
    image = next(p for p in parts if isinstance(p, ImagePart))
    assert image.mime == "image/png"
    assert base64.b64decode(image.data_b64)[:4] == b"\x89PNG"


# --------------------------------------------------------- derived semantics


def test_escalated_content_is_not_marked_derived() -> None:
    """Transcription is not synthesis.

    Marking a faithfully transcribed table as "AI-described" would put the badge
    on real document content and teach users to distrust the marker where it
    actually matters.
    """
    blocks = blocks_from_escalation(3, "Some prose.\n\n| a |\n| --- |\n| 1 |")
    assert all(b.is_derived is False for b in blocks)
    assert all(b.page == 3 for b in blocks)


def test_escalated_tables_come_back_atomic() -> None:
    blocks = blocks_from_escalation(1, "Intro line.\n| a | b |\n| --- | --- |\n| 1 | 2 |")
    tables = [b for b in blocks if b.block_type is BlockType.TABLE]
    assert len(tables) == 1
    assert "| 1 | 2 |" in tables[0].text


# ------------------------------------------------------- provider model routing


def test_models_follow_the_selected_provider() -> None:
    """"Swapping providers is a config change, never a code change" has to be
    true of the model ids too.

    They were hardcoded Gemini strings, so ``LLM_PROVIDER=groq`` posted
    `gemini-3.6-flash` to Groq and failed on every call.
    """
    groq = Settings(llm_provider="groq")
    assert groq.llm_model_generate == "llama-3.3-70b-versatile"
    assert groq.llm_model_route == "llama-3.1-8b-instant"

    gemini = Settings(llm_provider="gemini")
    assert gemini.llm_model_generate == "gemini-3.6-flash"


def test_an_explicit_model_still_wins() -> None:
    """Pinning one role must not take over the rest."""
    settings = Settings(llm_provider="groq", llm_model_generate="openai/gpt-oss-120b")
    assert settings.llm_model_generate == "openai/gpt-oss-120b"
    assert settings.llm_model_route == "llama-3.1-8b-instant"


def test_a_text_only_provider_reports_no_vision_model() -> None:
    """Empty is the signal Tier-2 escalation checks before rendering a page."""
    assert Settings(llm_provider="groq").llm_model_vlm == ""
    assert Settings(llm_provider="gemini").llm_model_vlm == "gemini-3.6-flash"


# ------------------------------------------------------------- rate limiting


async def test_a_429_is_retried_once_within_the_deadline() -> None:
    """Per-minute caps are routine on every free tier here, and the largest
    prompts trip them first — so the requests most worth completing are the ones
    that fail. One short wait recovers them instead of raising a 503."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, text="slow down")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = make_client(Settings(llm_provider="groq", groq_api_key="k"), handler)
    assert await client.complete([Message(role="user", content="hi")]) == "ok"
    assert calls["n"] == 2


async def test_a_persistent_429_raises_rate_limited_not_a_generic_error() -> None:
    """The distinct type is what lets a degradation record name the real cause."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "0"}, text="nope")

    client = make_client(Settings(llm_provider="groq", groq_api_key="k"), handler)
    with pytest.raises(LLMRateLimited):
        await client.complete([Message(role="user", content="hi")])
    assert calls["n"] == 2, "one retry, then give up"


async def test_a_429_is_not_retried_when_the_wait_exceeds_the_budget() -> None:
    """Sleeping past the caller's deadline just fails later and more
    expensively."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "25"}, text="wait")

    client = make_client(Settings(llm_provider="groq", groq_api_key="k"), handler)
    with pytest.raises(LLMRateLimited):
        await client.complete([Message(role="user", content="hi")], timeout=2.0)
    assert calls["n"] == 1, "must not sleep 25s inside a 2s budget"
