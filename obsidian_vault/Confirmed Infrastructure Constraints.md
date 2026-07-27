# Confirmed Infrastructure Constraints

Verified by Ronak, 2026-07-27. These are facts, not assumptions — design against them directly. Supersedes the guesses in earlier drafts.

## Hosting

| | |
|---|---|
| **Backend** | Render free/Hobby. **512 MB RAM · 0.1 vCPU.** Spins down after 15 min idle, ~60 s cold start. |
| **Frontend** | Vercel free. |
| **Postgres** | Render free, 1 GB. **Expires 30 days after creation**, then a **14-day grace period** to upgrade, after which Render **deletes the database and all its data**. One active free database per workspace. No backups, no managed connection pooling, and Render may restart it without notice. |
| **Railway** | ❌ **Do not use.** Its "free tier" is a one-time $5 / 30-day usage credit, not an ongoing tier. Not designable-against. |
| **Docker Compose** | Committed local-dev **and** deployment-fallback path. Must stay in parity with the deployed setup — do not let it drift. |

**512 MB / 0.1 vCPU is the single most binding constraint in the project.** Every in-process component must fit under it, alongside FastAPI itself.

### Three Render facts verified 2026-07-27 that change the plan

**1 · 750 free instance hours per calendar month, per workspace — and the uptime pinger nearly consumes them alone.** A month is 720–744 hours, so **one** always-on service fits with only a few hours of slack, and a second free service does not fit at all. Overrunning is not a soft limit: Render **suspends every free web service until the month ends.** For a project whose live link is a graded deliverable, that is the worst available failure mode.

Two consequences, both now architectural rather than preferential:

- **The cold-start pinger must be scoped, not continuous.** Pinging 24/7 to dodge the 60 s cold start burns ~730 h of a 750 h budget. Run it during review and demo windows and accept cold starts outside them — a 60 s spin-up is survivable; a month-long suspension is not.
- **The ingest worker runs in-process.** No separate Render service for async ingest — there is no instance-hour budget for one. Background tasks live inside the same FastAPI process, which means ingest work shares the same 512 MB ceiling as the API. This compounds the memory constraint rather than sitting beside it.

**2 · The filesystem is ephemeral.** "Whenever a service spins down, any changes to its local filesystem are lost" — and spin-down happens after 15 idle minutes. **Local disk cannot hold uploaded originals.** This directly affects `Document.blob_ref` in [[Retrieval Pipeline Contract]] §2; resolved there.

**3 · Postgres expiry is a deletion, not a downgrade.** The database is destroyed 44 days after creation unless upgraded. Not renewable. Three responses: **create it as late as possible** relative to submission, **state the expiry date plainly in the README** next to the live link, and treat **Docker Compose as the real durable deliverable** — which is exactly the fallback the brief allows and this vault already commits to. The absence of managed pooling and the "may restart without notice" caveat also mean the app owns its connection pool and must survive a dropped connection rather than surfacing it as a 503 on first failure.

## Qdrant

- Cloud free tier: **1 GB RAM + 4 GB disk** (two separate limits), 0.5 vCPU, single node, no card.
- **Sparse vectors and payload filtering are both fully available on the free tier** — not paywalled. Hybrid + per-document scoping are unblocked.
- ✅ **Use native weighted RRF (Qdrant 1.14+; current release is 1.18):** pass a `weights` array on the `FusionQuery`/`rrf` object, one weight per prefetch branch, in a single server-side call.
- ✅ **`k` is settable too.** The `rrf` object exposes `k` (integer, nullable — "K parameter for reciprocal rank fusion") alongside `weights`. **Pin it explicitly in config; never rely on the server default** (which the API reference does not publish). Contract §4 `grade` derives `RRF_MAX` from `k` and the branch weights, so an unstated default would put a guessed constant underneath a live threshold.
- ❌ **Do not implement client-side fusion** *within* a query. This supersedes [[NotebookRAG Reference Project]]'s `fusion.py`, whose "client-side keeps weights adjustable" rationale is now obsolete — the server supports weights. Rank merging *across* separately executed queries is a different operation and stays client-side; see [[Retrieval Pipeline Contract]] §4 `retrieve`.

## Embedding model — decision revised

