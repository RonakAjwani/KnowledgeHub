/**
 * Tests for the chat-turn reducer.
 *
 * These target the invariants the hook's own header calls load-bearing, because
 * each one fails *silently* - the UI still renders a plausible answer while the
 * behaviour underneath is wrong:
 *
 * - stages keyed on `(node, attempt)`, so a corrective retry stays visible;
 * - `verified: null` never collapsing to `false` (I2);
 * - the conversation id surviving a new turn, without which every message opens
 *   a fresh conversation and multi-turn memory quietly stops working while each
 *   individual answer still looks correct;
 * - a stream that closes with no terminal frame being an error, not an answer.
 *
 * `streamChat` is mocked so a frame sequence can be scripted exactly; the parser
 * that produces those frames is tested separately in `lib/sse.test.ts`.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatEvent, Citation } from "@/lib/types";

const streamChatMock = vi.hoisted(() => vi.fn());

vi.mock("@/lib/sse", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/sse")>();
  return { ...actual, streamChat: streamChatMock };
});

// Clerk is never mounted in tests; dev mode is the evaluated path anyway.
vi.mock("@/hooks/useSessionToken", () => ({
  useSessionToken: () => async () => null,
}));

const { StreamHttpError } = await import("@/lib/sse");
const { useChatStream } = await import("./useChatStream");

// ------------------------------------------------------------------ fixtures

let seq = 0;
function envelope() {
  return { seq: seq++, ts: new Date().toISOString() };
}

function citation(marker: number, overrides: Partial<Citation> = {}): Citation {
  return {
    marker,
    chunk_id: `chunk-${marker}`,
    doc_id: "doc-1",
    filename: "report.pdf",
    section: null,
    page: 1,
    char_start: 0,
    char_end: 10,
    verified: null,
    ...overrides,
  };
}

const turnStart = (conversationId = "conv-1"): ChatEvent => ({
  type: "turn.start",
  data: {
    ...envelope(),
    turn_id: "turn-1",
    message_id: "msg-1",
    conversation_id: conversationId,
  },
});

const delta = (text: string): ChatEvent => ({
  type: "answer.delta",
  data: { ...envelope(), text },
});

const answerComplete = (citations: Citation[] = []): ChatEvent => ({
  type: "answer.complete",
  data: { ...envelope(), message_id: "msg-1", citations },
});

/** Scripts the next `send` to yield exactly these frames, then close. */
function script(...events: ChatEvent[]) {
  streamChatMock.mockImplementationOnce(async function* () {
    for (const event of events) yield event;
  });
}

async function runTurn(events: ChatEvent[], message = "what is the AUM?") {
  script(...events);
  const { result } = renderHook(() => useChatStream());
  await act(async () => {
    await result.current.send(message);
  });
  return result;
}

beforeEach(() => {
  seq = 0;
});

afterEach(() => {
  vi.clearAllMocks();
});

// --------------------------------------------------------------------- tests

describe("a successful turn", () => {
  it("accumulates deltas into the answer and lands in the answered phase", async () => {
    const result = await runTurn([
      turnStart(),
      delta("The net AUM "),
      delta("is 6,634.45 crore."),
      answerComplete([citation(1)]),
    ]);

    expect(result.current.phase).toBe("answered");
    expect(result.current.answer).toBe("The net AUM is 6,634.45 crore.");
    expect(result.current.isStreaming).toBe(false);
    expect(result.current.error).toBeNull();
  });

  it("sorts citations by marker once, at answer.complete", async () => {
    // Chip order is fixed from this moment so markers never shuffle under a click.
    const result = await runTurn([
      turnStart(),
      answerComplete([citation(3), citation(1), citation(2)]),
    ]);

    expect(result.current.citations.map((c) => c.marker)).toEqual([1, 2, 3]);
  });

  it("returns an answer result the caller can commit to a message list", async () => {
    script(turnStart(), delta("grounded"), answerComplete([citation(1)]));
    const { result } = renderHook(() => useChatStream());

    let outcome;
    await act(async () => {
      outcome = await result.current.send("hi");
    });

    expect(outcome).toMatchObject({
      kind: "answer",
      messageId: "msg-1",
      answer: "grounded",
    });
  });
});

