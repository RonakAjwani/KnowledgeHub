"use client";

/**
 * The chat surface: message list, composer, and the live view of one turn.
 *
 * Three rendering decisions carry meaning rather than style:
 *
 * - **Inline `[n]` markers become chips in place.** The model emits citations
 *   inside the prose, so the text is split around each marker and the chip is
 *   rendered where the claim is, not collected into a footnote list. Adjacent
 *   markers (`[1][2]`) fall out of the same pass.
 * - **Abstain is not an error.** The pipeline refusing to answer from weak
 *   retrieval is the system working — it is presented calmly, and it names what
 *   was actually searched so the user can judge whether to rephrase or upload
 *   something. Styling it red would teach people to read honesty as breakage.
 * - **Degradations survive the turn.** The banner is attached to the committed
 *   message, not just to the live stream, so scrolling back still shows that
 *   this particular answer took a fallback (I1).
 */

import { CircleAlert, Copy, Send, SearchX, Square } from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { CitationChip } from "@/components/CitationChip";
import { DegradationBanner } from "@/components/DegradationBanner";
import { PipelineIndicator, PipelineShimmer } from "@/components/PipelineIndicator";
import { Button } from "@/components/ui/button";
import {
  useChatStream,
  type AbstainInfo,
  type ChatStreamError,
} from "@/hooks/useChatStream";
import { useSessionToken } from "@/hooks/useSessionToken";
import { api } from "@/lib/api";
import type { Citation, Degradation, PersistedMessage } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface ChatPaneProps {
  selectedDocIds: string[];
  onCitationClick: (citation: Citation) => void;
  /** Tags a brand-new conversation so it defaults to this workspace's own
   * documents and shows up filed under it. `null` = no workspace context. */
  workspaceId: string | null;
  /**
   * Which thread this pane shows. `null` means "a fresh, unsaved chat" — the
   * moment the first turn lands, the server mints an id and `onConversationStarted`
   * reports it so the sidebar can list it. A non-null value loads that
   * conversation's history before anything else renders.
   */
  conversationId: string | null;
  onConversationStarted?: (conversationId: string) => void;
}

interface UserTurn {
  id: string;
  role: "user";
  content: string;
}

interface AssistantTurn {
  id: string;
  role: "assistant";
  content: string;
  citations: Citation[];
  degradations: Degradation[];
  abstain: AbstainInfo | null;
  error: ChatStreamError | null;
  /** True when the user pressed stop — the text above is a partial answer. */
  stopped: boolean;
}

type Turn = UserTurn | AssistantTurn;

/**
 * A reloaded message becomes a turn exactly like a live one does. An abstain
 * has no separate representation once persisted — `abstain_node` writes its
 * refusal text as the message's own `content`, so it already reads as a plain,
 * honest answer bubble without needing the live-only `AbstainCard` styling.
 */
function messageToTurn(message: PersistedMessage): Turn {
  if (message.role === "user") {
    return { id: message.id, role: "user", content: message.content };
  }
  return {
    id: message.id,
    role: "assistant",
    content: message.content,
    citations: message.citations,
    degradations: message.degradations,
    abstain: null,
    error: null,
    stopped: false,
  };
}

// --------------------------------------------------------- answer rendering

type Segment =
  | { kind: "text"; key: string; text: string }
  | { kind: "citation"; key: string; marker: number; citation: Citation };

