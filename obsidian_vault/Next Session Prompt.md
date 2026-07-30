# Next Session Prompt

Paste the block below into a fresh session. Written to be self-contained —
it bootstraps from the vault rather than assuming any memory of this one.

---

Act as a senior AI Engineer continuing work on KnowledgeHub — a multi-document
RAG assistant with chat memory, built as my CV assessment. The previous session
ran out of context mid-way through a pipeline review. Everything is committed.

**Read these first, in this order, before doing anything:**

1. `obsidian_vault/Session Handoff 2026-07-29.md` — the state of play, and the
   first command to run
2. `obsidian_vault/Pipeline Review Log.md` — which pipeline stages are measured
   and which are not
3. `CLAUDE.md` — working rules for this repo
4. `README.md` — what has been built and the reasoning behind it

**The one thing that matters most:** the pipeline is proven to *work*
(`scripts/verify_api.py` passes 32/32 against a live stack) but its *answer
accuracy has never been measured*. That suite only asks three questions. The
full question bank in `backend/evals/questions.py` has never been run end to end
with generation, and three changes that alter retrieval landed since the last
attempt — table recovery, chunk packing, and disabling the conditional rerank
skip. The only accuracy number this project ever had is void; it was measured
against text corrupted by a parsing bug that has since been fixed.

So your first job is to answer, with numbers: **does this thing actually
retrieve and answer accurately?**

```bash
cd backend
docker compose up -d postgres qdrant
AUTH_MODE=dev PYTHONPATH=. poetry run python -m evals.run --tag post-review
```

Read the failures, not just the score. Then fix what the failures actually show
is broken — not what looks improvable.

**Budget trap, do not get caught by it.** Groq's `llama-3.3-70b-versatile` has a
100,000 tokens/day cap that does *not* appear in the rate-limit headers — only
in the body of a 429. A full 53-question eval can exhaust it. Pace the run, or
go section by section (`--section A`). `--retrieval-only` costs zero LLM calls
but measures the gate, not the answer, so it cannot answer this question.

**After accuracy, the review still has four stages open** (details and value
order in the Pipeline Review Log): `fuse.py` interleave-vs-RRF for multi-intent,
`crossref.py` firing rate, `embed.py` batch size against RSS, and `verify.py`
G4 coverage.

**How I want you to work:**

- Measure before you change anything. A number, not "this could be better."
  Follow the pattern of the existing probe scripts in `backend/scripts/`.
- Be brutally honest and push back rather than agreeing with me. This session
  is worth more as a conversation than as compliance. The previous session
  overturned two of its own conclusions by measuring them, and that was the
  most useful thing it did — do that again if the numbers say so.
- Verify by running it, not by asserting it should work. Commit each change
  separately with the measurement that justified it in the message.
- Respect the invariants (I1 degradation never silent, I2 unknown is not zero,
  I3 user_id scopes everything, I5 offsets into `normalized_text`, I6 two-attempt
  cap, I7 no per-query renormalisation). A change that breaks one is wrong even
  if the tests pass.
- **Do not touch `frontend/`** — it is being built in a separate session, and
  the uncommitted files there belong to it.
- Backend commands run through `backend/.venv` via `poetry run`. Nothing
  installs globally.
- Re-ingest after any parse or chunk change — chunk ids are content-derived, so
  stale vectors would linger:
  `PYTHONPATH=. poetry run python scripts/ingest_corpus.py --reset`
- Note: `mypy` was blocked by a Windows Application Control policy at the end of
  the last session. If it still fails to launch, say so — do not report a
  typecheck that did not run.

Run `/loop` and prompt yourself each iteration based on the specific thing
you are tackling, rather than repeating one generic prompt. Stop and report
when you can tell me, with evidence, whether the system answers accurately —
and what you changed to get it there.
