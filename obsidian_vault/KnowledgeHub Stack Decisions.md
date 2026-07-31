# KnowledgeHub Stack Decisions

Locked choices for [[KnowledgeHub Assignment Requirements]]. Carried over from [[NotebookRAG Reference Project]] unless noted.

## Locked

| Layer | Choice | Note |
|---|---|---|
| Vector DB | **Qdrant** | dense + sparse named vectors, one collection; server-side RRF/DBSF; best-in-class filtering for per-document scoping |
| Relational DB | **Postgres** | conversations, messages, documents, chunks mirror, citations |
| Backend | **FastAPI** | carried over |
| Frontend | **Next.js (App Router) + TS + shadcn/ui + Tailwind** | revised at planning time, 2026-07-27 - was React + Vite. The brief lists React/Next.js, so either satisfies it; Next.js is the better fit for the Vercel target and matches the `@clerk/nextjs` node already in the architecture diagram. Package manager `pnpm` |
| Backend deps | **Poetry**, `poetry.lock` committed | Compose is the durable deliverable after the Render database expires, so the build must be reproducible from a cold clone. Dependency groups keep `deepeval`/`pytest` out of the 512 MB runtime image |
| Orchestration | **LangGraph** | for state persistence + node event streaming, not autonomy - see [[Agentic RAG Decision]] |
| Auth | **Clerk** | over Firebase - smaller surface, hosted UI, straightforward JWT verify in FastAPI |
| Frontend deploy | **Vercel** | free tier |
| Backend deploy | **Railway or Render** | free tier |
| Local/fallback | **Docker Compose** | already exists; satisfies the brief's fallback deliverable |

## LLM provider strategy

**SETTLED 2026-07-31: Anthropic is the default provider.** Development ran on free tiers; the swap happened before deployment, and it was measured, not assumed.

| Run | Golden set | Median turn |
|---|---|---|
| Groq `llama-3.3-70b-versatile` | 13/22 | 60.0s |
| Anthropic `claude-sonnet-4-6` | **18/22** | **8.8s** |

Two reasons, in order. **Answer quality** is the headline. **A daily token cap cannot be paced around** is the one that decided it: Groq meters ~100k tokens/day, six diagnostic runs exhausted it in an afternoon, and once it is gone every turn sits in the pacer - which is where the 60s median comes from. A review window is exactly when that runs out, and no amount of per-minute pacing helps. Anthropic also puts a vision model on the same key as the text models, so Tier-2 page escalation works on the default provider instead of needing `LLM_PROVIDER=gemini`.

Gemini and Groq stay fully wired and are one env var away.

Role split (`MODELS_BY_PROVIDER` in `config.py` is the live table):

| Role | Model | Why |
|---|---|---|
| Route, rewrite, verify, generate-fallback | `claude-haiku-4-5` | latency-critical and mechanical; cheapest tier |
| Generation, VLM escalation | `claude-sonnet-4-6` | the quality-carrying calls, and the only vision model on the key |

**Sonnet 4.6 rather than Sonnet 5, and the reason is thinking.** Omitting the `thinking` parameter - which the adapter does - means *no thinking* on 4.6 but *adaptive thinking* on Sonnet 5, where it is the default rather than an opt-in. Thinking tokens come out of the same `max_tokens` bucket as the answer, so on Sonnet 5 the reasoning budget silently competes with `max_answer_tokens` - the identical failure that took `gpt-oss-120b` out of contention, an answer that runs out of budget mid-thought and returns as silence. Nothing here needs reasoning tokens: retrieval has done the work and generation is grounded extraction from supplied passages.

**`timeout_llm_route_s` moved 2.0 -> 4.0.** 2.0 was sized against Groq's ~0.2s route call. MEASURED on Haiku over five representative queries: median 1.30s, max 2.52s - so the old ceiling timed out the slowest quarter of routes. It fails open to `retrieve`, so nothing broke loudly; it just emitted a `route/timeout` degradation on healthy traffic, which is worse than useless, because I1 degradations are only readable if they mean something.

## ⚠️ Correction to earlier advice: do not use provider-native citations

An earlier recommendation was to use Anthropic's native citations API. **Withdrawn.** Provider-native citations do not port across providers, and the plan is to develop on Gemini/Groq and possibly swap at deploy.

Keep NotebookRAG's existing design instead: **LLM emits inline `[n]` markers referencing chunk IDs supplied in the prompt, verified post-hoc against chunk text.** Provider-agnostic and already built. The existing approach is the more portable one.

## Schema decision that cannot be retrofitted

Chunk metadata **must persist character offsets into the original file**: `{doc_id, page, heading, char_start, char_end}`.

Without this, the NotebookLM-style "click a citation -> highlight the exact span in the source pane" interaction is impossible to build later. Also store the original file blob so the source pane can render it. Design at ingest time.

## UX bar: NotebookLM

Stated goal is NotebookLM-class experience - fast *and* accurate. Engineering implications:

- **Async ingest** with status endpoint/SSE - a 200-page PDF cannot block a request
- **Node-level streaming** from LangGraph -> "searching 12 sources..." -> "found 5" -> answer streams. Perceived latency ≪ actual latency
- **Citations as inline clickable chips** that scroll+highlight the source pane, not a footnote list
- **Source pane alongside the answer**
- **Per-document scoping** checkboxes in the UI, backed by Qdrant payload filters

## Models - decided (revised against verified constraints)

**Embedding: `BAAI/bge-small-en-v1.5` via fastembed**, quantized ONNX, in-process.

⚠️ **Revised.** An earlier draft recommended `bge-base`; the measured 512 MB Render ceiling overrules it - see [[Confirmed Infrastructure Constraints]]. `bge-m3` is impossible in-process (no quantized variant, ~2.27 GB fp32).

**Reranker: remote cross-encoder - Cohere Rerank.**

A local cross-encoder is *also* ruled out by the 512 MB ceiling, so this is forced rather than chosen. Still the right call on merit: no JSON failure mode, faster than an LLM rerank round trip, and **its relevance score doubles as the CRAG grader signal** in [[Agentic RAG Decision]], removing five serial LLM grading calls.

But the trial quota (1,000 calls/month · 10 rpm) means reranking cannot run unconditionally. Three responses, all in the design:
1. **Conditional rerank** - skip when the fused top-1 margin is decisive
2. **Cache** on `(query_hash, doc_set)`
3. **Fallback chain** Cohere -> cache -> fused order, always logged

**Fusion: Qdrant native server-side weighted RRF (1.14+).** Single call with a `weights` array per prefetch branch. No client-side fusion.

**Judge placement: async + CI, never inline.** Stream the answer, verify in the background, patch citation badges in after. Applies to both the citation verifier and the completeness judge.

## Chunking - decided

**Parent-child.** Index small child chunks for retrieval precision; pass the larger parent window to the LLM for context. Avoids picking a single compromise size. Structure-aware boundaries (headings, page/layout) over fixed windows.

## Rejected outright (no eval)

HyDE and GraphRAG - see [[RAG Techniques Evaluated]]. Embedding fine-tuning also rejected: 20-30 synthetic queries is orders of magnitude short of what fine-tuning needs, and the reranker captures the same gain at no operational cost.

## Still open

Nothing blocking. Remaining unknowns are external facts, tracked in [[Open Verification Questions]].

[[KnowledgeHub Index]] · [[NotebookRAG Reference Project]]