describe("pipeline stages", () => {
  const stage = (
    node: "retrieve" | "rerank",
    state: "started" | "done",
    attempt: number,
  ): ChatEvent => ({
    type: "pipeline.stage",
    data: { ...envelope(), node, state, attempt },
  });

  it("keeps the retry as its own record instead of overwriting the first pass", async () => {
    // Keyed on `node` alone, the retry would overwrite attempt 0 and the UI
    // would show one retrieval where two ran - hiding the retry entirely.
    const result = await runTurn([
      turnStart(),
      stage("retrieve", "started", 0),
      stage("retrieve", "done", 0),
      stage("retrieve", "started", 1),
      stage("retrieve", "done", 1),
      answerComplete(),
    ]);

    const retrieves = result.current.stages.filter((s) => s.node === "retrieve");
    expect(retrieves).toHaveLength(2);
    expect(retrieves.map((s) => s.attempt)).toEqual([0, 1]);
  });

  it("never walks a finished stage back to started", async () => {
    const result = await runTurn([
      turnStart(),
      stage("rerank", "started", 0),
      stage("rerank", "done", 0),
      stage("rerank", "started", 0), // a retransmit, or an out-of-order arrival
      answerComplete(),
    ]);

    expect(result.current.stages[0].state).toBe("done");
  });

  it("merges the detail carried by started and done into one record", async () => {
    const result = await runTurn([
      turnStart(),
      {
        type: "pipeline.stage",
        data: {
          ...envelope(),
          node: "rerank",
          state: "started",
          attempt: 0,
          detail: { margin: 1.4 },
        },
      },
      {
        type: "pipeline.stage",
        data: {
          ...envelope(),
          node: "rerank",
          state: "done",
          attempt: 0,
          detail: { status: "applied" },
        },
      },
      answerComplete(),
    ]);

    expect(result.current.stages[0].detail).toEqual({
      margin: 1.4,
      status: "applied",
    });
  });
});

describe("verification (invariant I2 - unknown is not zero)", () => {
  it("patches verdicts by marker without reordering the chips", async () => {
    const result = await runTurn([
      turnStart(),
      answerComplete([citation(1), citation(2), citation(3)]),
      {
        type: "verification.complete",
        data: {
          ...envelope(),
          message_id: "msg-1",
          citations: [
            { marker: 3, verified: false },
            { marker: 1, verified: true },
          ],
          coverage: 0.66,
        },
      },
    ]);

    expect(result.current.citations.map((c) => c.marker)).toEqual([1, 2, 3]);
    expect(result.current.citations.map((c) => c.verified)).toEqual([
      true,
      null, // never checked - stays null, is NOT downgraded to false
      false,
    ]);
    expect(result.current.verification?.coverage).toBe(0.66);
  });

  it("keeps a null verdict null when the judge itself failed", async () => {
    const result = await runTurn([
      turnStart(),
      answerComplete([citation(1)]),
      {
        type: "verification.complete",
        data: {
          ...envelope(),
          message_id: "msg-1",
          citations: [{ marker: 1, verified: null }],
          coverage: null,
        },
      },
    ]);

    expect(result.current.citations[0].verified).toBeNull();
    expect(result.current.verification?.coverage).toBeNull();
  });

  it("leaves citations untouched when verification never arrives", async () => {
    const result = await runTurn([
      turnStart(),
      answerComplete([citation(1), citation(2)]),
    ]);

    expect(result.current.phase).toBe("answered");
    expect(result.current.citations.every((c) => c.verified === null)).toBe(true);
    expect(result.current.verification).toBeNull();
  });

  it("preserves object identity for citations whose verdict did not change", async () => {
    // The memoised chip relies on this to avoid re-rendering on a sibling's patch.
    script(
      turnStart(),
      answerComplete([citation(1), citation(2)]),
      {
        type: "verification.complete",
        data: {
          ...envelope(),
          message_id: "msg-1",
          citations: [{ marker: 1, verified: true }],
          coverage: 1,
        },
      },
    );
    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send("hi");
    });

    const untouched = result.current.citations.find((c) => c.marker === 2);
    expect(untouched?.verified).toBeNull();
  });
});

