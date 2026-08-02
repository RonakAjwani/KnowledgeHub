# Session Handoff - 2026-08-02

Three bugs found while recording the product walkthrough video, all root-caused
and fixed same-day, plus a fourth (the Sources footer) caught in the last
pre-deploy pass. **Nothing is committed** - every change below is still
sitting uncommitted in the working tree. Two more bugs surfaced right at the
end of recording and are logged but not investigated - see the bottom.

## Bug 1 - "unauthenticated" errors mid-chat, workspace sometimes fails to load

**Symptom:** a chat turn would fail with `Token has expired and is no longer
valid` / `unauthenticated`, and separately the workspace sidebar/grid would
sometimes fail to load on open. Ronak suspected the Render->Azure migration.

**Investigated:** checked commit `5872a7c` ("Migrate deploy target references
from Render to Azure Container Apps") diff in full - it only renames a config
`Literal["render"]` to `Literal["azure"]` and rewords comments. Zero lines
touch `auth.py`, Clerk config, CORS, or JWKS. Ruled out as the cause,
consistent with Ronak's own report that this happened before the migration too.

**Root cause:** `WorkspaceSidebar.tsx` and `WorkspaceGrid.tsx` both fired their
`useQuery` for the workspace list on mount with no guard on Clerk's
`isLoaded`/`isSignedIn` state. A query that fires before Clerk has hydrated a
session gets a token the backend correctly rejects as expired - a mount-time
race, not a real expiry. `useChatStream.ts`'s `send()` called the same
ungated `getToken()`. Compounding it: `app/providers.tsx`'s React Query client
never retries any 4xx including 401, so the race, once hit, stayed failed
until a manual reload.

**Fix:**
- `frontend/hooks/useSessionToken.ts` - added `useSessionTokenState()`,
  exposing `isLoaded`/`isSignedIn` alongside the token getter (dev mode
  reports itself as always loaded/signed-in). Old `useSessionToken()` kept as
  a back-compat shorthand.
- `WorkspaceSidebar.tsx`, `WorkspaceGrid.tsx` - gated `workspacesQuery` on
  `enabled: isLoaded && isSignedIn`; switched their `showLoading` flag from
  `isLoading` to `isPending`, since `isLoading` (`isPending && isFetching`)
  reads `false` on a disabled query and would have flashed the empty state
  while waiting on Clerk.
- `app/providers.tsx` - retry exactly once on a 401 (still never retries other
  4xx). Re-running `queryFn` re-fetches a token rather than reusing the stale
  one, so a genuine race self-heals; a truly dead session fails the same way
  on the second try and surfaces normally.
- `useChatStream.ts` - `streamChat` is a generator, so the `fetch`/401 doesn't
  fire until the first value is pulled; had to advance the iterator manually
  inside the retry logic rather than just retrying the call. One retry with a
  fresh token, nothing applied to state until it succeeds or exhausts.

**Checked:** 68/68 frontend tests pass, including two new ones
(`retries once on a 401 and succeeds`, `surfaces a failed turn when the 401
repeats`). `tsc --noEmit` and `pnpm lint` clean.

**Not verified:** only tested in dev mode (`AUTH_MODE=dev`, no real Clerk
session). Azure almost certainly runs real Clerk - this fix has not been
exercised against an actual Clerk-issued token that expires mid-race. Worth a
manual check against a real deployment before fully trusting it.

## Bug 2 - PDF table facts return "nothing found" (~0.26-0.28 relevance)

Ronak's own diagnosis: PDF table extraction produces scrambled text with
headers separated from values; proposed fix was LLM-synthesized
sentence-per-row before embedding.

**Investigated first, before writing anything:** `parse.py` already has a
mature, well-tested table pipeline - ruled-table detection, a caption-gated
borderless rescue (`_rescue_borderless_tables`), and a separate geometric
column-recovery pass for numeric tables (`_column_anchors`/`_aligned_table`).
Ronak's proposed fix (VLM/LLM row-to-sentence synthesis) is explicitly
**rejected** as a design decision on record in
[[Document Parsing And Complex PDFs]] - "a VLM reading values off a chart is a
hallucination dressed as extraction" - so implementing it would have violated
a deliberate project decision, not fixed a gap.

**Root cause, isolated with a real repro (`new_corpus/03_pricing_and_plans.pdf`,
the demo's actual pricing doc):** its feature-comparison table has no ruling
lines (`find_tables()` finds 0) **and** no "Table N" caption
(`_rescue_borderless_tables`'s trigger never fires) **and** cells are mostly
words not digits ("Up to 5", "Unlimited", "Yes") so too few rows qualify for
the numeric column-anchor recovery either. The table fell through every
existing path and flattened into reflowed prose - "Audit log Not Not 90 days
12 months retention available available" - header and two plans' values
silently reordered off their row.

**Fix:** `backend/app/ingest/parse.py` - new `_rescue_feature_grid()`. Detects
a `"Feature Free Starter Team Enterprise"`-shaped header line (`_FEATURE_HEADER_RE`,
strict: literal word "Feature" required, so it can't fire on ordinary prose),
then extends down through table rows using **function-word density**
(`_function_word_count`, a narrow closed vocabulary: "includes", "commits",
"plan", etc.) to find where the table ends and prose resumes - word count
alone doesn't separate them, since an unwrapped table row can run as long as a
short sentence. Crops the page to just that span before running pdfplumber's
text-strategy detector, so the noisy detector never sees the whole page (an
earlier attempt without the crop pulled the page title and the "restate the
table" paragraph into the same corrupted "table" - documented in the code
comments as a rejected approach, same class of regression
`_rescue_borderless_tables`'s own caption gate exists to avoid).

**Checked:** verified against the real PDF - table now extracts as a clean,
correctly-attributed markdown table (`"Uptime commitment | None | 99.5% |
99.9% | 99.95%"`), section context preserved, surrounding prose untouched. Two
new regression tests in `tests/test_parse_pdf.py`. 319/320 backend tests pass
(the one failure, `test_missing_document_is_404_not_403`, is pre-existing -
needs a running Postgres/Qdrant, fails identically with these changes
stashed out). ruff and mypy clean.

**Multi-turn follow-up half of Ronak's report ("What about Business?" failing)
- investigated, found NOT broken:** `rewrite_node` is unconditionally wired
into every query (`route -> rewrite -> retrieve`), does LLM coreference
resolution, runs in parallel with raw retrieval, fails open. Proven working on
a live pronoun case in [[Pipeline Review Log]] (M1). No code change made here
- flagged to Ronak that this diagnosis didn't match the code, no fix applied
since none was needed.

## Bug 3 - citations show yellow/unverified almost everywhere

**Investigated first:** an Explore agent read the whole verification stack
(`verify.py`, `chat.py`, `turn.py`, `CitationChip.tsx`) and found every piece
individually correct and I2-compliant - `null` (gray, dashed, "not yet
checked") is visually and semantically distinct from `false` (amber,
"unsupported") in `CitationChip.tsx`, and nothing coerces one into the other.
Live-tested the `Verifier` class twice against the real Anthropic API and it
behaved correctly both times. Could not reproduce a bug from static reading
alone - this is the point where I stopped and asked Ronak directly what the
chip actually looked like.

**Ronak confirmed: literally amber/yellow with a warning triangle** - i.e.
`verified === false`, a real verdict, not a stuck `null`. That reframed the
whole investigation: the judge was genuinely running and genuinely deciding
"unsupported" on citations that were, in fact, supported.

**Root cause, reproduced live:** wrote a probe script calling `Verifier.verify()`
directly with the real markdown table text and the answer "The Team plan has
an uptime commitment of 99.9%. [1]". Against a clean prose paraphrase of the
same fact the judge correctly said `True`; against the markdown table it said
`False` - **and its own `"reason"` field said "the Team plan row does show
99.9% uptime commitment" while still returning `supported: false`**. The old
prompt asked for `{"supported": ..., "reason": ...}` - verdict before
reasoning - so the model was committing to the "false" token before it had
worked out which row/column actually matched, especially prone to happening
on a table with several similar-looking values in a row (`None | 99.5% |
99.9% | 99.95%`).

**Fix:**
- `backend/app/graph/prompts.py` - `VERIFY_SYSTEM` reordered to
  `{"reason": ..., "supported": ...}`, with an explicit instruction to work
  out the row/column match before deciding, and a note that the source may be
  a table with similar-looking neighboring cells.
- `backend/app/graph/verify.py` - `_judge()`'s `max_tokens` raised 200 -> 350,
  since the old cap was sized for a one-line trailing reason and would
  truncate the now-longer chain-of-thought before the verdict token.

**Checked:** re-ran the exact repro against the patched code - clean table
now verifies `True`. Ran 5 varied true/false claims against both a clean and
a fragmented table (right plan/wrong plan, right number/wrong number,
contradicts-the-table) - all 5 correct, including the ones that should stay
`False`, so the fix isn't just making everything pass. 21/21 trials correct
across two follow-up variance checks (8+8+5). New regression test in
`tests/test_graph.py` asserting `reason` precedes `supported` in the prompt
schema, since `StubLLM`-based tests can't catch a prompt-ordering regression
by themselves. 57/57 `test_graph.py` tests pass, ruff/mypy clean.

**⚠️ NOT fully closed - one live inconsistency, unexplained:** ran the actual
question through a live Docker Compose stack (real Postgres/Qdrant/backend,
real Anthropic key) three times total.
- Run 1 and run 2 (same session, same answer text, same source chunk):
  citation verified **`false`**, contradicting 21/21 isolated trials on the
  identical text.
- Added temporary debug logging, rebuilt the backend, ran again: citation
  verified **`true`**.
- Final run (fresh containers, fresh ingest, requested explicitly by Ronak as
  "try it once and stop"): verified **`true`**.

Never found a code-level explanation for the two `false` runs - the debug
logs were reverted before capturing them (session ran low on budget), and
every isolated retest of the exact same answer/source pair came back `true`.
Leading hypothesis is genuine LLM sampling variance between the live SSE path
and the isolated probe script, but this was never confirmed. **Before fully
trusting citation coloring on camera, run the exact demo question 2-3 times
against a live stack and watch for a repeat of the false verdict** - if it
recurs, the next step is capturing the debug log output (`answer=`, `sources=`,
`result=` - add back the two `logger.warning` lines removed from
`run_verification` in `turn.py`, right before/after the `verifier.verify()`
call) on an actual failing run rather than a clean one.

## End-to-end verification performed

With Ronak's explicit go-ahead (this costs real API calls): started
`docker compose up --build`, created a workspace via `POST /workspaces`,
uploaded `new_corpus/03_pricing_and_plans.pdf` via `POST /documents`, polled
until `status: ready` (`tables_recovered: 1`, confirming Bug 2's fix fired in
the real ingest path - table came back cleaner than my isolated probe, likely
Tier-2 VLM escalation cleaning up remaining word-wrap on top of the Tier-1 fix),
then sent `"What is the uptime SLA on the Team plan?"` as a real `/chat` SSE
request. Final confirmed run: retrieval found the doc
(`relevance: 0.3532`, above the ~0.26-0.28 Ronak reported as failing), answer
was `"The Team plan carries a **99.9% uptime commitment**. [1]"`, citation
verified `true`. All three bugs' fixes work together on the real question.

## Not committed

Every change above is still sitting as an uncommitted diff:
- `frontend/hooks/useSessionToken.ts`
- `frontend/components/WorkspaceSidebar.tsx`
- `frontend/components/WorkspaceGrid.tsx`
- `frontend/hooks/useChatStream.ts` (+ `useChatStream.test.ts`)
- `frontend/app/providers.tsx`
- `backend/app/ingest/parse.py` (+ `tests/test_parse_pdf.py`)
- `backend/app/graph/prompts.py`
- `backend/app/graph/verify.py`
- `tests/test_graph.py`

Ronak asked about pushing to GitHub and deploying to Azure via `az cli` in
this same session - **explicitly declined** to do either: no Azure
credentials/CLI access were available in this session to push a build at all,
and the auth fix (Bug 1) has only been tested in dev mode, never against a
real Clerk-issued token on an actual deployment. Ronak said he'd switch to
another Claude subscription to handle the push himself. **Next session:
confirm with Ronak whether that push happened and whether the video got
recorded before doing anything else.**

## Two new bugs, found at the very end of recording - not yet investigated

### 1. Postgres deadlock on concurrent multi-document upload

Uploading two large PDFs to the same new workspace in quick succession (a
360ONE factsheet and a Tesla 10-Q, or generally two large PDFs close
together) triggered a genuine Postgres deadlock in the ingest pipeline.
Backend logs showed `sqlalchemy.exc.IntegrityError` /
`DeadlockDetectedError` on an `INSERT INTO chunks ... ON CONFLICT (id) DO
UPDATE` statement, two backend processes each waiting on a `ShareLock` held
by the other (`Process 1408 waits for ShareLock on transaction 4248; blocked
by process 1522` and the mirror image), and separately a second occurrence
where a background ingest task's `UPDATE documents SET ...` deadlocked
against another concurrent writer.

**Likely root cause (not confirmed, just read from the symptom):** the
frontend's upload loop (`NewWorkspaceDialog` in `WorkspaceSidebar.tsx`) awaits
each file's upload HTTP request sequentially, but each upload kicks off a
fire-and-forget background ingest task on the backend - so two uploads
submitted seconds apart still have overlapping ingest windows, and both tasks
end up writing `chunks`/`documents` rows for the same workspace concurrently,
in conflicting lock acquisition order.

**Workaround used during recording:** avoid the race - create the workspace
with one document, wait for it to reach `ready`, then upload the second.

**Unfixed.** Worth a real look next session, likely either serializing ingest
per-workspace on the backend (a lock/queue keyed on workspace_id) or wrapping
the chunk upsert in a retry-on-deadlock loop. Given CLAUDE.md's "no separate
ingest worker service" constraint (background tasks share the API process),
a per-workspace async lock is probably the more consistent fix.

### 2. Two small UI text-overflow bugs

- **Login page:** the "last used" pill (shown on whichever of
  "Continue with Google" or "Password" the user picked last) has its text not
  properly placed/positioned inside the pill.
- **New Workspace dialog:** the "Cancel" button's label text overflows/goes
  outside the button's box.

Both cosmetic, both unfixed, both need a look at the relevant component
(login shell mentioned in earlier memory as "unified auth shell" per commit
`9b4515c`; New Workspace dialog is `NewWorkspaceDialog` in
`WorkspaceSidebar.tsx`, same file as bug 1 above).

---

# Follow-up session - 2026-08-02 (afternoon)

Reviewed all three fixes above and closed out both new bugs. Everything is
still uncommitted; the GitHub push and Azure deploy described at the top of
this note **did not happen** - the working tree is unchanged from this
morning, so treat "did Ronak push?" as answered: no.

## Bug 3 is explained - the judge was not flaky, the verdict was cached

**This supersedes the "⚠️ NOT fully closed" section above, and the advice in
it is actively wrong.** `Verifier._judge` calls `complete_json` with the
default `temperature=0.0`, and `LLMClient.complete` caches every
temperature-0 call in the in-process `PromptCache` (this is documented in
`app/llm/cache.py`, which lists `verify` as cached). So a verdict is computed
**once per (answer, source, model) per process** and replayed thereafter.

Measured with a mocked transport rigged to answer `false` first and `true`
after (`Verifier.verify` twice, same input):

```
same process:  run1={1: False}  run2={1: False}  http_calls=1
after restart: run3={1: True}   http_calls=2
```

That reproduces every observation above exactly. Run 1 judged `false` once.
Run 2 - "same session, same answer text, same source chunk" - never reached
the judge at all; it was a cache hit. The run that came back `true` was the
one taken *after rebuilding the backend*, and the final `true` was on fresh
containers. Both were cold caches. So there was one divergent judgement, not
three, and 21/21 isolated trials passing is consistent rather than
contradictory - the probe script is a new process every time.

Consequence for the demo: **"run the exact question 2-3 times and watch for a
repeat" cannot work.** Runs 2 and 3 are cache hits and prove nothing. Only a
backend restart between attempts tests the judge again.

**Decided by Ronak, and changed:** correctness of the verdict beats one cheap
call. `_judge` now passes `use_cache=False`. `complete_json` had to grow the
parameter first - `complete` accepted it, but `complete_json` never forwarded
it, which is why every JSON-returning stage was cacheable whether or not that
suited it.

The principle worth keeping: **the cache is for deterministic transforms, and
a judgement is not one.** The model can differ between two identical asks, and
here that difference is the signal rather than noise - suppressing it turns
one bad sample into a permanent wrong answer. Every other cached stage
(route, rewrite, VLM page parse, eval judges) is a transform and stays cached.

Same probe after the change, and this is what "working" looks like:

```
same process:  run1={1: False}  run2={1: True}   http_calls=2
```

Pinned by `test_the_judge_is_asked_again_rather_than_served_from_cache` -
`StubLLM` bypasses the cache entirely, so only asserting on the flag itself
can catch a caller that starts caching judgements again.

`_judge` also now logs the model's `reason` whenever it returns `false`. That
is the record the earlier investigation needed and did not have: the failing
verdict is the one a reviewer challenges, and without the reason, diagnosing a
wrong `false` means re-running the judge and hoping it diverges again.

**On `max_tokens=350` and the reason-first prompt:** the cap cannot cost the
I2 guarantee. A response truncated mid-reason has no balanced JSON span,
`parse_json_tolerant` raises, `_judge` returns `None` - and that parser is
strict JSON at its core, so it never salvages a bare `true`/`false` out of the
reason text. Truncation therefore reads as *unknown* (grey chip), never as
*unsupported* (amber). It does mean an under-sized cap silently converts real
verdicts into "unchecked", so the failure is visible but quiet. Covered by
`test_a_truncated_judge_response_is_unknown_not_unsupported`.

## Bug 4 - the deadlock, root-caused against a real Postgres

**The stated likely cause above is wrong and is now disproven.** Two
different documents' ingests cannot deadlock: chunk ids are
`sha256(doc_id|chunk_index|text)` and the other unique key is
`(doc_id, chunk_index)`, both doc-scoped, so two documents never contend for
a row. Reproduced the exact scenario (two concurrent ingests, 320 chunks
each, 20 interleaved batches, own sessions, real Postgres) - clean, every
time. Now pinned by `test_two_documents_ingest_concurrently_without_deadlocking`.

**What does break under concurrent upload, measured:** six documents into one
workspace exhausted the connection pool -
`QueuePool limit of size 5 overflow 2 reached, connection timed out, timeout
30.00`, three requests dead. `pg_stat_activity` mid-ingest showed **6 of 7
connections held, 6 idle in transaction**. Half of those were the ingest SSE
streams: `GET /documents/{id}/events` takes `Depends(get_session)`, runs one
`SELECT` in `_owned`, and then holds that session - and its open read
transaction - for the entire ingest, while needing no database at all (progress
arrives over an in-process queue). So N uploads cost 2N connections.

Fixed, four parts, each aimed at a different failure:

1. **`documents.py` - the SSE stream releases its session.** Reads what it
   needs into a snapshot, then `await session.close()` before streaming.
2. **`documents.py` - `_run_ingest` takes a slot from a process-wide
   semaphore** (`Settings.ingest_max_concurrency`, default 1). Session opened
   inside it, so a queued document holds no connection. Global rather than
   per-workspace: RAM and the pool are per process, and a per-workspace lock
   leaves N workspaces free to run N ingests.
3. **`pipeline.py` - `_mirror_chunks` inserts rows sorted by `id`.** Circular-
   wait prevention: Postgres locks rows in VALUES order, so two writers with
   overlapping keys deadlock if they present them in opposite orders. Insert
   order used to be `chunk_index` order, arbitrary against a sha256 digest.
   Unlike the semaphore this **still holds across replicas**.
4. **`pipeline.py` - `_commit_chunk_batch` commits per batch and retries once
   on SQLSTATE 40P01.** A deadlock abort is transient by construction; the
   victim's re-run finds no contention. Covers the contender the semaphore
   cannot: `IngestPipeline.delete` takes a row-exclusive lock on the same
   `documents` row an in-flight chunk INSERT holds `FOR KEY SHARE` on through
   the foreign key.

Note on 2 vs 3/4: **the semaphore is per process and Container Apps can scale
past one replica**, so it is the weakest of the four by design. 3 and 4 are
the ones that survive that.

Also fixed while in there: `_embed_and_upsert` clears the document's existing
chunk rows before the first batch. `ON CONFLICT (id)` does not arbitrate
`uq_chunks_doc_index`, so a re-ingest whose text changed for a given index
(Tier-2 escalation is not bit-identical run to run) minted a new id for the
same `(doc_id, chunk_index)` and hit that constraint as a raw
`IntegrityError` - which is probably the `IntegrityError` reported above,
distinct from the deadlock. `_fail` now also deletes the doc's chunk rows,
which per-batch commits make necessary.

**Verified:** the same six-document upload now finishes with all six `ready`,
zero backend errors, and connections peaking at 5 with **0 idle in
transaction**.

## Bug 5 - both UI overflows, reproduced and fixed

**New Workspace "Cancel".** Root cause is the submit button, not Cancel.
`Uploading ${uploadingName}...` put an arbitrary-length filename inside a
`whitespace-nowrap shrink-0` button: a real 66-character factsheet name grew
it to **714px inside a 576px dialog**, and because the footer is
`justify-end` the overflow went left - **Cancel rendered 274px outside the
dialog**, floating over the page behind it. Screenshot confirmed. Fixed by
making the label bounded (`Uploading 2 of 3...`; the file list above already
spins on the active row, so the filename was redundant) plus `min-w-0
shrink` + a `truncate` span as the structural guard. Re-measured: nothing
escapes the dialog at 1440x900 or 390x844.

**Login "last used" pill.** Clerk's `lastAuthenticationStrategyBadge` gets a
hard `height` derived from the `appearance` `spacing` variable, which is set
to `0.7rem` here so the sign-up card fits AuthShell without scrolling. That
resolves to 14.17px around a 15px line box, `display: block`, no vertical
padding - the label hung 2.8px below the pill (`scrollHeight` 18 vs
`clientHeight` 12). Fixed per-element in `lib/clerk-appearance.ts` with
`h-auto` + `inline-flex items-center`; raising `spacing` back to 1rem was the
other option and is rejected because it re-breaks the sign-up card. See
findings 17-19 in [[Technology Documentation Links]] for how the descriptor
name was found - it is not in Clerk's published elements table.

## Multi-turn follow-ups: working. The test case was the problem.

Ronak reported the follow-up failing when he tested it, which contradicted this
morning's read-the-code conclusion. Settled by measurement instead: two real
`/chat` turns through the real endpoint with a recording stub LLM (no API
cost - `nodes.get_llm_client` swapped for a stub that records every prompt).

Turn 2's prompts carry the history in full:

```
--- rewrite prompt (turn 2) ---
Entities mentioned earlier:
- Team plan: Team plan

Conversation so far:
user: What is the uptime SLA on the Team plan?
assistant: The Team plan carries a 99.9% uptime commitment. [1]

Follow-up message: What about Business?
```

Same `conversation_id` on both turns, `recent_turns` loaded, entity ledger
populated, and `route` gets the transcript too. Nothing is dropped.

**Why the test looked like a failure: `new_corpus/03_pricing_and_plans.pdf`
has no Business plan.** Counted over the parsed text - Free 8, Starter 16,
Team 16, Enterprise 11, **Business 0**. "What about Business?" has no answer
in the corpus, so declining to answer it is correct behaviour that is
indistinguishable from a broken follow-up when you are the one testing. Use
**"What about Enterprise?"** as the demo follow-up.

### Correction to Bug 2's claimed result

The write-up above says the recovered table extracts as
`"Uptime commitment | None | 99.5% | 99.9% | 99.95%"`. It does not, at Tier 1.
Actual output:

```
| Uptime      |      |       |       |        |
|             | None | 99.5% | 99.9% | 99.95% |
| commitment  |      |       |       |        |
```

The **column attribution is correct** - which is the bug that was fixed, and
the values are under the right plans. But a two-word row label wraps across
physical lines, so the label and its values land on different markdown rows,
and the header row comes out as the first wrapped label rather than
`Feature | Free | Starter | ...` (the header shares a physical line with the
intro sentence, "Meridian offers four plans: ... Feature Free Starter Team
Enterprise").

This is not a regression and does not need chasing now: `complex_pages=[1]`,
so page 1 is flagged for Tier-2 escalation, and the VLM re-transcribes it in
the real ingest path - which is exactly what the end-to-end run above
observed ("table came back cleaner than my isolated probe"). Worth knowing
that the clean single-line row was the VLM's doing, not Tier 1's.

## Slow first load after sign-in - root-caused from a real trace

Ronak reported the deployed site taking a long time to show workspaces right
after login/signup, and believed the Azure backend stayed warm. Two of the
three hypotheses raised from reading code were wrong; his browser trace
settled it:

```
workspaces   200  preflight   0.0 kB   33.16 s
workspaces   200  fetch       0.1 kB    2.04 s
Finish: 47.22 s
```

**The backend cold-starts, and the cold start lands on the CORS preflight.**
Frontend is on Vercel, backend on Azure, so every call is cross-origin and the
browser sends `OPTIONS` first - the container has to boot to answer even that,
and the real request cannot begin until it returns. Confirmed against the
live config:

```
Name                  MinReplicas  MaxReplicas  Region
knowledgehub-backend            0           10  centralindia
```

`minReplicas: 0`. Container Apps defaults to that; always-on is opt-in. **Note
for future sessions: `az` IS installed and authenticated on this machine now**
(subscription `8fe4a493-…`, rg `knowledgehub-rg`) - the 2026-08-02 morning note
above says no CLI access was available, which is no longer true.

Ruled out by the same trace: **Clerk is not the bottleneck** (every call
sub-600 ms, all bundles served from disk cache at 1-15 ms), and the N+1 below
was not the cause here either - that account had *zero* workspaces, so the
list path ran one query. The residual 2.04 s is the first authenticated
request on a fresh process: the SDK's per-process JWKS fetch plus engine
creation and the first pool connection.

Not changed, because it is outward-facing and changes billing: setting
`--min-replicas 1` is the direct fix. The cheap alternative is warming the app
before a review window, which is exactly the compromise CLAUDE.md already
settled on ("no 24/7 uptime pinger - scope it to review windows and accept
60 s cold starts"). Worth re-deciding on the merits now rather than
inheriting it: that constraint was chosen against **Render's instance-hour
cap**, and Container Apps bills on vCPU-seconds and requests instead, so the
reason the pinger was killed does not automatically carry over.

## N+1 in `GET /workspaces` - fixed

Separate from the above and a genuine bug: `list_workspaces` looped over every
workspace and ran a document count and a conversation count per row, and
`_counts` selected every matching id to take `len()` of it in Python.

```
before:  18 workspaces -> 37 statements   (1 + 2N)
after:   18 workspaces ->  3 statements   (list + two grouped counts)
```

At a 25 ms round trip that is 0.93 s -> 0.07 s, and it no longer degrades as
an account accumulates workspaces. `_counts` is kept for the single-workspace
routes, where two queries for one row is correct.

Pinned by `test_listing_workspaces_costs_the_same_queries_at_any_size`, which
counts statements at two sizes rather than timing anything - the response body
is identical either way, and a local timing passes at any N, so nothing else
would catch a reintroduced loop.

## Delete confirmations, theme default, and a visual sweep

**Delete confirmations were truncating to "Delete t...".** Same root cause in
two places, and it was not a styling accident: the control put the question and
both buttons on one line with `truncate` on the label. That fits nowhere it is
used - a sidebar row and a workspace card are ~250-300px wide, and "Delete this
workspace?" plus two buttons needs ~330px. Now two rows: label (wrapping,
`line-clamp-2` so a long workspace name cannot grow the row without limit),
buttons right-aligned beneath. Measured after: label `scrollWidth == clientWidth`
at 1440/1024/390, confirm box inside the card at every width.

`WorkspaceGrid` had its own copy of the same markup, which had already drifted
on padding and radius while sharing the bug. It now uses the sidebar's
`InlineDeleteConfirm` with a `size` prop - fixing this twice is how they drift
again.

**Theme now defaults to `system`, not dark** - on the landing and sign-in pages
as much as inside the app. Changed in `layout.tsx`'s blocking init script and
`useTheme.ts` together; they must agree or first paint flashes the wrong theme.
One real trap in doing this: `resolve("system")` became reachable during SSR
(it was not while the default was `dark`, which never touched `matchMedia`), so
`systemPrefersDark()` needed an SSR guard or the server render crashes. Verified
with nothing in localStorage: OS light -> `pref=system`, no `.dark`,
`bg=rgb(251,250,247)`; OS dark -> `pref=system`, `.dark`, `bg=rgb(15,15,15)`.

Nothing on the landing page or `AuthShell` was hardcoded dark - both already
carried proper `dark:` variants - so the default was the whole fix.

### Visual sweep - desktop clean, mobile is not

Swept landing / workspaces / new-workspace-dialog x {1440, 1024, 390} x
{light, dark}, checking for horizontal page overflow and for text whose
`scrollWidth`/`scrollHeight` exceeds its own box without an intentional clip.

**1440 and 1024 are clean in both themes.** Every finding is at 390, and they
all trace to one cause rather than 21 separate ones:

```
page-scrolls-horizontally: scrollWidth 824 > viewport 390
```

**The app shell has a hard ~824px floor** - a 260px fixed sidebar plus a main
column whose header row (title, search, sort, "New workspace") does not wrap.
Below that the page scrolls sideways and the header controls sit off-screen.
That threshold means portrait tablets (768px) are affected too, not just
phones.

**Not fixed, and deliberately not attempted.** A real fix is the sidebar
becoming an overlay/drawer under a breakpoint plus a wrapping header row -
a layout change, not a patch, and this is a desktop-demo product. Flagged for
Ronak to decide rather than restructured unasked.

## Pre-production pipeline review - five foundational bugs

Ronak asked for an adversarial pass over the backend before pushing, on the
grounds that a reviewer will use a different corpus. Everything below is
measured, and **none of it is visible on a short corpus of ordinary English
prose** - which is exactly why it survived this long.

### 1. `count_tokens` saturated at 512 (`app/ingest/tokens.py`)

`bge-small-en-v1.5`'s tokenizer ships with truncation enabled at 512, and
`token_count` reports the length *after* truncation. Measured: a 320,000-char
string and a 3,200-char string both returned **512**.

That function is the ruler every size decision is made with, so every ceiling
became unenforceable in precisely the range where a ceiling matters. Fixed by
counting in 256-char windows when the single-call result lands on the cap -
deliberately *not* `tokenizer.no_truncation()`, which would mutate the same
instance `embed()` uses and hand the ONNX session over-length sequences.

### 2. Parent windows were unbounded (`_parent_window`)

The window started at the child's whole *block*. A block over the ceiling made
every parent in it the entire block. On a 54 KB single-section document, 41 of
42 parents breached the 1200-token cap and the largest was **~10,400 tokens,
8.7x**. The parent is what the model receives, so `fit_context` then trimmed
the turn to roughly one source instead of five.

**This is the cause of Ronak's "citations highlight the entire document"**:
`Chunk`'s own docstring says a citation highlights
`parent_char_start`/`parent_char_end`, and on a single-section document the
parent *was* the whole document. Now bounded by `_bounded_window`: 0/42 over,
largest 1,137 against a 1,200 cap.

### 3. No chunk-size backstop (`_enforce_ceiling` / `_hard_split`)

Both splitters divide on a natural boundary and neither can divide a unit
already over the ceiling: `_split_prose` keys on `.!?` and takes its first
sentence unconditionally, `_split_table` has the same shape for one huge row.
Once bug 1 was fixed and sizes became visible, unpunctuated prose produced a
**1,057-token** child and CJK (which ends sentences with 。, not `.`)
produced **2,906** - against a model that accepts 512. fastembed truncates
silently, so the dense vector described the chunk's opening and the remainder
was unreachable by the dense branch while BM25 still indexed all of it. A pure
recall hole with no error anywhere.

### 4 & 5. Citation highlight (`lib/text-match.ts`, `lib/markdown-highlight.ts`)

Neither is a chunking bug, despite presenting as one. The highlight does not
use the stored offsets at all - it *searches* for the passage, because
Markdown syntax characters are consumed by the parser and a PDF has
coordinates rather than character indices.

* **`ANCHOR_CHARS = 180` was a raw character cut**, so the highlight ended
  mid-word on essentially every citation long enough to reach it. Confirmed
  against Ronak's screenshot: the lit range was exactly 180 normalised
  characters, ending inside "limit" as "l". Now `anchorOf()` trims to the last
  word boundary, with a raw fallback for scripts that have no spaces.
* **The highlight was the search anchor, not the passage.** `until = at +
  target.length` lit only the first 180 characters - the same screenshot's
  badge read `cited 4,094-4,366`, 272 characters, of which ~180 were lit. Now
  extended character-by-character through the rest of the cited text for as
  long as the rendering agrees, so a passage that diverges stops at the
  divergence instead of lighting up whatever follows.

Covered by `frontend/lib/markdown-highlight.test.ts`, built on the real
section from `01_architecture_and_api_reference.md`.

### Flagged, not changed

* **`dedup` in CLAUDE.md's ingest line was stale - now removed.** Corrected
  twice on the way to this: it was *not* in `pipeline.py`'s docstring (that
  reads `upload -> parse -> sanitize -> chunk -> embed -> upsert`, correctly),
  and dedup was not an unbuilt feature - it was **rejected outright**, on
  record in both the README's trap table and [[NotebookRAG Reference Project]]
  ("unfiltered global vector search ... an unscoped cross-tenant read path,
  plus one Qdrant round trip per chunk"). CLAUDE.md line 63 was the single
  place in the repo still implying it existed, and it contradicted CLAUDE.md's
  own trap list nine lines later. Now says so explicitly rather than just
  dropping the word, so nobody re-adds it.

  **If it is ever revisited, do it at retrieval time, not ingest.** Dropping a
  chunk from document B because document A has the same text makes B's
  citations point at A - fatal in a product whose claim is that a citation
  names its source - and deleting A then silently guts B. Filtering the
  candidate list just before `fit_context` gets the whole benefit (context
  slots not spent twice on one passage) and touches no stored data, no
  offsets, no citations. Use **exact** normalised-text equality, never a
  similarity threshold: the demo corpus's pricing rows are near-identical
  strings differing only in the number that *is* the answer
  (`None | 99.5% | 99.9% | 99.95%`), so fuzzy dedup would discard the passage
  being demonstrated.
* **CJK parent windows still cut between characters** (33/37 in the probe).
  There is no whitespace to snap to and CJK has no spaces, so this is arguably
  correct; snapping to 。/， would be nicer.

### Not reviewed to the same depth

Ingest got the thorough pass and is where all five bugs were. Retrieval and
generation got a lighter read: `fuse.py` was checked against I7 and is sound
(analytic ceiling, no per-query renormalisation, `interleave_intents`
correctly refusing RRF across distinct sub-questions). The rerank fallback
chain, the CRAG grade/retry loop, and the abstain path were **not** examined
in detail. Worth a second pass with the same adversarial framing.

## Stage-by-stage loop review

One pipeline stage per iteration, adversarially, against the goal of surviving
a corpus the reviewer picks. Findings recorded as they land.

### Stage 1 - upload entry + parse ✅

**The PDF path is sound.** Every failure mode rejects loudly with a usable
message: 0-page, blank/scan-like, encrypted, truncated, non-PDF bytes,
unsupported mime. Nothing silently half-succeeds.

**The text/markdown path had two holes, both fixed.**

**1.1 Any non-UTF-8 upload with non-ASCII content was silently corrupted.**
Decoding was `data.decode("utf-8")` with `errors="replace"` behind it.
MEASURED across seven encodings: pure ASCII survived everything (the
interleaved NULs of a UTF-16 file are stripped downstream as control
characters, which repairs it by accident), but **non-ASCII was destroyed in
every non-UTF-8 encoding** - "Café" became "Caf�" under cp1252, latin-1
and UTF-16 alike; CJK under UTF-16 became noise.

Not an exotic case. cp1252 *is* what a text file saved out of a Windows editor
is, and it is where curly quotes, em dashes, £/€ and every accented name live -
so a document pasted out of Word lost a character at every smart quote and
nothing reported it. Now a BOM ladder → strict UTF-8 → cp1252 → latin-1. All
seven encodings round-trip at 100%.

*Caught by its own test during the fix:* the endian-specific codecs
(`utf-16-le`) decode correctly but leave the BOM as a stray `﻿`.
`sanitize_text` strips it - but only after `parse_markdown` has failed to match
`^(#{1,6})` against `﻿# Title`, losing the first heading and shifting
every section path beneath it. Fixed by using the BOM-aware codecs (`utf-16`,
`utf-32`), which consume the mark.

**1.2 An empty or whitespace-only text upload was accepted.** `parse_pdf`
already refused a document it could extract no text from; the text path had no
such guard, so an empty `.txt` parsed to zero blocks, ingested to `ready` with
zero chunks, and sat in the document list looking searchable while matching
nothing - the silent partial index `parse_document`'s own docstring rules out,
reached by the one file type that was not checked.

**Known limit, deliberate:** BOM-less UTF-16 is genuinely ambiguous with
latin-1 at the byte level and is not guessed at - guessing would risk
mis-decoding valid latin-1. Its ASCII content still survives via the NUL
stripping.

**Noted, not fixed:** PDF bytes uploaded with a `.txt` extension parse as
garbage text. `_guess_mime` trusts the extension by documented design (browsers
report `application/octet-stream` for `.md`), and this requires renaming a file
to the wrong extension. Low value.

356 backend tests pass, ruff and mypy clean.

### Stage 2 - sanitize + build_normalized_text (I5) ✅

**The backend half is clean, and that is a real result rather than an absence
of effort.** Probed with zero-width injection, bidi overrides, HTML comments,
C0 control characters, 90-space runs, blocks that sanitise away to nothing,
combining marks, and the cp1252 punctuation Stage 1 now delivers. For every
one: spans round-trip (`text[start:end]` is exactly the block's rendered
post-sanitisation content), spans are ordered and non-overlapping, and every
gap between spans is exactly `BLOCK_SEPARATOR` - so no highlight can include
padding the author did not write. `build_normalized_text` holds its contract.

**2.1 The *consumer* disagreed about what an offset means.** I5 is written as
"offsets index `normalized_text`, never raw bytes, never a chunk" - it does not
say *in what unit*, and the two ends of the system had answered differently.
Python indexes **code points**; a JavaScript string indexes **UTF-16 code
units**. They coincide until a document contains an astral character - an
emoji, a mathematical alphanumeric (𝐀), a rare CJK extension - after which the
browser's index is one unit ahead per such character.

MEASURED with a single 🚀 before the cited sentence:

```
cited span offsets 40..88
  python  -> 'The Team plan carries a 99.9% uptime commitment.'
  browser -> '\nThe Team plan carries a 99.9% uptime commitment'
```

The highlight starts on the preceding newline and loses its last character, and
the drift accumulates down the document rather than staying constant. It also
could not trip the existing out-of-range guard: a code point count is always
**≤** the code unit count, so a shifted offset stays inside the string and the
highlight is silently wrong rather than visibly absent - and the guard itself
compared against `text.length`, letting a genuinely out-of-range citation
through on such a document.

Fixed in `frontend/lib/offsets.ts` (`toCodeUnitOffsets`, `codePointLength`),
converted **once** in `SourcePane` where the offsets first meet the string, so
the range check, the clamp, the segment split and the `cited` slice all agree.
That last one matters beyond the Text tab: `cited` is the needle `DocumentView`
searches the PDF text layer and the Markdown tree with, so slicing it one unit
early would have shifted the highlight in all three views.

**Blast radius, checked before applying:** confined to `SourcePane`, and the
conversion is the identity function on any all-BMP document - which is **every
document in the demo corpus** (measured: 0 astral characters across all four
text files). So this is zero-risk for the recording and purely reviewer-corpus
robustness. Cost is one memoised `charCodeAt` scan.

Stage 1 re-probed from scratch afterwards and unchanged. 356 backend tests, 83
frontend tests, ruff, mypy, tsc, eslint all green.

### Stage 3 - chunking ✅

**The three fixes from earlier today hold.** Re-verified across 18 document
shapes - long single sections, unpunctuated prose, CJK, ruled and huge tables,
header-only tables, single characters, sixty tiny blocks, section and derived
boundaries, captioned tables, and four randomised shapes. For every chunk in
every shape: the child span slices back out of `normalized_text`, the parent
slices back verbatim and contains its child, no chunk exceeds the 512-token
embedder limit, no parent exceeds its 1200-token ceiling, `related_spans` point
at real text, derived chunks keep their marker, and ids are unique and
deterministic across runs. No I5 violations anywhere.

**3.1 The token counter collapsed on unbroken runs - the mirror image of the
saturation bug, and reachable by ordinary content.**

WordPiece's `max_input_chars_per_word` is **100**, and any longer unbroken run
becomes a single `[UNK]`. MEASURED - the cliff is exact:

```
  chars  count_tokens   ids[:6]
    100            52   [101, 13360, 11057, ...]
    110             3   [101, 100, 102]      <- [CLS] [UNK] [SEP]
   9000             3   [101, 100, 102]
```

So the count stops responding to length entirely. **A base64 data URI measured
4,422 characters at 13 tokens** - an image embedded in a markdown file, which
is not an exotic document. It passes every chunk and parent ceiling untouched,
becomes one chunk whose dense vector is the embedding of `[UNK]`, and reaches
the model in a parent roughly 85x the budget it was admitted under. Long URLs,
minified code, hex digests, and any PDF whose word-boundary recovery failed
(the exact failure `_X_TOLERANCE_RATIO` exists to prevent) all share the shape.

The windowing fix applied earlier could not catch it: that guards a count
saturating *at* the 512 cap, and this under-reports instead.

**`_heuristic_token_count` had the same hole, and it matters more than it
looks:** it scored a 9,000-character run at **1** token, and that is the
counter `fit_context` uses - so the budget that exists to keep a request under
the serving model's limit was blind to precisely the input most able to blow
it.

Fixed with a character floor (`_MIN_CHARS_PER_TOKEN = 8`) on both. MEASURED
chars-per-token is 1.9-4 for English and ~1 for CJK, so `len/8` sits below
anything genuine - **the floor is inert on ordinary text** and binds only when
the tokenizer has collapsed something. Verified: the word-boundary contrast
rows are byte-identical before and after (1,019 chars → 502 tokens; 9,179 →
4,555), while 9,000 unbroken chars went 3 → 1,125 and the base64 blob 13 → 552.
The "no whitespace" shape now splits into 5 chunks instead of surviving as one.

Stages 2 and 1 re-probed from scratch afterwards, both unchanged. 358 backend
tests, ruff and mypy clean.

### Stage 4 - embed + Qdrant upsert ✅

**Verified clean:** I3 tenant isolation (another user's search returns 0 hits),
upsert idempotency under deterministic point ids (`uuid5` over the chunk id -
correctly *not* a slice-and-reformat of the hex), `ensure_collection`
idempotency, and degenerate chunk text. That last one is worth recording:
punctuation-only and whitespace-only chunks produce **zero sparse terms** and
Qdrant accepts the point without complaint, so an empty BM25 vector is not a
failure mode here.

**4.1 An empty document selection was an unrestricted search - a workspace
isolation failure.** `_scope` tested `if doc_ids:`, which is falsy for both
`None` and `[]`, so the two collapsed. They are different questions: `None`
means "no document restriction", `[]` means "restricted to no documents" and
can only return nothing.

`chat.py` builds that list from the conversation's workspace, so an **empty
workspace produced `[]`**. Measured against a live Qdrant:

```
doc_ids=[doc-a]  -> 2 hits (correct)
doc_ids=[]       -> 2 hits   <- an EMPTY workspace retrieving another's document
doc_ids=None     -> 2 hits   (documented: all docs)
```

after the fix, `[]` returns **0**. `MatchAny(any=[])` matches nothing on Qdrant
1.18 - measured rather than assumed, since "empty means match nothing" and
"empty means no constraint" are both defensible readings of such an API.

**I3 was never at risk** (`user_id` is unconditional), and that is precisely why
this survived: a tenant leak would have been caught by the isolation tests that
already exist, but nothing tested workspace isolation. The same `or`-collapse
existed on the overview route (`selected or await list_docs(...)`), so an
overview asked in an empty workspace summarised other workspaces; fixed to
distinguish `None` from `[]` there too.

**4.2 The query that produces that list lacked `user_id` and a readiness
filter.** Added both. The readiness one connects back to an earlier fix and is
the more interesting: Qdrant is written before Postgres, so a document
mid-ingest already has partial vectors - what used to hide them was that its
Postgres chunk rows were *uncommitted*, so hydration dropped them ("no Postgres
mirror for chunk ...; dropping"). **Committing each embed batch - applied
earlier today to shorten lock windows - removed that accident.** `status ==
ready` restores the guarantee deliberately rather than by side effect, so an
answer is never composed from a document still being indexed.

Stage 3 re-probed from scratch afterwards, unchanged. 360 backend tests, ruff
and mypy clean.

### Stage 5 - retrieve + fuse (I7) ✅ no bugs found

The first stage of the loop to come back clean, and it was probed as hard as
the ones that did not.

**I7's empirical foundation re-verified, not assumed.** Re-ran
`scripts/probe_rrf_rank_base.py` against the live Qdrant 1.18: a chunk topping
both branches scores exactly `0.03333333 = 2/60`, so `rank_base` is still **0**
and `RRF_MAX` is still the analytic `(w_dense + w_sparse) / k`. Every G2
threshold derives from that constant, and CLAUDE.md asks for this to be re-run
after a version bump - it had not moved.

**Normalisation is analytic everywhere, and the scale is exactly [0, 1]:**
#1 in both branches → 1.0000, #1 in one branch → 0.5000, rank 39 (last of
`top_k=40`) → 0.3030. No per-query renormalisation anywhere; nothing exceeded
1.0 or went negative in any case tried.

**Edge cases, all clean:** `fuse_formulations` with no sets / one empty set /
both empty / one set / two identical / two disjoint - correct results, scores
in range, no crash. `interleave_intents` gave the third sub-question's *only*
supporting passage position 3 of 5 (the fairness property it exists for) and
deduplicated the tail. `attach_branch_ranks` correctly reports `None` for a
chunk missing from a branch. `is_decisive` was right at every boundary,
including the load-bearing one - a huge margin with the winner in only *one*
branch is **not** decisive, which is exactly when a cross-encoder earns its
call. Zero candidates yields `relevance 0.0` and `(False, None)` rather than an
exception, and is distinguishable from a retrieval *failure*, which raises a
named `DependencyUnavailable("qdrant")` 503.

**One observation for the tuning pass, not a bug.** The fused scale differs
depending on whether the rewrite produced a second formulation, because
`fuse_formulations` recomputes from rank rather than carrying the incoming
score:

```
rewrite changed nothing (1 set)              -> top 1.0000 -> relevance 1.0000
rewrite changed (2 sets), found by one only  -> top 0.5000 -> relevance 0.5000
```

`floor_fused` is **0.50**, so that second case lands exactly on the abstention
boundary and passes only because the comparison is `>=`. The behaviour is
defensible - agreement across formulations is genuine evidence, and the
single-set short-circuit deliberately avoids rescaling against a ceiling built
for two - but the gate is knife-edge for "found by one formulation only".
`floor_fused` is one of the deliberately-unresolved constants CLAUDE.md says
must be set against a corpus, so it is **left alone**; recorded here so the
tuning pass knows the boundary sits precisely where that case falls.

### Stage 6 - rerank ✅

**Verified correct:** 429 (rate) does **not** trip the breaker while 402
(quota) does - the distinction CLAUDE.md insists on, working. The cache stores
`(id, score)` **pairs** rather than order, so a hit replays real scores and the
reference project's constant-score bug cannot recur. Chunks Cohere did not rank
keep `None`, not `0.0` (I2). `_reorder` survives duplicate indices, out-of-range
indices and more results than were sent, without duplicating a candidate. A
missing API key records a degradation.

**6.1 Only the turn that tripped the breaker reported a degradation.** MEASURED
across four consecutive turns:

```
turn 1: status=failed degradations=1 reason=quota_exhausted
turn 2: status=failed degradations=0   <-- silently degraded
turn 3: status=failed degradations=0   <-- silently degraded
turn 4: status=failed degradations=0   <-- silently degraded
```

So once the monthly quota went, **every remaining answer of the deployment was
served on fused order while looking exactly like a reranked one** - I1's failure
condition stated literally. The breaker's docstring said "one degradation is
recorded when it trips", conflating *trip once* (right - don't re-trip, don't
re-log) with *record once* (wrong - I1 is about each answer). Now recorded per
turn; the breaker still makes exactly **one** HTTP call.

**6.2 A 200 carrying nothing usable was reported as a successful rerank.** The
quiet one, and the more dangerous. `{"results": []}`, a renamed `results` key,
every index out of range, or missing scores all produced `status=APPLIED` with
every `rerank_score` None. `grade` then reads its scores from the rerank source,
finds an empty list, scores the turn **0.0** and abstains - so a good retrieval
became "I could not find that", with no degradation anywhere to explain it. An
upstream response-shape change would have done that to every query at once,
silently.

Worse, the empty ordering was **written to the cache**, so a single malformed
response poisoned that (query, doc-set) permanently - every later identical
query replayed it as `CACHED` with no scores. Returning before the cache write
is the important half of the fix. Now falls back to fused order with a
`parse_error` degradation.

**6.3 The rerank cache was unbounded** - 50 distinct queries left 50 entries and
nothing evicted. Small entries make it a slow leak rather than a fast one, but
unbounded inside a 512 MB ceiling is still a leak, and `app/llm/cache.py` is
already bounded at 512. Now matched, oldest-first.

*Noted, not changed:* an empty candidate list returns `status=FAILED` with no
degradation. Semantically odd - nothing failed, there was simply nothing to
rank - but functionally correct, since `relevance_score([])` is 0.0 on either
path and the turn abstains regardless.

Stage 5 re-probed from scratch, unchanged. 367 backend tests, ruff and mypy
clean.

### Stage 7 - the graph ⚠️ STOPPED, needs Ronak's decision → resolved in Stage 11

**Verified correct:** the graph topology has exactly one cycle
(`retry -> retrieve`) and I6's cap holds - `grade_node` returns `ABSTAIN`
rather than `RETRY` once `attempt == 1`, so the counter binds even if
`_grade_branch` were re-entered. `_clean_queries` handles a bare string, a
non-list, an empty list, duplicates and over-long lists, all failing open to
the raw query. `route` and `rewrite` both fail open, and
`_rewrite_failure_reason` labels timeout / rate-limit / parse-error distinctly
rather than calling everything a timeout. `applicable_floor` is right for all
four statuses (0.35 reranked, 0.50 fused).

**7.1 (NEEDS A DECISION) - G2's abstention floor cannot fire once two
formulations are fused, and fires on everything when they are not.**

> ✅ **FIXED in Stage 11, 2026-08-02.** Deferred here on the basis that the
> strict path is what a standalone question takes. **Stage 11 disproved that
> basis:** `retry_node` sets `rewritten = True`, so the CRAG retry always lands
> on the fused path - and the retry is by construction the path every question
> the corpus cannot answer takes. The deferral protected the one path that had
> no protection to give. See *Stage 11* below.

`_to_candidate` normalises each Qdrant score against the analytic `RRF_MAX`
(I7), so a weak match carries a low `fused_score`. When there is one effective
query - the common case - `retrieve_node` then calls
`fuse_formulations([raw_candidates, *result_sets])`, and that function
**recomputes every score from rank alone**, discarding the normalised
magnitude. The top chunk is rank 0 in both sets by definition, so it always
scores `2/60 / (2/60) = 1.0`.

MEASURED, holding everything else constant and varying only how well the chunk
actually matched:

```
match quality                    score at boundary  after fusion  relevance  gate
excellent (top of one branch)               0.5000        1.0000     0.9967  answers
mediocre (rank 10)                          0.4286        1.0000     0.9967  answers
poor (rank 25)                              0.3529        1.0000     0.9967  answers
terrible (rank 39, last of top_k)           0.3030        1.0000     0.9967  answers

same inputs, rewrite changed nothing (fuse_formulations not called):
excellent                                                            0.4984  ABSTAINS
terrible                                                             0.3024  ABSTAINS
```

So G2 is **dead on one path and over-eager on the other**, and which one a turn
takes depends on whether the rewrite happened to alter the query text.

This is `config.py`'s own stated I7 failure mode reached by a different route.
That docstring warns that normalising against the observed maximum "forces
`max(score) == 1.0` on every query, which pins the G2 relevance blend above any
sane floor and makes the abstention gate meaningless". I7 was defended against
*observed-max normalisation*; rank-only recomputation produces the identical
outcome and was not.

**It bites the common path, not a rare one.** `relevance_score` only uses
fused scores when reranking was skipped or failed - and `rerank.py`'s own
docstring says conditional reranking makes the un-reranked path "the
designed-for majority rather than a rare fallback". When Cohere *does* rerank,
G2 gates on Cohere's calibrated score against `floor_rerank` and works
correctly.

**Why `fuse_formulations` is rank-based is sound, and is not the bug.** Ranks
are comparable across two separate Qdrant calls; raw scores are not. The defect
is that one number is then used for two different jobs - *ordering* (where rank
is right) and *relevance magnitude* (where rank carries no information at all).

**Not changed, deliberately.** Fixing it means deciding what magnitude survives
a cross-formulation merge - carry the max or mean of the incoming normalised
scores alongside the rank-based ordering is the obvious candidate - and that
changes abstention behaviour everywhere, interacting with `floor_fused`, which
CLAUDE.md lists among the deliberately-unresolved constants that must be set
against a corpus. That is Ronak's call, not a fix to apply mid-loop.

#### Measured against the real corpus - and half the finding above was wrong

Ronak asked the right question: does this actually stop the system answering?
Measured by ingesting the real `.md`/`.txt` corpus locally and running real
retrieval (no API spend; the PDF is skipped because Tier-2 costs a call):

```
question                                       strict path  fused path  verdict
API rate limit on the Team plan                     0.8699      0.9085  answers on both
uptime SLA on the Team plan                         0.8340      0.9085  answers on both
how long are audit logs retained                    0.7785      0.9085  answers on both
onboarding process for a new customer               0.8523      0.9085  answers on both
who won the 1998 football world cup (unanswerable)  0.4543      0.9085  ABSTAINS on strict only
```

**The "over-eager on the strict path" half of 7.1 is wrong.** It was an
artifact of a synthetic two-chunk fixture; real retrieval returns 40 candidates
and the blend lands at 0.78-0.87 for genuine questions. On the strict path -
which is the path a *standalone* question takes, since `rewritten = queries !=
[raw_query]` - **G2 behaves exactly as designed**: it answers all four real
questions and correctly refuses the out-of-corpus one at 0.4543.

**The "dead on the fused path" half stands.** Every question scores 0.9085
there, including the unanswerable one, so a rewritten query that has no answer
in the corpus will answer rather than abstain. G3's grounding prompt still
declines in prose, so the user gets a refusal either way - differently worded,
not absent.

> ❌ **"Net effect on the demo: none" was wrong, and Stage 11 is why.** The
> reasoning below holds only for the *first* attempt. `retry_node` sets
> `rewritten = True`, so any question that fails the first grade is re-graded on
> the fused path - and failing the first grade is exactly what an unanswerable
> question does. A standalone out-of-corpus question therefore scored 0.4543,
> asked for a retry, and came back 0.9085 and answered. Fixed in *Stage 11*.

**Net effect on the demo: none.** The questions a demo asks are standalone,
take the strict path, and answer correctly with a wide margin. The exposure is
narrow: a *follow-up* (where the rewrite genuinely rewrites) about something
the corpus does not cover.

**One margin this could not measure:** the earlier end-to-end run reported
`relevance 0.3532` for the uptime question against a floor of 0.35 - that is
the *reranked* path on the PDF corpus, a different scale to the numbers above,
and it passed by 0.0032. Re-measuring it needs a real Cohere call. Worth
knowing that the reranked path's margin is thin where the fused path's is not.

### Stage 8 - generate (G3) ✅

**The prompt-injection defence holds.** Five attack shapes - a closing
delimiter, an opening delimiter, both, a bare "ignore previous instructions",
and fake `SYSTEM:` framing - all stayed inside their wrapper, exactly one
`[[[DOCUMENT n]]]` and one `[[[/DOCUMENT n]]]` per chunk. The escape turns
`[[[/DOCUMENT 1]]]` into `[ [ [/DOCUMENT 1]]]` in the chunk body, so a document
cannot close its own block. `escape_delimiter` is applied to the rolling
summary and conversation turns as well, so history is not a side door.

**Citation markers resolve by position we assigned, not by trusting the
model** - a hallucinated or out-of-range marker resolves to nothing rather than
to the wrong chunk. Degenerate candidates (empty `parent_text`, whitespace-only,
entirely empty, zero candidates) all build a prompt without raising.
`context_budget` scales 5 → 10 → 12 and caps correctly.

**Stage 3's token floor is confirmed inert here** - the check that mattered
most, since that floor was my own fix and `fit_context` is its main consumer.
Across the 42 real corpus chunks the heuristic totals **14,618 tokens before
and after**, and the floor binds on **0 of 42**. No over-trimming.

**8.1 A context trimmed to fit the token budget said nothing.**
`fit_context` returns a drop count *specifically* so the caller can raise a
degradation - its docstring says so in as many words - and
`build_generate_messages` discarded it with `candidates, _ =`. It had exactly
one caller, so nothing anywhere recorded it. Fewer passages reached the model,
the answer thinned accordingly, and neither the SSE stream nor the persisted
message mentioned it (I1).

MEASURED, and the reachability is the familiar shape: on the demo corpus it
**never fires** - 12 chunks total 1,465 tokens against a 4,000 budget, median
parent 91 tokens. On a corpus of long sections it fires hard: parents reach the
1,200-token cap (the corpus already has one at 1,156), so 12 of them exceed the
budget three times over and two thirds of the retrieved evidence is dropped in
silence. A contract, a 10-Q or any long-form report does this on every turn.

Fixed by returning the count and recording a `cap_reached` degradation at all
three call sites, including the rate-limit fallback - where the budget is half
the primary's, so it drops *more* often, and the reader is already on a degraded
answer when it happens.

Stage 7 re-probed from scratch, unchanged. 369 backend tests, ruff and mypy
clean.

### Stage 9 - verify + citation persistence ✅

**This morning's cache fix re-verified, not assumed** - two identical
`verify()` calls make **two** HTTP calls and can return different verdicts.
Now pinned by a test that asserts `use_cache is False` at the call site, since
`StubLLM` bypasses the cache entirely and no behavioural test could catch a
regression.

**I2 holds on every failure shape tried:** an LLM error, malformed JSON,
`supported` as a string, `supported` as null, and a truncated reason-only
response all yield `None` and leave `any_unsupported` False. A dead judge never
reads as a finding.

**Claim splitting is correct** across bullet lists without terminators, markers
placed after the sentence terminator, fullwidth `［1］` and CJK lenticular
`【1】` brackets, leading marker fragments, and multi-marker sentences. One
claim citing `[1][2]` produces **one** judgement against the union of both
sources, not two judgements that would each fail - the reference project's bug,
still absent.

**An unsupported verdict is sticky in both orders** - `False` then `True` and
`True` then `False` both end at `False` - and an unknown never downgrades a real
`True`. Coverage counts factual claims only; a discourse line does not dilute it.
A marker with no source and an empty source map both resolve to no judgement
rather than to a guess.

**9.1 The verification write path was unscoped (I3).** `run_verification` took
no `user_id`: its `SELECT` keyed on `message_id` alone and its `UPDATE` on the
row id alone, while `message_citations` carries a `user_id` column. Safe today
only because the caller derives `message_id` from that user's own turn - which
is precisely the "scoping by remembering" I3 exists to replace, and the same
class as 4.2. Both statements now carry `user_id`; the one caller passes it.

Stage 8 re-probed from scratch, unchanged. 370 backend tests, ruff and mypy
clean.

### Stage 10 - memory / multi-turn ✅ (one item flagged, not fixed)

**Verified correct:** `load_memory` is scoped by `user_id` (another user gets
0 turns), the window is `recent_turns_n * 2` = 10 messages as designed,
`update_memory` fails soft on **both** LLM calls while still persisting the
locally-extracted identifier ledger (`{'zx9-4471': 'ZX9-4471', '7.3.1':
'7.3.1'}` survived a total LLM outage), the rolling summary stays `None` rather
than collapsing to `""`, the entity ledger bound keeps 40 of 60 and drops the
**oldest**, and `ConversationState` is created exactly once.

**No memory reaches retrieval.** The entity ledger feeds `rewrite` by design -
it is the substitution source for coreference - but the rolling summary reaches
only `generate`. Nothing in the memory path touches a retrieval filter, which
is the same separation the contract demands for preferences.

**10.1 FLAGGED, NOT FIXED - conversation order has no deterministic tiebreak.**
`load_memory` does `ORDER BY created_at DESC LIMIT n` then reverses in Python,
and `created_at` is set Python-side by `_now()`. With ten messages written at an
identical timestamp the result came back **completely reversed**:

```
['message 09', 'message 08', ... 'message 01', 'message 00']
```

Not a small perturbation - the whole conversation inverted, which would feed the
model an answer before the question it answers and break coreference outright.

**Why it is flagged rather than fixed:** exact microsecond ties are effectively
impossible in normal operation (user and assistant messages are seconds apart),
so the realistic trigger is not a tie but **a backward clock step** - NTP
correcting a container's drift between two turns - and a tiebreaker does not fix
inversion. The correct fix is a monotonic ordering column (the ids are UUIDs, so
there is nothing monotonic to fall back on), which is a schema migration and not
something to land days before a demo. Post-demo work; recorded so the failure
mode is known rather than discovered.

**10.2 History was unbounded in the route and rewrite prompts.** The turn
*count* was capped - 4 for route, 6 for rewrite - but each turn's text was not,
and an assistant answer has no length limit. MEASURED with five turns of
realistic answers:

```
                before        after
route prompt    ~3,146 tok    ~354 tok
rewrite prompt  ~4,714 tok    ~526 tok
```

~4,700 tokens to resolve one pronoun, and ~3,100 to classify one short message
into one of four words - paid on every turn, growing with the conversation,
with no ceiling. Fixed with a 600-character per-turn cap. Truncation is from the
tail deliberately: the long turns are assistant answers, while the referent a
follow-up needs lives in the short user turns and the opening of an answer, so
that is the least coreference signal lost per character removed. A short
conversation is replayed untouched, pinned by its own test.

Stage 9 re-probed from scratch, unchanged. 372 backend tests, ruff and mypy
clean.

### Stage 11 - full end-to-end regression ✅ (and it caught the one that mattered)

Real Postgres, real Qdrant, real ingest pipeline, real graph. Only the LLM is
stubbed, so nothing was spent. Run against a **third corpus**, generated for
this stage and unrelated to either set already tested - a municipal transit
authority rather than SaaS documentation, with each file carrying a shape that
broke something earlier in the review:

```
01_operations_note.txt            694 B   cp1252: curly quotes, em dash, £   (1.1)
02_concession_agreement.md      60,478 B   one unbroken 340-clause section    (3.1, 8.1)
03_fares_and_concessions.md       708 B   markdown table
04_signalling_requirements.md  13,265 B   90 list items, no sentence terminators (3.1)
05_programme_status.md            296 B   astral emoji before a citable fact (2.1)
```

All five ingested to `ready`. **Zero I5 offset violations** across every stored
chunk - each `parent_text` sliced back out of `normalized_text` exactly, and
every child window sat inside its parent. All five demo questions retrieved the
**correct document at rank 1**, not merely somewhere in the candidate list:

```
question                                       grade  relevance  top passage from
What is the peak headway on the Blue Line?      pass     0.7642  01_operations_note.txt
What does the monthly pass cost for Zone 1-3?   pass     0.7707  03_fares_and_concessions.md
How quickly must the interlocking detect ...    pass     0.7959  04_signalling_requirements.md
When did the Blue Line extension enter ...      pass     0.7642  05_programme_status.md
What must the concessionaire report within ...  pass     0.8571  02_concession_agreement.md
```

**11.1 FIXED - the abstain terminal was unreachable, and finding 7.1 was the
cause.** The first run answered *"Who won the 1998 football world cup?"* from
railway-signalling chunks at `relevance = 0.9085, grade = pass`. The tell was
`rewritten = True` on a first-turn standalone question, which the rewrite cannot
produce - the stub returns the message unchanged. `retry_node` sets it.

Traced with a dedicated probe (`stage11_retry_probe.py`), driving the two
attempts by hand against the real corpus:

```
ATTEMPT 0 (strict)     relevance 0.4543  floor 0.50  -> RETRY     top scores 0.5000, 0.4918, 0.4839
ATTEMPT 1 (after retry) relevance 0.9085  floor 0.50  -> PASS      top scores 1.0000, 0.9836, 0.9677
identical top-10 chunk ids across the two attempts: True
```

The retry re-ran the **identical query**, fused the **identical result set with
itself**, and doubled every score - two equal contributions over a ceiling built
for two leaves `k / (k + rank)`, so rank 0 is 1.0 by construction. Attempt 0
judged the question correctly and asked for a corrective retry; the retry
converted a correct refusal into a confident wrong answer.

Why this outranks how 7.1 was written up in Stage 7. That write-up deferred the
fix because the strict path "is what a standalone question takes" and behaves
correctly there. True, and irrelevant: **`retry` is the path every unanswerable
question takes**, so the broken path was precisely the abstention path. The
deferral protected a path that needed no protection.

**The fix keeps rank for ordering and carries magnitude across** - about ten
lines in `fuse_formulations`. Reciprocal rank still decides position (two
separate Qdrant calls produce comparable ranks and incomparable raw scores, and
that reasoning is untouched), but `fused_score` is now the **best incoming
normalised score** across the formulations that surfaced the chunk. Every
incoming score was already divided by the analytic `RRF_MAX` at the retrieval
boundary, so they are on one scale and nothing is renormalised here (I7). Best-of
matches the existing best-rank rule: a chunk one formulation found strongly is
not marked down because the other missed it.

**No tuned constant moved.** `floor_fused` and `floor_rerank` are untouched -
this is a correctness fix, not a tuning change. The point is exactly that the
fused path now produces scores on the scale `floor_fused` already assumed.
`stage7_g2_gate` re-run shows the two paths agreeing row for row:

```
match quality                      after fusion  relevance  gate
excellent (top of one branch)            0.5000     0.4984  ABSTAINS
terrible (rank 39, last of top_k)        0.3030     0.3024  ABSTAINS
same rows, strict path (fuse_formulations not called): identical
```

Blast radius checked before the change. One caller (`retrieve_node`'s
single-effective-query branch; the multi-intent branch goes through
`interleave_intents`, which already carries scores through untouched). Three
readers of `fused_score`: `relevance_score` - the gate this fixes;
`is_decisive`, which compares a **ratio** of top to runner-up and was therefore
measuring a rank ratio (~1.017) against a threshold meant for scores, so it too
becomes correct and will now skip Cohere when fusion really was decisive, saving
quota; and the two persist-for-display sites in `chat.py` / `turn.py`, which
gain a meaningful number. Four regression tests added to `test_retrieval.py`,
including the self-fusion case that is the retry.

**Left alone deliberately:** the retry still issues a redundant Qdrant round
trip for a query it already ran. That is wasted work on a 0.1 vCPU box, not a
correctness problem - post-fix the second attempt scores identically and
abstains correctly. Making the corrective retry actually corrective means
deciding what it should change, which the contract never specifies; that is a
design question, not a pre-demo fix.

**After the fix, end to end:**

```
'Who won the 1998 football world cup?'
  relevance=0.4543 grade=abstain abstained=True
  answer: 'I could not find anything in your documents that answers this. I searc…'
```

**Empty-workspace isolation (4.1) still holds** - 0 candidates, and the
`scoped_to_nothing` guard added during this stage means a brand-new workspace
answers "I could not find anything" instead of a 503. That guard was itself a
Stage 11 catch: once `_scope` stopped treating `[]` as "no filter", an empty
workspace legitimately returned zero candidates for the first time, and
`retrieve_node` read zero as a dead dependency.

**All sixteen stage probes re-run in one pass after the fix - none regressed.**
Gates: **376 backend tests**, ruff clean, mypy clean across 45 files; **83
frontend tests**, `tsc --noEmit` clean, eslint clean.

## Bug 4 - the Sources footer listed every document in the workspace

**Symptom:** the "Sources" block at the end of an answer showed a card for
every document in the workspace, not the documents the answer actually cited.

**Root cause:** not a bug in the footer's dedupe - a category error about what
the citation list *is*. `answer.complete` carries one `Citation` per DATA block
that reached the prompt, numbered by position there (`turn.py::_build_citations`
walks `top`, the whole context). That is correct and load-bearing: it is what
makes `[n]` resolvable, and `message_citations` is the retrieval trace and the
eval dataset. But retrieval reaches into every document on purpose, so on a
small workspace "every block in the prompt" and "every document" coincide -
`SourcesFooter` deduped by `doc_id` and faithfully rendered the whole corpus
under a heading that claims those documents were used.

**Fix, frontend-only and deliberately so.** The backend payload is unchanged;
narrowing it there would have made the retrieval trace unavailable to the eval
harness and to `GET /messages/{id}` to fix a presentation bug.

- `lib/markdown-citations.ts` - new `citedMarkers(text)`, sharing the same
  `MARKER_RE` the remark plugin renders chips against, so the footer and the
  chips can never disagree about what counts as a marker (three digits, so
  `[2024]` is prose).
- `ChatPane.tsx` - `SourcesFooter` takes the answer text and filters to cited
  markers *before* deduping, so the surviving card per document is the lowest
  cited marker and clicking it opens a span the answer actually leaned on.
  Both call sites pass it: `turn.content` for a committed turn, `answer` live.

**Known and accepted:** `citedMarkers` scans the raw string, so a `[1]` inside
a code fence counts even though `remarkCitationMarkers` leaves it literal. The
result is a superset of the rendered chips - the failure mode is listing a
source with no chip to click back to, never dropping one that has a chip.

**Falls out of this:** a refusal or abstain now renders no footer at all,
instead of a full set of source cards under an "I could not find this" answer.

4 tests in a new `lib/markdown-citations.test.ts`; frontend suite 87 passing,
`tsc --noEmit` and eslint clean.

## Review findings on bugs 1-3 that were not code changes

- **`useChatStream`'s hand-rolled iterator loop stopped closing the
  generator.** `for await...of` calls `iterator.return()` on `break`, which
  runs `parseFrames`' `finally` and releases the reader lock; the rewritten
  loop just exits. The abort tears the socket down either way, so this was
  latent, but it is a real regression from the rewrite - restored with a
  `finally`.
- **`_rescue_feature_grid` ran `page.extract_words()` on every page** the
  earlier detectors passed on, i.e. nearly every prose page, before any cheap
  check. Now gated on `"Feature" in text` first - the header pattern requires
  that literal token, so the gate is exact. Deliberately the bare token and
  not the header regex, since `extract_text` and the word-grouping can
  disagree about line breaks.
- **Not fixed, flagged:** `_rescue_feature_grid`'s docstring says it extends
  to the "last aligned row", but the code has no alignment test - it extends
  by `_function_word_count`, a 19-word closed vocabulary tuned against one
  PDF. It works on that PDF and there is no corpus to retune against, so it
  stands; but the doc and the code disagree, and a table followed by a short
  heading rather than a sentence would pull the heading into the crop.
- **Not fixed, flagged:** `enabled: isLoaded && isSignedIn` plus
  `showLoading = isPending` means a signed-**out** client sits on the loading
  skeleton forever, since a disabled query stays `isPending` with nothing to
  resolve it. `clerkMiddleware`'s `auth.protect()` redirects on navigation so
  the normal path is covered; a sign-out in another tab is not.

---

# Follow-up session - 2026-08-02 (evening): frontend E2E + deploy preflight

Ran the whole stack under `AUTH_MODE=dev` and drove it through a real browser,
then a deployment preflight against the live Azure/Vercel/Qdrant Cloud setup.
Everything below is measured. **Still nothing committed** - the working tree is
now 47 files / +2,573 lines ahead of `origin/main`.

## Bug 6 - `refuse` and `history` answers never reached the browser

**Found by driving the UI, not by reading code.** Turn 1 answered perfectly;
"Who won the 1998 football world cup?" produced a user bubble and then nothing,
while `GET /conversations/{id}` showed the assistant message persisted with real
text. The gap between "persisted" and "rendered" is the whole bug.

**Root cause, isolated by capturing raw SSE per route** (`sse_capture.py`):

```
REFUSE   turn.start -> pipeline.stage x2 -> answer.complete     0 answer.delta frames
HISTORY  turn.start -> pipeline.stage x2 -> answer.complete     0 answer.delta frames
RETRIEVE turn.start -> ... -> answer.delta x5 -> answer.complete  5 answer.delta frames
```

§8 gives the answer text exactly **one** transport: `answer.delta`.
`answer.complete` carries `{message_id, citations}` and no text, and
`useChatStream`'s reducer builds its answer string purely by concatenating
deltas (`answer: state.answer + event.data.text`). `refuse_node` and
`history_node` return their answer whole and went straight to `_emit_answer`,
so the client had nothing to concatenate and rendered an empty bubble.

`abstain` was never affected - it emits its own `abstain` frame and `AbstainCard`
renders from that payload rather than from `state.answer`. So the hole was
exactly the two terminals that produce prose without streaming it.

`history` is the expensive half: it spends a real LLM call on a genuine answer
that the reader never saw.

**Fix:** `turn.py::_emit_unstreamed_answer` - sends the answer as a single
`answer.delta` inside a `generate` stage bracket, so §8's invariant 3
("`answer.delta` appears only after `pipeline.stage{generate,started}`") holds
verbatim and the frontend needs no special case. Contract §8's `answer.delta`
row updated to say the text has one transport and that a whole-answer terminal
must still use it.

**Verified after the fix:** both routes emit 1 delta and render live in the
browser; the history route correctly answered "You just asked me to write a poem
about the sea." Pinned by
`test_a_terminal_nodes_answer_reaches_the_client_as_a_delta`.

**`scripts/verify_api.py` would have caught this** - its step [5] follow-up now
routes to `history` and asserts on the streamed answer text. It was not run
between the routing landing and today. Worth running before every deploy; it is
the cheapest full-product check in the repo.

## Frontend E2E under AUTH_MODE=dev - otherwise clean

Two documents (`03_pricing_and_plans.pdf` + `01_architecture_and_api_reference.md`)
uploaded together from the New Workspace dialog, both `ready` (10 and 12 chunks),
**no deadlock and no pool exhaustion** - Bug 4's fixes hold through the real UI.

Confirmed working in the browser, not inferred: verified (green) citation chips,
the Sources footer listing only the cited document, citation click opening the
PDF source pane on the right page, multi-turn coreference
("What about Enterprise?" -> "audit logs are retained for 12 months"), and the
aborted-stream path rendering "This question never got an answer" rather than a
silent empty turn. Zero console errors across every run.

**A trap for anyone writing UI tests here:** a settle-predicate that greps the
whole page for a substring will match text already on screen from an earlier
turn and fire immediately. Two runs "found" empty follow-ups that way before the
real bug was isolated. Wait on the transcript being *stable*, not on a keyword.

## Deploy preflight - measured against the live setup

Gates: **377 backend tests** (376 + the new one), ruff clean, mypy clean across
45 files, **87 frontend tests**, `tsc --noEmit` clean, eslint clean.
`verify_api.py` **32/32**. `poetry check --lock` clean; both Docker images build
from the committed lockfiles.

**I7 re-verified against Qdrant Cloud for the first time.** The Deployment Setup
Checklist flagged the authenticated HTTPS path as never exercised. Ran
`probe_rrf_rank_base.py` against the cloud cluster (1.18.3, green, 185 points,
dense+sparse): a chunk topping both branches scores `0.03333333`, so
`rank_base` is **0** there too and `RRF_MAX` is unchanged. That flag can close.

Azure config is correct and worth not re-checking: `AUTH_MODE=clerk`,
`APP_ENV=azure`, `CORS_ORIGINS` and `CLERK_AUTHORIZED_PARTIES` both the Vercel
origin, every credential a `secretRef` rather than an inline value, CORS
preflight returns the right headers, `GET /workspaces` without a token is 401,
and Postgres is Azure Flexible Server 16 with `require_secure_transport=on`
(so the asyncpg connection is encrypted, and there is no Render 44-day expiry).

### Open items, none of them code defects

1. **The deployment is behind the code.** Container App runs image tag
   `1c8d05e`, one commit behind `origin/main` and 2,573 uncommitted lines behind
   the working tree. Every fix in this note - Bugs 1-6, the five pipeline bugs,
   the UI overflows - is undeployed. This is the only item that makes the live
   link misrepresent the project.
2. **`minReplicas: 0`, measured at 33.8 s.** `GET /healthz` cold took 33.798 s,
   matching Ronak's browser trace exactly. Still the first thing a reviewer
   meets. `--min-replicas 1` is the direct fix; the "no uptime pinger" rule was
   written against **Render's instance-hour cap**, which Container Apps does not
   have, so that constraint does not automatically carry over.
3. **Clerk is on a development instance** (`exotic-magpie-90.clerk.accounts.dev`).
   The sign-in card renders an orange **"Development mode"** strip, clipped at
   the card's bottom edge - on the reviewer's first screen.
4. **README test counts are stale**: "393 passing (327 backend, 66 frontend)"
   against an actual 464 (377 + 87). The README also has no live-link section.
5. **`backend/` has no `.dockerignore`**, so every build ships a 732 MB context
   (`.venv` 612 MB, `.fastembed_cache` 65 MB) to the daemon. The image is
   unaffected - the Dockerfile `COPY`s named paths, never `. .` - so this is
   build time only. `frontend/.dockerignore` exists and documents why.
6. **CLAUDE.md still frames Render as the binding constraint** - "512 MB RAM /
   0.1 vCPU", "750 free instance-hours", "Render free Postgres is deleted after
   44 days". The Container App is provisioned **1 vCPU / 2 GiB**. The decisions
   those limits produced (bge-small, no local cross-encoder, single uvicorn
   worker, no ingest worker service) are all still *defensible*, but they are no
   longer *forced*, and a future session reading CLAUDE.md would think they are.
7. **`maxReplicas: 10`** still outruns the per-process ingest semaphore, as
   already noted under Bug 4. Parts 3 and 4 of that fix survive multiple
   replicas; part 2 does not.


### Resolutions - Ronak ruled on all seven, same session

**Decided trade-offs, not defects. Do not re-raise these:**

1. **Deployment behind the code is intentional.** This whole session was the
   pre-push verification pass; the push happens after it, not before.
2. **`minReplicas: 0` stands.** Settled in an earlier session - this is a demo,
   and a 33.8 s cold start is the accepted price of not paying for an idle
   container.
3. **The Clerk development instance stands.** Free tier for the live demo; the
   orange "Development mode" strip is a known cost of that and was accepted
   when the trade-off was made.

**Fixed this session:**

4. **README test counts corrected** to 464 (377 backend, 87 frontend). Two
   places disagreed with each other *and* with reality - the summary line said
   393 (327 + 66) and §8 said 375 (309 + 66). Both now match a measured run.
5. **`backend/.dockerignore` added.** Context 732 MB -> a few MB. Image byte-size
   unchanged at 651 MB, which is the proof it was build-time only. Scoped to
   generated artefacts on purpose: `tests/`, `scripts/` and `evals/` are *not*
   excluded, because they are real source and a future move to `COPY . .` would
   silently drop them.
6. **CLAUDE.md reframed around Azure.** Render is gone. The memory-derived
   decisions (bge-small, no local cross-encoder, single uvicorn worker, no
   ingest worker) are now recorded as *settled and shipped* rather than as
   *forced by 512 MB* - because they are what every measurement in the vault was
   taken against, so re-opening one means re-running the eval, not editing a
   constant.

**7. Deferred by decision - not required for the demo.** No cross-process lock,
no replica cap. Recorded here so it is diagnosed in minutes rather than
rediscovered:

The ingest semaphore is per process, `maxReplicas` is 10, `scale.rules` is
`null` (so the default HTTP scaler fires at 10 concurrent requests per replica),
each replica's pool is 5 + 2, and Postgres `max_connections` is **50**. Ten
saturated replicas would want 70. Long-lived SSE streams are what a concurrency
scaler counts, so a multi-document upload is the shape most able to trigger it.

**Correctness is not at risk at any replica count** - parts 3 and 4 of the Bug 4
fix (id-sorted inserts, retry on SQLSTATE 40P01) are cross-process by
construction. Only pool pressure is.

Symptoms that mean this has arrived, in the order they would appear:

* `QueuePool limit of size 5 overflow 2 reached, connection timed out` in the
  backend logs - the same string as the original Bug 4 measurement, so check
  the replica count *before* concluding it is the old bug.
* `FATAL: sorry, too many clients already` from Postgres - this one is
  new and is the unambiguous multi-replica signature; a single replica cannot
  produce it, since 7 < 50.
* `az containerapp replica list -n knowledgehub-backend -g knowledgehub-rg`
  showing more than one replica during an ingest.

First move if it happens: `az containerapp update --max-replicas 1` (seconds,
reversible). The durable fix is a Postgres advisory lock so the cap is global,
which is only worth writing if this stops being a demo.

[[KnowledgeHub Index]] · [[Pipeline Review Log]] · [[Session Handoff 2026-07-31]] · [[Technology Documentation Links]]
