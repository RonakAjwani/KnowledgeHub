# Session Handoff - 2026-07-31

The Anthropic switch, and the fixes it exposed. Read
[[Pipeline Review Log]] for the measurements behind each claim and
[[KnowledgeHub Stack Decisions]] for the provider decision itself.

## State

**Provider is Anthropic and it is the default in code, not just in `.env`.**
Haiku 4.5 for route / rewrite / verify / generate-fallback, Sonnet **4.6** for
generation and VLM escalation.

| Run | Golden set | Median turn |
|---|---|---|
| Groq `llama-3.3-70b-versatile` | 13/22 | 60.0s |
| Anthropic `claude-sonnet-4-6` | **18/22** measured, **19/22** projected | 8.8s |

Tests **327 backend + 66 frontend**, ruff and mypy clean.

**Sonnet 4.6 and not Sonnet 5, deliberately.** Omitting the `thinking`
parameter - which the adapter does - means *no thinking* on 4.6 but *adaptive
thinking* on Sonnet 5, where it is the default rather than an opt-in. Thinking
tokens come out of the same `max_tokens` bucket as the answer, so on Sonnet 5
the reasoning budget silently competes with `max_answer_tokens` - the same
failure that took `gpt-oss-120b` out of contention. Do not "upgrade" to Sonnet 5
without re-reading that note.

## ⚠️ The one thing that is NOT verified

**The full golden set has not been re-run since the last two changes** - the
`overview` route and the arithmetic rule in `GENERATE_SYSTEM`. Unit tests are
green and both were spot-checked live, but there is no full-suite confirmation
that neither regressed the other 20 questions. This is the first thing to do:

```bash
cd backend && PYTHONPATH=. poetry run python evals/run.py --tag post-overview
```

Roughly $0.65 on Sonnet 4.6. Compare against `evals/results/anthropic-sonnet46.json`.

## What changed this session

**Pipeline**

- `GENERATE_SYSTEM` gained a *do the arithmetic* rule. B6 laid out `105` and
  `59` and left the subtraction to the reader; the prompt never licensed
  computing a number that is not written anywhere. Now answers "46 more
  (105 - 59 = 46)". Fixes any "how many more / total / share" question.
- **New `overview` route.** Orientation questions - "what are these documents
  about", "summarise X", "which documents do I have" - scored 0.05-0.19 against
  a 0.35 floor and abstained. Six of seven tested. The reranker scores
  *passages* for topical relevance and no passage is relevant to a question
  about a document as a whole, so the floor is the wrong test rather than a
  mistuned one. `grade_node` skips the floor for this route; G3's grounding
  prompt still does the actual refusing. Verified: **0 of 5** should-decline
  questions misroute into it, **7 of 7** orientation questions are captured.
- **Overview retrieves per document, not by rank.** Ranked search answers "what
  is closest", which is wrong for coverage: one global search returned twelve
  chunks from **two** of four documents, and no amount of extra budget changed
  it. `_search_per_document` fans out one search per document at
  `overview_chunks_per_doc` (3) each. "Which documents do I have" now names all
  four with descriptions and citations. Needs `Deps.list_docs`, wired in
  `api/chat.py` and `evals/run.py`; without it the route degrades to ranked
  search rather than breaking.
- `max_context_tokens_overview` (8000). MEASURED: at 4000 only 4 of 12 chunks
  reached the prompt because `fit_context` binds before the chunk count; at 8000
  it is 9 chunks across 3 documents. Raising `max_context_chunks` alone does
  nothing - the two only work together.
- `timeout_llm_route_s` 2.0 -> 4.0. 2.0 was sized against Groq's ~0.2s route
  call; Haiku measures median 1.30s, max 2.52s, so it was timing out the slowest
  quarter of routes and emitting `route/timeout` on healthy traffic.

**Eval harness - two grader bugs, both of which moved the number the wrong way**

- `looks_like_decline` scanned the **whole** answer, so three fully-cited
  correct answers that closed by naming what the documents do not cover scored
  as refusals. Split into `looks_like_decline` (permissive, for should-decline
  questions) and `is_refusal` (strict, for answerable ones - no citations
  anywhere *and* a decline phrase in the opening two claims).
