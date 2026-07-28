/**
 * The wire contract, mirrored from the backend.
 *
 * These types are the frontend half of Retrieval Pipeline Contract §2 and §8.
 * They exist so the two sides cannot drift: if the backend adds a field, this
 * file is where the frontend learns about it, and TypeScript fails the build
 * rather than a component silently rendering `undefined`.
 *
 * Two invariants show up directly in these types and both matter:
 *
 * - `verified: boolean | null` — **null is not false.** A citation that has not
 *   been checked, or whose judge failed, is `null`. Rendering that as
 *   "unsupported" would report a finding nobody made (I2).
 * - `attempt: 0 | 1` on `pipeline.stage` — stage events **repeat on the
 *   corrective retry**. A UI keyed on `node` alone renders the retry as a
 *   duplicate; key on `(node, attempt)`.
 */

// ---------------------------------------------------------------- documents

export type DocumentStatus =
  | "queued"
  | "parsing"
  | "chunking"
  | "embedding"
  | "ready"
  | "failed";

/** What G3 removed at ingest — surfaced so sanitisation is visible, not silent. */
export interface SanitizationReport {
  removed_spans: number;
  kinds: Record<string, number>;
}

/**
 * Extraction quality, shown in the document manager.
 *
 * `pages_flagged` above `pages_escalated` means the per-document escalation cap
 * was hit and those pages got Tier-1 parsing only. A parser that fails loudly is
 * more useful than one that fails convincingly.
 */
export interface ExtractionSignal {
  pages_total: number;
  pages_escalated: number;
  pages_flagged: number;
  tables_recovered: number;
  figures_described: number;
  confidence: number;
  sanitization?: SanitizationReport;
}

export interface DocumentSummary {
  id: string;
  filename: string;
  mime: string;
  status: DocumentStatus;
  error: string | null;
  chunk_count: number;
  sanitization_report: SanitizationReport | Record<string, never>;
  extraction: ExtractionSignal | Record<string, never>;
  workspace_id: string | null;
  created_at: string | null;
}

/** `normalized_text` is THE offset referent — every citation indexes into it. */
export interface DocumentDetail extends DocumentSummary {
  normalized_text: string;
}

// ---------------------------------------------------------------- citations

export interface Citation {
  marker: number;
  chunk_id: string;
  doc_id: string;
  filename: string;
  section: string | null;
  page: number | null;
  /** Offsets into the document's `normalized_text`. Drives the source-pane highlight. */
  char_start: number;
  char_end: number;
  /** null = not yet checked, or the judge failed. Never render this as "false". */
  verified: boolean | null;
}

export type DegradationStage =
  | "route"
  | "rewrite"
  | "retrieve"
  | "rerank"
  | "generate"
  | "verify"
  | "parse";

export type DegradationReason =
  | "timeout"
  | "rate_limited"
  | "parse_error"
  | "quota_exhausted"
  | "unavailable"
  | "cap_reached";

/** Every fallback emits one of these. A degraded path is never silent (I1). */
export interface Degradation {
  stage: DegradationStage;
  reason: DegradationReason;
  fallback: string;
  detail: string | null;
}

// -------------------------------------------------------------- SSE frames

/** Every frame carries these; `seq` is monotonic per stream. */
export interface FrameEnvelope {
  seq: number;
  ts: string;
}

export type PipelineNode =
  | "route"
  | "rewrite"
  | "retrieve"
  | "rerank"
  | "grade"
  | "generate";

export interface PipelineStageDetail {
  route?: string;
  rewritten?: boolean;
  status?: string;
  margin?: number | null;
  relevance?: number;
  decision?: string;
}

export interface TurnStartEvent extends FrameEnvelope {
  turn_id: string;
  message_id: string;
  /**
   * The conversation this turn belongs to — minted by the server on a first
   * turn. Send it back on the next turn to thread conversation memory; without
   * it every turn starts a fresh conversation and follow-ups lose their history.
   */
  conversation_id: string;
}

