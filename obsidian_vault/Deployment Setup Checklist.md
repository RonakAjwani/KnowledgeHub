# Deployment Setup Checklist

Written 2026-07-30 for a parallel session doing the account/key wiring while the
eval runs elsewhere. **No backend code changes are required for any of this** -
every knob already exists in `backend/app/config.py`. This is provisioning and
environment variables only.

Each claim below was verified against the code, not remembered. File and line
references are given so you can re-check rather than trust.

## Order matters

Create **Render Postgres last**. Render deletes a free Postgres 44 days after
*creation*, not after last use, so every day it exists before submission is a
day off the reviewer's window. Everything else can be created now.

## 1 · Qdrant Cloud

Free tier, 1 GB. Take the cluster URL and an API key.

```
QDRANT_URL=https://<cluster>.<region>.aws.cloud.qdrant.io:6333
QDRANT_API_KEY=<key>
QDRANT_COLLECTION=chunks
```

Wired at `app/retrieval/qdrant_store.py:74` -
`AsyncQdrantClient(url=..., api_key=self.settings.qdrant_api_key or None)`.
The empty-string default becomes `None`, which is what makes the local
no-auth container work unchanged.

> **Untested path.** Every measurement in this repo ran against the local
> container, which uses no API key and plain HTTP. The authenticated HTTPS path
> has never been exercised. First thing to run after setting these:
>
> ```bash
> cd backend
> PYTHONPATH=. poetry run python scripts/probe_rrf_rank_base.py
> ```
>
> That probe re-measures `rank_base`, which `RRF_MAX` and therefore every
> relevance threshold derive from (I7). It is the cheapest possible proof that
> the cloud cluster behaves like the local one. **Re-run it after any Qdrant
> version bump** - Cloud may not be on the same version as the local image.

Then re-ingest, because a new cluster is empty:

```bash
PYTHONPATH=. poetry run python scripts/ingest_corpus.py --reset
```

## 2 · Clerk

Create an application; take both keys.

**Backend** - `auth.py:62` raises at startup if the secret is missing, so a
misconfiguration fails loudly on boot rather than on the first request:

```
AUTH_MODE=clerk
CLERK_SECRET_KEY=sk_...
CLERK_AUTHORIZED_PARTIES=https://<deployed-frontend-origin>
```

`CLERK_AUTHORIZED_PARTIES` is comma-separated and is the audience check -
`auth.py:70` splits it and passes it to `authenticate_request`. Leaving it empty
passes `None`, which disables that check. Set it.

**Frontend** (`frontend/.env.local`):

```
NEXT_PUBLIC_API_URL=https://<deployed-backend-origin>
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_...
CLERK_SECRET_KEY=sk_...
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
```

The two URL variables are not optional decoration: without them a signed-out
redirect lands on Clerk's hosted `accounts.clerk.dev` page instead of the app's
own styled forms at `app/sign-in/[[...sign-in]]`.

> **This is the one flow never exercised against real credentials.** The README
> already states it as a known limitation. `AUTH_MODE=dev` is the evaluated
> path; the Clerk path was built and tested only for correct *fallback* with no
> key configured. Budget time to click through sign-up -> sign-in -> a chat turn.

## 3 · CORS - the most likely first failure

```
CORS_ORIGINS=https://<deployed-frontend-origin>
```

Comma-separated, parsed at `config.py:439`. It defaults to
`http://localhost:3000`, so a deployed frontend calling a deployed backend is
blocked until this is set. The symptom is not an error page - it is every
request failing in the browser console while `curl` against the same backend
works perfectly, which sends people to debug the wrong side.

## 4 · Render

Both Dockerfiles already exist (`backend/Dockerfile`, `frontend/Dockerfile`) and
`backend/docker-entrypoint.sh` runs `alembic upgrade head` on start, so there is
**no separate migration step**.

```
DATABASE_URL=postgresql+asyncpg://...
```

Note the `+asyncpg` driver prefix - Render hands you a `postgres://` URL and it
must be rewritten, or SQLAlchemy picks the sync driver and every request fails.

Binding constraints, already designed against (see
[[Confirmed Infrastructure Constraints]]): 512 MB RAM / 0.1 vCPU, and 750 free
instance-hours per workspace per month with overrun suspending *every* free
service until month end. That is why there is no separate ingest worker and no
uptime pinger. Do not add either.

## 5 · LLM provider at submission

```
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

`MODELS_BY_PROVIDER` in `config.py:71` already carries the Anthropic row, so no
code change is needed and the per-role models resolve automatically.

> **This changes ingest behaviour, and it has never run.** Groq exposes no
> vision model, so `vlm` is `None` there and Tier-2 page escalation has been
> unavailable for every measurement this project has ever taken - visible as the
> `degraded [unavailable] 1 page(s) needed a vision model` line in every ingest.
> The Anthropic row sets `vlm`, so escalation **will** fire for the first time.
>
> Consequences to plan for, in order of likelihood:
> - Ingest gets slower and costs real tokens per escalated page.
> - Parsed text changes, so **chunk ids change** (they are content-derived) and a
>   re-ingest with `--reset` is mandatory.
> - Accuracy numbers measured on Groq no longer describe the deployed system.
>
> If time is short on submission day, the safe move is to switch the provider
> for *generation* and accept that Tier-2 is untested, rather than discovering a
> new parsing path an hour before the deadline. Setting
> `MAX_ESCALATED_PAGES=0` keeps the VLM path off while still using Anthropic for
> generation.

## Verification, in order

Each step's failure tells you something different, so run them in sequence
rather than jumping to the end.

```bash
cd backend
PYTHONPATH=. poetry run python scripts/probe_rrf_rank_base.py   # Qdrant reachable + RRF sane
PYTHONPATH=. poetry run python scripts/ingest_corpus.py --reset # corpus into the new cluster
PYTHONPATH=. poetry run python scripts/probe_golden_set.py      # every expected fact survived parsing
PYTHONPATH=. poetry run python scripts/verify_api.py            # full acceptance test, 32 checks
```

`verify_api.py` is the one that proves the product works end to end: upload ->
ingest -> hybrid retrieval -> a citation resolving to literal characters in
`normalized_text` -> a pronoun follow-up resolving against memory -> persistence ->
the error taxonomy.

## Already verified, do not re-litigate

A read-only integration pass on 2026-07-30 found **no frontend/backend
mismatches**: all 12 endpoints `frontend/lib/api.ts` calls exist, all 12 SSE
event names match, `Workspace` / `DocumentSummary` / `Citation` field shapes
match, the error envelope matches `ApiErrorBody`, CORS returns the right header
locally, and `sse.ts` correctly uses `fetch` + `ReadableStream` rather than
`EventSource`. Nothing in the uncommitted frontend work needs a backend change.

`GET/PUT /preferences` currently has no caller - that is expected. The settings
UI depends on Clerk, so it gains its consumer when step 2 lands.

[[KnowledgeHub Index]] · [[Confirmed Infrastructure Constraints]] · [[Session Handoff 2026-07-29]]
