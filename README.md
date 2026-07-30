# KnowledgeHub

A multi-document RAG assistant with chat memory. Upload PDFs, text or Markdown into a
workspace, ask questions across all of them, and get answers grounded in the retrieved
passages, with citations that click through to the exact highlighted span in the source.

Built as a CV assessment. The brief asked for upload, retrieval, multi-turn memory,
citations and a clean API. This README covers how to run it, what I chose and why, what
broke along the way, and how to test it.

## Contents

1. [Running it](#1-running-it)
2. [Tech stack and why](#2-tech-stack-and-why)
3. [How I worked](#3-how-i-worked)
4. [Day by day](#4-day-by-day)
5. [Architecture](#5-architecture)
6. [The pipeline, layer by layer](#6-the-pipeline-layer-by-layer)
7. [Problems I hit](#7-problems-i-hit)
8. [Testing](#8-testing)
9. [Known limitations](#9-known-limitations)

## 1. Running it

### Docker Compose

This is the fastest path and needs no accounts.

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env.local

# Add one LLM key to backend/.env. GROQ_API_KEY is the default provider and has
# the most generous free tier. Everything upstream of generation (upload, parsing,
# chunking, embedding, hybrid retrieval) runs with no key at all.

docker compose up --build
```

| | |
|---|---|
| Frontend | http://localhost:3000 |
| Backend | http://localhost:8000 (`/healthz`, `/docs`) |
| Auth | `AUTH_MODE=dev`, so every request maps to one fixed user. Nothing to sign into, no Clerk account needed. |
| Migrations | Run automatically on container start via `backend/docker-entrypoint.sh`. No separate step. |

### Manual setup

Backend, isolated in `backend/.venv`:

```bash
cd backend
poetry config virtualenvs.in-project true   # once per machine
poetry install
cp .env.example .env
docker compose up -d postgres qdrant        # just the datastores
poetry run alembic upgrade head
poetry run uvicorn app.main:app --reload --port 8000
```

Frontend, isolated in `frontend/node_modules`:

```bash
cd frontend
cp .env.example .env.local
pnpm install
pnpm dev
```

## 2. Tech stack and why

| Layer | Choice | Why this one |
|---|---|---|
| Vector DB | Qdrant | Dense and sparse vectors live as named vectors in one collection, and it fuses them server side in a single call. Pinecone would need two queries and client side fusion. |
| Embeddings | `bge-small-en-v1.5` via fastembed | Runs in 512 MB alongside the API. `bge-base` does not. fastembed is ONNX, so there is no torch in the image. |
| Sparse | `Qdrant/bm25` | Keeps BM25 in the same store as the dense vectors, so one query covers both. |
| Reranker | Cohere `rerank`, hosted | A local cross encoder is roughly 568 M params. That does not fit the memory budget next to everything else. |
| Orchestration | LangGraph | Used for checkpointing and per node event streaming, not for autonomy. The graph is bounded and its shape is fixed. |
| Backend | FastAPI, Python 3.12, Poetry | Async throughout, which matters when one request fans out to Qdrant, Cohere and an LLM. Poetry groups keep test tooling out of the runtime image. |
| Database | Postgres, SQLAlchemy, Alembic | Citations need real joins across documents, chunks, messages. SQLite would work locally and then not on Render. |
| Frontend | Next.js App Router, TypeScript, Tailwind, shadcn/ui | App Router for streaming, and shadcn because I wanted to own the component code rather than fight a component library's styling. |
| Auth | Clerk | Free tier is generous and it drops in cleanly. It is also skippable, which matters more (see `AUTH_MODE=dev`). |
| LLM | Provider agnostic adapter (Groq, Anthropic, Gemini) | Free tiers move and quotas get hit mid demo. Swapping provider is an env change, not a code change. |

Hosting is Docker Compose first. Render's free Postgres is deleted 44 days after creation,
so Compose is the deliverable that still works after a live link expires.

## 3. How I worked

The brief said it was measuring problem solving, technical skill, and the ability to build
something complex, and that I could simplify features with a reason, adapt the workflow,
and prioritise what I thought mattered. I took that seriously and it shaped the whole
project.

**Research with Perplexity.** Most of what this stack depends on moved after any model's
training cutoff, so I could not trust remembered API details. I used Perplexity to pull
current documentation and pin down facts before building on them: Qdrant's RRF query
object and whether it exposes `k`, Cohere's trial limits and which status code means quota
versus rate, Render's free tier rules, Groq's per day token caps. Anything I could not
verify went into an open questions list rather than into the design.

**Building with Claude Code.** I ran this across several sessions. Context does not carry
between sessions, so I kept an Obsidian vault (`obsidian_vault/`) as the project's memory:
decisions, the reasoning behind them, and what had already been ruled out. Before writing
code I wrote a retrieval pipeline contract with the invariants, types and stage behaviour
spelled out, so implementation had something to be checked against instead of being
improvised. I also used it to write and run measurement probes, which is where most of the
useful findings came from.

**What I decided myself.** The architecture, the invariants, what to cut, and what counted
as evidence. I made a rule early on that a number goes in the repo only if a script
produced it, and several proposals that sounded right died against their own measurements
(see [Problems I hit](#7-problems-i-hit)).

**What I chose to prioritise.** Depth over feature count. Specifically:

- Citations that resolve to exact character offsets, not footnote markers. This is the one
  thing that could not be retrofitted, so it was built first.
- Measuring the pipeline instead of asserting it works. The eval splits retrieval, answer,
  refusal and citation accuracy, because a single number hides which stage is broken.
- Treating uploaded documents as untrusted input. Most "chat with your PDF" demos skip this.

**What I simplified, with reasons.**

| Simplified | Why |
|---|---|
| No LlamaParse tier 3 parsing | Local parsing plus a vision model for hard pages already covers tables. A paid tier adds cost and an unverified free tier for a solved problem. |
| No separate ingest worker | Render gives 750 free instance hours per workspace per month, and overrunning suspends every free service. Two services would not fit. Ingest runs as a background task in the API process. |
| No uptime pinger | Same budget. I accept a 60 second cold start instead. |
| Contextual Retrieval wired but off | It costs an LLM call per chunk at ingest. The toggle exists, the default is off. |
| Rejected HyDE, GraphRAG, embedding fine tuning | HyDE showed no measurable gain on this corpus. GraphRAG solves multi hop entity networks, which this is not. Fine tuning needs a labelled corpus I do not have. |

## 4. Day by day

The brief estimated one to three days. I took five, because the assessment is about
engineering quality and I would rather submit something measured than something fast.

| Day | What happened |
|---|---|
| Mon 27 Jul | Research and architecture. Compared retrieval techniques, verified infrastructure limits, wrote the pipeline contract and the architecture diagram. Project setup. |
| Tue 28 Jul | Built the RAG pipeline end to end: parse, chunk, embed, hybrid retrieval, the LangGraph query graph, SSE streaming. First working UI on the same day. |
| Wed 29 Jul | Started measuring instead of assuming. Built the eval harness and the probe scripts, then fixed what the numbers exposed. Built the UI out properly. |
| Thu 30 Jul | Parsing accuracy. Found and fixed the table column bug and the missing text beside tables, added the source viewer with PDF rendering, and ran a full review pass over every stage. |
| Fri 31 Jul | Frontend test suite, diagrams, this README. Provider switch to Anthropic, deployment and demo video. |

## 5. Architecture

![Architecture](architecture-overview.svg)

The two pipelines are drawn as parallel columns because that is what they are. Ingest and
query share the stores and nothing else, and they run on different requests.

Three stores, kept separate on purpose:

- **Qdrant** holds chunk vectors, dense and sparse, as named vectors in one collection.
- **Postgres** holds documents, a chunk mirror for citation resolution, conversations,
  messages, and `message_citations` (the per citation retrieval trace).
- **Conversation state** is a rolling summary and an entity ledger, updated after a turn
  finishes, never during it, and never read by retrieval. User preferences sit in their own
  table and are also kept out of the query path, so a stored tone preference cannot quietly
  change what gets searched.

## 6. The pipeline, layer by layer

### Ingest: upload, parse, sanitize, chunk, embed, upsert

Runs as an async background task in the API process. A 200 page PDF cannot block an HTTP
request, and a separate worker does not fit the hosting budget.

**Parse.** Tier 1 is local, `pypdf` and `pdfplumber`. Pages that a cheap local heuristic
flags as complex escalate to a vision model through the same LLM adapter used for
generation, rendered with `pypdfium2`. Escalation is paced on tokens, not on image count,
because the multimodal ceiling is tokens per minute, and there is a per document cap that
emits a visible degradation when it is hit.

**Sanitize.** Uploaded documents are an untrusted input channel. Zero width characters,
white on white text, HTML comments and PDF annotation layers are stripped. They are counted
and reported, never used as a reason to reject the file.

**Chunk.** Parent and child chunks. Each child carries its section heading path in the
embedded text, which is what makes twenty near identical pages distinguishable at all.

**Embed and upsert.** Dense and sparse vectors are written to Qdrant first, then the
Postgres mirror. That order is on purpose: an orphaned vector can be recovered by
re-running ingest, an orphaned citation row cannot.

Chunk ids are derived from content, so re-uploading a file overwrites the same points
instead of duplicating them. The upload itself is deduplicated by content hash, scoped per
user and per workspace.

### Query: route, rewrite, retrieve, rerank, grade, generate

A LangGraph state graph over one shared state object, with `verify` off the request path
and `history`, `refuse` and `abstain` as terminal nodes.

**Route** decides whether the question needs a search at all or can be answered from
conversation history.

**Rewrite** resolves pronouns and references against the recent turns and the entity
ledger. Retrieval on the raw query fires in parallel with this, and the second Qdrant call
is skipped when the rewrite changed nothing, so most turns cost one round trip and not two.

**Retrieve** is hybrid. Qdrant prefetches the dense and sparse branches and fuses them with
weighted RRF in one call, with `k` pinned in config rather than inherited from the server
default. Scores are normalised against an analytic maximum computed from `k` and the
weights. Normalising against the observed maximum of the candidate set instead would force
the top score to 1.0 on every query and make every downstream threshold meaningless.

**Rerank** calls Cohere with a cache and a fallback chain that never fails: Cohere, then
cache, then the fused order. A 402 (quota) trips a circuit breaker for the rest of the
deployment; a 429 (rate) backs off. Those are different failures and the client treats them
differently.

**Grade** applies a relevance floor as a backstop. It is not the refusal mechanism, and
that is a measured conclusion rather than a design preference (see below).

**Generate** puts chunk text into delimited `[[[DOCUMENT n]]]` blocks with the delimiter
escaped inside the chunk text, so a document cannot forge its own block boundary. Citation
markers in the output are mapped back positionally to the chunk that occupied that prompt
slot, so a citation cannot be hallucinated into existence.

**Verify** runs after the answer has streamed. Each claim is checked against the passage it
cites. A failed judge yields `null`, never `false`, because reporting a citation as
unsupported when nobody actually checked it is a specific false claim.

### Two rules that hold everywhere

**One builder, one string.** `build_normalized_text(Block[]) -> (text, spans)` is the only
function allowed to concatenate parsed content. Sanitisation happens inside it, before
offsets are assigned, and the string is immutable afterwards. Every chunk offset, every
citation and the source pane highlight all index into that exact string. A stray `.strip()`
anywhere downstream would invalidate every offset in the document.

**Degradation is never silent.** Every fallback appends a structured record and emits an
SSE `degradation` event. A degraded answer is never visually indistinguishable from a
healthy one.

## 7. Problems I hit

**Table rows lost their columns.** The assistant answered `55.0` for February's
Manufacturing PMI when the real answer was `56.9`, and invented `26.2%` for a June cell
that is blank. Both were real values from the same row, in the wrong column. Flattened to
prose, a table row is a sequence of numbers with nothing tying each to its header, and a
sparse row cannot even be counted positionally. Widening pdfplumber's table detection was
the obvious fix and I had already measured it as dangerous (text strategy alone found 16
tables on a page that has 1, and dropped another document from 50 to 9). Numeric tables are
right aligned, so I recovered the columns from geometry instead by clustering right edges.
Both questions answer correctly now, and word coverage did not move on any document.

**Half a document was being deleted.** Text sitting beside a table was never read at all.
Coverage on that file went from 50.0% to 62.5% once columns beside a table were assigned
properly rather than cropped.

**Twenty fund pages were indistinguishable.** A question about one fund's Net AUM returned
another fund's. The chunk holding the answer read "Net AUM : 6,634.45 crore" under the
heading "AUM as on June 30, 2026", and both lines were identical on nineteen other pages,
so the right chunk sat at rank 28 of 40. Fixed by nesting headings by font size and
prepending the section path to the embedded text. That question now answers correctly at
the highest relevance in the set.

**The daily token cap that no header shows.** Generation quality dropped mid testing.
Groq's strongest model is metered at 100k tokens per day, which is invisible in the per
minute rate limit headers and only appears in an error body. The fix was a configured
fallback model, with the switch surfaced as a degradation event rather than hidden.

**The fallback then 413'd.** It had a smaller context window than the primary, and the
prompt was still being built at the primary's size. A 413 is not retryable, so the fallback
failed every time it was needed. The fallback now rebuilds its prompt at its own budget.

**Four graders were wrong before the pipeline was.** A refusal grader missed an inflection
("not reported" was matched, "do not report" was not) and scored a correct refusal as a
hallucination. A citation splitter dropped trailing markers, under reporting coverage on
every real turn. A multi turn grader compared the last turn's text against the first turn's
expectation. Every one of them moved the number down, which is what made them worth
finding: a bad result sent me to check the grader first from then on.

**A relevance floor cannot tell "right topic, missing fact".** When abstention
underperformed, the obvious move was a better relevance signal. I tested four across all 53
eval questions. In every one, the should decline questions sat inside the answerable range,
because they ask for a figure the corpus plausibly could hold but does not, so retrieval
correctly returns on topic chunks and correctly scores them highly. No threshold separates
them. The floor is now a backstop against degenerate retrieval, and the grounding prompt
does the actual refusing.

**A shortcut I had tuned turned out to cost more than it saved.** Rerank was skipped when
fusion looked decisive, and I had tuned the threshold to maximise Cohere savings. Then I
measured what the skipped queries lost: the reranker promotes a different top passage 29%
of the time, and top 5 overlap never exceeds 56% at any threshold. The skip is disabled.
The lesson was to ask what a change costs, not only what it saves.

**Retrieval was not the bottleneck.** The right passage reached the model 94% of the time
and six of those answers were still wrong. A single accuracy number would have hidden that
and sent me to tune the one stage that was already working.

## 8. Testing

The brief asks for "basic tests", which I read as: show that you can test the thing, and
cover the paths that matter. I went further in a few specific places, and only where a bug
would produce a plausible looking result instead of an error.

**375 tests, no live services needed, all running in CI.**

| Suite | Count | Covers |
|---|---|---|
| Backend (`pytest`) | 309 | The offset property (every chunk span round trips through `normalized_text`), RRF arithmetic, guardrail gating on both reranked and un-reranked paths, citation marker normalisation, idempotent re-ingest, the full error taxonomy, SSE frame ordering. Ten integration tests run against real Postgres and Qdrant. |
| Frontend (`vitest`) | 66 | The hand rolled SSE parser, the chat turn reducer, the REST error envelope, the citation chip's three verification states. |

```bash
# Backend
cd backend
poetry run pytest -q
poetry run ruff check app tests scripts evals
poetry run mypy app

# Frontend
cd frontend
pnpm test
pnpm lint
pnpm exec tsc --noEmit
```

**The acceptance test.** Unit tests cover components in isolation. This is the one that
proves the product works:

```bash
cd backend
PYTHONPATH=. poetry run python scripts/verify_api.py
```

It uploads real documents into a running API, waits for ingest, asks a question, resolves a
citation back to the literal characters in the source, asks a follow up containing a pronoun
and confirms it resolves against conversation memory, confirms the conversation persisted,
and exercises the error taxonomy. 32 checks.

**Adversarial and measurement scripts.**

| Script | What it does |
|---|---|
| `scripts/probe_guardrails.py` | Attacks the prompt guardrails with a hostile document. 8 of 8 pass. |
| `scripts/probe_rrf_rank_base.py` | Measures whether Qdrant ranks RRF from 0 or 1. Every relevance threshold derives from this, so re-run it after a Qdrant version bump. |
| `scripts/probe_golden_set.py` | Confirms every expected fact survived parsing, before spending tokens on an eval. |
| `evals/run.py` | The full eval. Reports retrieval, answer, refusal and citation accuracy separately. |

**What CI runs.** Lint, typecheck and both test suites on every push, with Postgres and
Qdrant as service containers so the integration tests actually execute instead of skipping
themselves. The eval and `verify_api.py` are deliberately not gated, because both need live
API keys and a job that goes green because a secret is unset reports "tests pass" while
testing nothing.

## 9. Known limitations

Stated here rather than left for a reviewer to find.

- **No ablation table.** A side by side recall comparison across dense only, BM25 only, RRF
  and RRF plus rerank was not built. The retrieval design is justified by reasoning and by
  two live probes, but not by that specific table.
- **Aggregation questions are out of reach.** "Which manager appears across the most fund
  pages" cannot be answered by top-k retrieval by construction. It is in the eval set,
  labelled, so it is measured rather than hidden.
- **The Original tab's highlight is best effort.** It matches cited text against pdf.js's
  text layer, because no bounding boxes are persisted. The Text tab's highlight is offset
  driven and cannot be wrong.
- **The Clerk sign in flow has not been exercised against a live account.** It was built and
  tested for correct fallback with no key configured, which is the path a reviewer running
  Compose actually takes.
- **Free tier quotas are real and visible by design.** If answers look simpler than
  expected, check the stream for a `degradation` event naming a fallback model before
  assuming retrieval is at fault.
