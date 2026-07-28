"""Application configuration.

Every tuning constant in the system lives here and nowhere else, and every one is
env-overridable. Two rules govern this file:

1.  Constants marked ``UNRESOLVED`` are placeholders, not decisions. They cannot be
    chosen without a corpus (see "Deliberately unresolved" in the vault index), so
    they are wired now and set in the tuning pass. Do not treat the current values
    as tuned, and do not quietly "improve" one because it looks arbitrary — it is
    arbitrary, deliberately, and changing it without measurement is guessing.

2.  ``rrf_max`` is *computed from configuration*, never from retrieved data. This
    is invariant I7. Normalising a fused score against the observed maximum of the
    current candidate set forces ``max(score) == 1.0`` on every query, which pins
    the G2 relevance blend above any sane floor and makes the abstention gate
    structurally incapable of firing. It looks perfectly reasonable in code, which
    is exactly why it needs to be stated here.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Per-role model ids for each provider. Verified against the live APIs rather
# than recalled — guessing ids from a training prior is exactly how this breaks.
# The 2.0-era Gemini ids these defaults originally held are dead:
# `gemini-2.5-flash` 404s ("model is not found") and `gemini-2.0-flash` returns
# 429 with no usable free-tier quota.
#
# ``vlm`` is None where the provider exposes no vision model. Groq's catalogue on
# this key is text-only, so Tier-2 page escalation is unavailable there and says
# so instead of failing per page.
MODELS_BY_PROVIDER: dict[str, dict[str, str | None]] = {
    # Confirmed 200 on 2026-07-28; 3.6-flash also confirmed for streaming and for
    # vision (it transcribed a rendered page image exactly), which is the whole
    # Tier-2 escalation path. Free tier is 20 requests/day on 3.6-flash, so the
    # mechanical roles deliberately sit on the separate flash-lite bucket.
    "gemini": {
        "route": "gemini-3.5-flash-lite",
        "rewrite": "gemini-3.5-flash-lite",
        "generate": "gemini-3.6-flash",
        "generate_fallback": "gemini-3.5-flash-lite",
        "verify": "gemini-3.5-flash-lite",
        "vlm": "gemini-3.6-flash",
    },
    # Confirmed 200 on 2026-07-28. Far more headroom than Gemini's free tier and
    # markedly faster — 70b-versatile answered in 0.19s against flash-lite's ~1s.
    # Generation stays on llama-3.3-70b-versatile because it is *not* a
    # reasoning model. gpt-oss-120b has more daily headroom and was tried, but it
    # spends the output budget on internal reasoning before emitting anything
    # and returned an empty answer at 2048 — a reasoning model behind a fixed
    # output cap fails as silence, which is the worst possible failure here.
    #
    # The cost is a 100k tokens/day ceiling, separate from and invisible to the
    # per-minute headers: six runs of a six-question diagnostic exhausted it in
    # an afternoon. A daily cap cannot be paced around, so if generation starts
    # failing with 429s while the minute budget looks healthy, that is what
    # happened. Mechanical roles sit on the 8b model — small prompts, own bucket.
    "groq": {
        "route": "llama-3.1-8b-instant",
        "rewrite": "llama-3.1-8b-instant",
        "generate": "llama-3.3-70b-versatile",
        "generate_fallback": "llama-3.1-8b-instant",
        "verify": "llama-3.1-8b-instant",
        "vlm": None,
    },
    "anthropic": {
        "route": "claude-haiku-4-5-20251001",
        "rewrite": "claude-haiku-4-5-20251001",
        "generate": "claude-sonnet-5",
        "generate_fallback": "claude-haiku-4-5-20251001",
        "verify": "claude-haiku-4-5-20251001",
        "vlm": "claude-sonnet-5",
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------------------------------------------------------- runtime
    app_env: Literal["local", "docker", "render"] = "local"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:3000"

    # ------------------------------------------------------------------- auth
    # dev returns a fixed subject so `docker compose up` works with no Clerk
    # account at all; clerk is the deployed path. There is no third mode, and
    # dev must never be reachable in a deployed environment.
    auth_mode: Literal["dev", "clerk"] = "dev"
    dev_user_id: str = "dev-user"
    clerk_secret_key: str = ""
    clerk_authorized_parties: str = ""

    # -------------------------------------------------------------- datastores
    database_url: str = (
        "postgresql+asyncpg://knowledgehub:knowledgehub@localhost:5432/knowledgehub"
    )
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "chunks"

    # Render's free Postgres ships no managed pooling and may restart without
    # notice, so the app owns its pool and survives a dropped connection.
    db_pool_size: int = 5
    db_max_overflow: int = 2

    # ------------------------------------------------------------------- LLM
    # Swapping providers is a config change, never a code change.
    #
    # Groq by default: Gemini's free tier is 20 requests/day on the generate
    # model, which is below what one demo conversation costs, and Groq's limits
    # are far higher and its responses faster. Gemini stays fully wired and is
    # the only configured provider with a vision model, so Tier-2 page
    # escalation needs LLM_PROVIDER=gemini.
    llm_provider: Literal["gemini", "anthropic", "groq"] = "groq"
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    groq_api_key: str = ""

    # Per-role model routing stays a config string. Latency-critical mechanical
    # roles get the fastest model; generation gets the strongest; judges run off
    # the request path and can be anything.
    #
    # Left empty, each resolves from MODELS_BY_PROVIDER for whichever provider is
    # selected. That is what makes "swapping providers is a config change, never
    # a code change" actually true: hardcoded Gemini ids meant `LLM_PROVIDER=groq`
    # would cheerfully post `gemini-3.6-flash` to Groq and fail on every call.
    # Setting any of these explicitly still wins, so a single role can be pinned
    # without taking over the rest.
    llm_model_route: str = ""
    llm_model_rewrite: str = ""
    llm_model_generate: str = ""
    # Used only when the primary is rate limited or out of quota. Free tiers
    # meter per *day* on the strongest model, so without this a demo simply
    # stops answering once that number is reached — a hard 503 where a visibly
    # degraded answer from a smaller model is obviously better (I1).
    llm_model_generate_fallback: str = ""
    llm_model_verify: str = ""
    # Empty means this provider has no vision model, and Tier-2 page escalation
    # skips with a visible degradation rather than posting an image to a
    # text-only endpoint (I1).
    llm_model_vlm: str = ""

    cohere_api_key: str = ""
    cohere_rerank_model: str = "rerank-v3.5"

    # ------------------------------------------------------- timeouts (§5)
    # Every external dependency has a timeout and a named fallback (I4). These
    # come straight from the contract's dependency table and are not tuning
    # constants — they encode which stages may degrade and which may not.
    timeout_qdrant_s: float = 3.0  # -> 503, retrieval cannot degrade
    timeout_cohere_s: float = 2.0  # -> fused order, fail open
    timeout_llm_route_s: float = 2.0  # -> assume `retrieve`, fail open
    timeout_llm_rewrite_s: float = 3.0  # -> raw query, fail open
    timeout_llm_generate_s: float = 30.0  # -> 503, cannot degrade
    timeout_llm_verify_s: float = 10.0  # -> verified: null, fail *unknown*
    timeout_postgres_s: float = 2.0  # -> one reconnect, then 503

    # ------------------------------------------------------ retrieval / fusion
    # `k` is ours to set. Qdrant publishes no default, and a threshold derived
    # from an unpublished default is a threshold that silently changes on a
    # version bump. Pinned here and passed explicitly on every query.
    rrf_k: int = 60

    # Whether Qdrant's fusion ranks from 0 or 1 decides whether RRF_MAX's
    # denominator is `k` or `k + 1`.
    #
    # SETTLED EMPIRICALLY 2026-07-28 against Qdrant v1.18.0: it ranks from **0**.
    # A chunk topping both branches with w=[1,1] and k=60 scores exactly
    # 0.03333333 = 2/60, not 2/61. The contract's written formula assumed
    # rank-from-1; the measurement disagrees, and the measurement wins.
    # Re-run `scripts/probe_rrf_rank_base.py` after any Qdrant version bump —
    # a silent change here leaves every FLOOR_FUSED comparison quietly wrong.
    rrf_rank_base: Literal[0, 1] = 0

    w_dense: float = Field(default=1.0, description="UNRESOLVED — needs corpus")
    w_sparse: float = Field(default=1.0, description="UNRESOLVED — needs corpus")

    retrieve_top_k: int = 40  # retrieve wide, compress late
    rerank_top_n: int = 5

    # A message can ask several distinct things at once ("Who is X? What are
    # their qualifications?"). Each gets its own retrieval, fused by the same
    # nested RRF used for raw-vs-rewritten. Capped because sub-queries multiply
    # Qdrant calls and every extra one buys less than the last.
    max_subqueries: int = 4

    # Ceiling on chunks handed to the model when a message was split.
    #
    # Passing the usual top-5 for a three-part question risks one part getting
    # no supporting chunk at all, so the budget scales with the number of
    # sub-queries — but only to a point. The binding constraint is *not* the
    # context window (1M tokens; twelve parent windows is under 2% of it) but
    # attention: past a dozen passages a model gets measurably worse at using
    # any of them. See "When More Documents Hurt RAG" (arXiv 2606.11350).
    max_context_chunks: int = 12

    # Output budget for the answer, shared by the streaming and non-streaming
    # generate paths — it was duplicated as a literal in both, which is how they
    # drift.
    #
    # Providers meter this as *reserved* output, so it is spent against the
    # per-minute allowance whether or not the answer uses it, and
    # `max_context_tokens` has to be chosen against the sum. Reasoning models
    # (Gemini 3.x, gpt-oss) also emit internal reasoning from this budget before
    # any visible text, so a value too close to the expected answer length shows
    # up as an answer that stops mid-sentence — which reads as a content failure
    # rather than a token limit. Raise it, and lower the context budget to match,
    # if answers are being cut off.
    max_answer_tokens: int = 2048

    # Ceiling on the DATA blocks, enforced separately from the chunk count. The
    # count cap is about model attention; this one is about the request being
    # accepted at all. Twelve parent windows at `parent_tokens` each is ~13k
    # tokens, and Groq's free tier rejects anything over 12k TPM with a 413 —
    # measured at 12,882 requested against a 12,000 limit, which failed precisely
    # the multi-part questions that needed the most context.
    #
    # Sized against the whole request rather than the prompt alone: providers
    # meter `max_answer_tokens` as *reserved* output, so one call costs roughly
    # context + answer + overhead against the per-minute allowance. At 6000 that
    # came to ~10.6k of 12k — room for barely one question a minute. 4000 brings
    # it to ~8.5k and restores usable headroom, at little cost in evidence since
    # the reranker puts the load-bearing passages first.
    max_context_tokens: int = 4000

    # Skip the reranker when fusion is already decisive. Cross-branch agreement
    # is the real signal: when dense and sparse independently rank the same chunk
    # first, a cross-encoder is unlikely to overturn it, so the call buys nothing
    # against a 1,000-call monthly trial.
    #
    # MEASURED 2026-07-28 (`scripts/probe_decisive_margin.py`, 53 questions over
    # the eval corpus). The placeholder 1.5 was borrowed from a scale that does
    # not exist here. RRF scores are `w/(k + rank)`, so a chunk ranked 0 in both
    # branches scores 2/60 = 0.03333 against a runner-up ranked 1 in both at
    # 2/61 = 0.03279 — a ratio of 1.017. The largest margin observed anywhere in
    # the corpus was 1.3033, so 1.5 sat above the metric's reachable ceiling and
    # fired zero times in 53 queries: every single query paid a Cohere call.
    # At 1.02, 24/53 queries skip (45% of the budget) while still requiring
    # top-3 agreement in both branches.
    decisive_ratio: float = 1.02

    # Two score sources, therefore two thresholds. Cohere relevance and normalised
    # RRF are different distributions; one shared floor across both is a bug.
    #
    # MEASURED 2026-07-28 (`evals.run --retrieval-only`, 34 answerable + 13
    # should-decline questions on the reranked path). The sweep is deliberately
    # flat: every floor from 0.15 to 0.45 lands within one question of the
    # optimum, so 0.35 is chosen as the middle of a plateau rather than a peak —
    # a knife-edge optimum on 47 questions would be a fit to this corpus, not a
    # threshold. Note the populations overlap heavily (answerable reaches down to
    # 0.064, should-decline up to 0.786): the floor is a backstop, and the
    # generator's grounding prompt is what actually refuses unanswerable
    # questions.
    floor_rerank: float = 0.35

    # STILL UNRESOLVED, and deliberately left so. Only 6 of 53 questions reached
    # the un-reranked path, all of them rerank *failures* rather than decisive
    # skips, and their scores do not separate at all: answerable landed in
    # 0.804–0.856 and the single should-decline question scored 0.852, inside
    # that range. One sample on the wrong side of the distribution is not a
    # calibration. Raising `decisive_ratio` above will start routing real traffic
    # here, which is what will finally produce enough samples to set it.
    floor_fused: float = Field(default=0.35, description="UNRESOLVED — needs corpus")

    # ------------------------------------------------------------- chunking
    child_tokens: int = Field(default=250, description="UNRESOLVED — needs corpus")
    parent_tokens: int = Field(default=1200, description="UNRESOLVED — needs corpus")
    embed_batch_size: int = Field(default=16, description="UNRESOLVED — measure RSS")
    embed_model: str = "BAAI/bge-small-en-v1.5"
    fastembed_cache_dir: str = ".fastembed_cache"

    # Accepted technique, shipped off. One LLM call per chunk at ingest is real
    # token spend and real latency at 0.1 vCPU; on by default would make every
    # upload slow to demo. Kept as an ablation row and a toggle.
    contextual_retrieval: bool = False

    # --------------------------------------------------------------- parsing
    max_upload_bytes: int = 20 * 1024 * 1024  # set against the 1 GB Postgres budget

    # The direct lever on Tier-2 token cost: a page image is a flat 258 tokens
    # when both dimensions are <= 384 px and tiles upward from there. Too low and
    # tables are unreadable; too high and one document eats the TPM minute.
    vlm_render_dpi: int = Field(default=150, description="UNRESOLVED — needs sample pages")
    # The TPM backstop. Hitting it emits a degradation (I1) — never a silent truncation.
    max_escalated_pages: int = Field(default=10, description="UNRESOLVED — needs corpus")

    # ---------------------------------------------------------------- memory
    recent_turns_n: int = Field(default=5, description="UNRESOLVED — needs corpus")

    # G1 is tuned loose on purpose: over-refusal on a benign query is the worse
    # failure, and G2's relevance floor catches what G1 lets through.
    route_threshold: float = Field(default=0.5, description="UNRESOLVED — needs corpus")

    # ------------------------------------------------------------- computed
    @model_validator(mode="after")
    def _resolve_models_for_provider(self) -> Settings:
        """Fill unset per-role models from the selected provider's table.

        Runs after env loading, so an explicit ``LLM_MODEL_GENERATE`` still wins
        and only the roles left blank get resolved. Without this, selecting a
        provider left the previous provider's model ids in place and every call
        failed on an id the endpoint had never heard of.
        """
        table = MODELS_BY_PROVIDER.get(self.llm_provider, {})
        for role in (
            "route",
            "rewrite",
            "generate",
            "generate_fallback",
            "verify",
            "vlm",
        ):
            field = f"llm_model_{role}"
            if not getattr(self, field):
                object.__setattr__(self, field, table.get(role) or "")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rrf_max(self) -> float:
        """The analytic ceiling of the weighted RRF score — never an observed max.

        A chunk ranked first in *both* branches scores
        ``(w_dense + w_sparse) / (k + rank_base)``. Because both terms are our own
        constants, the value is query-independent, which is the only reason
        ``floor_fused`` can mean the same thing on every query (I7).

        ``rank_base`` is measured, not assumed — see the field's comment. On
        Qdrant 1.18 it is 0, so the denominator is ``k``.

        A chunk that tops one branch and is absent from the other lands near half
        of this — real signal about one-sided evidence, and exactly the signal
        per-query renormalisation destroys.
        """
        return (self.w_dense + self.w_sparse) / (self.rrf_k + self.rrf_rank_base)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
