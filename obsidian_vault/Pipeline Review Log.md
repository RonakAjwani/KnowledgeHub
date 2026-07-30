# Pipeline Review Log

Stage-by-stage review of the RAG pipeline against the real 6-document corpus.
**Measurement before change** - a stage is only "reviewed" here once there are
numbers for it, and several proposals died on contact with those numbers.

Started 2026-07-29.

## Reviewed - measured and closed

| Stage | Finding | Outcome |
|---|---|---|
| `parse.py` tables | Captions vs detections per document: 24551 captions 3 / detects **0**; 24512 captions 1 / detects 1. Text strategy alone finds 16 where 1 exists, and drops another doc 50 -> 9. | **Fixed.** Fallback detector gated on *(no lines-detection on this page) AND (page text carries a table caption)*. +3 tables recovered, byte-identical output on the 3 documents already correct. |
| `nodes.grade` / `FLOOR_FUSED` | Four signals compared over all 53 questions: fused-all +0.85 sep / 79%; fused-top5 +0.89 / 74%; dense-all +0.60 / 81%; dense-top5 +0.72 / 81%. Decline population sits *inside* the answerable range in all four. | **Resolved as a backstop (0.50).** No threshold discriminates - the unanswerable questions are topically adjacent, so retrieval correctly scores them high. Refusal is G3's job. Disproved an earlier n=25 claim that dense cosine was the fix. |
| LLM fallback budget | Groq headers: primary 12,000 TPM, fallback 6,000. Our request is 4000 + 2048 = 6048 before overhead -> **413** on the fallback, and 413 is not retryable. | **Fixed.** Fallback rebuilds its prompt at `max_context_tokens_fallback` (2000). Verified live at a full bucket: 1,539-token prompt, status 200. |
| `qdrant_store.branch_search` | Docstring claimed "not used on the request path"; `_search` calls it on every query for cross-branch agreement. | Docstring corrected. |
| `rerank.py` conditional skip | Skip rate re-confirmed at 42% post-re-ingest. Then `probe_skip_cost.py` forced Cohere on every agreement query: at 1.02 the reranker promotes a *different* top passage 29% of the time, and top-5 overlap never exceeds 56% at **any** threshold. | **Disabled** (`DECISIVE_RATIO=99`). The premise - agreement means a cross-encoder won't overturn it - is false here. Budget it protects doesn't bind under 1,000 queries; mechanism stays configurable. |
| `chunk.py` sizing | 61% of chunks under half the 250-token target, median 54, 30.6% under 25 tokens. `_split_prose` split oversized blocks but nothing packed undersized ones. | **Fixed.** `_packed_prose` merges adjacent same-section prose. Median 54 -> 110, chunks 1222 -> 1054, zero offset violations. |

## 2026-07-30 - the first end-to-end accuracy measurement

Measured on the curated 22-question set against the 4-document corpus.
**Generation model was `openai/gpt-oss-20b`, not the configured default** -
`llama-3.3-70b-versatile` had exhausted its daily cap (`TPD: 100000, Used
99606`), which the per-minute header does not expose. The numbers describe that
model; re-run on the deployed provider before quoting them anywhere.

| Measurement | Result |
|---|---|
| Retrieval - every needed document reached the context | **16/17** |
| Answer - correct where retrieval succeeded | **10/16** |
| Refusal - declined when the corpus does not hold the answer | **4/5** |
| Citation - stated fact present in the passage its own claim cites | **7/13** |
| G4 coverage - factual claims carrying any citation | **14/67** |

**Retrieval is not the bottleneck; generation is.** The right passage was in
front of the model 94% of the time and it still got six of those wrong. Splitting
the three measurements is what made that visible - a single "accuracy: 59%"
would have sent the next session to tune retrieval, which is the one stage
already working.

### The dominant failure has one root cause

A2 answered `55.0` for Manufacturing PMI in **February**; E2 hallucinated
`26.2%` for a June cell that is **blank**. Both numbers are real values from the
same row, in the wrong column:

    Macro-Economic Indicators  June-26 May-26 Apr-26 Mar-26 Feb-26 Jan-26
    Manufacturing PMI            54.2   55.0   54.7   53.9   56.9   55.4

55.0 is May; 26.2 is January. **The parse is correct and the binding is still
lost**: flattened to prose, a row is a sequence of numbers with nothing tying
each to its header, and a sparse row (two-wheeler has five values under six
headers) cannot even be counted positionally. That page has *zero* detected
tables, so it never reaches the Markdown path where alignment survives.

Not fixed, deliberately. Extending borderless detection is the obvious move and
this log already records why it is dangerous: text strategy alone finds 16
tables where 1 exists and drops another document 50 -> 9. A measured limitation
beats a same-day regression in the three documents that parse cleanly.

### Two bugs found in the measurement itself, both fixed

- **`split_claims` dropped trailing citations.** Models write `"...as of July
  2026. [1][3]"`; the sentence splitter broke on the terminator and discarded
  the marker fragment. Coverage read `7/68` - an artifact. This is production
  code (`Verifier.verify` uses it), so G4 under-reported on every real turn and
  left checkable citations `null` (I2). Fixed: coverage `7/68 -> 14/67`,
  supported citations `5/14 -> 7/14`, dangling `1 -> 0`.
- **The decline grader missed an inflection.** `"not reported"` was present,
  `"do not report"` was not, so E6 was scored a hallucination for declining
  exactly as designed. Refusal `3/5 -> 4/5`.

Both moved the number *down* before they were found. A grader that cannot see a
correct refusal does not flatter the system - it blames the wrong stage, and the
next person tunes a floor that was never the problem.

### Still open, with evidence now attached

- **D6** answered the MathModDB half of a two-intent question and dropped the
  LegalKG half. First hard evidence on the `fuse.py` interleave-vs-RRF item.
- **M1**'s follow-up ("Who manages it?") found nothing, though the parse fix
  demonstrably recovered `Mayur Patel` and `Viral Mehta`. Coreference resolves,
  retrieval does not.
- **F2** abstained at relevance 0.064 on "summarize what langchain.md is" - an
  over-refusal on a question the corpus plainly answers.
- **D1** is the only retrieval miss: needed `langchain.md`, got MathModDB only.
- **D5** abstained as the labelled expected-hard case. Aggregation over a whole
  corpus is outside top-k's reach by construction; it is in the set to be
  measured, not to be fixed.

## Not yet measured

Listed so the gap is visible rather than implied-complete:

- `embed.py` - `embed_batch_size` never measured against RSS on the 512 MB
  target.
- `fuse.py` - the interleave-vs-RRF split for multi-intent is reasoned, not
  measured; no numbers on whether interleaving actually improves multi-part
  answers over fusing them.
- `crossref.py` / `sanitize.py` / `normalize.py` - the offset chain is covered
  by the property test, but no corpus-level numbers on how much
  cross-reference resolution actually fires.
- `prompts.py` / `verify.py` - G3 delimiting is tested for escape correctness;
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
  well-justified - but nothing had checked whether the skipped queries lost
  anything. They did, on ~30% of them. Ask what a change *costs*, not only what
  it saves.

[[KnowledgeHub Index]] · [[Retrieval Pipeline Contract]]
