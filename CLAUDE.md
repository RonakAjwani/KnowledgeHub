# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

**There is no source code yet, and no git repo.** The project is a CV assessment - KnowledgeHub, a multi-document RAG assistant with chat memory ([AI_Engineer_Assignment.md](AI_Engineer_Assignment.md)) - whose research phase closed 2026-07-27. Everything currently in the tree is specification:

- [obsidian_vault/](obsidian_vault/) - the design record. Flat, Title Case filenames, `[[wikilinks]]` at the bottom of each note.
- [architecture-overview.svg](architecture-overview.svg) - **live** diagram, embedded in the README. It is the only architecture diagram; the older, denser `architecture.svg` was deleted because nothing in it was legible once GitHub scaled it into a README column. Its layout contract (column x-ranges, five bands, the 860px width rule) is an XML comment at the top of the file; keep to it when editing, and re-check legibility at ~640px after any change.

## Commands

All backend commands run from `backend/` and go through the in-project venv at `backend/.venv`.

```bash
# Backend
poetry install                 # dev group included; --only main is what the image uses
poetry run pytest -q           # full suite
poetry run ruff check app tests scripts
poetry run mypy app
poetry run uvicorn app.main:app --reload --port 8000

# Diagnostics
poetry run alembic upgrade head                        # apply migrations
PYTHONPATH=. poetry run python scripts/probe_rrf_rank_base.py   # re-check RRF_MAX after a Qdrant bump

# Full stack
docker compose up --build      # AUTH_MODE=dev, so no Clerk account needed
```

`backend/.env` is required (copy `backend/.env.example`). The stack runs without an LLM key up to the point of generation.

Backend dependencies are managed with **Poetry**, and `poetry.lock` is committed - Compose is the durable deliverable for anyone who reads this after the hosted services are gone, so the build has to be reproducible from a cold clone. Dependency groups keep `deepeval` and `pytest` out of the runtime image. Frontend uses `pnpm`.

**Nothing installs globally.** Set `poetry config virtualenvs.in-project true` so the environment lives at `backend/.venv`, never in Poetry's shared cache and never in the system Python - this machine has other projects (`C:\Projects\NotebookRAG` among them) and their dependency sets must not interact. Run backend commands through `poetry run`. On the frontend, `pnpm install` into `frontend/node_modules`; use `pnpm dlx` for one-off CLIs such as shadcn rather than `npm i -g`. Both `.venv/` and `node_modules/` are gitignored and neither is copied into a Docker image.

## Read before building

Read in this order; do not work from a remembered summary, and do not re-derive facts these notes already establish:

1. [Retrieval Pipeline Contract](obsidian_vault/Retrieval%20Pipeline%20Contract.md) - **the build spec.** Invariants, core types, both pipelines stage-by-stage, timeout/fallback table, error taxonomy, persistence, SSE contract, LLM adapter interface. Precise enough that implementation is meant to be mechanical.
2. [Confirmed Infrastructure Constraints](obsidian_vault/Confirmed%20Infrastructure%20Constraints.md) - verified tier and limit facts. Design against these directly.
3. [KnowledgeHub Stack Decisions](obsidian_vault/KnowledgeHub%20Stack%20Decisions.md) - locked technology choices.
4. [KnowledgeHub Index](obsidian_vault/KnowledgeHub%20Index.md) - one-table decision summary and the list of deliberately-unresolved constants.

Research is closed. Settled and not to be re-litigated: Qdrant server-side weighted RRF (not client-side), `bge-small-en-v1.5` via fastembed, conditional Cohere rerank, LangGraph bounded CRAG with one retry, parent-child chunking, Clerk auth, DeepEval in CI. Rejected with reasons on record: HyDE, GraphRAG, embedding fine-tuning, Railway.

Scope decided at planning time, on top of the above: parsing ships **Tier 1 + Tier 2 + cross-reference resolution - LlamaParse Tier 3 is out**; Contextual Retrieval is wired behind a toggle but **off by default**; development runs on Docker Compose and managed services are provisioned last; **Ronak supplies the eval corpus and the golden set**, so the tuning pass blocks on him and nothing else does.

## Check the docs before coding against a library

[Technology Documentation Links](obsidian_vault/Technology%20Documentation%20Links.md) holds the current documentation URL for every library and service in the stack. **Read the relevant entry before implementing against a library, and again when a bug looks like an API mismatch** - a renamed parameter, a moved import, a changed signature. Come here before trying variations.

This stack moves faster than the model's knowledge cutoff, and several decisions are pinned to API surfaces that did not exist in older versions - Qdrant's `rrf` object exposing both `weights` and `k` is the sharpest case, and guessing it wrong silently breaks `RRF_MAX` and every threshold derived from it (I7). An empty `Docs` cell means unverified: say so rather than proceeding on a remembered API. Record anything learned under that note's **Findings** section so the next session does not re-read the same page.