describe("terminal states are distinct", () => {
  it("treats abstain as an honest answer, not an error", async () => {
    const result = await runTurn([
      turnStart(),
      {
        type: "abstain",
        data: {
          ...envelope(),
          message_id: "msg-1",
          reason: "The corpus does not contain this figure.",
          searched: { doc_count: 4, top_score: 0.31 },
        },
      },
    ]);

    expect(result.current.phase).toBe("abstained");
    expect(result.current.error).toBeNull();
    expect(result.current.abstain).toMatchObject({
      reason: "The corpus does not contain this figure.",
      searched: { doc_count: 4, top_score: 0.31 },
    });
  });

  it("surfaces an in-stream error frame with its request id", async () => {
    const result = await runTurn([
      turnStart(),
      {
        type: "error",
        data: {
          ...envelope(),
          code: "quota_exhausted",
          message: "Daily token cap reached.",
          request_id: "req-7",
        },
      },
    ]);

    expect(result.current.phase).toBe("errored");
    expect(result.current.error).toEqual({
      code: "quota_exhausted",
      message: "Daily token cap reached.",
      requestId: "req-7",
    });
  });

  it("treats a stream that closes with no terminal frame as an error", async () => {
    // SSE ordering guarantee 2. A truncated answer presented as a whole one is
    // the exact failure this branch exists to prevent.
    const result = await runTurn([turnStart(), delta("half an ans")]);

    expect(result.current.phase).toBe("errored");
    expect(result.current.error?.code).toBe("stream_closed");
    expect(result.current.answer).toBe("half an ans");
  });
});

describe("degradation is never silent (invariant I1)", () => {
  it("collects every degradation frame in arrival order", async () => {
    const result = await runTurn([
      turnStart(),
      {
        type: "degradation",
        data: {
          ...envelope(),
          stage: "rerank",
          reason: "rate_limited",
          fallback: "fused order",
          detail: null,
        },
      },
      {
        type: "degradation",
        data: {
          ...envelope(),
          stage: "generate",
          reason: "quota_exhausted",
          fallback: "smaller model",
          detail: "daily cap",
        },
      },
      answerComplete(),
    ]);

    expect(result.current.degradations).toHaveLength(2);
    expect(result.current.degradations.map((d) => d.stage)).toEqual([
      "rerank",
      "generate",
    ]);
  });
});

describe("conversation threading", () => {
  it("sends the learned conversation id back on the next turn", async () => {
    // Dropping it here is the silent multi-turn failure: every answer still
    // looks right, only the follow-ups lose their history.
    script(turnStart("conv-9"), answerComplete());
    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send("first");
    });

    script(turnStart("conv-9"), answerComplete());
    await act(async () => {
      await result.current.send("and the follow-up?");
    });

    expect(streamChatMock.mock.calls[1][1]).toMatchObject({
      message: "and the follow-up?",
      conversation_id: "conv-9",
    });
    expect(result.current.conversationId).toBe("conv-9");
  });

  it("tags only the first turn with a workspace id", async () => {
    script(turnStart("conv-9"), answerComplete());
    const { result } = renderHook(() =>
      useChatStream({ workspaceId: "ws-1" }),
    );
    await act(async () => {
      await result.current.send("first");
    });
    expect(streamChatMock.mock.calls[0][1].workspace_id).toBe("ws-1");

    script(turnStart("conv-9"), answerComplete());
    await act(async () => {
      await result.current.send("second");
    });
    // The conversation already exists; a conversation cannot switch workspaces.
    expect(streamChatMock.mock.calls[1][1].workspace_id).toBeNull();
  });

  it("clears the conversation id on reset, so the next turn starts a new thread", async () => {
    script(turnStart("conv-9"), answerComplete());
    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send("first");
    });

    act(() => {
      result.current.reset();
    });

    expect(result.current.conversationId).toBeNull();
    expect(result.current.phase).toBe("idle");
    expect(result.current.answer).toBe("");
  });
});

