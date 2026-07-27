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

Qdrant *server* is pinned to **v1.15.1** in `docker-compose.yml` (client 1.18.0 talks to it fine). Server version still to be confirmed against a running container in Phase 3.

### API findings — 2026-07-27, Phase 0

1. **`clerk-backend-api` 3.3.1 moved its auth helpers.** `clerk_backend_api.jwks_helpers` **does not exist** — this is the import most older examples use. Current surface: `authenticate_request` and `AuthenticateRequestOptions` are exported from the package root (implemented in `clerk_backend_api.security.*`). `authenticate_request(request, options) -> RequestState`; `RequestState` exposes `.is_signed_in`, `.payload`, `.reason`, `.message`. The `Requestish` protocol requires only a `.headers` mapping, so a Starlette `Request` satisfies it structurally with no adapter. **It is synchronous and does a (cached) JWKS fetch**, so it must be offloaded off the event loop — `app/auth.py` uses `run_in_threadpool`. Read `payload["sub"]` for the user id: correct under both v1 and v2 session tokens, and avoids `to_auth()` whose return type varies by token type.
2. **Poetry ignores a local `poetry.toml` until a `pyproject.toml` exists beside it.** `poetry config virtualenvs.in-project true --local` wrote the file but `poetry config --list` kept reporting `null`, because Poetry did not yet consider the directory a project root. Harmless once ordering is known: write `pyproject.toml` first, then the config takes effect. Worth knowing before debugging a venv that landed in the shared cache.
3. **Starlette 1.3.1 renamed two status constants.** `HTTP_413_REQUEST_ENTITY_TOO_LARGE` → `HTTP_413_CONTENT_TOO_LARGE`, `HTTP_422_UNPROCESSABLE_ENTITY` → `HTTP_422_UNPROCESSABLE_CONTENT`. Old names still work but emit `StarletteDeprecationWarning`.
4. **FastAPI's default 422 collides with the error taxonomy.** FastAPI returns 422 for request-validation failures, but §6 reserves 422 for `document_not_ready`. `app/errors.py` remaps `RequestValidationError` to 400 `invalid_request`; without that, "your JSON is malformed" and "your document is still embedding" are indistinguishable to a client.

### Open gaps in this note — 2026-07-27

Flagged rather than filled, because guessing a URL is the same mistake as guessing an API. Three matter; the rest of the blanks are fine.

1. ⚠️ **The Qdrant hybrid-query reference is the wrong page and it is the most load-bearing link here.** The pasted URL is a multi-representation-search *tutorial*. What the build needs is the concepts page covering `prefetch` + `FusionQuery`/`rrf` and the **`weights` and `k`** parameters on the `rrf` object — the two constants `RRF_MAX = (w_dense + w_sparse) / (k + 1)` is derived from, and therefore every G2 threshold (I7). Also unfilled: sparse vectors / `Qdrant/bm25` server-side IDF, and payload filtering on named-vector collections, both marked NA. The BM25 branch is half the retrieval design. **Fill these before Phase 3.**
2. ⚠️ **The Tailwind link points at v2** (`v2.tailwindcss.com`), two majors behind. shadcn/ui does not target v2, so following it would produce a config that does not work with the chosen component library. Needs the current Tailwind docs.
3. **LangGraph streaming is unfilled.** The overview link may cover it; if not, the streaming / `astream_events` page is what drives every `pipeline.stage` and `answer.delta` event in contract §8.

Fine as-is: Anthropic (the model has a maintained internal reference for it), TanStack Query, pytest, and the `fetch`/`ReadableStream` SSE row — all stable, well-known surfaces. Clerk's docs root is navigable to both the Python JWT-verification and `@clerk/nextjs` middleware pages.

---

[[KnowledgeHub Index]] · [[Retrieval Pipeline Contract]] · [[Confirmed Infrastructure Constraints]] · [[KnowledgeHub Stack Decisions]] · [[Open Verification Questions]]
