# RAG Techniques Evaluated

Accept/reject verdicts on techniques considered for [[KnowledgeHub Assignment Requirements]]. Rejections are as valuable as acceptances - they become README "design decisions" content.

## ❌ HyDE (Hypothetical Document Embeddings)

**Reject - likely actively harmful here.**

HyDE only helps when the hallucinated document lands in roughly the same embedding region as the real corpus. On internal documentation full of product-specific jargon, or legal/biomedical text the model hasn't memorized, the hallucination **drifts and HyDE hurts recall versus the plain query**.

KnowledgeHub retrieves over **arbitrary user-uploaded documents** - out-of-distribution by definition. That is precisely HyDE's worst case.

Second problem, specific to hybrid: HyDE produces a verbose hallucinated document. That helps the *dense* branch only, and pollutes the *sparse* branch with terms absent from the corpus. Asymmetric in the wrong direction.

**Cheap action:** the eval harness already exists - run HyDE once and report it as a *negative* result. "We tested HyDE, it cost N points of recall on user-uploaded docs" is a stronger README line than never having tried it.

## ❌ GraphRAG

**Reject - wrong cost curve for this UX.**

Reported gains: ~26% better comprehensiveness, ~11-point recall gain on multi-hop. Real, but conditional on the questions actually being multi-hop, which "chat with my uploaded documents" mostly is not.

Costs that kill it here:
- **10-40× indexing cost.** For a user who uploads a PDF and immediately asks a question, that lands directly on the moment that matters most.
- **~16.6% accuracy *drop* on time-sensitive queries** vs traditional RAG.

LazyGraphRAG / LightRAG / Fast GraphRAG cut indexing cost dramatically (LazyGraphRAG reportedly to ~0.1% of original) but add an entire subsystem. Out of scope; mention as considered-and-rejected.

## ❌ Fine-tuning embeddings on synthetic queries

**Reject for now - wrong order of operations.**

Needs a training pipeline, a held-out eval to prove it helped, and pins a model version to host and version forever. The reranker captures most of the same gain at near-zero operational cost.

Correct ordering: **bigger stock embedding model -> reranker -> *then* consider fine-tuning.** Currently on `bge-small-en-v1.5`; moving to `bge-base` / `bge-m3` is the cheap win and should be tried before any fine-tuning. Note this forces a full reindex, so decide before building.

## ✅ Contextual Retrieval (Anthropic)

**Accept, gated behind a per-document toggle.**

Prepend 50-100 tokens of LLM-generated situating context to each chunk before *both* embedding and BM25 indexing. Measured: −49% top-20 retrieval failure; −67% combined with reranking. Helps both branches - more embedding signal, more exact vocabulary for BM25.

Cost is one LLM call per chunk at ingest. Mitigate with prompt caching on the parent document. Make it a toggle so ingest speed can be demoed both ways.

## ✅ Structure-aware chunking + parent-child

**Accept, but don't pick a single chunk size.**

Structure-aware (headings, page/layout boundaries) beats fixed windows. But rather than choosing between 200-300 token chunks (precision) and larger ones (context), use **parent-child**: index small child chunks, pass the larger parent window to the LLM. Strictly better than compromising on a middle size.

Note: [[NotebookRAG Reference Project]]'s own ablation does **not** cleanly support recursive over fixed - treat this as unresolved.

## ✅ RAGAS-style faithfulness in CI

**Accept - and it's mostly already built.**

Faithfulness = proportion of claims in the answer verifiable against retrieved chunks. Standard practice is a ~50-example set, thresholds, wired into CI to catch hallucination regressions.

**NotebookRAG's judge-LLM citation verifier already is a faithfulness check.** The work is wiring it into GitHub Actions with a threshold gate - not adopting a new framework. Maps directly onto the assignment's CI bonus.

Caveat: could not confirm a specific "RAGAS 1.0" release via search. The 2026 landscape reads as RAGAS = conceptual framework, DeepEval = CI/CD ergonomics. Verify before citing a version number.

**Critical:** move the judge **out of the request path.** Inline verification adds latency to every answer and breaks the NotebookLM-speed goal. Run it async (annotate after streaming) or in CI only.

[[KnowledgeHub Index]] · [[Hybrid Retrieval Research]] · [[Agentic RAG Decision]]
