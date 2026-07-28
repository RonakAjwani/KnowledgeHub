# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

**There is no source code yet, and no git repo.** The project is a CV assessment — KnowledgeHub, a multi-document RAG assistant with chat memory ([AI_Engineer_Assignment.md](AI_Engineer_Assignment.md)) — whose research phase closed 2026-07-27. Everything currently in the tree is specification:

- [obsidian_vault/](obsidian_vault/) — the design record. Flat, Title Case filenames, `[[wikilinks]]` at the bottom of each note.
- [architecture.svg](architecture.svg) — **live** diagram, embedded in the README later. Its layout contract (column x-ranges, six bands, off-page connectors) is an XML comment at the top of the file; keep to it when editing.

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

Backend dependencies are managed with **Poetry**, and `poetry.lock` is committed — Compose is the durable deliverable after the Render database expires, so the build has to be reproducible from a cold clone. Dependency groups keep `deepeval` and `pytest` out of the 512 MB runtime image. Frontend uses `pnpm`.

**Nothing installs globally.** Set `poetry config virtualenvs.in-project true` so the environment lives at `backend/.venv`, never in Poetry's shared cache and never in the system Python — this machine has other projects (`C:\Projects\NotebookRAG` among them) and their dependency sets must not interact. Run backend commands through `poetry run`. On the frontend, `pnpm install` into `frontend/node_modules`; use `pnpm dlx` for one-off CLIs such as shadcn rather than `npm i -g`. Both `.venv/` and `node_modules/` are gitignored and neither is copied into a Docker image.

## Read before building

Read in this order; do not work from a remembered summary, and do not re-derive facts these notes already establish:

1. [Retrieval Pipeline Contract](obsidian_vault/Retrieval%20Pipeline%20Contract.md) — **the build spec.** Invariants, core types, both pipelines stage-by-stage, timeout/fallback table, error taxonomy, persistence, SSE contract, LLM adapter interface. Precise enough that implementation is meant to be mechanical.
2. [Confirmed Infrastructure Constraints](obsidian_vault/Confirmed%20Infrastructure%20Constraints.md) — verified tier and limit facts. Design against these directly.
3. [KnowledgeHub Stack Decisions](obsidian_vault/KnowledgeHub%20Stack%20Decisions.md) — locked technology choices.
4. [KnowledgeHub Index](obsidian_vault/KnowledgeHub%20Index.md) — one-table decision summary and the list of deliberately-unresolved constants.

Research is closed. Settled and not to be re-litigated: Qdrant server-side weighted RRF (not client-side), `bge-small-en-v1.5` via fastembed, conditional Cohere rerank, LangGraph bounded CRAG with one retry, parent-child chunking, Clerk auth, DeepEval in CI. Rejected with reasons on record: HyDE, GraphRAG, embedding fine-tuning, Railway.

Scope decided at planning time, on top of the above: parsing ships **Tier 1 + Tier 2 + cross-reference resolution — LlamaParse Tier 3 is out**; Contextual Retrieval is wired behind a toggle but **off by default**; development runs on Docker Compose and managed services are provisioned last; **Ronak supplies the eval corpus and the golden set**, so the tuning pass blocks on him and nothing else does.

## Check the docs before coding against a library

[Technology Documentation Links](obsidian_vault/Technology%20Documentation%20Links.md) holds the current documentation URL for every library and service in the stack. **Read the relevant entry before implementing against a library, and again when a bug looks like an API mismatch** — a renamed parameter, a moved import, a changed signature. Come here before trying variations.

This stack moves faster than the model's knowledge cutoff, and several decisions are pinned to API surfaces that did not exist in older versions — Qdrant's `rrf` object exposing both `weights` and `k` is the sharpest case, and guessing it wrong silently breaks `RRF_MAX` and every threshold derived from it (I7). An empty `Docs` cell means unverified: say so rather than proceeding on a remembered API. Record anything learned under that note's **Findings** section so the next session does not re-read the same page.

**That note's Version column is provisional, not a set of pins.** Ronak recorded the numbers by hand and asked that they be confirmed after installing rather than trusted. Read the resolved versions out of `poetry.lock` / `pnpm-lock.yaml` and off the running Qdrant, then overwrite the provisional values. Only two are load-bearing: Python **3.12** (3.14 is also installed on this machine and must not be picked up) and Qdrant **≥ 1.14**.

## Architecture

FastAPI backend (Python 3.12, Poetry) + Next.js App Router frontend (TypeScript, Tailwind, shadcn/ui, `@clerk/nextjs`). Three stores, kept separate on purpose: **Qdrant** (chunk vectors, dense + sparse named vectors in one collection), **Postgres** (documents, chunk mirror for citation resolution, conversations, messages, `message_citations`, user preferences), and conversation state. User preferences must never leak into a retrieval query.

**Ingest** (async, in-process background tasks): upload → tiered parse → sanitize → chunk → dedup → embed → upsert. Tier 1 is local `pypdf`/`pdfplumber`; pages a cheap local heuristic flags as complex escalate to a VLM through the existing LLM adapter, rendered with `pypdfium2` at a configured DPI. LlamaParse Tier 3 is **out of scope** — cost, an unconfirmed free tier, and Tier 2 already covers the table story. Qdrant is written before Postgres — an orphaned vector is recoverable, an orphaned citation row is not.

**Query** (LangGraph state graph over one shared `QueryState`): `route` → `rewrite` → `retrieve` → `rerank` → `grade` → `generate`, with `verify` off the request path and `history` / `refuse` / `abstain` as terminal nodes. Raw-query retrieval fires in parallel with `rewrite`; the second Qdrant call is skipped entirely when the rewrite changed nothing. LangGraph is here for checkpointing and node-level event streaming, **not** autonomy.