/** `[12]` — bounded to three digits so `[2024]` in prose is not eaten as a marker. */
const MARKER_RE = /\[(\d{1,3})\]/g;
/** A marker the stream has only half-delivered: `[`, `[1`, `[12`. */
const PARTIAL_MARKER_RE = /\[\d{0,3}$/;

function segmentAnswer(text: string, citations: Citation[]): Segment[] {
  const byMarker = new Map(citations.map((c) => [c.marker, c]));
  const segments: Segment[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;

  MARKER_RE.lastIndex = 0;
  while ((match = MARKER_RE.exec(text)) !== null) {
    const marker = Number(match[1]);
    const citation = byMarker.get(marker);
    // A marker with no matching citation stays literal text. Rendering a chip
    // that points nowhere would invent provenance the answer does not have.
    if (!citation) continue;

    if (match.index > cursor) {
      segments.push({
        kind: "text",
        key: `t${cursor}`,
        text: text.slice(cursor, match.index),
      });
    }
    segments.push({
      kind: "citation",
      key: `c${match.index}-${marker}`,
      marker,
      citation,
    });
    cursor = match.index + match[0].length;
  }

  if (cursor < text.length) {
    segments.push({ kind: "text", key: `t${cursor}`, text: text.slice(cursor) });
  }
  return segments;
}

function AnswerBody({
  text,
  citations,
  streaming,
  onCitationClick,
}: {
  text: string;
  citations: Citation[];
  streaming: boolean;
  onCitationClick: (citation: Citation) => void;
}) {
  // While tokens are still arriving, hide a marker that has only partly landed.
  // Otherwise the reader watches `[`, then `[1`, then a chip pop into place on
  // every single citation in the answer.
  const display = streaming ? text.replace(PARTIAL_MARKER_RE, "") : text;

  const segments = useMemo(
    () => segmentAnswer(display, citations),
    [display, citations],
  );

  return (
    <div className="whitespace-pre-wrap break-words text-sm leading-relaxed text-zinc-800 dark:text-zinc-100">
      {segments.map((segment) =>
        segment.kind === "text" ? (
          <span key={segment.key}>{segment.text}</span>
        ) : (
          <CitationChip
            key={segment.key}
            citation={segment.citation}
            onClick={onCitationClick}
          />
        ),
      )}
      {streaming ? (
        <span className="ml-0.5 inline-block h-4 w-[2px] animate-pulse bg-zinc-500 align-text-bottom dark:bg-zinc-300" />
      ) : null}
    </div>
  );
}

// ------------------------------------------------------- terminal-state cards

function AbstainCard({ abstain }: { abstain: AbstainInfo }) {
  const { doc_count, top_score } = abstain.searched;
  return (
    <div className="rounded-lg border border-zinc-300 bg-zinc-50 p-3 dark:border-zinc-700 dark:bg-zinc-900/60">
      <div className="flex items-start gap-2">
        <SearchX
          className="mt-px size-4 shrink-0 text-zinc-500 dark:text-zinc-400"
          aria-hidden
        />
        <div className="min-w-0 flex-1 text-sm">
          <p className="font-medium text-zinc-800 dark:text-zinc-100">
            Nothing in your documents supports an answer to this.
          </p>
          <p className="mt-1 text-zinc-600 dark:text-zinc-300">
            Searched {doc_count} {doc_count === 1 ? "document" : "documents"};
            the closest passage scored{" "}
            <span className="font-mono tabular-nums">{top_score.toFixed(2)}</span>
            , below the threshold for answering. Rather than guess from weak
            matches, this turn returns nothing.
          </p>
          {abstain.reason ? (
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              Reason: {abstain.reason}
            </p>
          ) : null}
          <p className="mt-1.5 text-xs text-zinc-500 dark:text-zinc-400">
            Try rephrasing with terms the documents would use, widening the
            document selection, or uploading a source that covers it.
          </p>
        </div>
      </div>
    </div>
  );
}

function ErrorCard({ error }: { error: ChatStreamError }) {
  const [copied, setCopied] = useState(false);

  const copy = useCallback(async () => {
    const payload = error.requestId
      ? `${error.code} · request_id ${error.requestId}`
      : error.code;
    try {
      await navigator.clipboard.writeText(payload);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be denied; the id is selectable text either way.
      setCopied(false);
    }
  }, [error]);

  return (
    <div
      role="alert"
      className="rounded-lg border border-red-300 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950/40"
    >
      <div className="flex items-start gap-2">
        <CircleAlert
          className="mt-px size-4 shrink-0 text-red-600 dark:text-red-400"
          aria-hidden
        />
        <div className="min-w-0 flex-1 text-sm">
          <p className="font-medium text-red-900 dark:text-red-100">
            This turn failed.
          </p>
          <p className="mt-1 text-red-800 dark:text-red-200">{error.message}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-red-700 dark:text-red-300">
            <code className="rounded bg-red-100 px-1.5 py-0.5 font-mono dark:bg-red-900/60">
              {error.code}
            </code>
            {error.requestId ? (
              <>
                {/* Quotable: this is the id support will ask for. */}
                <code className="select-all rounded bg-red-100 px-1.5 py-0.5 font-mono dark:bg-red-900/60">
                  request_id {error.requestId}
                </code>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={copy}
                  className="h-6 px-1.5 text-red-700 hover:bg-red-100 hover:text-red-900 dark:text-red-300 dark:hover:bg-red-900/50 dark:hover:text-red-100"
                >
                  <Copy className="size-3" aria-hidden />
                  {copied ? "Copied" : "Copy"}
                </Button>
              </>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ bubbles

function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-accent-100 px-3.5 py-2 text-sm text-zinc-900 shadow-sm dark:bg-accent-950/50 dark:text-zinc-100">
        {content}
      </div>
    </div>
  );
}

function AssistantBubble({
  turn,
  onCitationClick,
}: {
  turn: AssistantTurn;
  onCitationClick: (citation: Citation) => void;
}) {
  return (
    <div className="max-w-[95%] space-y-2">
      {turn.degradations.length > 0 ? (
        <DegradationBanner degradations={turn.degradations} />
      ) : null}

      {turn.error ? <ErrorCard error={turn.error} /> : null}
      {turn.abstain ? <AbstainCard abstain={turn.abstain} /> : null}

      {turn.content ? (
        <div className="rounded-2xl rounded-bl-sm border border-zinc-200 bg-white px-3.5 py-2.5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
          <AnswerBody
            text={turn.content}
            citations={turn.citations}
            streaming={false}
            onCitationClick={onCitationClick}
          />
          {turn.stopped ? (
            <p className="mt-2 text-xs italic text-zinc-500 dark:text-zinc-400">
              Stopped — this answer is partial.
            </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// -------------------------------------------------------------------- pane

export function ChatPane({
  selectedDocIds,
  onCitationClick,
  workspaceId,
  conversationId,
  onConversationStarted,
}: ChatPaneProps) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [showTrace, setShowTrace] = useState(false);
  /**
   * doc_id → filename, learned from `retrieval.result`. The scope line only
   * receives ids, and this pane deliberately does not fetch the document list
   * itself — that is the document manager's data, and duplicating the query
   * here would mean two components racing to own the same cache key.
   */


  const turnCounter = useRef(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottom = useRef(true);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const getToken = useSessionToken();

  const {
    send,
    cancel,
    reset,
    isStreaming,
    answer,
    citations,
    stages,
    retrieval,
    degradations,
    conversationId: liveConversationId,
  } = useChatStream({ selectedDocIds, workspaceId, conversationId });

  // The server only mints an id on a brand-new conversation's first turn — the
  // sidebar has nothing to list or highlight until this fires.
  const reportedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!liveConversationId || liveConversationId === reportedRef.current) return;
    reportedRef.current = liveConversationId;
    onConversationStarted?.(liveConversationId);
  }, [liveConversationId, onConversationStarted]);

  /**
   * Reset the visible turn list the instant `conversationId` changes, during
   * render rather than in an effect — the pattern React's own docs recommend
   * for "adjust state when a prop changes" ("You Might Not Need an Effect"),
   * and the only one that satisfies `react-hooks/set-state-in-effect`: that
   * rule exists because a synchronous reset inside an effect runs one paint
   * late, so old turns flash before the empty list does.
   *
   * Compared against `liveConversationId`, not just the previous prop value:
   * the parent echoes this pane's own freshly-minted id back down (to
   * highlight it in the sidebar), and without this comparison that echo would
   * look identical to "the user switched conversations" and wipe the very
   * turns that streamed the id into existence.
   */
  const [historyForId, setHistoryForId] = useState(conversationId);
  if (conversationId !== historyForId) {
    setHistoryForId(conversationId);
    if (conversationId !== liveConversationId) {
      setTurns([]);
      setLoadingHistory(conversationId !== null);
    }
  }

  // The genuine side effects of a real switch — aborting whatever the live
  // stream hook was doing, and fetching the newly-selected conversation's
  // history — stay in an effect. Guarded the same way as the render-time
  // reset above, so the self-authored echo does not abort its own stream.
  useEffect(() => {
    if (conversationId === liveConversationId) return;
    reset();
    if (!conversationId) return;

    let cancelled = false;
    (async () => {
      try {
        const token = await getToken();
        const detail = await api.getConversation(conversationId, token);
        if (cancelled) return;
        setTurns(detail.messages.map(messageToTurn));
      } catch {
        // The conversation may since have been deleted, or the fetch may have
        // raced a slow network — either way, an empty pane is the honest
        // fallback rather than a crash.
        if (!cancelled) setTurns([]);
      } finally {
        if (!cancelled) setLoadingHistory(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [conversationId, liveConversationId, getToken, reset]);

  // Derived, not synced. Filenames are a pure function of the retrieval events
  // already in state, so mirroring them into their own state via an effect would
  // add a render pass and a second source of truth for no gain.
  const docNames = useMemo(() => {
    const names: Record<string, string> = {};
    for (const record of retrieval) {
      for (const doc of record.documents) names[doc.doc_id] = doc.filename;
    }
    return names;
  }, [retrieval]);

  // Follow the stream, but stop following the moment the user scrolls up to
  // re-read something — yanking them back to the bottom mid-sentence is worse
  // than losing sight of the newest token.
  const onScroll = useCallback(() => {
    const node = scrollRef.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    stickToBottom.current = distance < 64;
  }, []);

  useEffect(() => {
    if (!stickToBottom.current) return;
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns, answer, stages, degradations]);

  const submit = useCallback(async () => {
    const text = draft.trim();
    if (!text || isStreaming) return;

    turnCounter.current += 1;
    const userTurnId = `u${turnCounter.current}`;
    setTurns((prev) => [
      ...prev,
      { id: userTurnId, role: "user", content: text },
    ]);
    setDraft("");
    setShowTrace(false);
    stickToBottom.current = true;

    const result = await send(text);

    turnCounter.current += 1;
    const assistantTurnId = `a${turnCounter.current}`;

    // Commit and clear in one continuation so the live view and the committed
    // message never both render — React batches these into a single paint.
    setTurns((prev) => {
      const base: AssistantTurn = {
        id: assistantTurnId,
        role: "assistant",
        content: "",
        citations: [],
        degradations: [],
        abstain: null,
        error: null,
        stopped: false,
      };

      switch (result.kind) {
        case "answer":
          return [
            ...prev,
            {
              ...base,
              content: result.answer,
              citations: result.citations,
              degradations: result.degradations,
            },
          ];
        case "abstain":
          return [
            ...prev,
            {
              ...base,
              abstain: result.abstain,
              degradations: result.degradations,
            },
          ];
        case "error":
          return [
            ...prev,
            {
              ...base,
              content: result.answer,
              error: result.error,
              degradations: result.degradations,
            },
          ];
        case "cancelled":
          // Nothing arrived before the stop — no empty bubble to show for it.
          if (!result.answer && result.degradations.length === 0) return prev;
          return [
            ...prev,
            {
              ...base,
              content: result.answer,
              degradations: result.degradations,
              stopped: true,
            },
          ];
      }
    });
    reset();
    // Sending via the button moves focus to the button; a chat surface should
    // leave you ready to type the next question, not hunting for the box.
    textareaRef.current?.focus();
  }, [draft, isStreaming, send, reset]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter sends; Shift+Enter is a newline. IME composition must not send —
      // committing a candidate with Enter would otherwise fire the turn.
      if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
        event.preventDefault();
        void submit();
      }
    },
    [submit],
  );

  const scopeNames = useMemo(
    () => selectedDocIds.map((id) => docNames[id]).filter(Boolean),
    [selectedDocIds, docNames],
  );

  const showLiveTurn = isStreaming || answer.length > 0;

  return (
    <section className="flex h-full min-h-0 flex-col">
      {/* Scope is stated up front: an answer drawn from 2 of 40 documents is a
          different claim from one drawn from all of them. */}
      {selectedDocIds.length > 0 ? (
        <div
          className="border-b border-zinc-200 bg-zinc-50 px-4 py-2 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900/60 dark:text-zinc-300"
          title={scopeNames.length > 0 ? scopeNames.join("\n") : undefined}
        >
          <span className="font-medium">Searching {selectedDocIds.length}</span>{" "}
          {selectedDocIds.length === 1 ? "document" : "documents"}
          {scopeNames.length > 0 ? (
            <span className="text-zinc-500 dark:text-zinc-400">
              {" · "}
              {scopeNames.slice(0, 3).join(", ")}
              {scopeNames.length > 3 ? ` +${scopeNames.length - 3} more` : ""}
            </span>
          ) : null}
        </div>
      ) : (
        <div className="border-b border-zinc-200 bg-zinc-50 px-4 py-2 text-xs text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900/60 dark:text-zinc-400">
          Searching all of your ready documents.
        </div>
      )}

      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4"
      >
        {turns.length === 0 && !showLiveTurn ? (
          <div className="mx-auto mt-10 max-w-md text-center text-sm text-zinc-500 dark:text-zinc-400">
            <p className="font-medium text-zinc-700 dark:text-zinc-200">
              Ask something about your documents.
            </p>
            <p className="mt-1.5">
              Answers cite the passage they came from. Click a citation to jump
              to it in the source.
            </p>
          </div>
        ) : null}

        {loadingHistory ? (
          <p className="shimmer-text text-sm font-medium">
            Loading conversation…
          </p>
        ) : null}

        {turns.map((turn) =>
          turn.role === "user" ? (
            <UserBubble key={turn.id} content={turn.content} />
          ) : (
            <AssistantBubble
              key={turn.id}
              turn={turn}
              onCitationClick={onCitationClick}
            />
          ),
        )}

        {showLiveTurn ? (
          <div className="max-w-[95%] space-y-2">
            {/* The shimmer fills the gap before any token has arrived — once the
                answer starts streaming, the appearing text is itself the signal
                that something is happening, so the line steps aside rather than
                shimmering alongside live text. An already-expanded trace stays
                open, though: a user who asked to see the detail keeps it. */}
            {isStreaming && answer.length === 0 ? (
              <PipelineShimmer
                stages={stages}
                expanded={showTrace}
                onToggle={() => setShowTrace((v) => !v)}
              />
            ) : null}

            {isStreaming && showTrace ? (
              <PipelineIndicator stages={stages} retrieval={retrieval} />
            ) : null}

            {degradations.length > 0 ? (
              <DegradationBanner degradations={degradations} />
            ) : null}

            {answer.length > 0 ? (
              <div className="rounded-2xl rounded-bl-sm border border-zinc-200 bg-white px-3.5 py-2.5 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
                <AnswerBody
                  text={answer}
                  citations={citations}
                  streaming={isStreaming}
                  onCitationClick={onCitationClick}
                />
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      <div className="border-t border-zinc-200 bg-white p-3 dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder="Ask a question about your documents…"
            aria-label="Message"
            className={cn(
              "max-h-40 min-h-[2.5rem] flex-1 resize-y rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm",
              "text-zinc-900 placeholder:text-zinc-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
              "dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-500",
            )}
          />

          {isStreaming ? (
            <Button
              variant="outline"
              onClick={cancel}
              aria-label="Stop generating"
              className="h-10"
            >
              <Square className="size-3.5 fill-current" aria-hidden />
              Stop
            </Button>
          ) : (
            <Button
              variant="accent"
              onClick={() => void submit()}
              disabled={draft.trim().length === 0}
              aria-label="Send message"
              className="h-10"
            >
              <Send className="size-3.5" aria-hidden />
              Send
            </Button>
          )}
        </div>
        <p className="mt-1.5 text-[0.7rem] text-zinc-400 dark:text-zinc-500">
          Enter to send · Shift+Enter for a new line
        </p>
      </div>
    </section>
  );
}
