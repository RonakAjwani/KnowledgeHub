# Technology Documentation Links

Current-meta reference for every library and service the build depends on. **Ronak pastes the links; Claude reads them before implementing against a library and again when a bug looks like an API mismatch.**

## Why this note exists

The model's knowledge has a cutoff, and most of this stack moves faster than that cutoff. A remembered API shape is a stale prior, and a stale prior that *looks* right is worse than an admitted gap — the same reasoning that produced [[Open Verification Questions]]. That note is for external *facts* (limits, tiers, pricing). This one is for external *interfaces*.

Several of the decisions in [[Retrieval Pipeline Contract]] are pinned to specific API surfaces that did not exist in older versions — Qdrant's server-side `rrf` object exposing both `weights` and `k` is the sharpest example, and getting it wrong silently breaks `RRF_MAX` and every threshold derived from it (I7). Guessing at these is not a small error.

## How to use it

- **Ronak** — paste the doc URL into the `Docs` cell. Add the version you checked against if you have it. Anything with ⚠️ is load-bearing or fast-moving; those matter most.
- **Claude** — before writing code against a library, read its linked docs. When a bug looks like a signature mismatch, a renamed parameter, or a moved import, come back here *before* trying variations. Record what you learn under **Findings** so the next session does not re-read the same page.
- An empty `Docs` cell means unverified. Say so rather than proceeding on a remembered API.

> ⚠️ **The Version column is provisional until an install confirms it.** These were recorded by hand and Ronak's standing instruction is to check them afterwards — *"confirm after installing as I might be wrong too sometimes."* After `poetry install` and `pnpm install`, read the resolved versions out of `poetry.lock` / `pnpm-lock.yaml` and overwrite the provisional values here; do the same for Qdrant from the running server. **Two are load-bearing rather than informational:** Python **3.12** (3.14 is also on this machine and must not be picked up) and Qdrant **≥ 1.14** (below it the `rrf` object has no `weights`/`k`, which breaks `RRF_MAX` and every G2 threshold). The rest are whatever the solver picks — record them, don't fight them.

---

## Backend core

| Library                         | Version                              | Docs                                                     |
| ------------------------------- | ------------------------------------ | -------------------------------------------------------- |
| Python                          | 3.12.10                              | https://www.python.org/doc/                              |
| **Poetry** ⚠️                   | Check the version after installation | https://python-poetry.org/docs/                          |
| FastAPI                         |                                      | https://fastapi.tiangolo.com/                            |
| uvicorn                         |                                      | https://uvicorn.dev/                                     |
| Pydantic v2 / pydantic-settings |                                      | https://pydantic.dev/docs/validation/latest/get-started/ |
| SQLAlchemy (2.x async) ⚠️       | 2.0.51                               | https://docs.sqlalchemy.org/en/20/                       |
| asyncpg                         |                                      | https://magicstack.github.io/asyncpg/current/            |
| Alembic                         |                                      | https://alembic.sqlalchemy.org/en/latest/                |
| httpx                           |                                      | https://www.python-httpx.org/                            |

## Retrieval and embedding