The chunk schema is the first implementation task and the one thing that cannot be retrofitted: `Document.normalized_text` plus `Chunk.{char_start, char_end, parent_char_start, parent_char_end, related_spans}`. The source-pane highlight, citation verification, and the eval harness all depend on it.

`POST /chat` streams SSE, so the browser's native `EventSource` **cannot** be used — it is GET-only and cannot carry the Clerk JWT. Contract §8 mentions `EventSource` as intent, not as an API. Use one fetch-based SSE reader (`fetch` + `ReadableStream`) for both the chat and ingest streams, and call FastAPI directly from the browser rather than proxying through a Next.js route handler.

## Invariants — violating one is a bug regardless of tests

Full statements in contract §0. The ones most easily broken by reasonable-looking code:

- **I1 · Degradation is never silent.** Every fallback appends a `Degradation` record and emits an SSE `degradation` event. A degraded path must never be indistinguishable from a healthy one.
- **I2 · Unknown is not zero.** A failed judge yields `null`, never `false`.
- **I3 · `user_id` scopes everything.** Every Postgres query and every Qdrant search carries it. There is no unscoped read path.
- **I5 · Offsets are into `normalized_text`** — never raw bytes, never a chunk.
- **I7 · No per-query renormalisation, ever.** Normalise against the analytic `RRF_MAX = (w_dense + w_sparse) / (k + rank_base)`, never the observed max of the candidate set. Pin Qdrant's `k` in config rather than inheriting the server default. **`rank_base` is 0 on Qdrant 1.18** — measured, not assumed (`backend/scripts/probe_rrf_rank_base.py`), so the denominator is `k`. Re-run that probe after any Qdrant version bump.

Two structural rules with the same force:

- **One builder, one string.** `build_normalized_text(Block[]) -> (text, spans)` runs once; sanitisation and derived-block insertion happen *inside* it, before spans are assigned. Nothing else may re-concatenate blocks, and the string is immutable afterwards — a stray `.strip()` invalidates every offset in the document.
- **Fusion within a query is server-side; fusion across queries is client-side, and only there** (merging the raw and rewritten result sets via nested RRF).

## Hard constraints that shape the code

- **512 MB RAM / 0.1 vCPU on Render** is the single most binding constraint. It forces `bge-small` over `bge-base`, rules out any local cross-encoder or local VLM parser, and makes embedding batch size a real tuning parameter.
- **750 free instance-hours per workspace per month**, and overrun suspends every free service until month end. So: **no separate ingest worker service** (background tasks share the API process and its 512 MB), and **no 24/7 uptime pinger** — scope it to review windows and accept 60 s cold starts. Both of these look reasonable if re-proposed from scratch; they are dead.
- **Render's filesystem is ephemeral.** `Document.blob_ref` is a Postgres `bytea`, never a path.
- **Render free Postgres is deleted 44 days after creation.** Create it late, state the expiry beside the live link, treat Docker Compose as the durable deliverable. No managed pooling and unannounced restarts mean the app owns its pool and retries once on connection-level errors only.
- **Cohere returns 402 for quota and 429 for rate**, and the client must distinguish them: 429 backs off, 402 trips a circuit breaker for the rest of the deployment. Trial is 1,000 calls/month at 10 rpm, hence conditional rerank + cache + fallback chain.
- **Gemini's multimodal ceiling is TPM, not images.** Page-image escalation must be paced on *tokens*, with a per-document cap on escalated pages that emits a visible degradation when hit. The RPM limiter belongs on Cohere, not here.

## Traps carried from the reference project

[NotebookRAG](obsidian_vault/NotebookRAG%20Reference%20Project.md) (`C:\Projects\NotebookRAG`, and a committed `venv/` to exclude from searches) is **reference only, never a foundation** — consult patterns, do not port wholesale. Port the citation-marker normalisation regex (`【1】`, `［1］`, `〔1〕`), the `0.6·max + 0.4·mean` blend, and deterministic chunk IDs. Do not reproduce: self-normalised fused scores, undelimited chunk text in prompts, the unscoped global vector search in dedup, two independent concatenations of one document, a text-only synchronous LLM client, 400-char source truncation before the judge, or verification in the request path.

Retrieved document text is an **untrusted input channel** — users upload arbitrary PDFs. Chunk content goes into delimited DATA blocks with the delimiter escaped inside chunk text, and ingest strips zero-width characters, white-on-white runs, HTML comments, and PDF annotation layers (counted, never rejected).

## Working conventions

- **Update the vault as decisions land**, and `architecture.svg` whenever the design changes. Vault notes are the memory between sessions.
- **Flag unverifiable facts; do not guess.** Rate limits, free-tier boundaries, and version numbers go to [Open Verification Questions](obsidian_vault/Open%20Verification%20Questions.md) — Ronak fetches current docs rather than receiving a confidently wrong answer built on a stale prior.
- **Do not invent the deliberately-unresolved constants** (RRF branch weights, `DECISIVE_RATIO`, `FLOOR_RERANK`/`FLOOR_FUSED`, child/parent sizes, G1 threshold, verbatim turn count `N`, VLM render DPI, max escalated pages, embed batch size). They need a corpus; picking them now is guessing dressed as a decision. Wire them in `config.py` as env-overridable placeholders and set them in the first tuning pass — which blocks on Ronak's corpus and golden set, and on the one empirical check that settles whether Qdrant's RRF ranks from 0 or 1.
- **The brief is not a spec to follow verbatim.** The assessment measures problem-solving and the ability to build complex applications, so depth beats feature coverage. Simplifications with a stated reason score well; README padding does not.
