"use client";

/**
 * An inline `[n]` citation marker, rendered as a clickable chip.
 *
 * The whole point of this component is that `verified` has **three** states and
 * they must look like three states (invariant I2):
 *
 * - `null`  - not yet checked, or the judge failed. This is what every citation
 *   looks like at `answer.complete`, and what it stays as forever if
 *   verification never arrives. Neutral and quiet.
 * - `true`  - checked and supported. A small check, deliberately subtle: the
 *   common case should not shout.
 * - `false` - checked and **not** supported by its source. Visibly flagged.
 *
 * Collapsing `null` into `false` would put a warning on every citation of every
 * answer whose verification was slow, reporting a finding nobody made. So the
 * pending state uses a dashed neutral outline and the unsupported state uses a
 * filled amber chip with an icon - different at a glance, not just different in
 * hue, which also keeps them apart for a colour-blind reader.
 */

import { Check, TriangleAlert } from "lucide-react";
import { memo } from "react";

import type { Citation } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface CitationChipProps {
  citation: Citation;
  onClick: (citation: Citation) => void;
  className?: string;
}

/** Human-readable provenance for the tooltip: file, then section, then page. */
function describeSource(citation: Citation): string {
  const parts: string[] = [citation.filename];
  if (citation.section) parts.push(citation.section);
  if (citation.page !== null) parts.push(`page ${citation.page}`);
  return parts.join(" · ");
}

function describeVerification(verified: boolean | null): string {
  switch (verified) {
    case true:
      return "Verified against the source passage.";
    case false:
      return "Not supported by the cited passage. Check the source before relying on it.";
    default:
      return "Not yet checked. Verification runs off the request path and may not have finished.";
  }
}

function CitationChipImpl({ citation, onClick, className }: CitationChipProps) {
  const { verified } = citation;

  return (
    <button
      type="button"
      onClick={() => onClick(citation)}
      title={`[${citation.marker}] ${describeSource(citation)}\n${describeVerification(verified)}`}
      aria-label={`Citation ${citation.marker}: ${describeSource(citation)}. ${describeVerification(verified)}`}
      className={cn(
        "mx-px inline-flex translate-y-[-1px] items-center gap-0.5 rounded-md px-1.5 py-0 align-middle",
        "text-[0.7rem] font-medium leading-[1.4] tabular-nums transition-colors",
        verified === true &&
          "border border-emerald-300 bg-emerald-50 text-emerald-800 hover:bg-emerald-100 dark:border-emerald-800 dark:bg-emerald-950/60 dark:text-emerald-300 dark:hover:bg-emerald-900/60",
        verified === false &&
          "border border-amber-400 bg-amber-100 text-amber-900 hover:bg-amber-200 dark:border-amber-600 dark:bg-amber-950/70 dark:text-amber-200 dark:hover:bg-amber-900/70",
        // Dashed = provisional. Nothing has been asserted about this one yet.
        verified === null &&
          "border border-dashed border-zinc-400 bg-zinc-100 text-zinc-600 hover:bg-zinc-200 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700",
        className,
      )}
    >
      <span>[{citation.marker}]</span>
      {verified === true ? (
        <Check className="size-3 shrink-0" aria-hidden />
      ) : null}
      {verified === false ? (
        <TriangleAlert className="size-3 shrink-0" aria-hidden />
      ) : null}
    </button>
  );
}

/**
 * Memoised so a `verification.complete` patch re-renders only the chips whose
 * verdict actually changed. The hook preserves object identity for untouched
 * citations precisely so this comparison can succeed.
 */
export const CitationChip = memo(CitationChipImpl);