export interface PipelineStageEvent extends FrameEnvelope {
  node: PipelineNode;
  state: "started" | "done";
  /** 0 or 1. Key your UI on (node, attempt) — see the file header. */
  attempt: number;
  detail?: PipelineStageDetail;
}

export interface RetrievalResultEvent extends FrameEnvelope {
  candidate_count: number;
  attempt: number;
  documents: { doc_id: string; filename: string; hits: number }[];
}

export interface AnswerDeltaEvent extends FrameEnvelope {
  text: string;
}

export interface AnswerCompleteEvent extends FrameEnvelope {
  message_id: string;
  /** Citations arrive with `verified: null` and upgrade later, or never. */
  citations: Citation[];
}

export interface AbstainEvent extends FrameEnvelope {
  message_id: string;
  reason: string;
  searched: { doc_count: number; top_score: number };
}

/** Arrives after `answer.complete` — **or never**. Never block on it. */
export interface VerificationCompleteEvent extends FrameEnvelope {
  message_id: string;
  citations: { marker: number; verified: boolean | null }[];
  coverage: number | null;
}

export type DegradationEvent = FrameEnvelope & Degradation;

export interface ErrorEvent extends FrameEnvelope {
  code: string;
  message: string;
  request_id: string;
}

/** Discriminated on the SSE `event:` name, not on a field in the payload. */
export type ChatEvent =
  | { type: "turn.start"; data: TurnStartEvent }
  | { type: "pipeline.stage"; data: PipelineStageEvent }
  | { type: "retrieval.result"; data: RetrievalResultEvent }
  | { type: "answer.delta"; data: AnswerDeltaEvent }
  | { type: "answer.complete"; data: AnswerCompleteEvent }
  | { type: "abstain"; data: AbstainEvent }
  | { type: "verification.complete"; data: VerificationCompleteEvent }
  | { type: "degradation"; data: DegradationEvent }
  | { type: "error"; data: ErrorEvent };

// --------------------------------------------------------- ingest stream

export interface DocumentStatusEvent extends FrameEnvelope {
  document_id: string;
  status: DocumentStatus;
  progress?: { done: number; total: number; unit: string };
}

export interface DocumentCompleteEvent extends FrameEnvelope {
  document_id: string;
  chunk_count: number;
  extraction: ExtractionSignal;
}

export interface DocumentErrorEvent extends FrameEnvelope {
  document_id: string;
  code: string;
  message: string;
}

export type IngestEvent =
  | { type: "document.status"; data: DocumentStatusEvent }
  | { type: "document.complete"; data: DocumentCompleteEvent }
  | { type: "document.error"; data: DocumentErrorEvent };

// -------------------------------------------------------------- messages

/**
 * A citation as it comes back from `GET /conversations/{id}` — the same shape
 * a live turn's `Citation` has, plus the retrieval-trace columns
 * (`rank`/`fused_score`/`rerank_score`) that only `message_citations` carries.
 * Reloading a past conversation must produce citation chips that click through
 * to a source exactly like a fresh answer's do, which is why this is a
 * superset of `Citation` rather than a separate, thinner shape.
 */
export interface PersistedCitation extends Citation {
  rank: number;
  fused_score: number | null;
  rerank_score: number | null;
}

export interface PersistedMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  degradations: Degradation[];
  pipeline: Record<string, unknown>;
  created_at: string | null;
  citations: PersistedCitation[];
}

export interface Conversation {
  id: string;
  title: string | null;
  workspace_id: string | null;
  created_at: string | null;
}

// ----------------------------------------------------------- workspaces

/**
 * A named group of documents that many conversations share — upload once,
 * open as many chats against it as you like without re-attaching files.
 */
export interface Workspace {
  id: string;
  name: string;
  document_count: number;
  conversation_count: number;
  created_at: string | null;
  updated_at: string | null;
}

// ------------------------------------------------------------ error shape

/** The single envelope every error path returns (§6). */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    detail: Record<string, unknown>;
    request_id: string;
  };
}
