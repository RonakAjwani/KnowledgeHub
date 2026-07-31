"use client";

/**
 * The chat surface: message list, composer, and the live view of one turn.
 *
 * Three rendering decisions carry meaning rather than style:
 *
 * - **Inline `[n]` markers become chips in place.** The model emits citations
 * inside the prose, so the text is split around each marker and the chip is
 * rendered where the claim is, not collected into a footnote list. Adjacent
 * markers (`[1][2]`) fall out of the same pass.
 * - **Abstain is not an error.** The pipeline refusing to answer from weak
 * retrieval is the system working - it is presented calmly, and it names what
 * was actually searched so the user can judge whether to rephrase or upload
 * something. Styling it red would teach people to read honesty as breakage.
 * - **Degradations survive the turn.** The banner is attached to the committed
 * message, not just to the live stream, so scrolling back still shows that
 * this particular answer took a fallback (I1).
 */

import { useQuery } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  CircleAlert,
  Copy,
  MessageSquare,
  RotateCcw,
  SearchX,
  Square,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { CitationChip } from "@/components/CitationChip";
import { DegradationBanner } from "@/components/DegradationBanner";
import {
  PipelineIndicator,
  PipelineShimmer,
} from "@/components/PipelineIndicator";
import { Button } from "@/components/ui/button";
import {
  useChatStream,
  type AbstainInfo,
  type ChatStreamError,
} from "@/hooks/useChatStream";
import { useCopyToClipboard } from "@/hooks/useCopyToClipboard";
import { useSessionToken } from "@/hooks/useSessionToken";
import { api } from "@/lib/api";
import { fileKind } from "@/lib/fileKind";
import {
  CITATION_NODE_TAG,
  CURSOR_NODE_TAG,
  CURSOR_SENTINEL,
  remarkCitationMarkers,
} from "@/lib/markdown-citations";
import { conversationsKey } from "@/lib/queryKeys";
import type { Citation, Degradation, PersistedMessage } from "@/lib/types";
import { cn, conversationLabel } from "@/lib/utils";

/**
 * Coarse (day-granularity) relative time for the Recents list. Deliberately
 * not minute/hour-precise: a finer-grained clock computed once on the server
 * and again on the client's first hydration pass is exactly the kind of
 * value that can disagree between the two and trip a hydration mismatch
 * (see hooks/useHasMounted.ts) - day-level differences essentially never
 * flip between a request and the hydration that follows milliseconds later.
 */
function formatRelativeDay(dateString: string | null): string {
  if (!dateString) return "";
  const created = new Date(dateString);
  const days = Math.floor((Date.now() - created.getTime()) / 86_400_000);
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  return `${days} days ago`;
}

export interface ChatPaneProps {
  selectedDocIds: string[];
  onCitationClick: (citation: Citation) => void;
  /** Tags a brand-new conversation so it defaults to this workspace's own
   * documents and shows up filed under it. `null` = no workspace context. */
  workspaceId: string | null;
  /**
   * Which thread this pane shows. `null` means"a fresh, unsaved chat"- the
   * moment the first turn lands, the server mints an id and `onConversationStarted`
   * reports it so the sidebar can list it. A non-null value loads that
   * conversation's history before anything else renders.
   */
  conversationId: string | null;
  onConversationStarted?: (conversationId: string) => void;
  /** Clicking a past conversation in the empty-state Recents list. Navigation
   * itself is the page's job (it owns the router), not this pane's. */
  onOpenConversation?: (conversationId: string) => void;
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
  /** True when the user pressed stop - the text above is a partial answer. */
  stopped: boolean;
}

type Turn = UserTurn | AssistantTurn;

/**
 * A reloaded message becomes a turn exactly like a live one does. An abstain
 * has no separate representation once persisted - `abstain_node` writes its
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

/** A marker the stream has only half-delivered: `[`, `[1`, `[12`. Stripped
 * before the text ever reaches `remarkCitationMarkers` (see `MARKER_RE`
 * there, kept in sync with this one), so a token boundary never renders as a
 * bare bracket for one frame. */
