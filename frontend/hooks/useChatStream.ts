"use client";

/**
 * One chat turn, as React state.
 *
 * The hook owns the whole lifetime of a `POST /chat` SSE stream and reduces its
 * frames into something a component can render directly. Three things in here
 * are load-bearing and easy to get subtly wrong:
 *
 * 1. **Stages are keyed on `(node, attempt)`.** The corrective retry re-emits
 *    `retrieve`/`rerank`/`grade` with `attempt: 1`. A map keyed on `node` alone
 *    overwrites the first pass, so the UI shows one retrieval where two ran and
 *    the retry becomes invisible — the opposite of what the retry is for.
 * 2. **`verification.complete` patches by `marker`, never by index, and never
 *    reorders.** It may also never arrive; nothing here waits for it. The answer
 *    is complete and usable at `answer.complete`.
 * 3. **Terminal states are distinct.** `abstain` is an honest answer, `error` is
 *    a failure, and a stream that just closes without either is itself an error
 *    (§8 ordering guarantee 2: a failing stream *must* emit `error`).
 *
 * State lives in a ref that mirrors into `useState`. That is deliberate: `send`
 * is async and needs to read the final reduced state synchronously when the
 * stream ends, which a `useReducer` dispatch cannot give it.
 */

import { useAuth } from "@clerk/nextjs";
import { useCallback, useEffect, useRef, useState } from "react";

import { API_URL } from "@/lib/api";
import { StreamHttpError, streamChat } from "@/lib/sse";
import type {
  ChatEvent,
  Citation,
  Degradation,
  PipelineNode,
  PipelineStageDetail,
  RetrievalResultEvent,
} from "@/lib/types";
import { CLERK_ENABLED } from "@/lib/utils";

// ------------------------------------------------------------------- types

/** One `(node, attempt)` pair. `key` is the identity the UI renders on. */
export interface StageRecord {
  key: string;
  node: PipelineNode;
  /** 0 = first pass, 1 = corrective retry. */
  attempt: number;
  state: "started" | "done";
  /** `started` and `done` each carry part of the detail; they are merged here. */
  detail: PipelineStageDetail;
  /** `seq` of the frame that introduced this stage — stable arrival order. */
  seq: number;
}

export interface RetrievalRecord {
  attempt: number;
  candidateCount: number;
  documents: RetrievalResultEvent["documents"];
}

export interface AbstainInfo {
  messageId: string;
  reason: string;
  searched: { doc_count: number; top_score: number };
}

/** Normalised across the three ways a turn can fail: HTTP, network, in-stream. */
export interface ChatStreamError {
  code: string;
  message: string;
  requestId: string | null;
}

export interface VerificationInfo {
  /** Fraction of claims covered by a citation, or `null` when the judge failed (I2). */
  coverage: number | null;
}

export type ChatPhase =
  | "idle"
  | "streaming"
  | "answered"
  | "abstained"
  | "errored"
  | "cancelled";

interface ChatStreamState {
  phase: ChatPhase;
  turnId: string | null;
  messageId: string | null;
  /**
   * Learned from `turn.start` and sent back on every later turn.
   *
   * Deliberately survives `start` — it belongs to the conversation, not the
   * turn. Clearing it between turns would make each message open a new
   * conversation, which is how multi-turn memory silently stops working while
   * every individual answer still looks correct.
   */
  conversationId: string | null;
  answer: string;
  citations: Citation[];
  stages: StageRecord[];
  retrieval: RetrievalRecord[];
  degradations: Degradation[];
  abstain: AbstainInfo | null;
  error: ChatStreamError | null;
  verification: VerificationInfo | null;
}

/** What `send` resolves to, so a caller can commit the turn to a message list. */
export type ChatTurnResult =
  | {
      kind: "answer";
      messageId: string | null;
      answer: string;
      citations: Citation[];
      degradations: Degradation[];
      verification: VerificationInfo | null;
    }
  | { kind: "abstain"; abstain: AbstainInfo; degradations: Degradation[] }
  | {
      kind: "error";
      error: ChatStreamError;
      answer: string;
      degradations: Degradation[];
    }
  | { kind: "cancelled"; answer: string; degradations: Degradation[] };