- Markdown emphasis split the phrase: `**not available** in` never matched
  `not available in`, so a correct refusal scored as a hallucination. Emphasis
  is stripped before matching now.
- Stored answers are no longer truncated at 400 chars. Both bugs sat past that
  cutoff and cost a paid rerun to see.
- `tests/test_eval_grading.py` - 18 cases, fixtures taken from real generation
  output. Nothing tested the grader before.

## Known limits - tell anyone testing manually

1. **English only.** `bge-small-en-v1.5`. A non-English corpus will retrieve
   badly and it will look like a pipeline bug.
2. **`FLOOR_RERANK` 0.35 is fit to this corpus.** A corpus where Cohere scores
   run lower will over-refuse. A11 is the live example: right chunk at **rank 1,
   score 0.381**, gated because the `0.6*max + 0.4*mean` blend lands at 0.323.
   The sweep says 0.30 keeps 16/17 answerable and gates the identical 2/5
   declines - a defensible change, deliberately not taken because it is
   corpus-fit.
3. **No semantic chunking and no overlap** (criteria 1 and 5 of the reference
   architecture). Structure-aware + parent-child instead - see below. Documents
   with no headings at all (transcripts, plain prose) degrade to sentence
   packing.
4. **Scanned PDFs** work - Tier-2 escalation is verified live on Anthropic, it
   read a figure and described the chart series - but it is capped by
   `max_escalated_pages` and paced on tokens. A long scanned document will be
   partly Tier-1, with a visible degradation.
5. **Cohere trial is 1,000 calls/month at 10 rpm.** Heavy manual testing can
   trip it; 402 is quota and opens the circuit breaker, 429 is rate and backs
   off.
6. **Anthropic budget was roughly $3 remaining** at the end of this session.

## The chunking question, settled with evidence

Measured on the 710-chunk eval corpus:

```
tokens: min=3  p25=33  median=69.5  p75=201  max=410   (target 250)
metadata: section 99.9%   page 90.7%   parent window 100%
types:    prose 552   table 158
```

The median looks alarming until it is split by document: the two papers and
`langchain.md` sit at 167 / 215 / 180 against a 250 target. The 360 ONE
factsheet is 524 of 710 chunks at median 46 - which is what a factsheet *is*,
short labelled fields, and it is not hurting anything (the Net AUM lookup
answers at 0.971). Sub-25-token chunks are protected by the section path being
prepended to the embedded text before it is vectorised.

**Against the reference architecture:** structure-aware splitting, table
isolation with the header repeated per fragment, and metadata enrichment are
implemented and measured. Semantic chunking and controlled overlap are not.
Parent-child is a deliberate substitution for overlap - embed the small precise
child, hand the model the enclosing section - and covers most of what overlap
buys, since two halves of a split fact usually share a parent.

**No measured failure traces to chunking.** Retrieval put the right passage in
front of the model on **17/17** answerable questions; every failure landed at
the gate, the router, the grader, the prompt, or one bad question. The BGE
asymmetric query prefix - the classic silent recall killer - is correctly
applied via `query_embed`.

If overlap is added later: it is a parameter in the prose splitter, it does not
change the offset model, but it changes every chunk ID (`sha256(doc_id|index|
text)`) and so forces a full re-ingest plus a fresh eval run.

## Still open

- [ ] **Run the full eval** (above). Highest priority.
- [ ] `FLOOR_RERANK` 0.30 - measured, defensible, not taken. Decision needed.
- [ ] **D6 is a bad question.** `LegalKG` appears in **0 chunks** - it is a
      nickname `evals/corpus.py` assigns to `2607.24551v1.pdf`, which calls
      itself *French Legal Knowledge Graph*. Retrieval returns the passage
      holding the answer at rank 1 (0.79), and the model correctly reports that
      no "LegalKG pipeline" is mentioned. Asked in the document's own words it
      grades `pass` at 0.87. Fix the question or drop it.
- [ ] **F2** is the same architectural class the `overview` route now handles;
      re-check whether it passes after a full run.
- [ ] Nothing is committed. 12 files changed plus
      `tests/test_eval_grading.py` and this note.

[[KnowledgeHub Index]] · [[Pipeline Review Log]] · [[KnowledgeHub Stack Decisions]] · [[Session Handoff 2026-07-30]]