const PARTIAL_MARKER_RE = /\[\d{0,3}$/;

/** Composer grows with the draft up to this many pixels, then scrolls internally.
 * 180px, not 160, so it agrees with the textarea's own `max-h-40` fallback -
 * Tailwind's spacing scale is rem-based and the root is 18px, so `40` is
 * 10rem = 180px here, not the 160 the number would suggest at a 16px root. */
const COMPOSER_MAX_HEIGHT = 180;

/**
 * The reading column: every part of the chat is centred and capped at one
 * measure rather than stretching to the window.
 *
 * Chat is a reading surface, and a full-width line of an answer is genuinely
 * harder to read than a narrow one - at ~1400px the eye loses the start of the
 * next line on every wrap. 42rem lands near 80 characters at this type size,
 * which is the usual comfortable measure.
 *
 * The *same* class is used for the workspace-home composer and the in-chat
 * one, so the input does not jump width the moment the first turn lands.
 */
const READING_COLUMN = "mx-auto w-full max-w-2xl";

/**
 * `ReactMarkdown`'s `components` map keyed on the two custom tags
 * `remarkCitationMarkers` emits. Built once per render of `AnswerBody`
 * (cheap - two closures) rather than hoisted, since both close over that
 * render's `citations` and `onCitationClick`.
 *
 * Cast to `Components`, not naturally assignable to it: that type is keyed on
 * `keyof JSX.IntrinsicElements`, which real HTML tags exhaust and a
 * hand-invented one like `citation-marker` never appears in. The cast is
 * exactly the gap between "a valid hast tag name" (unbounded - this is the
 * documented way to render a custom node type) and "a tag TypeScript has
 * static knowledge of."
 */
function useAnswerMarkdownComponents(
  citations: Citation[],
  onCitationClick: (citation: Citation) => void,
): Components {
  return useMemo(
    () =>
      ({
        [CITATION_NODE_TAG]: ({ marker }: { marker: number }) => {
          const citation = citations.find((c) => c.marker === marker);
          // A marker with no matching citation stays literal text. Rendering
          // a chip that points nowhere would invent provenance the answer
          // does not have.
          return citation ? (
            <CitationChip citation={citation} onClick={onCitationClick} />
          ) : (
            `[${marker}]`
          );
        },
        [CURSOR_NODE_TAG]: () => (
          <span className="ml-0.5 inline-block h-[1.1em] w-[2px] animate-pulse bg-zinc-500 align-text-bottom dark:bg-zinc-300" />
        ),
      }) as unknown as Components,
    [citations, onCitationClick],
  );
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
  // While tokens are still arriving, hide a marker that has only partly
  // landed - otherwise the reader watches `[`, then `[1`, then a chip pop
  // into place on every single citation - then splice in the sentinel that
  // becomes the inline blinking caret (see `CURSOR_SENTINEL`'s own comment
  // for why that has to happen here, in the markdown source, rather than as
  // a sibling appended after the rendered output).
  const display = streaming
    ? text.replace(PARTIAL_MARKER_RE, "") + CURSOR_SENTINEL
    : text;

  const components = useAnswerMarkdownComponents(citations, onCitationClick);

  return (
    // Answer prose is the one thing on this screen people read for minutes at
    // a time, so it gets the largest step and a looser line height than the
    // surrounding chrome - `text-sm` was sized like a label, not like body text.
    <div className="prose-kh break-words text-base text-zinc-800 dark:text-zinc-100">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCitationMarkers]}
        components={components}
      >
        {display}
      </ReactMarkdown>
    </div>
  );
}

/**
 * The documents an answer actually drew from, deduplicated to one entry per
 * document - distinct from the inline `[n]` chips, which mark *where in the
 * prose* a claim came from. This is the"what did you search"summary shown
 * once at the end, the reference's artifact card translated to this product's
 * own unit: not a generated file, a cited source. Clicking a card reuses the
 * same `onCitationClick` path as an inline chip, so it opens the exact cited
 * span, not just the document.
 */
