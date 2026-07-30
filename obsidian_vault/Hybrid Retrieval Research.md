# Hybrid Retrieval Research

Research backing the retrieval design for [[KnowledgeHub Assignment Requirements]]. Most of this is already implemented in [[NotebookRAG Reference Project]].

## Why hybrid is the default, not an option

Dense and sparse fail in **near-orthogonal** ways - that's the entire reason fusion works.

- **Dense fails on:** exact identifiers, error codes, version numbers, ticker symbols, SKUs, function names, rare proper nouns, acronyms, out-of-domain vocabulary. Embeddings compress, and identifiers are exactly what compression destroys.
- **Sparse fails on:** paraphrase, synonymy, conceptual queries - anything where the answer uses different words than the question.

Evidence: on financial documents, BM25 **outperforms `text-embedding-3-large` on every metric except Recall@20**. Two-stage hybrid + neural rerank reaches Recall@5 0.816 / MRR@3 0.605, beating all single-stage methods. A 2026 production writeup reports recall@10 going 78% -> 91% with BM25 + dense + RRF + cross-encoder.

For KnowledgeHub the corpus is whatever a reviewer uploads - could favour either branch. Hybrid is the only choice that doesn't gamble.

## Sparse branch: BM25

| | BM25 | SPLADE | BM42 |
|---|---|---|---|
| Mechanism | IDF-weighted exact match | learned sparse, term expansion | attention-based sparse |
| Quality | strong; wins on identifier-heavy domains | dominates BM25 on most BEIR | does **not** beat other vendors' BM25 |
| Cost | zero inference | ~1 order of magnitude slower | model inference required |
| Verdict | ✅ **use** | overkill | ❌ Qdrant labels it experimental |

Qdrant's `Qdrant/bm25` computes IDF server-side - no separate Elasticsearch, no IDF bookkeeping in app code.

SPLADE's advantage is recall via term expansion, and a cross-encoder over a wider candidate set recovers most of that more cheaply. Paying for both is paying twice for one gain.

## Fusion: RRF vs weighted alpha

Both are answers to the same problem: BM25 scores are **unbounded**, cosine is bounded, different distributions. `0.7 + 12.4` is meaningless.

### RRF

`score(d) = Σᵢ wᵢ / (k + rankᵢ(d))`, k ≈ 60

Discards scores entirely, uses only rank - the one thing both retrievers produce on a comparable scale.

- ✅ no normalisation problem (no scores to normalise)
- ✅ immune to outliers
- ✅ zero calibration, zero drift maintenance
- ✅ per-retriever weights still available (Qdrant, ES, Weaviate all support this)
- ❌ discards magnitude

### Weighted alpha

`score(d) = α·norm(dense) + (1−α)·norm(sparse)`

Preserves magnitude, but **normalisation aligns ranges without fixing distribution mismatch** - a normalised 0.9 from BM25 and 0.9 from cosine are not the same confidence. Worse, min-max over the candidate set is *query-dependent*: the same doc normalises differently depending on what else came back. And α needs retuning whenever the embedding model, analyser, or corpus distribution changes.

### The honest number

OpenSearch benchmarked RRF vs **tuned** score normalisation over six datasets: RRF ~3.86% lower NDCG@10, with 1-2% better latency at p50/p90/p99.

### Decision

**RRF default, weights exposed as config, α not tuned.** That 3.86% gap is against a *tuned* alternative; tuning α against ~30 hand-written questions is memorising, not tuning. Adds a maintenance liability to chase an unmeasurable number.

**DBSF** (Distribution-Based Score Fusion) is Qdrant's third native option - normalises per-query on mean ± 3σ then sums. One config line, so nearly free to add as an ablation row.

## Reranking - the biggest single jump

Larger than the hybrid gain itself. Anthropic: contextual hybrid = −49% retrieval failure; + reranker = −67%.

Retrieve wide (top-40-50 fused) -> cross-encoder -> top-5. Bi-encoder/BM25 optimises recall, cross-encoder optimises precision. Don't compress early.

| Model | Type | Note |
|---|---|---|
| Cohere Rerank 4 | managed | lowest friction, 100+ languages, ~600ms |
| Jina Reranker v3 | open + API | only top-tier model <200ms (81.33% Hit@1 @ 188ms), 131k ctx, listwise over 64 docs |
| BGE-reranker-v2-m3 | open, self-host | the open-license default, free, fits Compose |
| Voyage Rerank 2.5 | managed | ~595ms |

**Non-negotiable:** if the reranker times out or errors, fall back to fused order and log it. Never fail the query on a reranker fault - this is a directly citable "proper error handling" point.

## Multi-document scoping

Metadata filtering is a first-class requirement, not a nice-to-have - it's the "Multi-" in the project title. Qdrant's filterable HNSW means pre-filtering doesn't destroy recall.

Known interaction: when filtering hard (1 of 50 docs), BM25 IDF stays corpus-wide and dense search may need higher `ef` to find enough candidates inside the subset.

Relevant: *"When More Documents Hurt RAG"* (arXiv 2606.11350) - retrieval dilution as corpus grows; document-scoped retrieval is the mitigation. Good README material for *why* scoping exists.

[[KnowledgeHub Index]] · [[RAG Techniques Evaluated]] · [[Agentic RAG Decision]]