| Library / Service                                                           | Version              | Docs                                                                                                                                                                                                                                                                                                                                                                    |
| --------------------------------------------------------------------------- | -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Qdrant server** ⚠️                                                        | ≥ 1.14, current 1.18 | https://qdrant.tech/documentation/                                                                                                                                                                                                                                                                                                                                      |
| **`qdrant-client` (async)** ⚠️                                              |                      | https://qdrant.tech/documentation/cloud-quickstart/?q=qdrant-client                                                                                                                                                                                                                                                                                                     |
| — Query API: `prefetch` + `FusionQuery`/`rrf` with **`weights` and `k`** ⚠️ |                      | [https://qdrant.tech/documentation/tutorials-search-engineering/multi-representation-search/?q=prefetch](https://qdrant.tech/documentation/search/hybrid-queries/?selector=aHRtbCA%2BIGJvZHkgPiBtYWluID4gc2VjdGlvbiA%2BIGRpdiA%2BIGRpdiA%2BIGRpdjpudGgtb2YtdHlwZSgyKSA%2BIGRpdiA%2BIGRpdjpudGgtb2YtdHlwZSgxKSA%2BIGFydGljbGUgPiBoNDpudGgtb2YtdHlwZSgyKQ%3D%3D&q=weight) |
| — Sparse vectors + `Qdrant/bm25` (server-side IDF)                          |                      | NA                                                                                                                                                                                                                                                                                                                                                                      |
| — Payload filtering on named-vector collections                             |                      | NA                                                                                                                                                                                                                                                                                                                                                                      |
| **fastembed** ⚠️                                                            |                      | https://qdrant.github.io/fastembed/                                                                                                                                                                                                                                                                                                                                     |
| — `BAAI/bge-small-en-v1.5`, quantized ONNX                                  |                      | https://huggingface.co/BAAI/bge-small-en                                                                                                                                                                                                                                                                                                                                |
| **Cohere Rerank** ⚠️                                                        | v2 API               | https://docs.cohere.com/                                                                                                                                                                                                                                                                                                                                                |
| — error codes: 402 quota vs 429 rate                                        |                      |                                                                                                                                                                                                                                                                                                                                                                         |

## Document parsing

| Library                                              | Version          | Docs                                 |
| ---------------------------------------------------- | ---------------- | ------------------------------------ |
| pypdf                                                | Latest:# 6.14.2  | https://pypi.org/project/pypdf/      |
| pdfplumber (incl. `page.find_tables()`)              | Latest:# 0.11.10 | https://pypi.org/project/pdfplumber/ |
| **pypdfium2** ⚠️ (DPI-controlled page rasterisation) | Latest:# 5.12.1  | https://pypi.org/project/pypdfium2/  |

## LLM and orchestration

| Library / Service                                                           | Version | Docs                                                                                                                                                                                                             |
| --------------------------------------------------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **LangGraph** ⚠️ (state graph, conditional edges, checkpointers, streaming) |         | https://docs.langchain.com/oss/python/langgraph/overview?_gl=1*1vrg079*_gcl_au*MTU5MzYzMzEzMS4xNzgzMTUyMTAz*_ga*NDM1MDk3OTE1LjE3ODMxNTIxMDQ.*_ga_47WX3HKKY2*czE3ODUxNjc2MjUkbzIkZzAkdDE3ODUxNjc2MjUkajYwJGwwJGgw |
| — node-level event streaming / `astream_events` ⚠️                          |         | https://docs.langchain.com/oss/python/langgraph/streaming                                                                                                                                                        |
| **Gemini API — AI Studio** ⚠️                                               |         | https://ai.google.dev/gemini-api/docs                                                                                                                                                                            |
| — multimodal inline image parts, streaming                                  |         |                                                                                                                                                                                                                  |
| Anthropic API (optional final pass)                                         |         |                                                                                                                                                                                                                  |
| Groq API (optional final pass)                                              |         | https://console.groq.com/docs/overview                                                                                                                                                                           |

## Auth

| Library / Service                           | Version | Docs                   |
| ------------------------------------------- | ------- | ---------------------- |
| **Clerk — `clerk-backend-api` (Python)** ⚠️ |         | https://clerk.com/docs |
| — FastAPI JWT verification                  |         |                        |
| **Clerk — `@clerk/nextjs`** ⚠️              |         |                        |
| — `clerkMiddleware`, `useAuth().getToken()` |         |                        |

## Frontend

| Library                                                                                                   | Version | Docs                                                                                    |
| --------------------------------------------------------------------------------------------------------- | ------- | --------------------------------------------------------------------------------------- |
| **Next.js (App Router)** ⚠️                                                                               |         | https://nextjs.org/docs                                                                 |
| — `output: "standalone"` for the Compose image                                                            |         |                                                                                         |
| TypeScript                                                                                                |         | https://www.typescriptlang.org/docs/                                                    |
| Tailwind CSS                                                                                              |         | [https://v2.tailwindcss.com/docs](https://tailwindcss.com/docs/installation/using-vite) |
| shadcn/ui                                                                                                 |         | https://ui.shadcn.com/docs                                                              |
| TanStack Query                                                                                            |         |                                                                                         |
| pnpm                                                                                                      |         | https://pnpm.io/motivation                                                              |
| SSE over `fetch` + `ReadableStream` ⚠️ (native `EventSource` is unusable here — GET-only, no auth header) |         |                                                                                         |

## Evaluation and testing

| Library                                  | Version | Docs                                   |
| ---------------------------------------- | ------- | -------------------------------------- |
| **DeepEval** ⚠️ (pytest-native CI gates) |         | https://deepeval.com/docs/introduction |
| pytest / pytest-asyncio                  |         |                                        |

## Deployment and CI

| Service                                     | Docs                               |
| ------------------------------------------- | ---------------------------------- |
| Docker Compose                              | https://docs.docker.com/           |
| **Render** ⚠️ (web service + free Postgres) | https://render.com/docs            |
| Vercel                                      | https://vercel.com/docs            |
| GitHub Actions                              | https://docs.github.com/en/actions |

---

## Findings

Append here as docs are read. One entry per thing learned that contradicted an assumption or that the next session would otherwise have to rediscover. Date each entry.

### Resolved backend versions — 2026-07-27, Phase 0

From `backend/poetry.lock` after `poetry install --no-root`. These are the confirmed values the provisional column was pointing at; **your hand-recorded numbers were all correct** (SQLAlchemy 2.0.51, pypdf 6.14.2, pdfplumber 0.11.10, pypdfium2 5.12.1, Python 3.12.10).

```
Python 3.12.10          fastapi 0.140.7        starlette 1.3.1
sqlalchemy 2.0.51       uvicorn 0.51.0         pydantic 2.13.4
asyncpg 0.31.0          alembic 1.18.5         pydantic-settings 2.14.2
qdrant-client 1.18.0    fastembed 0.8.0        onnxruntime 1.28.0
langgraph 1.2.9         cohere 5.21.1          tokenizers 0.23.1
clerk-backend-api 3.3.1 httpx 0.28.1           python-multipart 0.0.32
pypdf 6.14.2            pdfplumber 0.11.10     pypdfium2 5.12.1
— dev group: pytest 9.1.1, pytest-asyncio 1.4.0, deepeval 3.9.9, ruff 0.16.0, mypy 1.20.2
```

Qdrant *server* is pinned to **v1.15.1** in `docker-compose.yml` and **confirmed from the running container** (`GET /` returns `{"version":"1.15.1"}`) — comfortably above the 1.14 floor where `rrf` gained `weights`/`k`. Client 1.18.0 talks to it fine.

### API findings — 2026-07-27, Phase 0

1. **`clerk-backend-api` 3.3.1 moved its auth helpers.** `clerk_backend_api.jwks_helpers` **does not exist** — this is the import most older examples use. Current surface: `authenticate_request` and `AuthenticateRequestOptions` are exported from the package root (implemented in `clerk_backend_api.security.*`). `authenticate_request(request, options) -> RequestState`; `RequestState` exposes `.is_signed_in`, `.payload`, `.reason`, `.message`. The `Requestish` protocol requires only a `.headers` mapping, so a Starlette `Request` satisfies it structurally with no adapter. **It is synchronous and does a (cached) JWKS fetch**, so it must be offloaded off the event loop — `app/auth.py` uses `run_in_threadpool`. Read `payload["sub"]` for the user id: correct under both v1 and v2 session tokens, and avoids `to_auth()` whose return type varies by token type.
2. **Poetry ignores a local `poetry.toml` until a `pyproject.toml` exists beside it.** `poetry config virtualenvs.in-project true --local` wrote the file but `poetry config --list` kept reporting `null`, because Poetry did not yet consider the directory a project root. Harmless once ordering is known: write `pyproject.toml` first, then the config takes effect. Worth knowing before debugging a venv that landed in the shared cache.
3. **Starlette 1.3.1 renamed two status constants.** `HTTP_413_REQUEST_ENTITY_TOO_LARGE` → `HTTP_413_CONTENT_TOO_LARGE`, `HTTP_422_UNPROCESSABLE_ENTITY` → `HTTP_422_UNPROCESSABLE_CONTENT`. Old names still work but emit `StarletteDeprecationWarning`.
4. **FastAPI's default 422 collides with the error taxonomy.** FastAPI returns 422 for request-validation failures, but §6 reserves 422 for `document_not_ready`. `app/errors.py` remaps `RequestValidationError` to 400 `invalid_request`; without that, "your JSON is malformed" and "your document is still embedding" are indistinguishable to a client.

### API findings — 2026-07-28, Phase 2

5. **`fastembed` 0.8.0 exposes `token_count` publicly** — `TextEmbedding.token_count(texts, batch_size=1024) -> int`. So chunk sizing uses the same tokenizer that produces the vectors, rather than an approximation of it. `TextEmbedding(model_name=..., cache_dir=..., threads=1)`; `lazy_load=True` also exists if startup RSS becomes a problem on Render. Quantized `bge-small-en-v1.5` is **64 MB on disk** once cached.
6. ⚠️ **Windows: fastembed's HuggingFace download hits `WinError 1314` (symlink privilege) unless Developer Mode is on.** It retries, falls back to copying, and succeeds — so it is noise rather than a blocker, but the first load is slow and logs two red ERROR lines that look worse than they are. Linux and the Docker image are unaffected. If it becomes annoying locally, enable Windows Developer Mode.

### API findings — 2026-07-28, Phase 2b

7. **The Anthropic Messages API differs from the OpenAI-compatible shape in three ways, all of which fail at runtime rather than at review.** The adapter handles each in `app/llm/client.py`, and there is a test pinning each:
   - **`system` is a top-level request field**, not a message with `role: "system"`. Leaving it in `messages[]` is rejected.
   - **Images are `{"type": "image", "source": {"type": "base64", "media_type", "data"}}`** — not the `image_url` data-URI block the OpenAI shape uses.
   - ⚠️ **`temperature` is *removed*, not deprecated, on current Claude models** (Opus 5 / 4.8 / 4.7, Fable 5, Sonnet 5) — sending it returns a **400**. The adapter drops it for those model prefixes and still sends it for older ones (e.g. Haiku 4.5). This is the one most likely to be missed, because passing temperature is harmless on every other provider.
   - Streaming delta shape also differs: `content_block_delta` → `delta.text` (Anthropic) vs `choices[0].delta.content` (OpenAI-compatible).
   - Current model IDs carry **no date suffix**: `claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`.
8. **Ruff's `ASYNC109` is a false positive for httpx-based clients.** It flags any async function taking a `timeout` parameter, assuming it hand-rolls `asyncio.timeout()`. Delegating to httpx is better — separate connect/read/write budgets — and the contract's §5 table is expressed as per-dependency timeouts, so the parameter *is* the interface. Ignored project-wide with that reason recorded in `pyproject.toml`.

### API findings — 2026-07-28, Phase 2c

9. ⚠️ **The Qdrant server floor is higher than "1.14+" — it is 1.16 or later, and 1.15.1 is not enough.** `qdrant-client` exposes two fusion shapes and they are **not** equivalent:
   - `FusionQuery(fusion=Fusion.RRF)` — the form nearly every example shows. Carries **neither `weights` nor `k`**: plain unweighted RRF against an unpublished server default.
   - `RrfQuery(rrf=Rrf(k=..., weights=[...]))` — the weighted form, and the only one where both terms of `RRF_MAX = (w_dense + w_sparse) / (k + 1)` are our own constants.

   Server **1.15.1 rejects the second outright**: `Format error in JSON body: Expected some form of vector, id, or a type of query`. Caught by an end-to-end test, not by review — using `FusionQuery` instead would have looked correct, returned plausible results, and made every G2 threshold silently meaningless (I7). **`docker-compose.yml` is pinned to `v1.18.0`**, which also clears the client/server compatibility warning (client 1.18 vs server 1.15 exceeded the one-minor-version tolerance).
10. **Deterministic chunk ids need upsert semantics on *both* stores.** Qdrant's `upsert` honours them for free; a plain SQLAlchemy `session.add` turns the same determinism into a primary-key violation on the second ingest. `app/ingest/pipeline.py` uses `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(...)`. Idempotency has to mean the same thing in both systems or it fails the whole ingest in one of them.

### Live-key findings — 2026-07-28, Phase 3

11. ✅ **RRF rank base settled: Qdrant 1.18 ranks from 0**, so `RRF_MAX = (w_dense + w_sparse) / k`. A chunk topping both branches with `w=[1,1]`, `k=60` scores exactly **0.03333333 = 2/60**, not 2/61. The contract's written `+1` assumed rank-from-1 and was wrong. `config.rrf_rank_base` now defaults to 0; reproduce or re-check with `backend/scripts/probe_rrf_rank_base.py` after any version bump. **This constant is no longer in the deliberately-unresolved list.**
12. ⚠️ **The Gemini model IDs in config were stale training priors and effectively dead.** Verified against Ronak's live key: `gemini-2.5-flash` and `gemini-2.5-flash-lite` return **404** ("model is not found"); `gemini-2.0-flash` returns **429** with no usable free-tier quota. The key itself is fine — model listing returns 41 generateContent models. Working, confirmed 200:
    - `gemini-3.6-flash` — generation **and** streaming **and** vision (transcribed a rendered page image exactly, which proves the whole Tier-2 escalation path)
    - `gemini-3.5-flash-lite`, `gemini-3.1-flash-lite`, `gemini-flash-lite-latest` — fast roles
    - `gemini-3.5-flash` / `gemini-flash-latest` returned 200 but no `choices` at low `max_tokens` — they are thinking models and need a larger budget.

    Config now routes route/rewrite/verify → `gemini-3.5-flash-lite`, generate/VLM → `gemini-3.6-flash`. **Always list models against the live key rather than trusting a remembered ID.**
13. ✅ **Cohere Rerank v2 works on the trial key.** `POST https://api.cohere.com/v2/rerank` with `model: rerank-v3.5` returned 200 and ranked correctly (revenue document 0.6485 vs 0.0249 / 0.0170 for irrelevant ones). Response shape is `{"results": [{"index": int, "relevance_score": float}]}` — `index` refers to the position in the `documents` array we sent, so ordering is mapped back through our own candidate list rather than trusting an echoed id.

### Open gaps in this note — 2026-07-27

Flagged rather than filled, because guessing a URL is the same mistake as guessing an API. Three matter; the rest of the blanks are fine.

1. ⚠️ **The Qdrant hybrid-query reference is the wrong page and it is the most load-bearing link here.** The pasted URL is a multi-representation-search *tutorial*. What the build needs is the concepts page covering `prefetch` + `FusionQuery`/`rrf` and the **`weights` and `k`** parameters on the `rrf` object — the two constants `RRF_MAX = (w_dense + w_sparse) / (k + 1)` is derived from, and therefore every G2 threshold (I7). Also unfilled: sparse vectors / `Qdrant/bm25` server-side IDF, and payload filtering on named-vector collections, both marked NA. The BM25 branch is half the retrieval design. **Fill these before Phase 3.**
2. ⚠️ **The Tailwind link points at v2** (`v2.tailwindcss.com`), two majors behind. shadcn/ui does not target v2, so following it would produce a config that does not work with the chosen component library. Needs the current Tailwind docs.
3. **LangGraph streaming is unfilled.** The overview link may cover it; if not, the streaming / `astream_events` page is what drives every `pipeline.stage` and `answer.delta` event in contract §8.

Fine as-is: Anthropic (the model has a maintained internal reference for it), TanStack Query, pytest, and the `fetch`/`ReadableStream` SSE row — all stable, well-known surfaces. Clerk's docs root is navigable to both the Python JWT-verification and `@clerk/nextjs` middleware pages.

14. ⚠️ **Gemini free tier is 20 requests *per day* on the generate model.** The 429 body names the quota explicitly: `GenerateRequestsPerDayPerProjectPerModel-FreeTier`, `quotaValue: 20`, for `gemini-3.6-flash`. It is a daily cap, not a per-minute one, so backing off does not recover it. `gemini-3.5-flash-lite` sits on a separate and much larger bucket and kept answering after `3.6-flash` was exhausted. Consequence: **a 55-question eval cannot run end-to-end on the free tier** — VLM page escalation alone spends the day's budget. The eval harness therefore grew a `--retrieval-only` mode that stops after `grade` and makes zero LLM calls.

15. ⚠️ **pdfplumber's default word tolerance silently destroys LaTeX PDFs.** Many PDFs encode no space glyphs — the gap between words is a horizontal jump, not a character — and pdfplumber's default absolute `x_tolerance` of 3pt is wider than the inter-word gap in a 9–10pt body font. Three of the six corpus documents came back as `Regulatorycomplianceinindustrialmaintenance...`: **BM25 tokenises the sentence as a single term**, so the sparse branch cannot match anything in the document, and nothing anywhere reports an error. Fixed with `x_tolerance_ratio=0.15` (scales with font size, unlike a fixed tolerance) on every `extract_words` / `extract_text` / `Table.extract` call in `app/ingest/parse.py`. Measured across the corpus: over-long tokens 199/98/181 → **0**, words recovered 1121 → 2633 in the worst document, short-word share moved under a percentage point (so nothing is over-split), and the two documents that already extracted cleanly are **byte-identical**. Worth checking on any new corpus — `sum(1 for w in words if len(w) > 25)` is the whole detector.

16. ✅ **Cohere's `relevance_score` must be carried through, not recomputed from position.** `_reorder` originally assigned `1.0 - position/len(order)`, which made `0.6·max + 0.4·mean` a fixed function of `top_n`: exactly **0.840 on every query** at `top_n = 5`, regardless of whether anything relevant was retrieved. `FLOOR_RERANK` was a comparison against a constant and could never fire — the same failure mode I7 forbids, arrived at from a different direction. The cache must store `(id, score)` pairs for the same reason.

17. ⚠️ **`DECISIVE_RATIO = 1.5` was unreachable by construction.** RRF scores are `w/(k + rank)`, so a chunk ranked 0 in both branches scores `2/60 = 0.03333` against a runner-up ranked 1 in both at `2/61 = 0.03279` — a ratio of **1.017**. Measured over 53 questions (`backend/scripts/probe_decisive_margin.py`): the largest margin observed anywhere was **1.3033**, and 1.5 fired **zero times**, so every query paid a Cohere call against a 1,000/month budget. Set to **1.02**, which skips 24/53 (45% of the budget) while still requiring top-3 agreement in both branches. Re-measured after the change: 25/53 queries skipped, matching the forecast.

18. ⛔ **`FLOOR_FUSED` cannot work on normalised RRF, and this is structural rather than a sample-size problem.** With `n = 25` on the un-reranked path, answerable questions scored median **0.841** and should-decline questions median **0.847** — the population we want to reject scores *higher* than the one we want to keep, and no threshold separates them. The cause is that RRF scores **rank, not similarity**: the top chunk is rank ~0 for every query, so the fused score is ~0.80–0.86 whether or not anything relevant exists. It measures "something came back first", not "what came back is relevant". Raw dense cosine does carry signal on the same questions (answerable median 0.801 vs decline 0.752, best floor ≈ 0.75 giving 40/53) and is the obvious candidate score source for that path — **an open design question for the contract's §4, not a change made unilaterally.**

19. ✅ **Groq is wired and is now the default provider.** Base URL `https://api.groq.com/openai/v1`, OpenAI-compatible, so the existing adapter needed no new code path — only per-provider model ids, which were hardcoded Gemini strings and would have posted `gemini-3.6-flash` to Groq. `MODELS_BY_PROVIDER` in `config.py` now resolves them, so `LLM_PROVIDER` genuinely is the only switch.

20. ⚠️ **Groq's free tier meters tokens per *day* as well as per minute, and only the per-minute figure is in the response headers.** `x-ratelimit-*` reported 11,953/12,000 tokens remaining while every generation call failed — the real message was in the 429 body: *tokens per day (TPD): Limit 100000, Used 97649*. Six runs of a six-question diagnostic spent it in an afternoon. A daily cap cannot be paced around, so when generation 429s while the minute budget looks healthy, this is why. Per-model buckets are independent, so switching the generate model is the immediate workaround.

21. ⚠️ **A 413 and a 429 mean different things and only one is retryable.** Over-large single requests return **413** *"Request too large … on tokens per minute (TPM): Limit 12000, Requested 12882"* — no retry recovers that, the request has to shrink. Sustained overuse returns **429**. Hence `max_context_tokens`, which caps the DATA blocks by tokens rather than by chunk count; twelve parent windows at `parent_tokens` each is ~13k tokens and exceeded the limit on exactly the multi-part questions that needed the most context. Note the budget must be sized against **context + `max_answer_tokens` + overhead**, because providers meter reserved output too.

22. ⛔ **Do not put a reasoning model behind a fixed output budget for generation.** `openai/gpt-oss-120b` has far more daily headroom than `llama-3.3-70b-versatile` and was tried as the default; it returned an **empty answer** at `max_tokens=2048`, having spent the whole budget on internal reasoning before emitting any visible text. Same class of failure seen on Gemini 3.x, where multi-part answers stopped mid-sentence. Generation stays on the non-reasoning `llama-3.3-70b-versatile`.

23. ℹ️ **Groq has no vision model on this key** (catalogue is llama 3.1/3.3, gpt-oss, qwen, whisper, prompt-guard — no llama-4-scout/maverick). Tier-2 page escalation therefore requires `LLM_PROVIDER=gemini`. `llm_model_vlm` resolving to empty is the signal, and `escalate_document` now returns one visible degradation instead of failing per flagged page.

---

[[KnowledgeHub Index]] · [[Retrieval Pipeline Contract]] · [[Confirmed Infrastructure Constraints]] · [[KnowledgeHub Stack Decisions]] · [[Open Verification Questions]]