function SourcesFooter({
  citations,
  onCitationClick,
}: {
  citations: Citation[];
  onCitationClick: (citation: Citation) => void;
}) {
  const unique = useMemo(() => {
    const seen = new Set<string>();
    const list: Citation[] = [];
    for (const citation of citations) {
      if (seen.has(citation.doc_id)) continue;
      seen.add(citation.doc_id);
      list.push(citation);
    }
    return list;
  }, [citations]);

  if (unique.length === 0) return null;

  return (
    <div>
      <p className="mb-1.5 text-xs font-medium text-zinc-500 dark:text-zinc-400">
        Sources
      </p>
      <div className="flex flex-col gap-1.5">
        {unique.map((citation) => {
          const kind = fileKind(citation.filename, "");
          const location = citation.page
            ? `page ${citation.page}`
            : citation.section;
          return (
            <button
              key={citation.doc_id}
              type="button"
              onClick={() => onCitationClick(citation)}
              // `dark:bg-zinc-950` (#1a1a1a), matching the panel behind it
              // rather than sitting a step lighter: in the reference these
              // end-of-answer source cards read as *outlined* on the chat
              // background, not as raised tiles (the raised, lighter #2c2c2c
              // treatment belongs to the artifacts-panel rows). Hover lifts
              // one step to z900 so the card is still clearly interactive.
              className="flex w-full max-w-sm items-center gap-3 rounded-xl border border-zinc-200 bg-white p-3 text-left hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950 dark:hover:bg-zinc-900"
            >
              <span
                className={cn(
                  "flex size-10 shrink-0 items-center justify-center rounded-lg",
                  kind.swatchClassName,
                )}
              >
                <kind.Icon className="size-5" aria-hidden />
              </span>
              <span className="min-w-0 flex-1">
                {/* dark:text-zinc-100, not -50 - see DocumentManager's
 DocumentRow for why: `z50` stays dark in dark mode. */}
                <span className="block truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                  {citation.filename}
                </span>
                <span className="block truncate text-xs text-zinc-500 dark:text-zinc-400">
                  {kind.label}
                  {location ? ` · ${location}` : ""}
                </span>
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------- terminal-state cards

/**
 * Shown when the last turn in the pane is a user message with nothing after
 * it - no live view running, no committed assistant turn either.
 *
 * That state is reachable without any one component doing anything wrong:
 * `send()` returns `cancelled` with an empty answer whenever a turn gets
 * superseded before its first token, and `runTurn` correctly appends nothing
 * for that case (inventing an empty assistant bubble would be worse). The gap
 * is what happens *later* - the question was already persisted, but a
 * conversation reopened after that is a dangling user bubble with no
 * indication anything went wrong, forever, unless the reader happens to
 * remember they never got a reply. This card is the difference between "the
 * app silently dropped that" and "the app told me and let me retry" - the
 * same I1 (degradation is never silent) reasoning `DegradationBanner` exists
 * for, just for a turn that did not produce a turn at all.
 */
function UnansweredCard({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="rounded-lg border border-zinc-300 bg-zinc-50 p-3 dark:border-zinc-700 dark:bg-zinc-900/60">
      <div className="flex items-start gap-2">
        <CircleAlert
          className="mt-px size-4 shrink-0 text-zinc-500 dark:text-zinc-400"
          aria-hidden
        />
        <div className="min-w-0 flex-1 text-sm">
          <p className="font-medium text-zinc-800 dark:text-zinc-100">
            This question never got an answer.
          </p>
          <p className="mt-1 text-zinc-600 dark:text-zinc-300">
            The connection likely dropped before a reply came back - closing
            the tab or navigating away mid-turn are the usual causes.
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={onRetry}
            className="mt-2"
          >
            <RotateCcw className="size-3" aria-hidden />
            Try again
          </Button>
        </div>
      </div>
    </div>
  );
}

function AbstainCard({ abstain }: { abstain: AbstainInfo }) {
  const { doc_count, top_score } = abstain.searched;
  return (
    <div className="rounded-lg border border-zinc-300 bg-zinc-50 p-3 dark:border-zinc-700 dark:bg-zinc-900/60">
      <div className="flex items-start gap-2">
        <SearchX
          className="mt-px size-4 shrink-0 text-zinc-500 dark:text-zinc-400"
          aria-hidden
        />
        {/* An abstain *is* the answer for this turn, so it is set at answer
 size like one - only its metadata lines stay small. */}
        <div className="min-w-0 flex-1 text-base leading-[1.6]">
          <p className="font-medium text-zinc-800 dark:text-zinc-100">
            Nothing in your documents supports an answer to this.
          </p>
          <p className="mt-1 text-zinc-600 dark:text-zinc-300">
            Searched {doc_count} {doc_count === 1 ? "document" : "documents"};
            the closest passage scored{" "}
            <span className="font-mono tabular-nums">
              {top_score.toFixed(2)}
            </span>
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
  const { copied, copy } = useCopyToClipboard();
  return (
    <div className="group flex flex-col items-end">
      {/* zinc-100 is only ~2 RGB units off the page's own zinc-50 in light
 mode (#fbfbf9 vs #fcfcfb, both tuned to sit near-invisibly close to
 each other as *surface* steps) - a bubble in that colour reads as
 no bubble at all. zinc-200 (#f0f0ee) is the nearest step with real
 contrast against the page. */}
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-3xl bg-zinc-200 px-5 py-3 text-base leading-[1.6] text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100">
        {content}
      </div>
      <button
        type="button"
        onClick={() => void copy(content)}
        aria-label="Copy message"
        className="mt-1 flex items-center gap-1 rounded p-1 text-xs text-zinc-400 opacity-0 transition-opacity hover:bg-zinc-100 hover:text-zinc-700 group-hover:opacity-100 group-focus-within:opacity-100 dark:text-zinc-500 dark:hover:bg-zinc-900 dark:hover:text-zinc-200"
      >
        <Copy className="size-3" aria-hidden />
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/**
 * Copy/Retry, hover-revealed. Retry is offered on every committed turn -
 * answered, errored, abstained, or stopped - since resubmitting makes sense
 * in all four, and a failed turn is arguably where it matters most. It is a
 * client-side-only"replace this turn"illusion: the backend has no message-
 * supersession concept, so `POST /chat` always inserts new rows, and
 * reloading this conversation (`GET /conversations/{id}`) will show the full,
 * honest history - original question/answer *and* the retried one, not one
 * replacing the other. Real supersession would be separate backend work.
 */
function MessageToolbar({
  content,
  onRetry,
}: {
  content: string;
  onRetry: () => void;
}) {
  const { copied, copy } = useCopyToClipboard();
  return (
    <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
      {content ? (
        <button
          type="button"
          onClick={() => void copy(content)}
          aria-label="Copy answer"
          className="flex items-center gap-1 rounded p-1 text-xs text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:text-zinc-500 dark:hover:bg-zinc-900 dark:hover:text-zinc-200"
        >
          <Copy className="size-3" aria-hidden />
          {copied ? "Copied" : "Copy"}
        </button>
      ) : null}
      <button
        type="button"
        onClick={onRetry}
        aria-label="Retry"
        className="flex items-center gap-1 rounded p-1 text-xs text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:text-zinc-500 dark:hover:bg-zinc-900 dark:hover:text-zinc-200"
      >
        <RotateCcw className="size-3" aria-hidden />
        Retry
      </button>
    </div>
  );
}

function AssistantBubble({
  turn,
  onCitationClick,
  onRetry,
}: {
  turn: AssistantTurn;
  onCitationClick: (citation: Citation) => void;
  onRetry: () => void;
}) {
  return (
    // Full column width, not 95% of it: the column itself is now the measure
    // (see READING_COLUMN), so an extra inset here only produced an
    // asymmetric right margin that made answers look off-centre.
    <div className="group w-full space-y-2">
      {turn.degradations.length > 0 ? (
        <DegradationBanner degradations={turn.degradations} />
      ) : null}

      {turn.error ? <ErrorCard error={turn.error} /> : null}
      {turn.abstain ? <AbstainCard abstain={turn.abstain} /> : null}

      {turn.content ? (
        <div>
          <AnswerBody
            text={turn.content}
            citations={turn.citations}
            streaming={false}
            onCitationClick={onCitationClick}
          />
          {turn.stopped ? (
            <p className="mt-2 text-xs italic text-zinc-500 dark:text-zinc-400">
              Stopped. This answer is partial.
            </p>
          ) : null}
        </div>
      ) : null}

      <SourcesFooter
        citations={turn.citations}
        onCitationClick={onCitationClick}
      />

      <MessageToolbar content={turn.content} onRetry={onRetry} />
    </div>
  );
}

// -------------------------------------------------------- workspace recents

/**
 * The workspace-home"Recents"list - the chats inside this workspace, shown
 * once composer + this list replace the message area entirely (no
 * conversation selected yet). Reuses the exact query key the sidebar's own
 * expanded-row conversation list uses, so opening this costs nothing extra
 * once the sidebar has already fetched it (and vice versa).
 */
function WorkspaceRecents({
  workspaceId,
  onOpen,
}: {
  workspaceId: string;
  onOpen: (conversationId: string) => void;
}) {
  const getToken = useSessionToken();
  const conversationsQuery = useQuery({
    queryKey: conversationsKey(workspaceId),
    queryFn: async () => api.listConversations(workspaceId, await getToken()),
  });

  const conversations = conversationsQuery.data ?? [];
  if (conversations.length === 0) return null;

  return (
    <div className="mt-8 w-full text-left">
      <p className="px-1 pb-1.5 text-xs font-medium text-zinc-500 dark:text-zinc-400">
        Recents
      </p>
      <div className="space-y-0.5">
        {conversations.map((conversation) => (
          <button
            key={conversation.id}
            type="button"
            onClick={() => onOpen(conversation.id)}
            className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-900"
          >
            <MessageSquare
              className="size-3.5 shrink-0 text-zinc-400 dark:text-zinc-500"
              aria-hidden
            />
            <span className="min-w-0 flex-1 truncate font-medium text-zinc-800 dark:text-zinc-100">
              {conversationLabel(conversation)}
            </span>
            <span className="shrink-0 text-xs text-zinc-400 dark:text-zinc-500">
              {formatRelativeDay(conversation.created_at)}
            </span>
          </button>
        ))}
      </div>
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
  onOpenConversation,
}: ChatPaneProps) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [showTrace, setShowTrace] = useState(false);

  const turnCounter = useRef(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const stickToBottom = useRef(true);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const getToken = useSessionToken();

  // Grows with the draft instead of offering a manual drag handle - height is
  // measured off `scrollHeight` on every keystroke and capped at
  // `COMPOSER_MAX_HEIGHT`, past which the textarea's own `overflow-y-auto`
  // takes over rather than the box continuing to grow. Collapsed to"0px"// (not"auto") before measuring: once the box has scrolled internally at
  // max height, resetting to"auto"can still report the pre-shrink
  // `scrollHeight` on the next character deleted, so the box never shrinks
  // back down - collapsing all the way first forces a real remeasure. A
  // layout effect, not a plain effect, so the resize lands before the browser
  // paints rather than one frame late.
  useLayoutEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.style.height = "0px";
    node.style.height = `${Math.min(node.scrollHeight, COMPOSER_MAX_HEIGHT)}px`;
  }, [draft]);

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

  // The server only mints an id on a brand-new conversation's first turn - the
  // sidebar has nothing to list or highlight until this fires.
  const reportedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!liveConversationId || liveConversationId === reportedRef.current)
      return;
    reportedRef.current = liveConversationId;
    onConversationStarted?.(liveConversationId);
  }, [liveConversationId, onConversationStarted]);

  /**
   * Reset the visible turn list the instant `conversationId` changes, during
   * render rather than in an effect - the pattern React's own docs recommend
   * for"adjust state when a prop changes"("You Might Not Need an Effect"),
   * and the only one that satisfies `react-hooks/set-state-in-effect`: that
   * rule exists because a synchronous reset inside an effect runs one paint
   * late, so old turns flash before the empty list does.
   *
   * Compared against `liveConversationId`, not just the previous prop value:
   * the parent echoes this pane's own freshly-minted id back down (to
   * highlight it in the sidebar), and without this comparison that echo would
   * look identical to"the user switched conversations"and wipe the very
   * turns that streamed the id into existence.
   *
   * That echo comparison only makes sense for a *non-null* id, though - you
   * cannot"echo"into starting a new chat. `liveConversationId` is `null`
   * until this pane's own hook actually mints one, which never happens for a
   * conversation that was only ever loaded from history (opened, not typed
   * into) - so navigating from such a conversation to"New chat"produces
   * `conversationId === null === liveConversationId` by pure coincidence, not
   * an echo, and without the explicit null check below that coincidence was
   * read as"nothing changed,"leaving the old conversation's turns on
   * screen after the sidebar's New chat button had already navigated away.
   */
  const [historyForId, setHistoryForId] = useState(conversationId);
  if (conversationId !== historyForId) {
    setHistoryForId(conversationId);
    if (conversationId === null || conversationId !== liveConversationId) {
      setTurns([]);
      setLoadingHistory(conversationId !== null);
    }
  }

  // `liveConversationId` read through a ref, not as a reactive dependency
  // below - it must be current *without* being a trigger. It transitions
  // null -> newId the instant `turn.start` arrives, which is well before the
  // parent's echo of that id reaches this component as the `conversationId`
  // prop (that round-trips through `onConversationStarted` -> `router.replace`
  // -> a fresh render). If this effect depended on `liveConversationId`
  // directly, that transition alone re-ran it - with the `conversationId`
  // prop still at its old value - so the echo guard below saw a real id on
  // one side and stale `null` on the other, missed the match, and called
  // `reset()` on a stream that was only a few hundred milliseconds old. That
  // aborted the fetch the instant every single first turn started (confirmed
  // via a direct repro: the `POST /chat` request completes fine standalone,
  // but fails client-side with `net::ERR_ABORTED` when run through the UI),
  // which is why a chat's first answer never arrived - not a backend issue at
  // all in the end.
  const liveConversationIdRef = useRef(liveConversationId);
  useEffect(() => {
    liveConversationIdRef.current = liveConversationId;
  }, [liveConversationId]);

  // The genuine side effects of a real switch - aborting whatever the live
  // stream hook was doing, and fetching the newly-selected conversation's
  // history - stay in an effect. Guarded the same way as the render-time
  // reset above, so the self-authored echo does not abort its own stream.
  // Fires only on an actual `conversationId` (URL) change - see the ref note
  // above for why `liveConversationId` itself must stay out of this array.
  useEffect(() => {
    if (
      conversationId !== null &&
      conversationId === liveConversationIdRef.current
    )
      return;
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
        // raced a slow network - either way, an empty pane is the honest
        // fallback rather than a crash.
        if (!cancelled) setTurns([]);
      } finally {
        if (!cancelled) setLoadingHistory(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [conversationId, getToken, reset]);

  // Follow the stream, but stop following the moment the user scrolls up to
  // re-read something - yanking them back to the bottom mid-sentence is worse
  // than losing sight of the newest token.
  const onScroll = useCallback(() => {
    const node = scrollRef.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    const atBottom = distance < 64;
    stickToBottom.current = atBottom;
    setShowScrollToBottom(!atBottom);
  }, []);

  useEffect(() => {
    if (!stickToBottom.current) return;
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [turns, answer, stages, degradations]);

  const scrollToBottom = useCallback(() => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    stickToBottom.current = true;
    setShowScrollToBottom(false);
  }, []);

  /**
   * Shared by a fresh send and a retry - `replaceTurnId` absent appends a new
   * assistant turn (today's only path, before retry existed); present, it
   * splices the result into that existing slot instead. Extracted from what
   * used to be the tail of `submit()` itself so retry does not re-implement
   * the same 4-way `result.kind` switch.
   */
  const runTurn = useCallback(
    async (text: string, replaceTurnId?: string) => {
      const result = await send(text);

      let assistantTurnId = replaceTurnId;
      if (!assistantTurnId) {
        turnCounter.current += 1;
        assistantTurnId = `a${turnCounter.current}`;
      }
      const id = assistantTurnId;

      // Commit and clear in one continuation so the live view and the
      // committed message never both render - React batches these into a
      // single paint.
      setTurns((prev) => {
        const base: AssistantTurn = {
          id,
          role: "assistant",
          content: "",
          citations: [],
          degradations: [],
          abstain: null,
          error: null,
          stopped: false,
        };

        let next: AssistantTurn | null;
        switch (result.kind) {
          case "answer":
            next = {
              ...base,
              content: result.answer,
              citations: result.citations,
              degradations: result.degradations,
            };
            break;
          case "abstain":
            next = {
              ...base,
              abstain: result.abstain,
              degradations: result.degradations,
            };
            break;
          case "error":
            next = {
              ...base,
              content: result.answer,
              error: result.error,
              degradations: result.degradations,
            };
            break;
          case "cancelled":
            // Nothing arrived before the stop - no empty bubble to show for it,
            // and nothing to replace a retried turn with either.
            next =
              !result.answer && result.degradations.length === 0
                ? null
                : {
                    ...base,
                    content: result.answer,
                    degradations: result.degradations,
                    stopped: true,
                  };
            break;
        }

        if (!next) return prev;
        if (replaceTurnId) {
          return prev.map((turn) => (turn.id === replaceTurnId ? next! : turn));
        }
        return [...prev, next];
      });
      reset();
      // Sending via the button moves focus to the button; a chat surface should
      // leave you ready to type the next question, not hunting for the box.
      textareaRef.current?.focus();
    },
    [send, reset],
  );

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

    await runTurn(text);
  }, [draft, isStreaming, runTurn]);

  // One user turn can precede several assistant turns only in the sense that
  // each assistant turn has exactly one preceding user turn - walked once per
  // `turns` change, not recomputed per row.
  const precedingUserContent = useMemo(() => {
    const map: Record<string, string> = {};
    let lastUserContent: string | null = null;
    for (const turn of turns) {
      if (turn.role === "user") lastUserContent = turn.content;
      else if (lastUserContent !== null) map[turn.id] = lastUserContent;
    }
    return map;
  }, [turns]);

  const retry = useCallback(
    (assistantTurnId: string) => {
      if (isStreaming) return;
      const text = precedingUserContent[assistantTurnId];
      if (!text) return;
      setShowTrace(false);
      stickToBottom.current = true;
      void runTurn(text, assistantTurnId);
    },
    [isStreaming, precedingUserContent, runTurn],
  );

  // `retry` above replaces an *existing* assistant turn, keyed on its id -
  // there is none here, since the whole point of `UnansweredCard` is a user
  // turn with nothing after it. This appends a fresh assistant turn instead,
  // same as a normal `submit()` minus the (already-persisted) user bubble.
  const retryUnanswered = useCallback(() => {
    if (isStreaming || turns.length === 0) return;
    const last = turns[turns.length - 1];
    if (last.role !== "user") return;
    setShowTrace(false);
    stickToBottom.current = true;
    void runTurn(last.content);
  }, [isStreaming, turns, runTurn]);

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter sends; Shift+Enter is a newline. IME composition must not send -
      // committing a candidate with Enter would otherwise fire the turn.
      if (
        event.key === "Enter" &&
        !event.shiftKey &&
        !event.nativeEvent.isComposing
      ) {
        event.preventDefault();
        void submit();
      }
    },
    [submit],
  );

  const showLiveTurn = isStreaming || answer.length > 0;
  // The workspace-home state: composer + Recents replace the message area
  // entirely, matching the reference's project-home screen - there is
  // nothing to scroll yet, so the scope banner and scroll container (both
  // about an in-progress or past conversation) don't apply either.
  const isHome =
    conversationId === null &&
    turns.length === 0 &&
    !showLiveTurn &&
    !loadingHistory;

  // One pill-shaped card, not a bordered textarea beside a separate labelled
  // button - the textarea itself carries no border or ring (the card supplies
  // both, via `focus-within`), and the send/stop control is a circular
  // icon-only button anchored bottom-right inside the same card. Used
  // identically for the workspace-home composer and the active-chat one, so
  // the two never drift into two different chrome treatments of "the input."
  const composerRow = (
    <div
      className={cn(
        "flex flex-col gap-1.5 rounded-3xl border border-zinc-200 bg-white p-2.5 pl-4",
        "focus-within:border-accent-400 dark:border-zinc-700 dark:bg-zinc-900 dark:focus-within:border-accent-500",
      )}
    >
      <textarea
        ref={textareaRef}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={onKeyDown}
        rows={1}
        placeholder="Ask a question about your documents..."
        aria-label="Message"
        // `shadow-none!`, not just `outline-none`: the global focus ring in
        // globals.css paints via `box-shadow`, and it's plain unlayered CSS,
        // so a normal-weight utility can't outrank it - only `!important`
        // can. Needed here specifically because a `<textarea>` counts as
        // `:focus-visible` on a plain mouse click, not just keyboard
        // navigation (unlike `<button>`/`<a>`), so without this the ring
        // this card deliberately opts out of would still show on click.
        className={cn(
          "max-h-40 min-h-[1.75rem] w-full resize-none overflow-y-auto bg-transparent py-1 text-base",
          "text-zinc-900 placeholder:text-zinc-400 focus-visible:outline-none focus-visible:shadow-none!",
          "dark:text-zinc-100 dark:placeholder:text-zinc-500",
        )}
      />

      <div className="flex items-center justify-end">
        {isStreaming ? (
          <Button
            variant="default"
            size="icon"
            onClick={cancel}
            aria-label="Stop generating"
            className="rounded-full"
          >
            <Square className="size-3 fill-current" aria-hidden />
          </Button>
        ) : (
          <Button
            variant="accent"
            size="icon"
            onClick={() => void submit()}
            disabled={draft.trim().length === 0}
            aria-label="Send message"
            className="rounded-full"
          >
            <ArrowUp className="size-4" aria-hidden />
          </Button>
        )}
      </div>
    </div>
  );

  if (isHome) {
    return (
      <section className="flex min-h-0 flex-1 flex-col overflow-y-auto px-6 py-10">
        <div className={READING_COLUMN}>
          {composerRow}
          {workspaceId && onOpenConversation ? (
            <WorkspaceRecents
              workspaceId={workspaceId}
              onOpen={onOpenConversation}
            />
          ) : null}
        </div>
      </section>
    );
  }

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      {/* No scope ribbon. It used to sit here, stating"Searching N documents"above every conversation on the grounds that an answer drawn from 2 of
 40 documents is a different claim from one drawn from all of them -
 but as a permanent full-width band it was chrome on a reading
 surface, and the same fact is already legible where it is actually
 set: the documents panel's own selection checkboxes. Per-answer
 provenance is carried by the citations, which is the stronger signal
 anyway. */}
      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="h-full overflow-y-auto px-4 py-6"
        >
          {/* The scroller itself stays full-width, so the scrollbar rides the
 pane's own right edge rather than floating mid-page; only the
 content inside it is held to the reading column. */}
          <div className={cn(READING_COLUMN, "space-y-6")}>
            {loadingHistory ? (
              <p className="shimmer-text text-sm font-medium">
                Loading conversation...
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
                  onRetry={() => retry(turn.id)}
                />
              ),
            )}

            {/* Gated on `!showLiveTurn` and `!loadingHistory` so this only
                ever applies to a turn that is truly done and unanswered -
                never to the ordinary moment between the optimistic user
                bubble landing and the live view taking over, nor to history
                still loading in. */}
            {!loadingHistory &&
            !showLiveTurn &&
            turns.length > 0 &&
            turns[turns.length - 1].role === "user" ? (
              <UnansweredCard onRetry={retryUnanswered} />
            ) : null}

            {showLiveTurn ? (
              <div className="w-full space-y-2">
                {/* The shimmer fills the gap before any token has arrived - once the
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
                  <AnswerBody
                    text={answer}
                    citations={citations}
                    streaming={isStreaming}
                    onCitationClick={onCitationClick}
                  />
                ) : null}

                {!isStreaming ? (
                  <SourcesFooter
                    citations={citations}
                    onCitationClick={onCitationClick}
                  />
                ) : null}
              </div>
            ) : null}
          </div>
        </div>

        {showScrollToBottom ? (
          <button
            type="button"
            onClick={scrollToBottom}
            aria-label="Scroll to latest message"
            className="absolute bottom-3 left-1/2 flex size-9 -translate-x-1/2 items-center justify-center rounded-full border border-zinc-200 bg-white text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700"
          >
            <ArrowDown className="size-4" aria-hidden />
          </button>
        ) : null}
      </div>

      {/* No rule and no bar colour of its own: the composer card already reads
 as a distinct, lifted surface, and a full-width divider under a
 centred column drew a line the content did not follow. */}
      <div className="px-4 pb-4 pt-2">
        <div className={READING_COLUMN}>
          {composerRow}
          <p className="mt-2 text-center text-xs text-zinc-400 dark:text-zinc-500">
            Enter to send · Shift+Enter for a new line
          </p>
        </div>
      </div>
    </section>
  );
}
