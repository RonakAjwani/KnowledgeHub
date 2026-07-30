# Retrieval Pipeline Contract

The interface spec the implementation is built against. Stage boundaries, what crosses each one, and what happens when a stage fails. Written against [[Confirmed Infrastructure Constraints]] - every choice here is bounded by 512 MB / 0.1 vCPU.

Not code. Precise enough that implementation is mechanical.

---

## 0 · Cross-cutting invariants

These hold at every stage. Violating one is a bug regardless of test results.

**I1 · Degradation is never silent.** Every fallback appends a `Degradation` record to the response. A fallback path must never be indistinguishable from a healthy one. Generalises [[NotebookRAG Reference Project]]'s `rerank_ok`.

**I2 · Unknown is not zero.** A failed judge yields `null`, never `false`. A dead verifier must not read as "citations unsupported."

**I3 · `user_id` scopes everything.** Every Postgres query and every Qdrant search carries it as a filter. There is no unscoped read path.

**I4 · Every external dependency has a timeout and a named fallback.** No unbounded waits. See §5.

**I5 · Offsets are into `normalized_text`.** Never into raw bytes, never into a chunk. See §1.

**I6 · Retrieval attempts are hard-capped at 2** (initial + one corrective retry). Enforced by a counter in state, not by prompt instruction.

**I7 · A score means the same thing on every query.** No per-query renormalisation, ever. Scores are normalised against a *fixed, analytically known* maximum - never against the observed maximum of the current candidate set. Renormalising per query makes every threshold in the system meaningless while looking perfectly reasonable in code. See §4 `grade`.

---

## 1 · The offset decision (resolve first - cannot be retrofitted)

Character offsets need a referent. Two options were considered:

| | Renders | Offsets map to | Complexity |
|---|---|---|---|
| **(a) Normalized text** ✅ | Extracted, sanitized plain text | Directly, 1:1 | Low |
| (b) Original PDF via pdf.js | Original visual document | Requires bounding boxes, not char offsets | High |

**Chosen: (a).** Uniform across PDF/txt/md, trivially correct, and *better for the verification story* - the source pane shows the user exactly the text the retriever saw, not a visually similar rendering of it. If the extraction mangled a table, the user sees that, which is the honest outcome.

**Consequence:** `normalized_text` is persisted per document and is the single referent for every offset in the system.

**Ordering constraint:** normalization and sanitization (G3) run **before** offsets are computed. Sanitizing after offset assignment invalidates every offset. This ordering is load-bearing.

### Construction rule - one builder, one string

`normalized_text` is built **exactly once**, by one function:

```
build_normalized_text(Block[]) -> (text: str, spans: BlockSpan[])

BlockSpan
  block_index  int
  start, end   int      into text
  section      str?
  page         int?
  block_type   prose | table | figure | formula
  is_derived   bool
```

It returns the string *and* every block's span within it. Sanitisation (G3) and derived-block insertion both happen **inside** this function, before spans are assigned. Every downstream stage - chunker, cross-reference scanner, citation resolver - consumes `spans`. **Nothing else may ever re-concatenate blocks.**

Two independent concatenations of the same document drift, and the drift is silent. [[NotebookRAG Reference Project]] already carries this bug in embryo: `LoadedDocument.full_text` joins blocks with `"\n\n"`, while its fixed chunker rebuilds a separate `combined` string with hand-rolled `cursor += len(block.text) + 2` arithmetic. Nothing depended on the two agreeing, so nothing caught it. Once offsets are load-bearing, that disagreement is a citation highlighting the wrong span.

**After the builder returns, `normalized_text` is immutable.** Any later transformation - a stray `.strip()`, a whitespace collapse, a re-clean - invalidates every offset in the document.

---

## 2 · Core types

```
Document
  id              str        uuid
  user_id         str        Clerk subject
  filename        str
  mime            str        pdf | text/plain | text/markdown
  content_sha256  str        idempotency key, scoped per user
  blob_ref        str        original file - download only. NOT a filesystem path; see below
  normalized_text str        THE offset referent; source pane renders this
  status          enum       queued | parsing | chunking | embedding | ready | failed
  error           str?       populated iff status == failed
  chunk_count     int
```

