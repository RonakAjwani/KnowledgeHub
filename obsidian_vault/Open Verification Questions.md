# Open Verification Questions

Running log of facts that need external verification before being designed against. Per the working principle in [[KnowledgeHub Index]]: flag, don't guess.

## Round 1 - resolved 2026-07-27

All ten questions answered by Ronak. Results recorded in **[[Confirmed Infrastructure Constraints]]**.

Three answers changed the design:

1. **Render is 512 MB / 0.1 vCPU** -> overruled the `bge-base-en-v1.5` recommendation; the project uses `bge-small-en-v1.5`. Also rules out any local cross-encoder.
2. **Qdrant 1.14+ supports server-side weighted RRF** -> client-side fusion (as in [[NotebookRAG Reference Project]]) is obsolete; use a single server-side call.
3. **Cohere trial is 1,000 calls/month** -> forces conditional reranking + caching rather than reranking on every query.

Two answers corrected earlier plans: **Railway is not viable** (one-time credit, not an ongoing tier), and **RAGAS is not a CI gate** - DeepEval fills that role.

## Round 2 - resolved 2026-07-27

All three answered by Ronak. Results recorded in [[Confirmed Infrastructure Constraints]]; design consequences folded into [[Retrieval Pipeline Contract]] and [[Document Parsing And Complex PDFs]].

**1 · Qdrant RRF `k` - settable, so the question dissolves.** Current version is **1.18**. The `rrf` object exposes **both** `k` (integer, nullable - "K parameter for reciprocal rank fusion") and `weights`. The API reference does not publish a default, and it no longer matters: **we pin `k` explicitly in config and never rely on the server default.** `RRF_MAX = (w_dense + w_sparse) / (k + 1)` is then determined entirely by our own constants. Depending on an unstated default for a value a threshold is derived from would have been the actual mistake.

> ⚠️ **One residual, downgraded from blocking to a five-minute check.** The `+1` assumes Qdrant ranks from 1; if it ranks from 0 the denominator is `k`. Rather than guess, the first tuning pass confirms it empirically: issue a query whose top hit is known to rank #1 in *both* branches and read the fused score directly - that yields `RRF_MAX` and settles the rank base in one shot. G2 is no longer blocked on anything external.

**2 · Gemini multimodal - no image cap that binds; TPM is the real ceiling.** Up to 3,600 images per request (never approached here). Inline base64 is fine for our use; the request payload limit rose 20 MB -> 100 MB in January 2026, and local PDFs cap at 50 MB via standard file input. Images are scaled and padded to at most 3072×3072, aspect preserved. Cost: 258 tokens flat when both dimensions are ≤ 384 px, tiled above that with each tile adding more.

The free tier imposes **no separate image limit** - which is exactly why this needs care. Page images consume the existing TPM budget far faster than text: one tiled page can run several hundred to a couple of thousand tokens before the prompt is even read, against a ~250K-1M TPM ceiling. Three consequences, all in [[Document Parsing And Complex PDFs]]: the escalation limiter must be **token**-denominated rather than RPM-denominated, render DPI becomes a real tuning constant, and escalated pages per document need a hard cap with a visible degradation when hit.

**3 · LlamaParse - a credit is not a page.** A *page* costs N credits, and N varies by tier: Fast 1 · Cost-effective 3 · Agentic 10 · Agentic Plus 45, plus 3/page for layout extraction. List price is $1.25 per 1,000 credits.

Two things matter more than the rates:

- **Fast (1 credit) outputs spatial text only, no markdown** - so the cheapest tier does *not* solve the table problem that motivates Tier 3 at all. Cost-effective at 3 credits/page is the realistic floor for structured table extraction.
- **`target_pages` bills only the pages you ask for**, which composes exactly with the tiered-parsing design. Tier 3 sends only the pages the local heuristic already flagged, never whole documents. A 50-page paper with 6 complex pages costs 18 credits at Cost-effective, 60 at Agentic - not 500.

⚠️ **Still unconfirmed: the free tier itself.** The pricing page documents paid rates only and says nothing about the quoted 10,000 free credits/month. Low-stakes now that the cash figure is known - 10,000 credits is $12.50 - so Tier 3 is affordable whether or not the free allowance exists. Worth a glance at the plan-comparison page if Tier 3 is actually built.

## Round 3 - resolved 2026-07-27

Cohere and Render docs supplied by Ronak. Both answers were larger than the questions.

**4 · Cohere at exhaustion - two codes, not one.** **429** is the per-minute rate limit (transient, and the error text reveals a *token* limit too: 100K tokens/min on trial keys). **402** is quota/billing exhaustion (terminal). No `Retry-After` on either. Because they are distinguishable, the fallback must distinguish them: 429 backs off, 402 **trips a circuit breaker** that disables the reranker for the rest of the deployment rather than spending 2 s per query rediscovering it. In [[Retrieval Pipeline Contract]] §4 `rerank`.

**5 · Render free tier - three findings, two of which change the plan.**

- **Postgres expiry is a deletion.** 30 days from creation, then a 14-day grace period to upgrade, then the database *and all its data* are destroyed. Not renewable. One active free DB per workspace, no backups, no managed pooling, restarts without notice.
- **750 instance hours per workspace per calendar month**, and overrun **suspends every free web service until month end**. A month is 720-744 h, so exactly one always-on service fits - with no room for a second. This collides directly with the sub-15-minute uptime pinger recorded in [[Confirmed Infrastructure Constraints]] as the cold-start mitigation: run continuously and it consumes ~730 of the 750 hours by itself. Pinger becomes scoped to review windows; the ingest worker becomes in-process rather than a second service.
- **The filesystem is ephemeral** - local writes are lost on every spin-down, i.e. after 15 idle minutes. `Document.blob_ref` cannot be a path. Resolved to Postgres `bytea` in [[Retrieval Pipeline Contract]] §2.

## Round 4 - open

Nothing external is blocking. Remaining unknowns all need the running system:

- **Qdrant's RRF rank base** - 0 or 1. Settles `RRF_MAX`'s denominator; one query during the first tuning pass (see Round 2 · 1)
- Real measured RSS of FastAPI + fastembed + `bge-small` under load on Render, to confirm actual headroom
- Qdrant free-tier behaviour when the 1 GB RAM limit is approached (eviction? errors? degraded search?)
- **Actual instance-hour burn** once deployed, against the 750 h/month ceiling - the one Render limit that suspends the whole deployment if misjudged. Watch it from the first week rather than discovering it in week four

[[KnowledgeHub Index]] · [[Confirmed Infrastructure Constraints]]