**That note's Version column is provisional, not a set of pins.** Ronak recorded the numbers by hand and asked that they be confirmed after installing rather than trusted. Read the resolved versions out of `poetry.lock` / `pnpm-lock.yaml` and off the running Qdrant, then overwrite the provisional values. Only two are load-bearing: Python **3.12** (3.14 is also installed on this machine and must not be picked up) and Qdrant **≥ 1.14**.

## Architecture

FastAPI backend (Python 3.12, Poetry) + Next.js App Router frontend (TypeScript, Tailwind, shadcn/ui, `@clerk/nextjs`). Three stores, kept separate on purpose: **Qdrant** (chunk vectors, dense + sparse named vectors in one collection), **Postgres** (documents, chunk mirror for citation resolution, conversations, messages, `message_citations`, user preferences), and conversation state. User preferences must never leak into a retrieval query.

**Ingest** (async, in-process background tasks): upload -> tiered parse -> sanitize -> chunk -> embed -> upsert. There is **no dedup stage**, and its absence is a decision rather than a gap: near-duplicate detection was rejected outright (see the trap table below and the README) because the reference project's version was an unscoped cross-tenant vector search costing a Qdrant round trip per chunk, and deterministic chunk IDs already deliver the idempotency it was there for. This line used to list one, which is the only place in the repo that claimed it. Tier 1 is local `pypdf`/`pdfplumber`; pages a cheap local heuristic flags as complex escalate to a VLM through the existing LLM adapter, rendered with `pypdfium2` at a configured DPI. LlamaParse Tier 3 is **out of scope** - cost, an unconfirmed free tier, and Tier 2 already covers the table story. Qdrant is written before Postgres - an orphaned vector is recoverable, an orphaned citation row is not.

**Query** (LangGraph state graph over one shared `QueryState`): `route` -> `rewrite` -> `retrieve` -> `rerank` -> `grade` -> `generate`, with `verify` off the request path and `history` / `refuse` / `abstain` as terminal nodes. Raw-query retrieval fires in parallel with `rewrite`; the second Qdrant call is skipped entirely when the rewrite changed nothing. LangGraph is here for checkpointing and node-level event streaming, **not** autonomy.

The chunk schema is the first implementation task and the one thing that cannot be retrofitted: `Document.normalized_text` plus `Chunk.{char_start, char_end, parent_char_start, parent_char_end, related_spans}`. The source-pane highlight, citation verification, and the eval harness all depend on it.

`POST /chat` streams SSE, so the browser's native `EventSource` **cannot** be used - it is GET-only and cannot carry the Clerk JWT. Contract §8 mentions `EventSource` as intent, not as an API. Use one fetch-based SSE reader (`fetch` + `ReadableStream`) for both the chat and ingest streams, and call FastAPI directly from the browser rather than proxying through a Next.js route handler.

## Invariants - violating one is a bug regardless of tests

Full statements in contract §0. The ones most easily broken by reasonable-looking code:

- **I1 · Degradation is never silent.** Every fallback appends a `Degradation` record and emits an SSE `degradation` event. A degraded path must never be indistinguishable from a healthy one.
- **I2 · Unknown is not zero.** A failed judge yields `null`, never `false`.
- **I3 · `user_id` scopes everything.** Every Postgres query and every Qdrant search carries it. There is no unscoped read path.
- **I5 · Offsets are into `normalized_text`** - never raw bytes, never a chunk.
- **I7 · No per-query renormalisation, ever.** Normalise against the analytic `RRF_MAX = (w_dense + w_sparse) / (k + rank_base)`, never the observed max of the candidate set. Pin Qdrant's `k` in config rather than inheriting the server default. **`rank_base` is 0 on Qdrant 1.18** - measured, not assumed (`backend/scripts/probe_rrf_rank_base.py`), so the denominator is `k`. Re-run that probe after any Qdrant version bump.

Two structural rules with the same force:

- **One builder, one string.** `build_normalized_text(Block[]) -> (text, spans)` runs once; sanitisation and derived-block insertion happen *inside* it, before spans are assigned. Nothing else may re-concatenate blocks, and the string is immutable afterwards - a stray `.strip()` invalidates every offset in the document.
- **Fusion within a query is server-side; fusion across queries is client-side, and only there** (merging the raw and rewritten result sets via nested RRF).

## Hard constraints that shape the code

**The deploy target is Azure, and it is live.** Backend on Azure Container Apps (`knowledgehub-backend` in `knowledgehub-rg`, 1 vCPU / 2 GiB, `minReplicas: 0`), frontend on Vercel, Postgres on Azure Flexible Server 16 (`Standard_B1ms`, `max_connections` 50, TLS enforced), vectors on Qdrant Cloud. Render is gone and is not coming back; the notes below say "measured on Render" only where the number was.

