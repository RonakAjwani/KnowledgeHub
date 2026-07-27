# NotebookRAG Reference Project

Ronak's existing side project. Repo: https://github.com/RonakAjwani/NotebookRAG · local: `C:\Projects\NotebookRAG`

**Status: reference only, NOT a foundation.** KnowledgeHub is a from-scratch build. Consult this for patterns and hard-won fixes; do not port wholesale. Its eval numbers do not carry over — KnowledgeHub will be evaluated independently.

Source read: 2,890 LOC across `backend/app` (a `venv/` is committed — exclude it when searching).

## Architecture as built

`api/routes.py` → `retrieval/{retriever,fusion,reranker,embeddings,vector_store}` → `generation/{generator,prompts,verification,confidence}`, with `ingestion/{loaders,chunkers,dedup,pipeline}` and a standalone `evals/` harness.

Dense `bge-small-en-v1.5` via fastembed + Qdrant BM25 → **client-side** weighted RRF → listwise LLM rerank → grounded generation with `[n]` markers → claim-level verification → composite confidence → abstention gate.

## Patterns worth carrying forward

1. **Citation marker normalisation.** Models emit `【1】` (CJK lenticular), `［1］` (fullwidth), `〔1〕` — not just ASCII. The code comment records that missing this alone *"scored correctly-cited answers as 0.0 citation accuracy in the first run."* Pure hard-won knowledge; port the regex.
2. **"Unknown is not zero."** `RetrievalResult.rerank_ok` and `VerificationError` exist so a dead judge can't masquerade as "all citations unsupported," and a fallback ordering can't be mistaken for a reranked one. *"failure becomes indistinguishable from signal."* Best instinct in the codebase — make it a project-wide invariant.
3. **Claim-level verification against the UNION of cited sources.** A sentence citing `[1][2]` draws on both; judging each marker separately against the whole sentence wrongly fails both.
4. **Line-aware claim splitting.** Bullet items carry no terminal punctuation, so a pure sentence split mis-pairs claims and markers — the comment records this dragged citation accuracy to 0.29 on lookup questions.
5. **`retrieval_confidence` = `0.6*max + 0.4*mean`.** A precise lookup is often answered by one strongly-relevant chunk; a flat mean would wrongly trip the abstention gate. ⚠️ Carry the *blend*; do **not** carry its normalisation — see the issues table.
6. **Deterministic chunk IDs** — `sha256(doc_id|strategy|index|text)[:24]` → idempotent upserts. Directly satisfies the idempotent-ingest requirement.
7. **Eval harness shape** — `evals/{dataset,metrics,runner,report,schemas}` as a standalone module.

## Issues found — fix in the new build

| Issue | Where | Impact |
|---|---|---|
| **No injection defence** — raw chunk text interpolated into the prompt, undelimited | `generation/prompts.py:build_context` | Fine for a trusted personal corpus; **a real hole once users upload arbitrary PDFs.** See [[RAG Guardrails Design]] |
| **`retrieval_confidence` self-normalises fused scores** — divides by the *observed* max of the current candidate set | `generation/confidence.py:47-57` | Forces `max = 1.0` every query, pinning the blend ≥ 0.6 so the gate can never fire, and inflating confidence exactly when rerank failed. Nearly harmless there (rerank almost always ran); **fatal here**, where conditional rerank makes the fused path the majority. Resolved in contract §4 `grade` + invariant I7 |
| **Near-duplicate dedup does an unfiltered global vector search** | `ingestion/dedup.py:47` | `nearest_dense_score` carries no `user_id` filter — an unscoped cross-tenant read path, violating I3 — and costs one Qdrant round trip *per chunk*, untenable at 0.1 vCPU. **Rejected outright**, not ported: deterministic-ID upsert (contract §3.5) already delivers idempotency |
| **Two independent concatenations of the same document** | `loaders.py:33` (`full_text`) vs `chunkers.py:52-60` (`combined`) | Nothing depended on them agreeing, so the drift was invisible. With offsets load-bearing it becomes a wrong highlight. Resolved by the single-builder rule in contract §1 |
| **LLM client is text-only and sync** | `llm/client.py` | No `stream()` (needed by SSE §8) and no image content parts (needed by Tier-2 VLM escalation). Both are additions, not carry-overs — contract §9 |
| **Source truncated to 400 chars** before the verification judge | `generation/verification.py:146` | Evidence past the cutoff → false "unsupported." Likely contributor to the 0.736 citation accuracy |
| **Coverage denominator = all claims**, including uncited discourse | `verification.py:184` | "Here's a summary:" counts against coverage. Filter to factual claims first |
| **Verifier + completeness judge run in the request path** | `verification.py`, `confidence.py` | Latency tax on every answer — breaks the NotebookLM-speed goal. Move async / CI |
| **No `char_start`/`char_end` on `Chunk`** | `ingestion/chunkers.py` | Blocks click-citation→highlight-span. Must be designed at ingest |
| **No metadata filtering in retrieval** | `retrieval/retriever.py` | No per-document scoping — expected, corpus was fixed |
| Char-based chunk sizes, not token-based | `chunkers.py` | Less predictable across documents of differing character density |
| Client-side RRF | `retrieval/fusion.py` | Docstring says this is to keep weights adjustable. **Verify whether Qdrant server-side fusion now supports per-branch weights** — if so, saves a round trip |

## Gaps → the actual KnowledgeHub build

Multi-turn chat memory · user upload + document lifecycle · Postgres conversation persistence · Clerk auth and multi-tenancy · streaming · per-document scoping · cloud deploy · **injection defence**.

[[KnowledgeHub Index]] · [[Hybrid Retrieval Research]] · [[RAG Guardrails Design]] · [[KnowledgeHub Stack Decisions]]
