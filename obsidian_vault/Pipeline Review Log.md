# Pipeline Review Log

Stage-by-stage review of the RAG pipeline against the real 6-document corpus.
**Measurement before change** — a stage is only "reviewed" here once there are
numbers for it, and several proposals died on contact with those numbers.

Started 2026-07-29.

## Reviewed — measured and closed

| Stage | Finding | Outcome |
|---|---|---|
| `parse.py` tables | Captions vs detections per document: 24551 captions 3 / detects **0**; 24512 captions 1 / detects 1. Text strategy alone finds 16 where 1 exists, and drops another doc 50 → 9. | **Fixed.** Fallback detector gated on *(no lines-detection on this page) AND (page text carries a table caption)*. +3 tables recovered, byte-identical output on the 3 documents already correct. |
| `nodes.grade` / `FLOOR_FUSED` | Four signals compared over all 53 questions: fused-all +0.85 sep / 79%; fused-top5 +0.89 / 74%; dense-all +0.60 / 81%; dense-top5 +0.72 / 81%. Decline population sits *inside* the answerable range in all four. | **Resolved as a backstop (0.50).** No threshold discriminates — the unanswerable questions are topically adjacent, so retrieval correctly scores them high. Refusal is G3's job. Disproved an earlier n=25 claim that dense cosine was the fix. |
| LLM fallback budget | Groq headers: primary 12,000 TPM, fallback 6,000. Our request is 4000 + 2048 = 6048 before overhead → **413** on the fallback, and 413 is not retryable. | **Fixed.** Fallback rebuilds its prompt at `max_context_tokens_fallback` (2000). Verified live at a full bucket: 1,539-token prompt, status 200. |
| `qdrant_store.branch_search` | Docstring claimed "not used on the request path"; `_search` calls it on every query for cross-branch agreement. | Docstring corrected. |
| `rerank.py` conditional skip | Skip rate re-confirmed at 42% post-re-ingest. Then `probe_skip_cost.py` forced Cohere on every agreement query: at 1.02 the reranker promotes a *different* top passage 29% of the time, and top-5 overlap never exceeds 56% at **any** threshold. | **Disabled** (`DECISIVE_RATIO=99`). The premise — agreement means a cross-encoder won't overturn it — is false here. Budget it protects doesn't bind under 1,000 queries; mechanism stays configurable. |
| `chunk.py` sizing | 61% of chunks under half the 250-token target, median 54, 30.6% under 25 tokens. `_split_prose` split oversized blocks but nothing packed undersized ones. | **Fixed.** `_packed_prose` merges adjacent same-section prose. Median 54 → 110, chunks 1222 → 1054, zero offset violations. |

## Not yet measured

Listed so the gap is visible rather than implied-complete:

- `embed.py` — `embed_batch_size` never measured against RSS on the 512 MB
  target.
- `fuse.py` — the interleave-vs-RRF split for multi-intent is reasoned, not
  measured; no numbers on whether interleaving actually improves multi-part
  answers over fusing them.
- `crossref.py` / `sanitize.py` / `normalize.py` — the offset chain is covered
  by the property test, but no corpus-level numbers on how much
  cross-reference resolution actually fires.
- `prompts.py` / `verify.py` — G3 delimiting is tested for escape correctness;
  G4 claim verification has no measured coverage figure.

## Standing conclusions

- **A scalar relevance floor cannot detect "right topic, missing fact."**
  Measured across four signals. This is a property of retrieval scoring, not a
  tuning miss, and it is why G3's grounding prompt is the real refusal
  mechanism and G2 is a backstop.
- **A small biased sample is worse than no sample.** The `FLOOR_FUSED` claim
  that stood in this repo for two sessions came from n=25 drawn only from
  questions that happened to skip rerank in one run. Measured uniformly across
  53, it was wrong in both its diagnosis and its proposed fix.
- **Measuring how often a shortcut fires is not measuring whether it is safe.**
  `DECISIVE_RATIO` was tuned to maximise Cohere savings, and the number looked
  well-justified — but nothing had checked whether the skipped queries lost
  anything. They did, on ~30% of them. Ask what a change *costs*, not only what
  it saves.

[[KnowledgeHub Index]] · [[Retrieval Pipeline Contract]]
