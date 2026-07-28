"""Drive the whole product through its own REST API and check what came back.

This is the acceptance check for the assignment's requirements, run against a
live server rather than against mocks: upload several documents, watch them
become ready, ask questions, follow up with a pronoun, resolve a citation back
to the exact characters it points at, and confirm the conversation survived in
Postgres. Unit tests cover the pieces; this covers the product.

    docker compose up -d postgres qdrant
    AUTH_MODE=dev poetry run uvicorn app.main:app --port 8000
    PYTHONPATH=. poetry run python scripts/verify_api.py

Exits non-zero on the first failed check, so it can gate CI later.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import pathlib
import sys

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:8000"
CORPUS = pathlib.Path(__file__).resolve().parents[2] / "document corpus"

# Small and varied: one markdown, one short PDF. The point is the round trip,
# not throughput — a 43-page 10-Q would spend minutes proving the same thing.
#
# The PDF has to be the one the questions below are about. An earlier version
# uploaded a different paper and still "passed" the answer checks, because the
# model wrote a confident paragraph out of an unrelated table of contents — the
# exact failure the citation chain exists to make visible. Questions and corpus
# are chosen together, or the harness measures nothing.
UPLOADS = ["langchain.md", "2607.24512v1.pdf"]

# Free-tier providers meter tokens per minute, and this script is the only place
# that asks several questions back to back. Long enough for the smaller model's
# bucket to refill too, since a rate-limited turn falls back onto it and the next
# turn would otherwise find it just as empty.
PACE_S = float(os.environ.get("VERIFY_PACE_S", "45"))

_passed = 0
_failed: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    global _passed
    if ok:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed.append(label)
        print(f"  FAIL  {label}  {detail}")
    return ok


async def sse(client: httpx.AsyncClient, path: str, payload: dict) -> list[tuple[str, str]]:
    """Read one SSE response to completion as (event, data) pairs.

    A fetch-style reader rather than EventSource, matching the frontend: the
    chat endpoint is a POST and has to carry an auth header, and EventSource can
    do neither.
    """
    events: list[tuple[str, str]] = []
    async with client.stream("POST", path, json=payload, timeout=120.0) as response:
        response.raise_for_status()
        name = ""
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                events.append((name, line[5:].strip()))
    return events


async def main() -> int:
    import json

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as client:
        print("\n[1] upload and manage documents")
        doc_ids: dict[str, str] = {}
        for name in UPLOADS:
            path = CORPUS / name
            if not path.exists():
                check(f"corpus has {name}", False, "missing")
                continue
            mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
            response = await client.post(
                "/documents", files={"file": (name, path.read_bytes(), mime)}
            )
            # 201 the first time, 200 once the same bytes are already stored —
            # this script is expected to be run repeatedly against a live server,
            # so treating the deduplicated reply as a failure would only ever be
            # testing whether the database happened to be empty.
            ok = check(
                f"upload {name}",
                response.status_code in (200, 201),
                response.text[:120],
            )
            if ok:
                doc_ids[name] = response.json()["id"]

        # Re-uploading the same bytes must not reprocess or duplicate.
        if UPLOADS[0] in doc_ids:
            first = CORPUS / UPLOADS[0]
            again = await client.post(
                "/documents",
                files={"file": (UPLOADS[0], first.read_bytes(), "text/markdown")},
            )
            check(
                "duplicate upload is deduplicated, not reprocessed",
                again.status_code == 200 and again.json()["id"] == doc_ids[UPLOADS[0]],
                f"{again.status_code}",
            )

        print("\n[2] ingest reaches ready")
        for name, doc_id in doc_ids.items():
            status = ""
            for _ in range(120):
                detail = (await client.get(f"/documents/{doc_id}")).json()
                status = detail.get("status", "")
                if status in ("ready", "failed"):
                    break
                await asyncio.sleep(2)
            check(
                f"{name} -> ready",
                status == "ready",
                f"status={status}",
            )

        listing = await client.get("/documents")
        check(
            "document list returns what was uploaded",
            listing.status_code == 200 and len(listing.json()) >= len(doc_ids),
            listing.text[:120],
        )

        print("\n[3] grounded answer with citations")
        events = await sse(
            client,
            "/chat",
            {"message": "What are the three tools exposed by the MathModDB MCP server?"},
        )
        names = [n for n, _ in events]
        # Surface the server's own explanation rather than leaving a bare "no
        # answer streamed" to be guessed at.
        for name, data in events:
            if name in ("error", "degradation"):
                print(f"        {name}: {data[:220]}")
        check("turn.start is first", names[:1] == ["turn.start"], str(names[:3]))
        check("answer streamed", "answer.delta" in names, str(set(names)))
        # §8: exactly one of answer.complete / abstain / error closes the stream.
        # verification.complete may follow it, or never arrive at all.
        terminals = sum(names.count(n) for n in ("answer.complete", "abstain", "error"))
        check("exactly one terminal frame", terminals == 1, str(names[-3:]))

        conversation_id = ""
        citations: list[dict] = []
        answer = ""
        for name, data in events:
            payload = json.loads(data)
            if name == "turn.start":
                conversation_id = payload.get("conversation_id", "")
            elif name == "answer.delta":
                answer += payload.get("text", "")
            elif name == "answer.complete":
                citations = payload.get("citations", [])
        check("conversation_id on turn.start", bool(conversation_id))
        check("answer is non-empty", len(answer.strip()) > 30, repr(answer[:80]))
        check("answer carries citations", bool(citations), "none returned")

        print("\n[4] a citation resolves to the right characters")
        if citations:
            citation = citations[0]
            doc = (await client.get(f"/documents/{citation['doc_id']}")).json()
            text = doc.get("normalized_text") or ""
            start, end = citation.get("char_start"), citation.get("char_end")
            span = text[start:end] if isinstance(start, int) else ""
            check(
                "citation offsets index into normalized_text",
                bool(span.strip()),
                f"[{start}:{end}] of {len(text)} chars",
            )
            check(
                "citation names the file it came from",
                bool(citation.get("filename")),
                str(citation)[:120],
            )
            check(
                "markers are 1-based and contiguous",
                [c["marker"] for c in citations] == list(range(1, len(citations) + 1)),
                str([c["marker"] for c in citations]),
            )
            print(f"        [{citation['marker']}] {citation.get('filename')}")
            print(f"        cited: {' '.join(span.split())[:100]!r}")

        print("\n[5] follow-up with a pronoun resolves against memory")
        await asyncio.sleep(PACE_S)
        follow = await sse(
            client,
            "/chat",
            {
                "message": "Which of them must be called before any SPARQL runs?",
                "conversation_id": conversation_id,
            },
        )
        follow_answer = ""
        for name, data in follow:
            payload = json.loads(data)
            if name == "answer.delta":
                follow_answer += payload.get("text", "")
            elif name in ("abstain", "error", "degradation"):
                print(f"        {name}: {data[:200]}")
            elif name == "pipeline.stage" and payload.get("state") == "done":
                print(f"        {payload.get('node')}: {payload.get('detail', {})}")
        check(
            "follow-up answered without repeating the subject",
            len(follow_answer.strip()) > 20,
            repr(follow_answer[:80]),
        )
        print(f"        answer: {' '.join(follow_answer.split())[:140]!r}")

        print("\n[6] conversation persisted in Postgres")
        conversations = await client.get("/conversations")
        check(
            "conversation appears in the list",
            conversations.status_code == 200
            and any(c["id"] == conversation_id for c in conversations.json()),
            conversations.text[:120],
        )
        history = await client.get(f"/conversations/{conversation_id}")
        messages = history.json().get("messages", []) if history.status_code == 200 else []
        check(
            "both turns stored with their replies",
            len(messages) >= 4,
            f"{len(messages)} messages",
        )

        print("\n[7] error handling")
        missing = await client.get("/documents/does-not-exist")
        check("unknown document -> 404", missing.status_code == 404, str(missing.status_code))
        body = missing.json()
        check(
            "error envelope has code and request_id",
            "code" in body.get("error", body) and "request_id" in str(body),
            missing.text[:160],
        )
        empty = await client.post("/chat", json={"message": ""})
        check(
            "invalid chat body -> 400, not FastAPI's 422",
            empty.status_code == 400,
            str(empty.status_code),
        )
        bad_type = await client.post(
            "/documents", files={"file": ("x.zip", b"PK\x03\x04", "application/zip")}
        )
        check(
            "unsupported media type is rejected",
            bad_type.status_code in (400, 415),
            str(bad_type.status_code),
        )

    print(f"\n{'=' * 60}\n{_passed} passed, {len(_failed)} failed")
    for name in _failed:
        print(f"  - {name}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
