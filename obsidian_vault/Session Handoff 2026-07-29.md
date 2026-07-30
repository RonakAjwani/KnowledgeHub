# Session Handoff - 2026-07-29

Written because the previous session's context ran out mid-review. Start here.

## Direct answer to "is the pipeline accurate?"

**Not verified.** The plumbing is proven; the *answers* are not.

`scripts/verify_api.py` passes 32/32 against a live stack and covers upload ->
ingest -> hybrid retrieval -> a citation resolving to literal characters in
`normalized_text` -> a pronoun follow-up resolving against memory -> Postgres
persistence -> the error taxonomy. That is real, and it is what makes the system
demonstrably *work*.

But it asks **three questions**. Answer *correctness* across the ~53-question
bank in `backend/evals/questions.py` has never been measured end to end with
generation. The one partial attempt (15/25 answerable) was two sessions ago,
against text that was corrupted by the pdfplumber spacing bug, and is void.

Three changes since then all altered retrieval and **none** has been
accuracy-checked:

1. borderless tables recovered (+3 tables, commit `2067064`)
2. chunk packing (1222 -> 1054 chunks, median 54 -> 110 tokens, commit `86ffd43`)
3. conditional rerank skip disabled (every query now reranks, commit `cbb4e71`)

## THE FIRST THING TO DO

Measure answer accuracy on the current corpus:

```bash
cd backend
docker compose up -d postgres qdrant      # if not already up
AUTH_MODE=dev PYTHONPATH=. poetry run python -m evals.run --tag post-review
```

Read the failures, not just the score. `evals/run.py` grades answerable
questions on substring presence (deliberately not a judge model - a judge
sharing the pipeline's model measures consistency, not correctness) and
unanswerable ones on whether the system declined.

**Budget warning.** This is ~53 questions × (route + rewrite + generate).
Groq limits, measured:

| model | TPM | notes |
|---|---|---|
| `llama-3.3-70b-versatile` | 12,000 | **100,000 tokens/day** - invisible in headers, only in the 429 body |
| `llama-3.1-8b-instant` | 6,000 | the configured fallback |

A full eval run can exhaust the daily cap. Pace it, or run in sections
(`--section A`). `--retrieval-only` costs zero LLM calls but measures the gate,
not the answer - it will not answer the accuracy question.

## Completed this session (all committed, all verified by running)

| Commit | What |
|---|---|
| `053b363` | **CI** - the last unmet bonus. Postgres + Qdrant as service containers so the 10 integration tests run rather than skip. Evals/verify_api deliberately not gated (need live keys). |
| `8eed7f0` | `FLOOR_FUSED` resolved as a backstop (0.50). Four signals measured; none discriminates. **Disproved this repo's own earlier claim** that dense cosine was the fix. |
| `2067064` | Borderless (booktabs) tables recovered - gated on caption + no lines-detection, so it doesn't over-detect. |
| `51c191b` | Fallback model rebuilds its prompt smaller - it had 6,000 TPM against a 6,048-token request and returned 413. |
| `cbb4e71` | Conditional rerank skip **disabled** - measured, it changed the top passage 29% of the time. |
| `86ffd43` | Chunk packing - splitting existed, packing did not. |

Earlier: `c9bc3a5` README + two cold-clone fixes (no migration step in the
Docker image; missing frontend `.dockerignore`).

## Still unmeasured - the remaining review

See `Pipeline Review Log.md` for the authoritative list. In value order:

- **`fuse.py`** - `interleave_intents` vs RRF for multi-intent questions is
  reasoned, never measured. Does interleaving actually produce better
  multi-part answers than fusing? A multi-part question with a known
  three-part answer would settle it.
- **`crossref.py`** - how often cross-reference resolution actually fires on
  the corpus. If it is near zero, the caption/lead-line ladder is dead code
  carrying its own complexity.
- **`embed.py`** - `embed_batch_size` never measured against RSS on the 512 MB
  target. Matters for the deployment ceiling, not for accuracy.
- **`verify.py`** - G4 claim-verification coverage has no measured figure.

## Environment gotchas

- **`mypy` is blocked** on this machine as of mid-session: *"An Application
  Control policy has blocked this file."* It ran fine earlier the same day, so
  this is a machine policy change, not a code problem. `ruff` and all 297 tests
  still run. Do not report a typecheck that did not execute.
- Backend commands go through `backend/.venv` (`poetry run`, or
  `./.venv/Scripts/python.exe` directly). Never install globally.
- The frontend is being built in a **separate session** - do not touch
  `frontend/`. Uncommitted files there belong to that session.
- Re-ingest after any parse/chunk change: chunk ids are content-derived, so
  stale points would linger in Qdrant.
  `PYTHONPATH=. poetry run python scripts/ingest_corpus.py --reset`

## Standing conclusions - do not relearn these

- **A scalar relevance floor cannot detect "right topic, missing fact."**
  Measured across four signals. The unanswerable questions are topically
  adjacent, so retrieval correctly scores them high. G3's grounding prompt is
  the real refusal mechanism; G2 is a backstop.
- **A small biased sample is worse than no sample.** The `FLOOR_FUSED` claim
  survived two sessions on n=25 drawn only from queries that happened to skip
  rerank. Measured across all 53, it was wrong in both diagnosis and cure.
- **Measuring how often a shortcut fires is not measuring whether it is safe.**
  `DECISIVE_RATIO` was tuned to maximise savings; nobody checked what the
  skipped queries lost. They lost the top passage 29% of the time.

[[Pipeline Review Log]] · [[KnowledgeHub Index]] · [[Retrieval Pipeline Contract]]