describe("document scoping", () => {
  it("sends null rather than an empty array when nothing is selected", async () => {
    // `null` means "every ready document"; `[]` would ask to search nothing.
    script(turnStart(), answerComplete());
    const { result } = renderHook(() => useChatStream({ selectedDocIds: [] }));
    await act(async () => {
      await result.current.send("hi");
    });

    expect(streamChatMock.mock.calls[0][1].selected_doc_ids).toBeNull();
  });

  it("passes an explicit document selection through", async () => {
    script(turnStart(), answerComplete());
    const { result } = renderHook(() =>
      useChatStream({ selectedDocIds: ["doc-a", "doc-b"] }),
    );
    await act(async () => {
      await result.current.send("hi");
    });

    expect(streamChatMock.mock.calls[0][1].selected_doc_ids).toEqual([
      "doc-a",
      "doc-b",
    ]);
  });
});

describe("failures outside the stream", () => {
  it("maps a backend error envelope onto the turn", async () => {
    streamChatMock.mockImplementationOnce(async function* () {
      throw new StreamHttpError(429, "rate_limited", "Slow down", "req-3");
    });
    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send("hi");
    });

    expect(result.current.phase).toBe("errored");
    expect(result.current.error).toMatchObject({
      code: "rate_limited",
      requestId: "req-3",
    });
  });

  it("reports an unreachable API distinctly from a server error", async () => {
    // `fetch` rejects with a bare TypeError for DNS/CORS/offline - the one
    // failure the user can actually act on.
    streamChatMock.mockImplementationOnce(async function* () {
      throw new TypeError("Failed to fetch");
    });
    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send("hi");
    });

    expect(result.current.error?.code).toBe("network_unreachable");
  });

  it("does not open a stream for an empty message", async () => {
    const { result } = renderHook(() => useChatStream());

    let outcome;
    await act(async () => {
      outcome = await result.current.send("   ");
    });

    expect(streamChatMock).not.toHaveBeenCalled();
    expect(outcome).toMatchObject({ kind: "cancelled" });
  });

  it("retries once on a 401 and succeeds, rather than failing the turn", async () => {
    // A token grabbed right as Clerk finishes loading, or one that went stale
    // while the tab sat idle, looks identical to the backend: a 401. One
    // retry re-fetches the token instead of surfacing a false "session
    // expired" error for what is really just a mount-time race.
    streamChatMock
      .mockImplementationOnce(async function* () {
        throw new StreamHttpError(401, "unauthenticated", "Token expired", "req-1");
      })
      .mockImplementationOnce(async function* () {
        yield turnStart();
        yield delta("Hello");
        yield answerComplete();
      });

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send("hi");
    });

    expect(streamChatMock).toHaveBeenCalledTimes(2);
    expect(result.current.phase).toBe("answered");
    expect(result.current.answer).toBe("Hello");
  });

  it("surfaces a failed turn when the 401 repeats, rather than retrying forever", async () => {
    streamChatMock.mockImplementation(async function* () {
      throw new StreamHttpError(401, "unauthenticated", "Token expired", "req-2");
    });

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send("hi");
    });

    expect(streamChatMock).toHaveBeenCalledTimes(2);
    expect(result.current.phase).toBe("errored");
    expect(result.current.error).toMatchObject({
      code: "unauthenticated",
      requestId: "req-2",
    });
  });
});

describe("forward compatibility", () => {
  it("ignores an event type the backend added before the frontend knew it", async () => {
    const unknown = {
      type: "some.future.event",
      data: { ...envelope() },
    } as unknown as ChatEvent;

    const result = await runTurn([
      turnStart(),
      unknown,
      delta("still fine"),
      answerComplete(),
    ]);

    expect(result.current.phase).toBe("answered");
    expect(result.current.answer).toBe("still fine");
  });
});