✅ **`BAAI/bge-small-en-v1.5`** via fastembed (quantized ONNX, fastembed's default).

This **overrules the earlier `bge-base` recommendation.** Quantized `bge-base` is ~105 MB on disk but leaves too little headroom on a 512 MB host — only viable if empirically tested under real load.

❌ **`bge-m3` cannot be used in-process.** fastembed ships no quantized variant; only full fp32 ONNX at ~2.27 GB. Would require a separate, adequately-resourced embedding service.

## LLM providers

- **Development + testing:** Gemini via AI Studio free tier (Flash / Flash-Lite).
- **Final pass before demo recording:** possibly Anthropic Claude (paid, ~$10) or Groq free tier.
- **Required:** `LLM_PROVIDER=gemini|anthropic|groq`, provider logic behind a thin adapter. **Switching providers must be a config change, never a code change.**
- Groq's free tier is scoped **per-organisation, not per-key**; limits vary by model — reconfirm before relying on it for the final pass.

### Gemini multimodal — the ceiling is TPM, not images

Verified 2026-07-27. Governs Tier-2 VLM page escalation ([[Document Parsing And Complex PDFs]]).

| | |
|---|---|
| Images per request | **3,600** — never approached here |
| Delivery | Inline base64 is fine for this use case. File API is for reuse across requests or oversized files |
| Request payload | **100 MB** (raised from 20 MB, January 2026) |
| Local PDF input | **50 MB** per file via standard file input |
| Resolution | No fixed pixel limit; scaled and padded to at most **3072×3072**, aspect preserved |
| Token cost | **258 tokens** flat when both dimensions ≤ 384 px. Larger images are **tiled**, each tile adding cost |

⚠️ **There is no separate free-tier image limit — and that is the problem, not the reassurance.** Page images draw on the same TPM budget as text and drain it far faster: a single tiled page can cost several hundred to a couple of thousand tokens before the model reads the prompt, against a ~250K–1M TPM ceiling. Escalation must therefore be **token-paced, not request-paced** — the RPM limiter carried over from [[NotebookRAG Reference Project]] is the wrong instrument for this path.

### LlamaParse — a credit is not a page

| Tier | Credits **per page** |
|---|---|
| Fast | 1 — ⚠️ **spatial text only, no markdown** |
| Cost-effective | 3 |
| Agentic | 10 |
| Agentic Plus | 45 |

Layout extraction adds +3/page to any tier. List price **$1.25 per 1,000 credits**. Parsed files cache for 48 h — re-parsing inside that window is free.

Two consequences:

- **The 1-credit tier does not solve the problem Tier 3 exists for.** Fast emits spatial text without markdown, so it recovers no table structure. Cost-effective (3/page) is the realistic floor.
- **`target_pages` bills only the requested pages**, which composes exactly with tiered parsing: Tier 3 sends only the pages the local heuristic flagged. 6 complex pages out of 50 costs 18 credits at Cost-effective, not 500.

⚠️ The quoted **10,000 free credits/month is still unconfirmed** — the pricing page documents paid rates only. Low-stakes: 10,000 credits is $12.50, so Tier 3 is affordable either way.

## Evaluation / CI

- ✅ **DeepEval** for CI gates — pytest-native, designed to fail builds on metric regression.
- ⚪ **RAGAS** optional, for deeper offline dataset-level reporting. **Not a CI gate tool — do not wire it into CI.**

## Reranking — and the constraint it creates

Cohere Rerank trial: **1,000 calls / month · 10 req / min.**

### Exhaustion behaviour — verified 2026-07-27, and the two failures are different

The question was "hard 429 or silent degradation?" The answer is neither, quite: Cohere returns **two different status codes for two different conditions**, and the fallback logic has to tell them apart.

| Code | Condition | Example message | Nature |
|---|---|---|---|
| **429** | Per-minute rate limit | *"You are past the per minute request limit, please wait and try again later."* · *"trial token rate limit exceeded, limit is 100000 tokens per minute"* | **Transient** — waiting fixes it |
| **402** | Billing / quota exhausted | *"Maximum billing reached for this API key as set in your dashboard…"* | **Terminal** — waiting never fixes it |

**No `Retry-After` header is documented for either.** Our own limiter still sets one on our 429s (§6); we simply have nothing upstream to pass through.

**This is why the fallback chain needs a circuit breaker, not just a retry.** A 429 should back off and try again — the next query may well succeed. A 402 means every subsequent call this month returns 402, so retrying spends 2 s of timeout budget per query to learn something already known. On a 402 the reranker is disabled for the deployment and the pipeline runs cache → fused order from then on. [[NotebookRAG Reference Project]]'s client already fails fast on non-retryable 4xx, but has no breaker — it would keep issuing doomed calls forever. Specified in [[Retrieval Pipeline Contract]] §4 `rerank`.

Note also the **token** rate limit visible in the 429 examples (100K tokens/min on trial keys). Rerank sends top-40 candidates, so a call is order 10K tokens — the 10 req/min ceiling still binds first, but not by a wide margin.

⚠️ **This collides with the 512 MB ceiling.** A local cross-encoder (`bge-reranker-v2-m3`, ~568 M params) does not fit either, so reranking *must* be remote — and remote means 1,000 calls/month total. Reranking unconditionally on every query caps the deployment at ~1,000 lifetime queries.

Three design responses, all worth building:

1. **Conditional reranking** — skip the reranker when the fused top-1 margin is decisive; only rerank when the top candidates are genuinely close. Cuts call volume substantially while preserving quality exactly where it matters.
2. **Cache** rerank results keyed on `(query_hash, doc_set)`.
3. **Fallback chain**: Cohere → cache → fused order. Logged, surfaced, never a hard failure. Extends [[NotebookRAG Reference Project]]'s `rerank_ok` pattern.

The 10 req/min ceiling also means rerank needs a client-side rate limiter, not just retries.

## Auth

✅ Clerk's FastAPI JWT verification is **officially documented and maintained** via the `clerk-backend-api` Python SDK. Safe to build on.

[[KnowledgeHub Index]] · [[KnowledgeHub Stack Decisions]] · [[Open Verification Questions]]
