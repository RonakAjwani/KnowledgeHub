/**
 * SSE over `fetch` + `ReadableStream`. One reader for both streams.
 *
 * **Not `EventSource`, and this is not a preference.** The browser's native
 * `EventSource` is GET-only and cannot set request headers, so it can carry
 * neither the `POST` body a chat turn needs nor the `Authorization: Bearer`
 * header Clerk requires. The contract mentions `EventSource` as intent; this is
 * the API that actually satisfies it.
 *
 * The parser is deliberately hand-rolled rather than pulled from a library: the
 * wire format is a dozen lines of the SSE spec, and the two behaviours that
 * matter here — treating `:` comment lines as keepalives rather than data, and
 * never buffering a partial frame across a chunk boundary — are exactly the
 * behaviours a generic library tends to get subtly wrong.
 */

import type { ChatEvent, IngestEvent } from "./types";

export interface StreamOptions {
  /** Clerk session token. Omitted in dev mode, where the backend assigns a fixed user. */
  token?: string | null;
  signal?: AbortSignal;
}

/** Raised when the server answers with an error envelope rather than a stream. */
export class StreamHttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "StreamHttpError";
  }
}

function authHeaders(token?: string | null): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function raiseForStatus(response: Response): Promise<void> {
  if (response.ok) return;
  let code = "dependency_unavailable";
  let message = `Request failed with ${response.status}`;
  let requestId: string | undefined;
  try {
    const body = await response.json();
    code = body?.error?.code ?? code;
    message = body?.error?.message ?? message;
    requestId = body?.error?.request_id;
  } catch {
    // A non-JSON error body is still an error; keep the status-derived message.
  }
  throw new StreamHttpError(response.status, code, message, requestId);
}

/**
 * Parse an SSE byte stream into `{ event, data }` pairs.
 *
 * Frames are separated by a blank line. A chunk boundary can fall anywhere —
 * including mid-frame or mid-UTF-8-character — so bytes are decoded with a
 * streaming decoder and only complete frames are emitted. Emitting on chunk
 * arrival instead is the classic bug: it works until a token happens to straddle
 * a TCP segment, then silently truncates an answer.
 */
async function* parseFrames(
  response: Response,
  signal?: AbortSignal,
): AsyncGenerator<{ event: string; data: unknown }> {
  const body = response.body;
  if (!body) throw new Error("Response carried no body");

  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        boundary = buffer.indexOf("\n\n");

        // `: keepalive` — proof the connection is alive during a quiet stretch.
        // Not data; skipping it is the whole point of the comment syntax.
        if (raw.startsWith(":")) continue;

        let eventName = "message";
        const dataLines: string[] = [];
        for (const line of raw.split("\n")) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (dataLines.length === 0) continue;

        try {
          yield { event: eventName, data: JSON.parse(dataLines.join("\n")) };
        } catch {
          // A malformed frame must not kill an otherwise healthy stream.
          console.warn("skipping unparseable SSE frame", raw.slice(0, 200));
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

/**
 * Stream one chat turn.
 *
 * `POST` with a body, which is precisely why `EventSource` cannot be used here.
 */
export async function* streamChat(
  apiUrl: string,
  request: {
    message: string;
    conversation_id?: string | null;
    /** Tags a brand-new conversation with the workspace it started in; ignored
     * once `conversation_id` is set — a conversation cannot switch workspaces. */
    workspace_id?: string | null;
    selected_doc_ids?: string[] | null;
  },
  options: StreamOptions = {},
): AsyncGenerator<ChatEvent> {
  const response = await fetch(`${apiUrl}/chat`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
      ...authHeaders(options.token),
    },
    body: JSON.stringify(request),
    signal: options.signal,
  });

  await raiseForStatus(response);

  for await (const frame of parseFrames(response, options.signal)) {
    yield { type: frame.event, data: frame.data } as ChatEvent;
  }
}

/** Stream ingest progress for one document. Exactly one terminal event. */
export async function* streamIngest(
  apiUrl: string,
  documentId: string,
  options: StreamOptions = {},
): AsyncGenerator<IngestEvent> {
  const response = await fetch(`${apiUrl}/documents/${documentId}/events`, {
    headers: { accept: "text/event-stream", ...authHeaders(options.token) },
    signal: options.signal,
  });

  await raiseForStatus(response);

  for await (const frame of parseFrames(response, options.signal)) {
    yield { type: frame.event, data: frame.data } as IngestEvent;
  }
}
