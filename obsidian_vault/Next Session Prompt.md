# Next Session Prompt

Paste the block below into a fresh session. Self-contained - bootstraps from
the vault rather than assuming memory of the previous session.

**Two decisions were settled before writing this** (see the end of this note for
the reasoning): the multi-account API-key rotation is rejected, and the corpus
shrinks to 3-4 documents with a curated question set.

---

Act as a senior AI Engineer continuing work on KnowledgeHub, a multi-document
RAG assistant with chat memory, built as my CV assessment. The previous session
ran out of context mid-way through a pipeline review. Everything is committed.

Read these first, in this order, before doing anything:

- `obsidian_vault/Session Handoff 2026-07-29.md` - the state of play and the
  first command to run
- `obsidian_vault/Pipeline Review Log.md` - which pipeline stages are measured
  and which are not
- `CLAUDE.md` - working rules for this repo (may be stale in places; check it
  against the code rather than trusting it)
- `README.md` - what has been built and the reasoning behind it

**The one thing that matters most:** the pipeline is proven to *work*
(`scripts/verify_api.py` passes 32/32 against a live stack) but its *answer
accuracy has never been measured*. That suite only asks three questions. The
full question bank in `backend/evals/questions.py` has never been run end to end
with generation, and three changes that alter retrieval landed since the last
attempt - table recovery, chunk packing, and disabling the conditional rerank
skip. The only accuracy number this project ever had is void; it was measured
against text corrupted by a parsing bug that has since been fixed.

There are **three** distinct things to measure, and collapsing them into one
word ("accuracy") is how the third gets skipped:

1. **Retrieval** - is the right passage actually found? (partially measured)
2. **Answer** - is the answer correct? (never measured end to end)
3. **Citation faithfulness** - does `[1]` actually *support* the sentence it is
   attached to? (never measured; `verify_api.py` only checks that a citation
   resolves to real characters, not that those characters back the claim. G4 in
   `app/graph/verify.py` does claim-level verification and has no measured
   coverage figure.)

**First task - shrink the corpus and curate the question set.** Six
heterogeneous documents make a failure impossible to attribute, and a reviewer
will realistically upload two or three files and ask a handful of questions, not
53. Cut to 3-4 documents that between them still cover every angle:

- a borderless-table paper (`2607.24512v1.pdf` answers well already)
- a ruled-table financial document (`360_ONE_MF...pdf` - 22 pages, faster to
  iterate on than TSLA's 43)
- `langchain.md` for the non-PDF path
- optionally a fourth paper, so cross-document synthesis stays genuinely
  testable - keep at least three with real topical overlap, or "multi-document"
  stops being something the system is tested on at all

Do **not** rewrite the questions from scratch. The existing entries carry
`must_include` substrings already validated against the corpus, and that is the
expensive part. Curate roughly 20 that cover the angles a reviewer would
probe - exact-value lookup, table lookup, paraphrase, multi-part question,
cross-document synthesis, follow-up with a pronoun, and unanswerable - then add
targeted ones only for gaps the curation exposes.

**On API limits:** do not build multi-account key rotation. It violates the
providers' terms, risks suspending accounts that are not only mine, and would
sit in the repo that is itself the thing being assessed. The legitimate headroom
is already architected: Groq meters **per model**, so `llama-3.3-70b-versatile`
(100k tokens/day), `llama-3.1-8b-instant`, `qwen/qwen3.6-27b` and
`openai/gpt-oss-20b` each have separate buckets on one key, and
`MODELS_BY_PROVIDER` / `llm_model_generate_fallback` already exist to route
between them. Spreading eval load across models is fine. A smaller corpus and a
curated question set cut the cost roughly in half again. If that still is not
enough, say so and I will enable billing - it is cents at this volume.

**Then answer, with numbers: does this pipeline actually retrieve and answer
accurately?**

```bash
cd backend
docker compose up -d postgres qdrant
AUTH_MODE=dev PYTHONPATH=. poetry run python -m evals.run --tag post-review
```

Read the failures, not just the score. Then fix what the failures actually show
is broken, not what merely looks improvable.

**After accuracy**, four review stages remain open (details and value order in
the Pipeline Review Log): `fuse.py` interleave-vs-RRF for multi-intent,
`crossref.py` firing rate, `embed.py` batch size against RSS, and `verify.py`
G4 coverage.

How I want you to work:

1. Help me make the decisions, and tell me when you think I am wrong.
2. Measure before you change anything. A number, not "this could be better."
   Follow the pattern of the existing probe scripts in `backend/scripts/`.
3. Be brutally honest and push back rather than agreeing with me. This session
   is worth more as a conversation than as compliance. The previous session
   overturned two of its own conclusions by measuring them, and that was the
   most useful thing it did - do that again if the numbers say so.
4. Verify by running it, not by asserting it should work. Commit each change
   separately with the measurement that justified it in the message.
5. Respect the invariants (I1 degradation never silent, I2 unknown is not zero,
   I3 `user_id` scopes everything, I5 offsets into `normalized_text`, I6
   two-attempt cap, I7 no per-query renormalisation). A change that breaks one
   is wrong even if the tests pass.
6. Do not touch `frontend/` for now - it is complete and waiting to be wired to
   the backend, and the uncommitted files there belong to that work.
7. Backend commands run through `backend/.venv` via `poetry run`. Nothing
   installs globally.
8. Re-ingest after any parse or chunk change - chunk ids are content-derived, so
   stale vectors would linger:
   `PYTHONPATH=. poetry run python scripts/ingest_corpus.py --reset`
9. `mypy` was blocked by a Windows Application Control policy at the end of the
   last session. If it still will not launch, say so - do not report a typecheck
   that did not run.

Run `/loop` and prompt yourself each iteration based on the specific thing you
are tackling, rather than repeating one generic prompt. Stop and report when you
can tell me, with evidence, whether the system retrieves accurately, answers
accurately, and cites faithfully - and what you changed to get it there.

---

## Why the two decisions went this way

**Multi-account API keys - rejected.** Using additional accounts to get around
free-tier quota is prohibited by the providers' terms; the exposure is account
suspension, and not only the owner's. It would also live in the repo that is
itself the artifact under assessment - every other constraint in this project
was met by measuring and adapting, and this would be the single place that
routed around one instead. It is also unnecessary: Groq's per-model daily
buckets plus a smaller corpus already cover the need, and paid tier at this
volume costs cents.

**Corpus reduction to 3-4 documents - accepted, and for better reasons than
quota.** Six heterogeneous documents make failures unattributable, and slow
every iteration. Fewer documents means faster ingest, faster measurement cycles,
and failures that point somewhere specific. The constraint is a floor, not a
ceiling: keep at least three with genuine topical overlap or multi-document
synthesis - the thing the project is named after - stops being tested.
