# Session Handoff - 2026-07-30

Supersedes [[Session Handoff 2026-07-29]], whose headline ("accuracy is NOT yet
verified") is now out of date. Start here.

## State

Pushed to `github.com/RonakAjwani/KnowledgeHub`, branch `main`. Working tree
clean. Not yet deployed.

| Gate | Result |
|---|---|
| Backend tests | 309 pass, ruff clean |
| `scripts/verify_api.py` | 32/32 against Qdrant Cloud |
| `scripts/probe_guardrails.py` | 8/8 adversarial |
| Frontend | tsc + eslint clean, production build passes, PDF viewer verified in a browser |
| `mypy` | **never ran** - "An Application Control policy has blocked this file", every attempt, all day |

## The only task left

Switch the provider to Anthropic. The key is bought; the wiring is already in
`MODELS_BY_PROVIDER`, so no code change is needed.

```
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
MAX_ANSWER_TOKENS=4096      # see below - this one bites silently
MAX_ESCALATED_PAGES=0       # see below - this one bites during a demo
```

**`MAX_ANSWER_TOKENS=4096`.** `claude-sonnet-5` runs adaptive thinking whenever
the `thinking` parameter is omitted, which this adapter does, and `max_tokens`
caps thinking *plus* answer text together. At the current 2048 an answer can
stop mid-sentence, which reads as a content failure rather than a token limit.

**`MAX_ESCALATED_PAGES=0`.** Groq exposes no vision model, so Tier-2 page
escalation has **never executed in any measurement this project has taken** -
visible as the `degraded [unavailable] 1 page(s) needed a vision model` line in
every ingest. The Anthropic row sets `vlm`, so it fires for the first time on
the first upload after switching. It changes parsed text, therefore changes
content-derived chunk ids, therefore mandates a `--reset` re-ingest. Leave it at
0 for the demo and enable it deliberately afterwards, not during a recording.

After switching, re-run in this order. Each failure tells you something
different, so do not jump to the end:

```bash
cd backend
PYTHONPATH=. poetry run python scripts/probe_rrf_rank_base.py   # Qdrant sane
PYTHONPATH=. poetry run python scripts/ingest_corpus.py --reset
PYTHONPATH=. poetry run python scripts/probe_golden_set.py      # facts survived parsing
PYTHONPATH=. poetry run python scripts/verify_api.py            # 32 checks
PYTHONPATH=. poetry run python scripts/probe_guardrails.py backend/scripts/fixtures_poisoned_document.md
AUTH_MODE=dev PYTHONPATH=. poetry run python -m evals.run --tag anthropic
```

The last one costs about $0.18 and matters: every accuracy number on record was
measured on `openai/gpt-oss-20b`, because `llama-3.3-70b-versatile` had
exhausted its 100k-token daily cap. Re-run the guardrail probe too - injection
resistance is model behaviour, not only prompt structure.

## What today changed, in one line each

- **Half the 360 ONE factsheet was being deleted.** Text beside a table was
  never read. Coverage 50.0% -> 62.5%; held-out CMVF 95.9% -> 97.2%.
- **Every table row carried the row above's numbers.** Figures sat 1.4pt higher
  than their label and a `(top, x0)` sort put them first.
- **Blank cells collapsed**, so the model read February's PMI as May's and
  invented a figure for an empty June cell. Recovered by clustering right edges
  (numeric tables are right-aligned). A2 and E2 both pass now.
- **`split_claims` dropped trailing citations**, under-reporting G4 coverage on
  every real turn. `7/68 -> 14/67`.
- **Qdrant had no retry.** DNS to the managed cluster failed 1 in 12 attempts,
  killing an ingest and a delete. 31/32 -> 32/32.
- **Source pane renders the uploaded format** - PDFs via pdf.js, Markdown
  rendered - with the exact-offset text view kept as a second tab.

## Standing conclusions - do not relearn these

- **Three graders were wrong before the pipeline was.** A decline marker missing
  an inflection scored a correct refusal as a hallucination; a disjunctive
  grader let a third of a multi-part answer pass; an abstain frame's `reason`
  was scraped as prose and read as a hallucination. All three moved the number
  the *wrong* way. When a result looks bad, check the grader first.
- **Retrieval is not the bottleneck; generation is.** The right passage was in
  context 94% of the time and six of those answers were still wrong. A single
  accuracy figure hides this and sends the next session to tune the one stage
  already working.
- **A probe that ignores configuration is worse than no probe.**
  `probe_rrf_rank_base.py` hardcoded localhost and would have certified a cloud
  cluster it never touched.

## Known limitations, measured rather than guessed

- **D5** (which manager appears across the most fund pages) is unanswerable by
  top-k retrieval by construction. Kept in the set labelled, to be measured, not
  fixed. Avoid it in a demo.
- **A3** reads a different fund's AUM row - retrieval precision across 20
  near-identical fact-pages. Untested since the table fix; different root cause,
  so do not expect that fix to have helped.
- The **highlight in the source pane's Original tab is best-effort**: it matches
  cited text against pdf.js's text layer because no bounding boxes are
  persisted. The Text tab's highlight is offset-driven and cannot be wrong.

[[Deployment Setup Checklist]] · [[Pipeline Review Log]] · [[KnowledgeHub Index]]