```
Chunk
  id                str    sha256(doc_id | chunk_index | text)[:24]   deterministic
  doc_id            str
  user_id           str
  chunk_index       int

  text              str    CHILD - embedded, indexed, BM25'd
  char_start        int    into Document.normalized_text
  char_end          int

  parent_text       str    PARENT - what the LLM actually receives
  parent_char_start int
  parent_char_end   int

  section           str?   heading path, e.g. "Setup > Auth"
  page              int?   PDF only
  token_count       int

  chunk_type        enum   prose | table | figure | formula
  is_derived        bool   synthesised (e.g. VLM figure description), not extracted
  related_spans     list[(int,int)]   where the document itself discusses this object
```

**`blob_ref` cannot point at local disk.** Render's free filesystem is ephemeral - everything written locally is lost on spin-down, which happens after 15 idle minutes ([[Confirmed Infrastructure Constraints]]). A `blob_ref` holding a path would resolve to a missing file within the hour.

**Resolution: store the original as a Postgres `bytea`, under a size cap.** Weighing the alternatives:

| | |
|---|---|
| Local disk | ❌ Wiped on every spin-down |
| External object storage | Correct in general, but adds a provider, credentials and a failure mode for a feature nothing depends on |
| **Postgres `bytea`** ✅ | Already a hard dependency; 1 GB is ample for an assessment corpus; deletion cascade stays in one transaction |
| Drop the feature | Viable - see below |

Worth being clear that this is a *convenience* feature, not a load-bearing one. §1 chose `normalized_text` as the thing the source pane renders, so no citation, highlight, or verification path touches the blob. It exists so a user can download their own file back and so a corpus can be re-ingested under different chunking without re-uploading. If the 1 GB budget gets tight, dropping the original download costs the product almost nothing - and the ingest ceiling (413, §6) should be set against that 1 GB budget either way.

`related_spans` carries cross-reference resolution results - the caption's label (`Table 2`, `Fig. 3`) scanned back across the document to find every body-text mention. Those spans are **real document text**, so a table citation can highlight both the table and the paragraph explaining it.

`chunk_type` and `is_derived` are required by [[Document Parsing And Complex PDFs]]. Derived spans are inserted into `normalized_text` at the source position and explicitly marked, so offsets stay valid and one citation mechanism covers both extracted and derived content. **Citations resolving to derived chunks must be visually distinguished in the UI** - the user has to be able to tell "this is in the document" from "this is a model's description of a picture in the document."

Child is the retrieval unit; parent is the generation unit. `char_start/end` drive the source-pane highlight; the parent range drives the scroll target.

```
RetrievedChunk
  chunk         Chunk
  dense_rank    int?
  sparse_rank   int?
  fused_score   float      from Qdrant server-side weighted RRF
  rerank_score  float?     None when rerank was skipped or failed
```

```
Degradation
  stage      str       route | rewrite | retrieve | rerank | generate | verify
  reason     str       timeout | rate_limited | parse_error | quota_exhausted | unavailable
  fallback   str       what ran instead
  detail     str?
```

```
Citation
  marker      int       the [n] in the answer
  chunk_id    str
  doc_id      str
  filename    str
  section     str?
  page        int?
  char_start  int       -> source pane highlight
  char_end    int
  verified    bool?     None = not yet checked or judge failed (I2)
```

---

## 3 · Ingest pipeline

Async. Status transitions push to the client over SSE.