export interface UseChatStreamOptions {
  /** `null`/empty searches every ready document — the contract's default. */
  selectedDocIds?: string[];
  /**
   * Pins the hook to an existing conversation — e.g. one resumed from
   * `GET /conversations`.
   *
   * Usually unnecessary: the hook learns the id from `turn.start` and threads
   * subsequent turns automatically. Supplying it here takes precedence, which
   * is what lets a sidebar switch conversations.
   */
  conversationId?: string | null;
  apiUrl?: string;
}

const INITIAL: ChatStreamState = {
  phase: "idle",
  turnId: null,
  messageId: null,
  conversationId: null,
  answer: "",
  citations: [],
  stages: [],
  retrieval: [],
  degradations: [],
  abstain: null,
  error: null,
  verification: null,
};

// ----------------------------------------------------------------- reducer

type Action =
  | { type: "reset" }
  | { type: "start" }
  | { type: "event"; event: ChatEvent }
  | { type: "failed"; error: ChatStreamError }
  | { type: "cancelled" }
  | { type: "closed" };

function reduceEvent(
  state: ChatStreamState,
  event: ChatEvent,
): ChatStreamState {
  switch (event.type) {
    case "turn.start":
      return {
        ...state,
        turnId: event.data.turn_id,
        messageId: event.data.message_id,
        conversationId: event.data.conversation_id,
      };

    case "pipeline.stage": {
      const d = event.data;
      const key = `${d.node}:${d.attempt}`;
      const index = state.stages.findIndex((s) => s.key === key);
      if (index === -1) {
        const record: StageRecord = {
          key,
          node: d.node,
          attempt: d.attempt,
          state: d.state,
          detail: d.detail ?? {},
          seq: d.seq,
        };
        return { ...state, stages: [...state.stages, record] };
      }
      const prev = state.stages[index];
      const stages = state.stages.slice();
      stages[index] = {
        ...prev,
        // Never walk a finished stage back to "started": frames are ordered by
        // `seq`, but a retransmit or an out-of-order arrival must not un-finish
        // a node the user already saw complete.
        state: prev.state === "done" ? "done" : d.state,
        detail: { ...prev.detail, ...(d.detail ?? {}) },
      };
      return { ...state, stages };
    }

    case "retrieval.result": {
      const d = event.data;
      const record: RetrievalRecord = {
        attempt: d.attempt,
        candidateCount: d.candidate_count,
        documents: d.documents,
      };
      const index = state.retrieval.findIndex((r) => r.attempt === d.attempt);
      if (index === -1) {
        return { ...state, retrieval: [...state.retrieval, record] };
      }
      const retrieval = state.retrieval.slice();
      retrieval[index] = record;
      return { ...state, retrieval };
    }

    case "answer.delta":
      return { ...state, answer: state.answer + event.data.text };

    case "answer.complete":
      return {
        ...state,
        phase: "answered",
        messageId: event.data.message_id,
        // Sorted once, here. Every later mutation is an in-place patch, so chip
        // order is fixed from this moment — markers never shuffle under a click.
        citations: [...event.data.citations].sort((a, b) => a.marker - b.marker),
      };

    case "abstain":
      return {
        ...state,
        phase: "abstained",
        messageId: event.data.message_id,
        abstain: {
          messageId: event.data.message_id,
          reason: event.data.reason,
          searched: event.data.searched,
        },
      };

    case "verification.complete": {
      const verdicts = new Map(
        event.data.citations.map((c) => [c.marker, c.verified]),
      );
      let changed = false;
      const citations = state.citations.map((citation) => {
        if (!verdicts.has(citation.marker)) return citation;
        const verified = verdicts.get(citation.marker) ?? null;
        // Identity is preserved when nothing changed, so a memoised chip that
        // was already correct does not re-render just because a sibling did.
        if (verified === citation.verified) return citation;
        changed = true;
        return { ...citation, verified };
      });
      return {
        ...state,
        citations: changed ? citations : state.citations,
        verification: { coverage: event.data.coverage },
      };
    }

    case "degradation": {
      const { stage, reason, fallback, detail } = event.data;
      return {
        ...state,
        degradations: [...state.degradations, { stage, reason, fallback, detail }],
      };
    }

    case "error":
      return {
        ...state,
        phase: "errored",
        error: {
          code: event.data.code,
          message: event.data.message,
          requestId: event.data.request_id,
        },
      };

    default:
      // `sse.ts` casts the wire event name straight onto the union, so an event
      // the backend adds before the frontend knows about it lands here. Ignoring
      // it is forward-compatible; throwing would break a working stream.
      return state;
  }
}

