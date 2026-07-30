# Multi Turn Memory Architecture

The largest genuinely-new component in KnowledgeHub. [[NotebookRAG Reference Project]] is single-turn; the brief's "follow-up questions should work" is the main differentiator.

## The failure this solves

Embedding the raw follow-up turn is the default mistake. `"what about the second one?"` has no retrievable semantic content. Roughly **60% of follow-up messages carry unresolved coreferences** - this is why SemEval-2026 Task 8 exists as a shared task.

## Three separate memory stores - do not merge

| Store | Location | Scope | Purpose |
|---|---|---|---|
| Document corpus | Qdrant | per user | retrieval |
| Conversation state | Postgres | per session | history, rewriting, summaries |
| User preferences | Postgres | per user, survives sessions | tone, defaults, pinned sources |

User preferences must **never** leak into retrieval queries. Keeping them separate is an explicit architecture decision worth stating in the README.

## Query rewriting - with a hybrid-specific constraint

Rewrite each turn into a standalone query using recent history, before retrieval. Standard. But the rewrite feeds **both** retrieval branches, so:

> Resolve references by substituting the **literal prior mention verbatim**. Preserve entity names, identifiers, error codes and technical terms exactly as they appeared. Do not paraphrase, expand, or normalise terminology.

A rewrite that resolves the pronoun but paraphrases away an exact term silently damages the BM25 branch, which can only match surface forms. This constraint does not appear in most rewriting guidance because most guidance assumes dense-only retrieval.

## Nested RRF over multiple formulations

Run **both** the raw query and the rewritten query through the hybrid pipeline, then RRF the two result sets. RRF is already a primitive, so this is nearly free. SemEval-2026 systems reported nested-RRF multi-strategy rewriting improving over unaugmented baselines.

Second benefit is architectural: the rewrite is a serial LLM call on the critical path. **Firing raw-query retrieval in parallel with the rewrite hides most of that latency.** The quality design and the latency design are the same design.

## Long conversations

- **Last N turns verbatim** (N ≈ 4-6)
- **Rolling summary** of everything older
- **Entity/fact ledger** - small structured list of entities and facts established in the conversation

The entity ledger does double duty: it is both conversational memory *and* the substitution source for coreference resolution during query rewriting. One structure, two jobs.

## Retrieval gate

Not every turn needs the vector DB. "thanks", "summarise what you just said", "explain that more simply" should be answered from conversation state. A cheap classifier saves a full retrieval round trip and avoids diluting context with irrelevant chunks. This is the `route` node in [[Agentic RAG Decision]].

## Where this is specified

Node-level contracts for `route`, `rewrite` and the memory stores live in [[Retrieval Pipeline Contract]] §4. This note holds the reasoning; that note holds the interface.

[[KnowledgeHub Index]] · [[Hybrid Retrieval Research]] · [[Retrieval Pipeline Contract]]