| # | Stage | In -> Out | Failure -> fallback |
|---|---|---|---|
| 1 | **Upload** | file -> `Document{status:queued}` + blob | 413 too large · 415 unsupported type · **duplicate `content_sha256` for this user -> return existing doc, HTTP 200, no reprocessing** (idempotency, I3-scoped) |
| 2 | **Parse** | blob -> ordered `Block[]` with heading path, page, `block_type` | **Tiered** - see [[Document Parsing And Complex PDFs]]. T1: `pypdf`/`pdfplumber` always. T2: pages flagged complex by a cheap local heuristic (`page.find_tables()`, figure objects) render to image -> VLM via the **existing LLM adapter** -> markdown. No GPU, no new dependency, no RAM cost. Unparseable -> `status:failed` + user-facing reason; never a partial index. Persists an extraction-quality signal (pages escalated, tables recovered) surfaced in the document manager. |
| 3 | **Sanitize · G3** | `Block[]` -> `normalized_text` | Strips zero-width chars, zero-opacity/white-on-white runs, HTML comments, PDF annotation + metadata layers. **Non-fatal by design** - suspicious content is removed and counted, never rejected. Emits `sanitization_report{removed_spans:int, kinds:[]}` persisted on the Document. |
| 4 | **Chunk** | `normalized_text` -> `Chunk[]` | Structure-aware boundaries (headings, pages). Child ≈ 200-300 tokens; parent = enclosing section window, capped. **Offsets assigned here**, against `normalized_text`. **Tables are atomic** - they bypass the prose splitter; if oversized, split by row with the header row repeated in every split, never mid-row. Table and figure chunks get a lead line before embedding (raw markdown embeds badly as prose) sourced by the escalation ladder in [[Document Parsing And Complex PDFs]]: **caption -> author's referencing narrative -> table content -> VLM synthesis as fallback only.** Prefer the document's own words; synthesised descriptions are marked `is_derived`. |
| 5 | **Dedup** | `Chunk[]` -> `Chunk[]` | Deterministic IDs make re-ingest an idempotent upsert. Identical text at a different index is a distinct chunk (position is meaning). |
| 6 | **Embed** | `Chunk.text[]` -> dense vectors | fastembed `bge-small-en-v1.5`, in-process, **batched** - 0.1 vCPU makes batch size a real tuning parameter, not a detail. OOM/timeout -> `status:failed`, no partial collection writes. |
| 7 | **Upsert** | -> Qdrant point + Postgres row | Qdrant point carries dense + sparse named vectors and payload `{user_id, doc_id, chunk_id, chunk_index, page, section, char_start, char_end}`. Postgres mirrors the chunk for citation resolution. **Qdrant first, then Postgres** - an orphaned vector is recoverable, an orphaned citation row is not. |

**Deletion contract:** delete cascades Qdrant points -> Postgres chunks -> blob -> document row, in that order. Partial failure leaves the document in `status:failed` with the remainder retried, never silently half-deleted.

---

## 4 · Query pipeline

LangGraph state graph. Nodes read and write one shared `QueryState`.

```
QueryState
  # input
  user_id, conversation_id, raw_query
  selected_doc_ids   list[str]?   None = all of the user's ready documents

  # conversation memory
  recent_turns       Turn[]       last 4-6 verbatim
  rolling_summary    str?
  entity_ledger      dict         doubles as the coreference source for Rewrite

  # routing
  route              retrieve | history | refuse
  # rewriting
  effective_query    str          rewritten, or raw if rewrite degraded

  # retrieval
  candidates         RetrievedChunk[]
  attempt            int          0 or 1 - hard cap (I6)
  rerank_status      applied | skipped_decisive | cached | failed

  # grading
  relevance          float
  grade              pass | retry | abstain

  # output
  answer             str
  citations          Citation[]
  degradations       Degradation[]      (I1)
```

### Node contracts

**`route` · G1** - reads `raw_query`, `recent_turns`. Writes `route`.
Classifies: needs retrieval / answerable from history / out of scope.
*Tuned loose* - over-refusal on benign queries is the worse failure.
**Fails open:** on timeout or parse error, assume `retrieve` and record a degradation. Refusing because a classifier died is unacceptable.

**`rewrite`** - reads `raw_query`, `recent_turns`, `entity_ledger`. Writes `effective_query`.
Resolves coreference by **verbatim substitution of the prior mention**. Preserves identifiers, error codes, and technical terms exactly - paraphrase silently damages the BM25 branch.
**Fails open:** degraded -> `effective_query = raw_query`, record degradation.
*Latency note:* fire the raw-query retrieval in parallel with this call. How the two result sets combine - and when the second call is skipped outright - is specified in `retrieve` below.

**`retrieve`** - reads `effective_query`, `selected_doc_ids`, `user_id`. Writes `candidates`.

Each retrieval is **one** Qdrant call: `prefetch[dense, sparse]` + `rrf(weights=[w_dense, w_sparse])`, server-side (1.14+). Payload filter is always `user_id`, plus `doc_id ∈ selected_doc_ids` when scoped. Returns top-40.

**Two formulations - and when the second is skipped.** The raw-query retrieval fires in parallel with `rewrite`. When `rewrite` returns:

| Condition | Action |
|---|---|
| `effective_query == raw_query` - nothing to resolve, or rewrite degraded | Use the raw result set as-is. **No second Qdrant call.** |
| otherwise | Issue the rewritten retrieval, then **nested RRF** across the two result sets |

The skip matters: only ~60% of follow-up turns carry an unresolved coreference ([[Multi Turn Memory Architecture]]), so the second call is avoidable on a large fraction of turns, and on every first turn of a conversation.

**Nested RRF does not violate the no-client-side-fusion rule.** That rule ([[Confirmed Infrastructure Constraints]]) governs dense↔sparse fusion, where the obsolete justification was "client-side keeps weights adjustable" and Qdrant 1.14+ now supports per-branch weights server-side. Merging two *independently executed* queries is a different operation, and one the server cannot perform at all - the branches were never in the same request. Scoped precisely:

> **Fusion *within* a query is server-side. Fusion *across* queries is client-side, and only here.**

The alternative - waiting for the rewrite, then issuing all four branches as prefetches in a single call - is server-side throughout but puts the rewrite back on the critical path, which is the whole thing the parallelism buys. The one place client-side rank merging survives is the place it is actually load-bearing.

**Degradation.** Retrieval as a whole cannot degrade - total failure -> 503, retrieval is the product. But a *partial* failure is not a 503: if the raw retrieval succeeded and the rewritten one failed, answer from the raw result set and record `Degradation{stage: retrieve, fallback: "raw formulation only"}`. Recall is reduced; the turn is not lost.

**`rerank`** - reads `candidates`. Writes `rerank_score`, `rerank_status`.

Conditional, because the Cohere trial is 1,000 calls/month:

> **Skip when the result is already decisive:**
> `fused[0].score / fused[1].score ≥ DECISIVE_RATIO` **and** `fused[0]` is top-3 in *both* branches.
>
> Cross-branch agreement is the real signal. When dense and sparse independently rank the same chunk first, a cross-encoder is unlikely to overturn it - so the call buys nothing.

Cache on `(effective_query_hash, doc_set_hash)`. Client-side rate limiter for the 10 rpm ceiling.
**Fallback chain:** Cohere -> cache -> fused order. Every step below the first records a degradation.

**Two upstream failures, two responses.** Cohere distinguishes them by status code ([[Confirmed Infrastructure Constraints]]), so the client must too - treating them alike wastes the timeout budget on every remaining query of the month:

| Upstream | Meaning | Response |
|---|---|---|
| **429** | Per-minute limit | Transient. Back off; the next query may succeed. Fall back for *this* query only |
| **402** | Monthly quota / billing exhausted | Terminal. **Trip a circuit breaker** - stop calling Cohere for the rest of the deployment and go straight to cache -> fused order |
| timeout / 5xx | Unavailable | Fall back for this query; do not trip the breaker |

The breaker is the point. A 402 guarantees every subsequent call returns 402, so retrying spends 2 s per query to re-learn a known fact. Once tripped it emits one degradation and `rerank_status` reads `failed` from then on - which, per §4 `grade`, routes scoring to `FLOOR_FUSED` rather than disabling the gate. The system runs indefinitely on fused ordering, visibly degraded and still correct.

No `Retry-After` is documented on either code, so backoff is ours to choose rather than obey.

**`grade` · G2** - reads `candidates`, `rerank_status`. Writes `relevance`, `grade`.

`relevance = 0.6·max(score) + 0.4·mean(score)` - a precise lookup answered by one strong chunk must not be penalised by a flat mean.

**Two score sources, therefore two thresholds.** Conditional reranking makes the un-reranked branch the *designed-for majority*, not a rare fallback, so its scale has to be exactly as trustworthy as the reranked one:

| `rerank_status` | Score source | Threshold |
|---|---|---|
| `applied` · `cached` | `rerank_score` - Cohere, already calibrated 0-1 | `FLOOR_RERANK` |
| `skipped_decisive` · `failed` | `fused_score / RRF_MAX` | `FLOOR_FUSED` |

One shared `FLOOR` across both is a bug. Cohere relevance and normalised RRF are different distributions; a single number cannot mean the same thing in both.

