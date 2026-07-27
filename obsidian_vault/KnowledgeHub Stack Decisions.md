# KnowledgeHub Stack Decisions

Locked choices for [[KnowledgeHub Assignment Requirements]]. Carried over from [[NotebookRAG Reference Project]] unless noted.

## Locked

| Layer | Choice | Note |
|---|---|---|
| Vector DB | **Qdrant** | dense + sparse named vectors, one collection; server-side RRF/DBSF; best-in-class filtering for per-document scoping |
| Relational DB | **Postgres** | conversations, messages, documents, chunks mirror, citations |
| Backend | **FastAPI** | carried over |
| Frontend | **Next.js (App Router) + TS + shadcn/ui + Tailwind** | revised at planning time, 2026-07-27 — was React + Vite. The brief lists React/Next.js, so either satisfies it; Next.js is the better fit for the Vercel target and matches the `@clerk/nextjs` node already in `architecture.svg`. Package manager `pnpm` |
| Backend deps | **Poetry**, `poetry.lock` committed | Compose is the durable deliverable after the Render database expires, so the build must be reproducible from a cold clone. Dependency groups keep `deepeval`/`pytest` out of the 512 MB runtime image |
| Orchestration | **LangGraph** | for state persistence + node event streaming, not autonomy — see [[Agentic RAG Decision]] |
| Auth | **Clerk** | over Firebase — smaller surface, hosted UI, straightforward JWT verify in FastAPI |
| Frontend deploy | **Vercel** | free tier |
| Backend deploy | **Railway or Render** | free tier |
| Local/fallback | **Docker Compose** | already exists; satisfies the brief's fallback deliverable |

## LLM provider strategy

Development on free tiers, Anthropic key added at deployment if wanted.

- Existing code already speaks **OpenAI-compatible API** (Cerebras + Groq). Keep that as the primary adapter — Groq, Cerebras and Gemini's compat endpoint all fit behind it.
- Approximate free-tier limits *(volatile — verify before relying on them)*: Gemini AI Studio ~1,500 RPD / 15 RPM / 1M TPM, no card. Groq ~30 RPM / 6K TPM / 1K RPD, best latency.

Role split:

| Role | Model choice | Why |
|---|---|---|
| Routing + query rewriting | fastest available (Groq, Gemini Flash) | latency-critical, mechanical |
| Generation | Gemini 2.5 Pro free tier in dev | 1M context, generous RPD |
| Judge / faithfulness | anything — **runs async or in CI, never in request path** | not latency-critical |

## ⚠️ Correction to earlier advice: do not use provider-native citations

An earlier recommendation was to use Anthropic's native citations API. **Withdrawn.** Provider-native citations do not port across providers, and the plan is to develop on Gemini/Groq and possibly swap at deploy.

Keep NotebookRAG's existing design instead: **LLM emits inline `[n]` markers referencing chunk IDs supplied in the prompt, verified post-hoc against chunk text.** Provider-agnostic and already built. The existing approach is the more portable one.

## Schema decision that cannot be retrofitted

Chunk metadata **must persist character offsets into the original file**: `{doc_id, page, heading, char_start, char_end}`.

Without this, the NotebookLM-style "click a citation → highlight the exact span in the source pane" interaction is impossible to build later. Also store the original file blob so the source pane can render it. Design at ingest time.

## UX bar: NotebookLM

Stated goal is NotebookLM-class experience — fast *and* accurate. Engineering implications:

- **Async ingest** with status endpoint/SSE — a 200-page PDF cannot block a request
- **Node-level streaming** from LangGraph → "searching 12 sources…" → "found 5" → answer streams. Perceived latency ≪ actual latency
- **Citations as inline clickable chips** that scroll+highlight the source pane, not a footnote list
- **Source pane alongside the answer**
- **Per-document scoping** checkboxes in the UI, backed by Qdrant payload filters

## Models — decided (revised against verified constraints)

**Embedding: `BAAI/bge-small-en-v1.5` via fastembed**, quantized ONNX, in-process.

⚠️ **Revised.** An earlier draft recommended `bge-base`; the measured 512 MB Render ceiling overrules it — see [[Confirmed Infrastructure Constraints]]. `bge-m3` is impossible in-process (no quantized variant, ~2.27 GB fp32).

**Reranker: remote cross-encoder — Cohere Rerank.**

A local cross-encoder is *also* ruled out by the 512 MB ceiling, so this is forced rather than chosen. Still the right call on merit: no JSON failure mode, faster than an LLM rerank round trip, and **its relevance score doubles as the CRAG grader signal** in [[Agentic RAG Decision]], removing five serial LLM grading calls.

But the trial quota (1,000 calls/month · 10 rpm) means reranking cannot run unconditionally. Three responses, all in the design:
1. **Conditional rerank** — skip when the fused top-1 margin is decisive
2. **Cache** on `(query_hash, doc_set)`
3. **Fallback chain** Cohere → cache → fused order, always logged

**Fusion: Qdrant native server-side weighted RRF (1.14+).** Single call with a `weights` array per prefetch branch. No client-side fusion.

**Judge placement: async + CI, never inline.** Stream the answer, verify in the background, patch citation badges in after. Applies to both the citation verifier and the completeness judge.

## Chunking — decided

**Parent-child.** Index small child chunks for retrieval precision; pass the larger parent window to the LLM for context. Avoids picking a single compromise size. Structure-aware boundaries (headings, page/layout) over fixed windows.

## Rejected outright (no eval)

HyDE and GraphRAG — see [[RAG Techniques Evaluated]]. Embedding fine-tuning also rejected: 20–30 synthetic queries is orders of magnitude short of what fine-tuning needs, and the reranker captures the same gain at no operational cost.

## Still open

Nothing blocking. Remaining unknowns are external facts, tracked in [[Open Verification Questions]].

[[KnowledgeHub Index]] · [[NotebookRAG Reference Project]]
