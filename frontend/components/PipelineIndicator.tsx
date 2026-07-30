"use client";

/**
 * Live view of the LangGraph run: route -> rewrite -> retrieve -> rerank -> grade ->
 * generate.
 *
 * Two decisions drive the whole layout:
 *
 * - **Passes, not nodes.** Stages are grouped by `attempt`, so the corrective
 *   retry renders as a visibly second pass under its own heading. A list keyed
 *   on node alone would overwrite pass 0 with pass 1 and make the single most
 *   interesting thing the pipeline does - noticing its own retrieval was bad and
 *   redoing it - look like nothing happened.
 * - **Expected states read as expected.** `rerank: skipped_decisive` means the
 *   top result was decisive enough that spending a Cohere call was pointless.
 *   That is the system working well, so it is styled as an optimisation. Only
 *   `failed` gets a warning tint, and it always has a `degradation` beside it in
 *   the banner explaining what ran instead.
 */

import { Check, ChevronDown, Loader2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { RetrievalRecord, StageRecord } from "@/hooks/useChatStream";
import type { PipelineNode, PipelineStageDetail } from "@/lib/types";
import { cn } from "@/lib/utils";

const NODE_ORDER: PipelineNode[] = [
  "route",
  "rewrite",
  "retrieve",
  "rerank",
  "grade",
  "generate",
];

const NODE_LABEL: Record<PipelineNode, string> = {
  route: "Route",
  rewrite: "Rewrite",
  retrieve: "Retrieve",
  rerank: "Rerank",
  grade: "Grade",
  generate: "Generate",
};

export interface PipelineIndicatorProps {
  stages: StageRecord[];
  retrieval: RetrievalRecord[];
  className?: string;
}

type DetailTone = "neutral" | "good" | "warn";

interface StageDetailText {
  text: string;
  tone: DetailTone;
}

/** Turns `pipeline.stage.detail` into a sentence a non-author can read. */
function describeDetail(
  node: PipelineNode,
  detail: PipelineStageDetail,
): StageDetailText | null {
  switch (node) {
    case "route": {
      if (!detail.route) return null;
      if (detail.route === "history") {
        return { text: "answered from conversation history", tone: "neutral" };
      }
      if (detail.route === "refuse") {
        return { text: "out of scope - declined", tone: "neutral" };
      }
      return { text: "searching your documents", tone: "neutral" };
    }

    case "rewrite": {
      if (detail.rewritten === undefined) return null;
      return detail.rewritten
        ? { text: "question rewritten for search", tone: "neutral" }
        : // Not a failure: the raw question was already a good query, so the
          // second Qdrant call is skipped outright.
          { text: "question used as written", tone: "neutral" };
    }

    case "rerank": {
      const margin =
        detail.margin === undefined || detail.margin === null
          ? ""
          : ` (margin ${detail.margin.toFixed(2)})`;
      switch (detail.status) {
        case "applied":
          return { text: `reranked${margin}`, tone: "good" };
        case "skipped_decisive":
          return {
            text: `skipped - the top result was already decisive${margin}`,
            tone: "good",
          };
        case "cached":
          return { text: "served from the rerank cache", tone: "good" };
        case "failed":
          return {
            text: "unavailable - ranked by fused score instead",
            tone: "warn",
          };
        default:
          return detail.status
            ? { text: detail.status, tone: "neutral" }
            : null;
      }
    }

    case "grade": {
      const relevance =
        detail.relevance === undefined
          ? ""
          : `relevance ${detail.relevance.toFixed(2)}`;
      switch (detail.decision) {
        case "pass":
          return {
            text: ["good enough to answer", relevance]
              .filter(Boolean)
              .join(" · "),
            tone: "good",
          };
        case "retry":
          return {
            text: ["below threshold - retrying retrieval", relevance]
              .filter(Boolean)
              .join(" · "),
            tone: "warn",
          };
        case "abstain":
          return {
            text: ["still below threshold - abstaining", relevance]
              .filter(Boolean)
              .join(" · "),
            tone: "warn",
          };
        default:
          return relevance ? { text: relevance, tone: "neutral" } : null;
      }
    }

    default:
      return detail.status ? { text: detail.status, tone: "neutral" } : null;
  }
}

const TONE_CLASS: Record<DetailTone, string> = {
  neutral: "text-zinc-500 dark:text-zinc-400",
  good: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-400",
};

/**
 * Which nodes to draw for a pass, in canonical order.
 *
 * Pass 0 projects the full path so the user sees where the run is going, except
 * when `route` short-circuited to `history`/`refuse` - then the retrieval nodes
 * will never fire and drawing them as "pending" would be a lie that never
 * resolves. Later passes only draw what actually arrived, because the retry
 * re-runs a subset.
 */
function nodesForPass(
  attempt: number,
  seen: StageRecord[],
): PipelineNode[] {
  const seenNodes = new Set(seen.map((s) => s.node));
  if (attempt !== 0) {
    return NODE_ORDER.filter((node) => seenNodes.has(node));
  }
  const route = seen.find((s) => s.node === "route")?.detail.route;
  if (route === "history" || route === "refuse") {
    return NODE_ORDER.filter(
      (node) => seenNodes.has(node) || node === "route",
    );
  }
  return NODE_ORDER;
}

function RetrievalSummary({ record }: { record: RetrievalRecord }) {
  const sourceCount = record.documents.length;
  return (
    <div className="mt-1 space-y-1">
      <p className="text-zinc-500 dark:text-zinc-400">
        searching {sourceCount} {sourceCount === 1 ? "source" : "sources"} -> found{" "}
        {record.candidateCount}{" "}
        {record.candidateCount === 1 ? "passage" : "passages"}
      </p>
      {sourceCount > 0 ? (
        <ul className="flex flex-wrap gap-1">
          {record.documents.map((doc) => (
            <li key={doc.doc_id} className="min-w-0">
              <Badge
                variant="outline"
                className="max-w-[16rem]"
                title={`${doc.filename} - ${doc.hits} matching ${doc.hits === 1 ? "passage" : "passages"}`}
              >
                <span className="truncate">{doc.filename}</span>
                <span className="shrink-0 tabular-nums text-zinc-400 dark:text-zinc-500">
                  {doc.hits}
                </span>
              </Badge>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function StageRow({
  node,
  record,
  retrieval,
}: {
  node: PipelineNode;
  record: StageRecord | undefined;
  retrieval: RetrievalRecord | undefined;
}) {
  const status = record?.state ?? "pending";
  const detail = record ? describeDetail(node, record.detail) : null;

  return (
    <li className="flex items-start gap-2">
      <span className="mt-[3px] flex size-3.5 shrink-0 items-center justify-center">
        {status === "done" ? (
          <Check className="size-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden />
        ) : status === "started" ? (
          <Loader2
            className="size-3.5 animate-spin text-accent-600 dark:text-accent-400"
            aria-hidden
          />
        ) : (
          <span className="size-1.5 rounded-full bg-zinc-300 dark:bg-zinc-600" />
        )}
      </span>

      <div className="min-w-0 flex-1">
        <span
          className={cn(
            "font-medium",
            status === "pending"
              ? "text-zinc-400 dark:text-zinc-500"
              : "text-zinc-700 dark:text-zinc-200",
          )}
        >
          {NODE_LABEL[node]}
        </span>
        {detail ? (
          <span className={cn("ml-2", TONE_CLASS[detail.tone])}>
            {detail.text}
          </span>
        ) : null}
        {node === "retrieve" && retrieval ? (
          <RetrievalSummary record={retrieval} />
        ) : null}
      </div>
    </li>
  );
}

const STEP_LABEL: Record<PipelineNode, string> = {
  route: "Reading your question",
  rewrite: "Refining the search",
  retrieve: "Searching your documents",
  rerank: "Ranking the best passages",
  grade: "Checking relevance",
  generate: "Writing your answer",
};

export interface PipelineShimmerProps {
  stages: StageRecord[];
  expanded: boolean;
  onToggle: () => void;
  className?: string;
}

/**
 * One shimmering line naming the current step, in place of a blank gap while
 * the answer is still being assembled - the same shape as Claude's own
 * in-progress indicator ("Searching...", "Thinking...").
 *
 * `stages` is appended to on a node's *first* sighting and patched in place
 * after that (see the reducer in `useChatStream`), so the last entry is always
 * the most recently introduced node - which, since the pipeline runs each node
 * strictly after the last, is also the one presently doing something. That
 * makes "read the last element" a correct way to find "what's happening right
 * now" without tracking it separately.
 *
 * This is the default view; `PipelineIndicator`'s full per-node trace is one
 * click away for anyone who wants to see the retry, the margins, the actual
 * relevance score - the shimmer alone answers "is it still working", not "what
 * did it decide", and some readers want the second question too.
 */
export function PipelineShimmer({
  stages,
  expanded,
  onToggle,
  className,
}: PipelineShimmerProps) {
  const last = stages[stages.length - 1];
  const label = last ? (STEP_LABEL[last.node] ?? "Working") : "Starting";
  const isRetry = (last?.attempt ?? 0) > 0;

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      className={cn(
        "flex items-center gap-1.5 text-left text-sm font-medium",
        className,
      )}
    >
      <span className="shimmer-text">
        {label}
        {isRetry ? " - second pass" : ""}...
      </span>
      <ChevronDown
        className={cn(
          "size-3.5 shrink-0 text-zinc-400 transition-transform dark:text-zinc-500",
          expanded && "rotate-180",
        )}
        aria-hidden
      />
    </button>
  );
}

export function PipelineIndicator({
  stages,
  retrieval,
  className,
}: PipelineIndicatorProps) {
  // Group by attempt. Attempts are 0 or 1 today, but nothing here assumes that.
  const attempts = Array.from(new Set(stages.map((s) => s.attempt))).sort(
    (a, b) => a - b,
  );
  const showPassHeadings = attempts.length > 1;

  if (stages.length === 0) {
    return (
      <div
        className={cn(
          "flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400",
          className,
        )}
      >
        <Loader2 className="size-3.5 animate-spin" aria-hidden />
        <span>Starting...</span>
      </div>
    );
  }

  return (
    <div
      className={cn(
        "rounded-lg border border-zinc-200 bg-zinc-50/70 p-3 text-xs dark:border-zinc-800 dark:bg-zinc-900/50",
        className,
      )}
      aria-live="polite"
    >
      {attempts.map((attempt) => {
        const passStages = stages.filter((s) => s.attempt === attempt);
        const nodes = nodesForPass(attempt, passStages);
        return (
          <div key={attempt} className={attempt === attempts[0] ? "" : "mt-3"}>
            {showPassHeadings ? (
              <p className="mb-1.5 font-semibold uppercase tracking-wide text-[0.65rem] text-zinc-500 dark:text-zinc-400">
                {attempt === 0
                  ? "First pass"
                  : `Corrective retry · pass ${attempt + 1}`}
              </p>
            ) : null}
            <ul className="space-y-1.5">
              {nodes.map((node) => (
                <StageRow
                  key={`${node}:${attempt}`}
                  node={node}
                  record={passStages.find((s) => s.node === node)}
                  retrieval={
                    node === "retrieve"
                      ? retrieval.find((r) => r.attempt === attempt)
                      : undefined
                  }
                />
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}