**⚠️ `RRF_MAX` is the analytic maximum, never the observed one.** This is invariant I7, and it is the sharpest trap carried over from [[NotebookRAG Reference Project]], which normalised fused scores by the top score *of the current candidate set*. That forces `max(score) = 1.0` on literally every query, pinning the blend at ≥ 0.6 - permanently above any sane floor. Its own eval report records both consequences: the abstention gate never fired on un-reranked configs (0/7 - an architectural certainty misread as a measurement), and confidence *inflated* precisely when evidence was worst, the run's highest-confidence answer of all (0.979) being a half-wrong one whose rerank had failed.

For weighted RRF over two branches the ceiling is fixed by the constants, not by the data:

```
RRF_MAX = (w_dense + w_sparse) / (k + rank_base)   # a chunk ranked #1 in both branches
```

> ✅ **`rank_base` settled empirically 2026-07-28 - it is 0, so the denominator is `k`.**
> Measured against Qdrant **v1.18.0**: a chunk topping both branches with `w = [1, 1]` and `k = 60` scores exactly **0.03333333 = 2/60**, not 2/61. The `+1` written below assumed Qdrant ranks from 1; it ranks from 0. Reproduce with `backend/scripts/probe_rrf_rank_base.py`, and **re-run it after any Qdrant version bump** - a silent change leaves every `FLOOR_FUSED` comparison quietly mis-scaled while looking healthy.

Query-independent, so `FLOOR_FUSED` means the same thing on every query. A chunk that tops one branch and is absent from the other lands near half of `RRF_MAX` - real signal about one-sided evidence, and exactly the signal self-normalisation destroys.

**`k` is ours to set.** Qdrant's `rrf` object exposes `k` alongside `weights` (verified on 1.18). **Pin it in config and pass it on every query** - never rely on the server default, which is not published. Both terms of `RRF_MAX` are then our own constants, which is the only way a derived threshold stays trustworthy across a version bump.

~~One residual, confirmed empirically during the first tuning pass rather than assumed: the `+1` holds if Qdrant ranks from 1, and the denominator is `k` if it ranks from 0.~~ **Resolved 2026-07-28 - see the callout above. Qdrant 1.18 ranks from 0; the denominator is `k`.** The method was exactly as written: issue a query whose top hit ranks #1 in both branches and read the fused score, which yields `RRF_MAX` directly and settles the rank base in one shot.

**This also closes NotebookRAG's fail-open hole.** There, the gate ran *only* when rerank succeeded, so a dead reranker meant no gate at all. Here every path has a defined scale and a defined threshold, so the gate always runs - `failed` degrades the score source, never the check.

**No LLM call.** The signal already exists; spending five serial grading calls to recompute it is the mistake most CRAG implementations make.

Branches, where `FLOOR` is whichever of the two applies: `relevance ≥ FLOOR` -> **pass** · `< FLOOR and attempt == 0` -> **retry** · `< FLOOR and attempt == 1` -> **abstain**.

**`generate` · G3** - reads `candidates`, `effective_query`, conversation memory. Writes `answer`.
Assembles parent windows into **delimited DATA blocks** with explicit framing that content inside is data and never instruction. **The delimiter sequence is escaped inside chunk text** so content cannot break out of its own wrapper.
Streams tokens immediately.
Failure -> 503; a partial stream is closed with an explicit error frame, never truncated silently.

**`verify` · G4** - **async, off the request path.**
Claim-level, against the **union** of a claim's cited chunks - a sentence citing `[1][2]` draws on both, so judging markers separately fails both.
Fixes carried from [[NotebookRAG Reference Project]]: send the **full parent text**, not a 400-char prefix; count only **factual** claims in the coverage denominator, not discourse ("Here's a summary:").
Normalise marker variants - `【1】` `［1］` `〔1〕` - before extraction.
**Fails to `verified: null`, never `false`** (I2). Results patch citation chips in after the answer has streamed.

**`refuse` / `history` / `abstain`** - terminal nodes. `abstain` returns a structured refusal naming what was searched and why nothing qualified. Never a guess.

---

## 5 · Dependency, timeout, fallback

| Dependency | Timeout | On failure | Direction |
|---|---|---|---|
| Qdrant search | 3 s | **503** | cannot degrade |
| Cohere rerank | 2 s | fused order · **402 trips the breaker** | fail open |
| LLM · route | 2 s | assume `retrieve` | fail open |
| LLM · rewrite | 3 s | raw query | fail open |
| LLM · generate | 30 s (stream) | **503** | cannot degrade |
| LLM · verify | 10 s | `verified: null` | **fail unknown** |
| Postgres | 2 s | **one reconnect, then 503** | cannot degrade |