function reduce(state: ChatStreamState, action: Action): ChatStreamState {
  switch (action.type) {
    case "reset":
      // Reset means "new conversation", so the id goes too.
      return INITIAL;
    case "start":
      // A new *turn* clears the previous turn's answer, stages and citations —
      // but NOT the conversation id, which identifies the thread the turn joins.
      // Dropping it here would send `conversation_id: null` on every message,
      // opening a fresh conversation each time. Every individual answer would
      // still look right; only the follow-ups would quietly lose their history.
      return { ...INITIAL, phase: "streaming", conversationId: state.conversationId };
    case "event":
      return reduceEvent(state, action.event);
    case "failed":
      return { ...state, phase: "errored", error: action.error };
    case "cancelled":
      return state.phase === "streaming"
        ? { ...state, phase: "cancelled" }
        : state;
    case "closed":
      // Guarantee 2: exactly one of answer.complete / abstain / error, and the
      // stream closes after it. Reaching here still in "streaming" means the
      // connection dropped mid-turn — a truncated answer presented as a whole
      // one is the failure mode this branch exists to prevent.
      return state.phase === "streaming"
        ? {
            ...state,
            phase: "errored",
            error: {
              code: "stream_closed",
              message:
                "The connection closed before the answer finished. Anything above this point is partial.",
              requestId: null,
            },
          }
        : state;
  }
}

// ------------------------------------------------------------------ helpers

function toStreamError(err: unknown): ChatStreamError {
  if (err instanceof StreamHttpError) {
    return {
      code: err.code,
      message: err.message,
      requestId: err.requestId ?? null,
    };
  }
  // `fetch` rejects with a bare TypeError for DNS/CORS/offline, which is the
  // one failure the user can actually act on, so it gets its own code.
  if (err instanceof TypeError) {
    return {
      code: "network_unreachable",
      message: `Could not reach the API. Check that it is running and reachable at ${API_URL}.`,
      requestId: null,
    };
  }
  return {
    code: "unexpected_error",
    message: err instanceof Error ? err.message : String(err),
    requestId: null,
  };
}

function snapshot(state: ChatStreamState): ChatTurnResult {
  switch (state.phase) {
    case "answered":
      return {
        kind: "answer",
        messageId: state.messageId,
        answer: state.answer,
        citations: state.citations,
        degradations: state.degradations,
        verification: state.verification,
      };
    case "abstained":
      return {
        kind: "abstain",
        // `phase === "abstained"` is only reachable via the abstain branch, so
        // `abstain` is set; the fallback keeps the type honest without a cast.
        abstain: state.abstain ?? {
          messageId: state.messageId ?? "",
          reason: "unknown",
          searched: { doc_count: 0, top_score: 0 },
        },
        degradations: state.degradations,
      };
    case "errored":
      return {
        kind: "error",
        error: state.error ?? {
          code: "unexpected_error",
          message: "The turn failed without reporting a reason.",
          requestId: null,
        },
        answer: state.answer,
        degradations: state.degradations,
      };
    default:
      return {
        kind: "cancelled",
        answer: state.answer,
        degradations: state.degradations,
      };
  }
}

type TokenGetter = () => Promise<string | null>;

function useClerkToken(): TokenGetter {
  const { getToken } = useAuth();
  return getToken;
}

function useAnonymousToken(): TokenGetter {
  return useCallback(async () => null, []);
}

/**
 * Picked once at module scope, not per render.
 *
 * `useAuth()` throws outside a `ClerkProvider`, and `CLERK_ENABLED` is a
 * build-time constant — so branching here is a stable choice of hook, not a
 * conditional hook call. Without this the dev-mode build (no publishable key,
 * no provider) crashes on the first render of the chat pane.
 */
