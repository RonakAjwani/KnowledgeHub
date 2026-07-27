# KnowledgeHub Assignment Requirements

Source brief: `AI_Engineer_Assignment.md`. Project = Multi-Document RAG Assistant with Chat Memory.

## Stated requirements → what's actually graded

| Stated | Actually testing |
|---|---|
| Upload/manage **multiple** documents (PDF/txt/md) | Metadata scoping — can a user query one document or a subset? Corpus-wide-only is a fail |
| Chunk + embed + store in vector DB | Table stakes, no points |
| Multi-turn chat, **"follow-up questions should work"** | **The main trap** — see [[Multi Turn Memory Architecture]] |
| Answers grounded, **with source citations** | Are citations *verifiable* (span → chunk → doc) or `[Source 1]` hallucinated into the answer text? |
| Store conversations in a DB | Schema design — separately queryable messages/retrievals/citations, or one JSON blob? |
| Clean REST API, **proper error handling** | Explicitly graded: consistent error envelope, correct status codes, no leaked stack traces, graceful dependency degradation |

## Deliverables

GitHub repo · README (setup + architecture + design decisions) · deployed live link (or Docker Compose fallback) · 5–8 min demo video · basic tests.

## Bonus list

Streaming · auth · hybrid search/re-ranking · CI pipeline.

Note: **hybrid search is listed as bonus**, so the baseline expectation is dense-only. Making it default is right, but complexity without justification reads as over-engineering. The justification artifact is the ablation table.

## Non-functional (graded but unstated)

- Idempotent ingest — re-uploading a file must not duplicate vectors
- Async ingest — a 200-page PDF cannot block an HTTP request
- Every external dependency (reranker, Qdrant, LLM) needs a timeout **and a fallback path**
- Refusal behaviour on unanswerable queries — hallucinating here is the worst demo failure
- Deletion actually deletes: Postgres rows + Qdrant vectors + blob

## Data model sketch

`documents` → `chunks` (mirror of Qdrant, for citation resolution) → `conversations` → `messages` → `message_citations` (join to chunks, with score + rank).

`message_citations` is what makes citations verifiable and gives the retrieval trace for free.

## The highest-leverage artifact

A four-row ablation table measured on the project's own corpus:

| Config | Recall@5 | Recall@10 | MRR@10 |
|---|---|---|---|
| Dense only | | | |
| BM25 only | | | |
| RRF fusion | | | |
| RRF + rerank | | | |

Goes in the README and is the strongest 45 seconds of the demo video. [[NotebookRAG Reference Project]] already has a harness and a version of this.

## Golden set composition (25–40 questions)

- ~10 exact-match / identifier → BM25 should win
- ~10 paraphrase / conceptual → dense should win
- ~10 multi-turn follow-ups with coreferences → tests the rewrite stage
- ~5 **unanswerable** → tests abstention

## Timeline

Brief estimates 1–3 days. **Explicitly deprioritised** — focus is development quality, not deadline.

[[KnowledgeHub Index]] · [[KnowledgeHub Stack Decisions]]