**Principle:** fail *toward answering*, with a degradation marker - except where correctness is the point (verification), where the system fails toward *unknown*.

**Postgres gets one reconnect before the 503.** Render's free tier ships no managed connection pooling and "may restart the database without notice" ([[Confirmed Infrastructure Constraints]]), so a dropped connection is an expected event rather than an outage. The app owns its pool and retries once on a connection-level error - but only once, and only on connection errors, never on a query that failed on its merits.

---

## 6 · Error taxonomy

Single envelope on every error path:

```
{ "error": { "code": "...", "message": "...", "detail": {...}, "request_id": "..." } }
```

| Status | Code | When |
|---|---|---|
| 400 | `invalid_request` | Malformed body / params |
| 401 | `unauthenticated` | Missing or invalid Clerk JWT |
| 403 | `forbidden` | Document not owned by `user_id` |
| 404 | `not_found` | Unknown document or conversation |
| 409 | *(not an error)* | Duplicate upload -> **200** with the existing document |
| 413 | `file_too_large` | Over the ingest ceiling |
| 415 | `unsupported_media_type` | Not pdf/txt/md |
| 422 | `document_not_ready` | Queried before `status == ready` |
| 429 | `rate_limited` | Own limiter or upstream passthrough; sets `Retry-After` |
| 503 | `dependency_unavailable` | Qdrant / Postgres / LLM down; names the dependency |

**No 500 ever reaches the client with a stack trace.** Unhandled exceptions map to 503 with a `request_id` that correlates to the server log.

---

## 7 · Persistence contract

Written per assistant turn:

- `messages` - role, content, `degradations[]`, latency breakdown per node
- `message_citations` - one row per citation: `chunk_id`, `marker`, `rank`, `fused_score`, `rerank_score`, `verified`

That second table is what makes citations verifiable *and* gives the retrieval trace for free - it is the evaluation dataset and the debugging surface, not just a UI join.

`rolling_summary` and `entity_ledger` are updated after each turn, not during.

---

## 8 · Streaming contract (SSE)

The frontend↔backend interface. Defined here because building either side against an assumed shape is how you lose a day to a refactor.

### Transport

Standard SSE. Every frame:

```
event: <type>
data:  {"seq": <int>, "ts": "<iso8601>", ...payload}
```

- `seq` is monotonic per stream - client-side ordering and dedup.
- **Heartbeat every 15 s** as an SSE comment (`: keepalive`). Idle SSE connections die silently through proxies; without this the client cannot distinguish "still thinking" from "connection dropped."
- **No resumability.** `Last-Event-ID` is not honoured. On disconnect the client does **not** resume the turn - it fetches `GET /messages/{id}` for final state. Simpler and correct; a half-resumed token stream is worse than a refetch.

### Ingest stream - `GET /documents/{id}/events`

| Event | Payload | Notes |
|---|---|---|
| `document.status` | `{document_id, status, progress?}` | `status` ∈ `queued\|parsing\|chunking\|embedding`. `progress` = `{done, total, unit}` where known |
| `document.complete` | `{document_id, chunk_count, extraction}` | Terminal. `extraction` = `{pages_total, pages_escalated, tables_recovered, figures_described, confidence}` - drives the quality indicator in the document manager |
| `document.error` | `{document_id, code, message}` | Terminal. `code` from §6 |

Exactly one terminal event per stream.

### Query stream - `POST /chat`

