"use client";

/**
 * Invariant I1, rendered.
 *
 * Every fallback the pipeline takes appends a `Degradation` and emits an SSE
 * event, so that a degraded answer is never indistinguishable from a healthy
 * one. This component is the last hop of that guarantee - if it is missing,
 * dismissible, or ignorable, the invariant is broken in the only place the user
 * can see it.
 *
 * So: not dismissible, and not styled as an error. The answer above it is real
 * and usable; something in the path fell back to a second-best route and the
 * user is entitled to know which. Amber, not red - red would train people to
 * read "a fallback ran" as "this answer is broken", and they would start
 * discarding correct answers.
 *
 * The `reason` codes are backend vocabulary (`quota_exhausted`, `cap_reached`).
 * They are translated here into what actually changed about the answer, because
 * "quota_exhausted" tells a user nothing and "results are ordered by fused
 * ranking instead of reranking" tells them what to distrust.
 */

import { TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type {
  Degradation,
  DegradationReason,
  DegradationStage,
} from "@/lib/types";
import { cn } from "@/lib/utils";

export interface DegradationBannerProps {
  degradations: Degradation[];
  className?: string;
}

/**
 * Stage-specific copy, keyed `stage:reason`. The consequence for the answer
 * differs by stage even when the reason is identical - a rerank timeout changes
 * result ordering, a verify timeout changes what the citation chips can claim -
 * so the pairs get their own sentences rather than a generic template.
 */
const SPECIFIC_COPY: Partial<Record<`${DegradationStage}:${DegradationReason}`, string>> = {
  "rerank:quota_exhausted":
    "Reranking is unavailable for the rest of this deployment; results are ordered by fused retrieval score instead.",
  "rerank:rate_limited":
    "Reranking was rate-limited on this question; results are ordered by fused retrieval score instead.",
  "rerank:timeout":
    "Reranking did not answer in time; results are ordered by fused retrieval score instead.",
  "rerank:unavailable":
    "The reranking service could not be reached; results are ordered by fused retrieval score instead.",

  "rewrite:timeout":
    "Query rewriting did not answer in time; your question was searched exactly as you typed it.",
  "rewrite:parse_error":
    "Query rewriting returned something unreadable; your question was searched exactly as you typed it.",
  "rewrite:rate_limited":
    "Query rewriting was rate-limited; your question was searched exactly as you typed it.",
  "rewrite:unavailable":
    "Query rewriting was unavailable; your question was searched exactly as you typed it.",

  "route:timeout":
    "Routing did not answer in time, so the question was sent to document search by default.",
  "route:parse_error":
    "Routing returned something unreadable, so the question was sent to document search by default.",
  "route:unavailable":
    "Routing was unavailable, so the question was sent to document search by default.",

  "retrieve:timeout":
    "Part of the search timed out; the answer was built from the results that did come back.",
  "retrieve:unavailable":
    "One search path was unavailable; the answer was built from the results that did come back.",

  "generate:timeout":
    "The answer model was slow to respond; a fallback finished the answer.",
  "generate:rate_limited":
    "The answer model was rate-limited; a fallback finished the answer.",
  "generate:unavailable":
    "The primary answer model was unavailable; a fallback produced this answer.",

  // Verification is the one stage that fails toward *unknown* rather than
  // toward answering, so the copy has to say what the chips now mean.
  "verify:timeout":
    "Citation checking did not finish, so citations show as unchecked, not as unsupported.",
  "verify:unavailable":
    "Citation checking could not run, so citations show as unchecked, not as unsupported.",
  "verify:rate_limited":
    "Citation checking was rate-limited, so citations show as unchecked, not as unsupported.",
  "verify:quota_exhausted":
    "Citation checking is out of quota, so citations show as unchecked, not as unsupported.",

  "parse:cap_reached":
    "A document hit its page-escalation cap during ingest; some pages were read with basic text extraction only, so parts of them may be missing here.",
  "parse:parse_error":
    "Part of a document could not be parsed cleanly, so some of its content may be missing from this answer.",
  "parse:timeout":
    "Parsing timed out on part of a document, so some of its content may be missing from this answer.",
};

const STAGE_LABEL: Record<DegradationStage, string> = {
  route: "Routing",
  rewrite: "Query rewriting",
  retrieve: "Retrieval",
  rerank: "Reranking",
  generate: "Answer generation",
  verify: "Citation checking",
  parse: "Document parsing",
};

const GENERIC_COPY: Record<DegradationReason, string> = {
  timeout: "timed out, so a fallback ran in its place.",
  rate_limited: "was rate-limited, so a fallback ran in its place.",
  parse_error: "returned an unreadable response, so a fallback ran in its place.",
  quota_exhausted: "is out of quota, so a fallback ran in its place.",
  unavailable: "was unavailable, so a fallback ran in its place.",
  cap_reached: "hit a configured cap, so a fallback ran in its place.",
};

function explain(degradation: Degradation): string {
  const specific =
    SPECIFIC_COPY[`${degradation.stage}:${degradation.reason}` as const];
  if (specific) return specific;
  return `${STAGE_LABEL[degradation.stage]} ${GENERIC_COPY[degradation.reason]}`;
}

interface GroupedDegradation {
  key: string;
  degradation: Degradation;
  count: number;
}

/**
 * Identical degradations are collapsed with a count rather than dropped.
 *
 * The same fallback firing on both retrieval passes is one fact, not two, but
 * "×2" is still information - it says the retry hit the same wall.
 */
function group(degradations: Degradation[]): GroupedDegradation[] {
  const groups: GroupedDegradation[] = [];
  const index = new Map<string, number>();
  for (const degradation of degradations) {
    const key = [
      degradation.stage,
      degradation.reason,
      degradation.fallback,
      degradation.detail ?? "",
    ].join("|");
    const existing = index.get(key);
    if (existing === undefined) {
      index.set(key, groups.length);
      groups.push({ key, degradation, count: 1 });
    } else {
      groups[existing].count += 1;
    }
  }
  return groups;
}

export function DegradationBanner({
  degradations,
  className,
}: DegradationBannerProps) {
  if (degradations.length === 0) return null;

  const groups = group(degradations);

  return (
    <div
      // `status`, not `alert`: this is information about how the answer was
      // produced, not an interruption demanding attention.
      role="status"
      className={cn(
        "rounded-lg border border-amber-300 bg-amber-50 p-3 text-xs text-amber-900",
        "dark:border-amber-800/70 dark:bg-amber-950/40 dark:text-amber-100",
        className,
      )}
    >
      <div className="flex items-start gap-2">
        <TriangleAlert className="mt-px size-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="font-semibold">
            This answer is complete, but{" "}
            {groups.length === 1 ? "a fallback ran" : "some fallbacks ran"}.
          </p>

          <ul className="mt-1.5 space-y-1.5">
            {groups.map(({ key, degradation, count }) => (
              <li key={key} className="leading-snug">
                <span className="font-medium">
                  {STAGE_LABEL[degradation.stage]}
                  {count > 1 ? ` ×${count}` : ""}:
                </span>{" "}
                <span>{explain(degradation)}</span>
                <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.7rem] text-amber-700/90 dark:text-amber-300/80">
                  <Badge variant="warning" className="font-mono">
                    {degradation.fallback}
                  </Badge>
                  {degradation.detail ? <span>{degradation.detail}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
