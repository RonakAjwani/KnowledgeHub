# KnowledgeHub Index

Master index for the KnowledgeHub CV assessment — a Multi-Document RAG Assistant with Chat Memory.

**Status: research phase complete (2026-07-27).** All decisions made, all external constraints verified, pipeline contract written. Next session is planning + development.

**Contract amended 2026-07-27** after a full read of the reference codebase against the spec. Five wrinkles resolved, none of them re-opening a decision: invariant **I7** (no per-query renormalisation), §1 **single-builder rule** for `normalized_text`, §4 **two G2 thresholds** replacing one, §4 **fusion rule scoped** (within-query server-side, across-query client-side), and new §9 **LLM adapter interface**.

**Verification rounds 2 and 3 closed the same day.** Qdrant's RRF `k` is settable, so we pin it rather than inherit it; Gemini's real multimodal ceiling is TPM, not images, which makes Tier-2 escalation token-paced and capped; a LlamaParse credit is not a page, and `target_pages` makes Tier 3 cheap by reusing escalation flags we already compute; Cohere returns **402 for quota and 429 for rate**, so exhaustion trips a circuit breaker instead of retrying forever; and Render's free tier has **750 instance-hours per month**, an **ephemeral filesystem**, and a Postgres expiry that **deletes the data**. **Nothing external is blocking any longer.**

⚠️ **Two prior plans were overturned by the Render facts** — the 24/7 uptime pinger (it alone would consume ~730 of 750 hours) and any notion of a separate ingest worker service. See [[Confirmed Infrastructure Constraints]] § Three Render facts.

## Start here

1. [[Retrieval Pipeline Contract]] — **the build spec, complete.** Invariants, types, both pipelines stage-by-stage, dependency/timeout/fallback table, error taxonomy, persistence, and the SSE streaming contract (§8).
2. [[Confirmed Infrastructure Constraints]] — verified tier/limit facts. Design against these directly, don't re-derive.
3. [[KnowledgeHub Stack Decisions]] — locked technology choices.
4. [[Technology Documentation Links]] — current docs for every library in the stack. **Read the relevant entry before coding against a library, and again when a bug looks like an API mismatch.** Ronak fills the links; findings get recorded back into it.

## Research record

- [[KnowledgeHub Assignment Requirements]] — what the brief asks for and what it's actually grading
- [[Hybrid Retrieval Research]] — sparse/dense fusion, RRF vs alpha, reranking
- [[RAG Guardrails Design]] — the five guardrail layers, incl. indirect prompt injection
- [[Agentic RAG Decision]] — bounded control loop vs open agent; the LangGraph rationale
- [[Multi Turn Memory Architecture]] — query rewriting, rolling summaries, entity ledger
- [[Document Parsing And Complex PDFs]] — tables, charts, formulae; Unlimited-OCR; tiered parsing. **Amends the contract §3**
- [[RAG Techniques Evaluated]] — HyDE, GraphRAG, contextual retrieval, embedding fine-tuning: accept/reject verdicts
- [[NotebookRAG Reference Project]] — prior side project. **Reference only, not a foundation.** Patterns to reuse, issues to fix.
- [[Open Verification Questions]] — running log of external *facts* needing verification; round 1 resolved. Its counterpart for external *interfaces* is [[Technology Documentation Links]]

## Artifacts

- `architecture.svg` (project root) — **live** architecture diagram. Update whenever the design changes; will be embedded in the README. Layout contract is documented in an XML comment at the top of the file. Known to need further work.

## Working principles

- **The brief is not a spec to follow verbatim.** The assessment measures problem-solving, technical skill, and ability to build complex applications. Simplifying with a stated reason, modifying workflow to showcase strengths, and adding original touches score higher than exhaustive feature coverage. Depth over breadth; no README padding.
- **Flag unverifiable facts, don't guess.** Rate limits, free-tier boundaries and version numbers go to [[Open Verification Questions]] rather than being asserted from stale priors. Ronak verifies and supplies documents.
- **Degradation is never silent.** Every fallback is recorded and surfaced. A degraded path must never look like a healthy one.

## Decision summary

| | |
|---|---|
| Retrieval | Qdrant hybrid, dense + sparse named vectors, **server-side weighted RRF** (1.14+), single call |
| Embedding | `bge-small-en-v1.5` via fastembed, quantized ONNX, in-process — forced by the 512 MB ceiling |
| Rerank | Cohere Rerank, **conditional** (skip when fusion is decisive) + cached + fallback chain |
| Orchestration | LangGraph — bounded CRAG, hard cap of one corrective retry |
| Chunking | Parent-child; child indexed, parent generated from; char offsets into `normalized_text`. Tables atomic, headers repeated on split |
| Parsing | Tiered — local text always; complex pages escalate to a VLM through the existing LLM adapter (no GPU, no new dep) |
| Tables / figures | **Cross-reference resolution** — retrieve the author's own explanation via caption-label scanning. VLM synthesis is a marked fallback, never the default |
| Memory | Query rewriting + rolling summary + entity ledger; three separate stores |
| Guardrails | G1 topical · G2 relevance floor · G3 data-not-instructions · G4 async verify · G5 source pane |
| Auth | Clerk (`clerk-backend-api`) |
| Eval / CI | DeepEval gates in CI; RAGAS optional offline only |
| Rejected | HyDE · GraphRAG · embedding fine-tuning · client-side fusion · Railway |

## Deliberately unresolved

These are **tuning constants that cannot be chosen without a corpus.** Leaving them open is correct; picking them now would be guessing dressed as a decision. Set them in the first tuning pass once ingest + retrieval run end to end.

- RRF branch weights (`w_dense`, `w_sparse`)
- `DECISIVE_RATIO` — conditional rerank skip threshold
- `FLOOR_RERANK` and `FLOOR_FUSED` — the two G2 relevance gates. **Two, not one:** Cohere relevance and normalised RRF are different distributions, so one threshold cannot serve both. See contract §4 `grade`
- Child chunk size · parent window cap
- Route gate threshold (G1)
- `N` verbatim turns before summarisation
- **VLM page render DPI** — the direct lever on Tier-2 token cost (flat 258 tokens at ≤384 px, tiled and climbing above). Too low and tables are unreadable, too high and one document eats the TPM minute. Needs sample pages
- **Max escalated pages per document** — the TPM backstop. Must emit a degradation when hit, never truncate silently

## Next session

Planning + development. Research is closed — don't re-litigate settled architecture.

Open [[Retrieval Pipeline Contract]] first. **First implementation task: the chunk schema** — `Document.normalized_text` plus `Chunk.{char_start, char_end, parent_char_start, parent_char_end, related_spans}`. It is the one decision that cannot be retrofitted; the source-pane highlight, citation verification, and the eval harness all depend on it.