| Event | Payload | Notes |
|---|---|---|
| `turn.start` | `{turn_id, message_id, conversation_id}` | **Always first.** Lets the client key optimistic UI to a server id. `conversation_id` added 2026-07-28 - without it a client that starts a fresh conversation never learns the id the server minted, so every turn opens a new conversation and multi-turn memory is unreachable from the UI even though the backend implements it correctly |
| `pipeline.stage` | `{node, state, attempt, detail?}` | Generic on purpose - adding a node must not break the contract. `node` ∈ `route\|rewrite\|retrieve\|rerank\|grade\|generate`; `state` ∈ `started\|done` |
| `retrieval.result` | `{candidate_count, documents:[{doc_id, filename, hits}], attempt}` | Drives *"searching 12 sources -> found 5"*. The one stage event with a bespoke shape, because the UI renders it directly |
| `answer.delta` | `{text}` | Token chunk. Only between `pipeline.stage{generate,started}` and a terminal event |
| `answer.complete` | `{message_id, citations:[Citation]}` | Terminal-for-content. Citations carry `verified: null` at this point |
| `abstain` | `{message_id, reason, searched:{doc_count, top_score}}` | Terminal. Structured refusal naming what was searched and why nothing qualified |
| `verification.complete` | `{message_id, citations:[{marker, verified}], coverage}` | **After** `answer.complete`. Patches citation chips in place. **May never arrive** |
| `degradation` | `Degradation` (§2) | Fires whenever a fallback engages. Invariant I1 made observable |
| `error` | `{code, message, request_id}` | Terminal. A stream that fails **must** emit this - never just close |

### `pipeline.stage.detail` per node

```
route      {route: retrieve|history|refuse}
rewrite    {rewritten: bool}                    # false ⇒ raw query used
rerank     {status: applied|skipped_decisive|cached|failed, margin?}
grade      {relevance: float, decision: pass|retry|abstain}
```

`rewrite.rewritten:false` and `rerank.status != applied` are *expected* states, not failures - the accompanying `degradation` event distinguishes "deliberately skipped" from "fell back."

### Ordering guarantees

1. `turn.start` is always the first event.
2. **Exactly one** of `answer.complete` · `abstain` · `error` per stream, and the stream closes after it - *except* that `verification.complete` may follow `answer.complete`.
3. `answer.delta` appears only after `pipeline.stage{generate,started}`.
4. **`pipeline.stage` events repeat on retry.** The client must key on `(node, attempt)`, not `node` - `attempt` is 0 or 1 (invariant I6). A UI that assumes each node fires once will render the retry as a duplicate.
5. `degradation` may fire at any point, any number of times.
6. **The client must never block on `verification.complete`.** The answer is complete and usable without it; verification is an enhancement that arrives late or not at all. Citation chips render in an unverified state and upgrade in place.

### Why verification stays on the same stream

It could be a poll or a second channel. Same stream is better: the client already holds an open `EventSource`, the answer streams without waiting, and the connection simply stays open a few seconds longer. The tradeoff - a client that disconnects early misses the patch - is covered by persisting verification and exposing `GET /messages/{id}`.

---

## 9 · LLM adapter interface

[[NotebookRAG Reference Project]]'s provider-agnostic client is carried over - but it is **text-only and synchronous**, and two stages of this design quietly assume it is neither. Specified here so that is discovered now rather than mid-build.

```
Message      { role, content: str | ContentPart[] }
ContentPart  = TextPart{text} | ImagePart{mime, data_b64}

complete(messages, *, temperature, max_tokens, timeout) -> str
complete_json(messages, ...)                            -> dict            # tolerant parse
stream(messages, ...)                                   -> Iterator[str]
```

- **`ImagePart` is what makes Tier-2 VLM escalation free.** [[Document Parsing And Complex PDFs]] justifies page escalation on "no new dependency, no GPU, no RAM - it goes through the existing LLM adapter." That holds only if the message type can carry an image; the reference client's `list[dict[str, str]]` signature cannot express one.
- **`stream()` is required by §8.** `answer.delta` has no source otherwise, and the reference client has no streaming path at all.

**Cache policy: non-streaming calls only.** The prompt-hash cache is what makes eval re-runs nearly free, and it composes badly with token streaming. The split falls out naturally - every cacheable call is a deterministic temp-0 one:

| Cached | Not cached |
|---|---|
| `route` · `rewrite` · `verify` · VLM page parse · eval judges | `generate` (streams) |

Per-role model routing stays a config string, never code ([[Confirmed Infrastructure Constraints]]). The reference client's per-provider pacing limiter carries over unchanged and is reused directly for Cohere's 10 rpm ceiling (§4 `rerank`).

[[KnowledgeHub Index]] · [[Confirmed Infrastructure Constraints]] · [[RAG Guardrails Design]] · [[Agentic RAG Decision]] · [[Multi Turn Memory Architecture]]
