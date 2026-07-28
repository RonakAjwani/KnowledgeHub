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

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    llm_provider: Literal["gemini", "anthropic", "groq"] = "gemini"
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    groq_api_key: str = ""

    # Per-role model routing stays a config string. Latency-critical mechanical
    # roles get the fastest model; generation gets the strongest; judges run off
    # the request path and can be anything.
    #
    # Verified against the live API 2026-07-28, because guessing model IDs from a
    # training prior is exactly how this breaks: the 2.0-era ids these defaults
    # originally held are effectively dead — `gemini-2.5-flash` now 404s
    # ("model is not found"), and `gemini-2.0-flash` returns 429 with no usable
    # free-tier quota. The ids below all returned 200, and 3.6-flash was
    # confirmed for generation, streaming *and* vision (it transcribed a rendered
    # page image exactly), which is the whole Tier-2 escalation path.
    llm_model_route: str = "gemini-3.5-flash-lite"
    llm_model_rewrite: str = "gemini-3.5-flash-lite"
    llm_model_generate: str = "gemini-3.6-flash"
    llm_model_verify: str = "gemini-3.5-flash-lite"
    llm_model_vlm: str = "gemini-3.6-flash"

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

    # Skip the reranker when fusion is already decisive. Cross-branch agreement
    # is the real signal: when dense and sparse independently rank the same chunk
    # first, a cross-encoder is unlikely to overturn it, so the call buys nothing
    # against a 1,000-call monthly trial.
    decisive_ratio: float = Field(default=1.5, description="UNRESOLVED — needs corpus")

    # Two score sources, therefore two thresholds. Cohere relevance and normalised
    # RRF are different distributions; one shared floor across both is a bug.
    floor_rerank: float = Field(default=0.35, description="UNRESOLVED — needs corpus")
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
