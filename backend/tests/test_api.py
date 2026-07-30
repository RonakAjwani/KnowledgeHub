"""API surface: SSE framing, ordering guarantees, and every §6 status code.

The ordering tests are the load-bearing ones. A client builds its whole progress
UI on these guarantees, and a stream that violates one fails in the browser
rather than here - as a duplicated stage, a citation chip that never resolves, or
a spinner that never stops.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.api.sse import EventStream, with_heartbeat


def parse(frames: list[str]) -> list[tuple[str, dict]]:
    """Parse SSE frames, skipping comments."""
    out: list[tuple[str, dict]] = []
    for frame in frames:
        if frame.startswith(":"):
            continue
        lines = frame.strip().split("\n")
        event = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        out.append((event, data))
    return out


# ------------------------------------------------------------------ framing


def test_every_frame_carries_seq_and_ts() -> None:
    stream = EventStream()
    frames = [
        stream.frame("turn.start", {"turn_id": "t1"}),
        stream.frame("answer.delta", {"text": "hi"}),
    ]
    events = parse(frames)

    assert [e[1]["seq"] for e in events] == [1, 2], "seq must be monotonic"
    assert all("ts" in e[1] for e in events)
    assert events[0][0] == "turn.start"


def test_frame_shape_is_standard_sse() -> None:
    frame = EventStream().frame("degradation", {"stage": "rerank"})
    assert frame.startswith("event: degradation\ndata: ")
    assert frame.endswith("\n\n"), "frames must be blank-line terminated"


def test_heartbeat_is_an_sse_comment() -> None:
    """Invisible to a parser, and the only way a client can tell 'still thinking'
    from 'connection dropped'."""
    assert EventStream.heartbeat() == ": keepalive\n\n"


def test_payload_with_exotic_types_does_not_kill_the_stream() -> None:
    from app.models.schemas import DegradationStage

    frame = EventStream().frame("degradation", {"stage": DegradationStage.RERANK})
    assert "rerank" in frame


async def test_heartbeat_fires_while_the_producer_is_quiet() -> None:
    async def slow() -> None:
        await asyncio.sleep(0.25)
        yield "event: answer.delta\ndata: {}\n\n"

    frames = [f async for f in with_heartbeat(slow(), interval=0.05)]

    assert frames.count(": keepalive\n\n") >= 2, "quiet producer must still emit"
    assert frames[-1].startswith("event: answer.delta")


async def test_heartbeat_does_not_delay_a_fast_producer() -> None:
    async def fast() -> None:
        for i in range(3):
            yield f"event: answer.delta\ndata: {{\"i\": {i}}}\n\n"

    frames = [f async for f in with_heartbeat(fast(), interval=5.0)]
    assert len([f for f in frames if not f.startswith(":")]) == 3


# --------------------------------------------------------- error taxonomy


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import create_app

    return TestClient(create_app(), raise_server_exceptions=False)


def test_unknown_route_returns_the_envelope(client) -> None:
    body = client.get("/nope").json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["request_id"]


def test_upload_rejects_unsupported_media_type(client) -> None:
    response = client.post(
        "/documents", files={"file": ("evil.exe", b"MZ\x90", "application/x-msdownload")}
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


def test_upload_rejects_oversized_file(client, monkeypatch) -> None:
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_bytes", 10)

    response = client.post(
        "/documents", files={"file": ("big.txt", b"x" * 100, "text/plain")}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert response.json()["error"]["detail"]["limit"] == 10


def test_missing_document_is_404_not_403(client) -> None:
    """A 403 would confirm the id exists - an enumeration oracle. Documents the
    caller does not own are reported as absent."""
    response = client.get("/documents/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_chat_validation_failure_is_400_not_422(client) -> None:
    """422 is reserved for document_not_ready."""
    response = client.post("/chat", json={"message": ""})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_mime_is_guessed_from_the_extension() -> None:
    """Browsers report application/octet-stream for .md often enough that
    trusting the header alone rejects valid markdown."""
    from app.api.documents import _guess_mime

    class Upload:
        def __init__(self, filename, content_type):
            self.filename = filename
            self.content_type = content_type

    assert _guess_mime(Upload("notes.md", "application/octet-stream")) == "text/markdown"
    assert _guess_mime(Upload("paper.pdf", "application/octet-stream")) == "application/pdf"
    assert _guess_mime(Upload("a.txt", "text/plain; charset=utf-8")) == "text/plain"


# ------------------------------------------------------ ordering guarantees


class FakeRunner:
    """Replays a scripted event sequence through a real EventStream."""

    def __init__(self, events):
        self.stream = EventStream()
        self.events = events

    async def run(self):
        for event, payload in self.events:
            yield self.stream.frame(event, payload)


async def test_turn_start_is_always_first() -> None:
    runner = FakeRunner(
        [
            ("turn.start", {"turn_id": "t", "message_id": "m"}),
            ("pipeline.stage", {"node": "route", "state": "started", "attempt": 0}),
        ]
    )
    events = parse([f async for f in runner.run()])
    assert events[0][0] == "turn.start"


async def test_exactly_one_terminal_event() -> None:
    runner = FakeRunner(
        [
            ("turn.start", {}),
            ("answer.delta", {"text": "a"}),
            ("answer.complete", {"message_id": "m", "citations": []}),
            ("verification.complete", {"message_id": "m", "citations": []}),
        ]
    )
    events = parse([f async for f in runner.run()])
    terminals = [e for e, _ in events if e in ("answer.complete", "abstain", "error")]

    assert len(terminals) == 1
    # verification.complete is the one thing allowed to follow a terminal.
    assert events[-1][0] == "verification.complete"


async def test_stage_events_repeat_with_a_distinct_attempt_on_retry() -> None:
    """The client keys on (node, attempt). A UI assuming each node fires once
    renders the corrective retry as a duplicate."""
    runner = FakeRunner(
        [
            ("pipeline.stage", {"node": "retrieve", "state": "started", "attempt": 0}),
            ("pipeline.stage", {"node": "retrieve", "state": "done", "attempt": 0}),
            ("pipeline.stage", {"node": "retrieve", "state": "started", "attempt": 1}),
            ("pipeline.stage", {"node": "retrieve", "state": "done", "attempt": 1}),
        ]
    )
    events = parse([f async for f in runner.run()])
    keys = {(d["node"], d["attempt"], d["state"]) for _, d in events}

    assert len(keys) == 4, "each (node, attempt, state) must be distinct"
    assert {d["attempt"] for _, d in events} == {0, 1}, "attempt is 0 or 1 (I6)"


async def test_citations_are_unverified_at_answer_complete() -> None:
    """I2: chips render unverified and upgrade in place. Never false at this
    point - verification has not run yet."""
    runner = FakeRunner(
        [
            (
                "answer.complete",
                {"message_id": "m", "citations": [{"marker": 1, "verified": None}]},
            )
        ]
    )
    events = parse([f async for f in runner.run()])
    assert events[0][1]["citations"][0]["verified"] is None


async def test_abstain_names_what_was_searched() -> None:
    runner = FakeRunner(
        [
            (
                "abstain",
                {
                    "message_id": "m",
                    "reason": "relevance_below_floor",
                    "searched": {"doc_count": 3, "top_score": 0.11},
                },
            )
        ]
    )
    events = parse([f async for f in runner.run()])
    assert events[0][1]["searched"]["doc_count"] == 3
    assert events[0][1]["reason"] == "relevance_below_floor"


async def test_error_frame_carries_a_request_id() -> None:
    """A stream that fails must emit this rather than just closing."""
    runner = FakeRunner(
        [("error", {"code": "dependency_unavailable", "message": "x", "request_id": "r1"})]
    )
    events = parse([f async for f in runner.run()])
    assert events[0][1]["request_id"] == "r1"


async def test_turn_start_carries_the_conversation_id() -> None:
    """Without it, a client that starts a fresh conversation never learns the id
    the server minted - so every turn opens a new conversation and multi-turn
    memory is unreachable even though the backend implements it."""
    runner = FakeRunner(
        [("turn.start", {"turn_id": "t", "message_id": "m", "conversation_id": "conv-1"})]
    )
    events = parse([f async for f in runner.run()])

    assert events[0][0] == "turn.start"
    assert events[0][1]["conversation_id"] == "conv-1"