const useSessionToken: () => TokenGetter = CLERK_ENABLED
  ? useClerkToken
  : useAnonymousToken;

// --------------------------------------------------------------------- hook

export function useChatStream(options: UseChatStreamOptions = {}) {
  const [state, setState] = useState<ChatStreamState>(INITIAL);
  const stateRef = useRef<ChatStreamState>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);
  /** Bumped by every `send`/`reset`; a stale stream's frames are discarded. */
  const runIdRef = useRef(0);
  const optionsRef = useRef(options);

  useEffect(() => {
    optionsRef.current = options;
  });

  const getToken = useSessionToken();

  const apply = useCallback((action: Action) => {
    const next = reduce(stateRef.current, action);
    if (next === stateRef.current) return;
    stateRef.current = next;
    setState(next);
  }, []);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    runIdRef.current += 1;
    apply({ type: "reset" });
  }, [apply]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  // A turn left running after unmount would hold a socket open and keep
  // decoding tokens nobody will read.
  useEffect(() => () => abortRef.current?.abort(), []);

  const send = useCallback(
    async (message: string): Promise<ChatTurnResult> => {
      const text = message.trim();
      if (!text) {
        return { kind: "cancelled", answer: "", degradations: [] };
      }

      // A second send supersedes the first rather than interleaving with it.
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      runIdRef.current += 1;
      const runId = runIdRef.current;
      const isCurrent = () => runIdRef.current === runId;

      apply({ type: "start" });

      const { selectedDocIds, conversationId, apiUrl } = optionsRef.current;

      try {
        const token = await getToken();
        if (!isCurrent()) {
          return { kind: "cancelled", answer: "", degradations: [] };
        }

        const stream = streamChat(
          apiUrl ?? API_URL,
          {
            message: text,
            // An explicitly supplied id wins (a sidebar switching conversations);
            // otherwise use the one learned from the previous turn's
            // `turn.start`. Null only on the very first turn, where the server
            // mints one and reports it back.
            conversation_id:
              conversationId ?? stateRef.current.conversationId ?? null,
            // `null` means "every ready document" per the contract; an empty
            // array would be a request to search nothing.
            selected_doc_ids:
              selectedDocIds && selectedDocIds.length > 0
                ? selectedDocIds
                : null,
          },
          { token, signal: controller.signal },
        );

        for await (const event of stream) {
          if (!isCurrent()) break;
          apply({ type: "event", event });
        }

        if (isCurrent()) {
          // The parser returns cleanly on abort rather than throwing, so an
          // aborted stream reaches here looking exactly like a closed one.
          apply({ type: controller.signal.aborted ? "cancelled" : "closed" });
        }
      } catch (err) {
        if (!isCurrent()) {
          return { kind: "cancelled", answer: "", degradations: [] };
        }
        if (controller.signal.aborted) {
          // The user pressed stop; `fetch` rejecting is the expected outcome,
          // not a failure worth showing them.
          apply({ type: "cancelled" });
        } else {
          apply({ type: "failed", error: toStreamError(err) });
        }
      } finally {
        if (isCurrent()) abortRef.current = null;
      }

      // A superseded turn must not report the *replacement* turn's state, which
      // `stateRef` now holds. Its caller gets "cancelled" and commits nothing.
      if (!isCurrent()) {
        return { kind: "cancelled", answer: "", degradations: [] };
      }
      return snapshot(stateRef.current);
    },
    [apply, getToken],
  );

  return {
    send,
    cancel,
    reset,
    isStreaming: state.phase === "streaming",
    phase: state.phase,
    turnId: state.turnId,
    messageId: state.messageId,
    // Exposed so a conversation sidebar can show which thread is live; the hook
    // threads turns on its own without the caller touching this.
    conversationId: state.conversationId,
    answer: state.answer,
    citations: state.citations,
    stages: state.stages,
    retrieval: state.retrieval,
    degradations: state.degradations,
    abstain: state.abstain,
    error: state.error,
    verification: state.verification,
  };
}

export type UseChatStreamReturn = ReturnType<typeof useChatStream>;