- **The memory ceiling is a sizing decision now, not a hard limit.** `bge-small` over `bge-base`, no local cross-encoder, no local VLM parser, and a tuned embedding batch size were all forced by Render's 512 MB / 0.1 vCPU. The container is 1 vCPU / 2 GiB, so they are no longer forced - but they are still **what ships**, they are what every measurement in the vault was taken against, and re-opening one means re-running the eval, not just changing a constant. Treat them as settled unless there is a measured reason.
- **Still one process, still no separate ingest worker.** Background ingest shares the API process; the single uvicorn worker is deliberate because each worker loads its own copy of the ONNX embedding model. The original reason (Render's 750 free instance-hours) is void - Container Apps bills vCPU-seconds and requests - but the design reason is not, and a second worker doubles model memory rather than adding throughput on this workload.
- **`minReplicas: 0` is a deliberate demo trade-off.** Cold start measured at **33.8 s**, and it lands on the CORS preflight because the frontend is cross-origin on Vercel. Accepted; do not "fix" it by adding an uptime pinger.
- **The container filesystem is ephemeral.** `Document.blob_ref` is a Postgres `bytea`, never a path.
- **The app owns its connection pool.** No managed pooling in front of Postgres, so the pool is the app's (`db_pool_size` + `db_max_overflow`, 5 + 2) and it retries once on connection-level errors only. `max_connections` is 50 server-side, which is what bounds any future scale-out - see the ingest-concurrency note in [Session Handoff 2026-08-02](obsidian_vault/Session%20Handoff%202026-08-02.md).
- **Cohere returns 402 for quota and 429 for rate**, and the client must distinguish them: 429 backs off, 402 trips a circuit breaker for the rest of the deployment. Trial is 1,000 calls/month at 10 rpm, hence conditional rerank + cache + fallback chain.
- **Gemini's multimodal ceiling is TPM, not images.** Page-image escalation must be paced on *tokens*, with a per-document cap on escalated pages that emits a visible degradation when hit. The RPM limiter belongs on Cohere, not here.

## Traps carried from the reference project

[NotebookRAG](obsidian_vault/NotebookRAG%20Reference%20Project.md) (`C:\Projects\NotebookRAG`, and a committed `venv/` to exclude from searches) is **reference only, never a foundation** - consult patterns, do not port wholesale. Port the citation-marker normalisation regex (`【1】`, `［1］`, `〔1〕`), the `0.6·max + 0.4·mean` blend, and deterministic chunk IDs. Do not reproduce: self-normalised fused scores, undelimited chunk text in prompts, the unscoped global vector search in dedup, two independent concatenations of one document, a text-only synchronous LLM client, 400-char source truncation before the judge, or verification in the request path.

Retrieved document text is an **untrusted input channel** - users upload arbitrary PDFs. Chunk content goes into delimited DATA blocks with the delimiter escaped inside chunk text, and ingest strips zero-width characters, white-on-white runs, HTML comments, and PDF annotation layers (counted, never rejected).

## Working conventions

- **Update the vault as decisions land**, and `architecture-overview.svg` whenever the design changes. Vault notes are the memory between sessions.
- **Flag unverifiable facts; do not guess.** Rate limits, free-tier boundaries, and version numbers go to [Open Verification Questions](obsidian_vault/Open%20Verification%20Questions.md) - Ronak fetches current docs rather than receiving a confidently wrong answer built on a stale prior.
- **Do not invent the deliberately-unresolved constants.** Still unresolved and still placeholders: RRF branch weights, child/parent sizes, G1 threshold, verbatim turn count `N`, VLM render DPI, max escalated pages, embed batch size. They need a corpus; picking them now is guessing dressed as a decision. They are wired in `config.py` as env-overridable placeholders marked `UNRESOLVED`, and set in a tuning pass - which blocks on Ronak's corpus and golden set.
- **Four constants have since been resolved by measurement. Do not re-open them, and do not describe them as placeholders** - the reasoning is in the field comments in `config.py` and in [Pipeline Review Log](obsidian_vault/Pipeline%20Review%20Log.md). `rank_base` = **0** (measured on Qdrant 1.18, `scripts/probe_rrf_rank_base.py`; re-run after any version bump). `FLOOR_RERANK` = **0.35**, the middle of a flat plateau across 47 questions rather than a peak. `FLOOR_FUSED` = **0.50**, deliberately a *backstop* rather than a discriminator: across 53 questions no threshold separates answerable from should-decline, because unanswerable questions are topically adjacent and retrieval correctly scores them high - so the floor catches only degenerate retrieval and G3's grounding prompt does the real refusing. `DECISIVE_RATIO` = **99.0**, which disables the conditional-rerank skip on purpose because this deployment's Cohere budget does not bind.
- **The brief is not a spec to follow verbatim.** The assessment measures problem-solving and the ability to build complex applications, so depth beats feature coverage. Simplifications with a stated reason score well; README padding does not.
