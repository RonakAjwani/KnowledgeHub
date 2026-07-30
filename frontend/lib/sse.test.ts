/**
 * Tests for the hand-rolled SSE reader.
 *
 * The parser is hand-rolled precisely because two behaviours matter and generic
 * libraries get them subtly wrong, so those two are tested directly rather than
 * assumed: a frame split across a chunk boundary must not be emitted early, and
 * a `:` comment must be treated as a keepalive rather than as data.
 *
 * The failure these guard against is the nastiest kind - it does not throw. An
 * answer silently loses its tail the first time a token straddles a TCP segment,
 * and the bug reproduces only under network timing nobody can reproduce locally.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { StreamHttpError, streamChat, streamIngest } from "./sse";

const encoder = new TextEncoder();

/** A response body that hands out exactly the chunks given, in order. */
function bodyOf(...chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(
          typeof chunk === "string" ? encoder.encode(chunk) : chunk,
        );
      }
      controller.close();
    },
  });
}

function sseResponse(...chunks: (string | Uint8Array)[]): Response {
  return new Response(bodyOf(...chunks), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function mockFetch(response: Response) {
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function collect<T>(stream: AsyncGenerator<T>): Promise<T[]> {
  const out: T[] = [];
  for await (const item of stream) out.push(item);
  return out;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("frame parsing", () => {
  it("parses a well-formed frame into its event name and decoded data", async () => {
    mockFetch(
      sseResponse(
        'event: turn.start\ndata: {"turn_id":"t1","message_id":"m1","conversation_id":"c1"}\n\n',
      ),
    );

    const events = await collect(streamChat("http://api", { message: "hi" }));

    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("turn.start");
    expect(events[0].data).toMatchObject({
      turn_id: "t1",
      conversation_id: "c1",
    });
  });

  it("does not emit a frame split across a chunk boundary until it completes", async () => {
    // The classic bug: emitting on chunk arrival truncates the answer here.
    mockFetch(
      sseResponse(
        'event: answer.delta\ndata: {"text":"hel',
        'lo world"}\n\n',
      ),
    );

    const events = await collect(streamChat("http://api", { message: "hi" }));

    expect(events).toHaveLength(1);
    expect(events[0].data).toMatchObject({ text: "hello world" });
  });

  it("decodes a multi-byte character split across a chunk boundary", async () => {
    // A chunk boundary can fall mid-UTF-8-character. Decoding each chunk in
    // isolation yields a replacement character; the streaming decoder does not.
    const full = encoder.encode(
      'event: answer.delta\ndata: {"text":"café au lait"}\n\n',
    );
    const midCharacter = full.indexOf(0xc3) + 1;
    expect(midCharacter).toBeGreaterThan(0);

    mockFetch(
      sseResponse(full.slice(0, midCharacter), full.slice(midCharacter)),
    );

    const events = await collect(streamChat("http://api", { message: "hi" }));

    expect(events[0].data).toMatchObject({ text: "café au lait" });
  });

  it("emits several frames arriving in one chunk, in order", async () => {
    mockFetch(
      sseResponse(
        'event: answer.delta\ndata: {"text":"a"}\n\n' +
          'event: answer.delta\ndata: {"text":"b"}\n\n' +
          'event: answer.delta\ndata: {"text":"c"}\n\n',
      ),
    );

    const events = await collect(streamChat("http://api", { message: "hi" }));

    expect(events.map((e) => (e.data as { text: string }).text)).toEqual([
      "a",
      "b",
      "c",
    ]);
  });

  it("skips `:` keepalive comments rather than treating them as data", async () => {
    mockFetch(
      sseResponse(
        ": keepalive\n\n" + 'event: answer.delta\ndata: {"text":"a"}\n\n',
      ),
    );

    const events = await collect(streamChat("http://api", { message: "hi" }));

    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("answer.delta");
  });

  it("survives a malformed frame and keeps delivering the ones after it", async () => {
    // A single bad frame killing the stream would lose an otherwise good answer.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    mockFetch(
      sseResponse(
        "event: answer.delta\ndata: {not json\n\n" +
          'event: answer.delta\ndata: {"text":"recovered"}\n\n',
      ),
    );

    const events = await collect(streamChat("http://api", { message: "hi" }));

    expect(events).toHaveLength(1);
    expect(events[0].data).toMatchObject({ text: "recovered" });
    expect(warn).toHaveBeenCalled();
  });

  it("joins a payload spread over several data lines", async () => {
    mockFetch(sseResponse('event: answer.delta\ndata: {"text":\ndata: "split"}\n\n'));

    const events = await collect(streamChat("http://api", { message: "hi" }));

    expect(events[0].data).toMatchObject({ text: "split" });
  });

  it("ignores a frame that carries no data line", async () => {
    mockFetch(
      sseResponse("event: answer.delta\n\n" + 'event: error\ndata: {"code":"x"}\n\n'),
    );

    const events = await collect(streamChat("http://api", { message: "hi" }));

    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("error");
  });

  it("yields nothing when the signal is already aborted", async () => {
    mockFetch(sseResponse('event: answer.delta\ndata: {"text":"a"}\n\n'));
    const controller = new AbortController();
    controller.abort();

    const events = await collect(
      streamChat("http://api", { message: "hi" }, { signal: controller.signal }),
    );

    expect(events).toEqual([]);
  });
});

describe("error responses", () => {
  it("raises the backend error envelope as a StreamHttpError", async () => {
    mockFetch(
      new Response(
        JSON.stringify({
          error: {
            code: "rate_limited",
            message: "Too many requests",
            request_id: "req-42",
          },
        }),
        { status: 429 },
      ),
    );

    await expect(
      collect(streamChat("http://api", { message: "hi" })),
    ).rejects.toMatchObject({
      name: "StreamHttpError",
      status: 429,
      code: "rate_limited",
      message: "Too many requests",
      requestId: "req-42",
    });
  });

  it("still raises when the error body is not JSON", async () => {
    // An upstream proxy returning an HTML 502 must not surface as a parse crash.
    mockFetch(new Response("<html>502</html>", { status: 502 }));

    const error = await collect(
      streamChat("http://api", { message: "hi" }),
    ).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(StreamHttpError);
    expect((error as StreamHttpError).status).toBe(502);
    expect((error as StreamHttpError).message).toContain("502");
  });
});

describe("request shape", () => {
  it("POSTs the chat request and carries the bearer token", async () => {
    const fetchMock = mockFetch(sseResponse(""));

    await collect(
      streamChat(
        "http://api",
        { message: "hi", conversation_id: "c1", selected_doc_ids: ["d1"] },
        { token: "tok-abc" },
      ),
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api/chat");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok-abc");
    expect(JSON.parse(init.body)).toMatchObject({
      message: "hi",
      conversation_id: "c1",
      selected_doc_ids: ["d1"],
    });
  });

  it("omits the Authorization header in dev mode, where there is no token", async () => {
    const fetchMock = mockFetch(sseResponse(""));

    await collect(streamChat("http://api", { message: "hi" }, { token: null }));

    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty(
      "Authorization",
    );
  });

  it("streams ingest progress from the document events endpoint", async () => {
    const fetchMock = mockFetch(
      sseResponse('event: document.status\ndata: {"status":"parsing"}\n\n'),
    );

    const events = await collect(streamIngest("http://api", "doc-1"));

    expect(fetchMock.mock.calls[0][0]).toBe("http://api/documents/doc-1/events");
    expect(events[0].type).toBe("document.status");
  });
});
