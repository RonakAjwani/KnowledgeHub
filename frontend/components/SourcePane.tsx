"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useAuth } from "@clerk/nextjs";
import {
  Download,
  LoaderCircle,
  PanelRight,
  TriangleAlert,
  WandSparkles,
  X,
} from "lucide-react";

import { ApiError, api, triggerBrowserDownload } from "@/lib/api";
import { DocumentView } from "@/components/DocumentView";
import { fileKind } from "@/lib/fileKind";
import { CLERK_ENABLED, cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";

// ------------------------------------------------------------------- auth

type TokenGetter = () => Promise<string | null>;

function useClerkToken(): TokenGetter {
  const { getToken } = useAuth();
  return useCallback(() => getToken(), [getToken]);
}

function useNoToken(): TokenGetter {
  return useCallback(async () => null, []);
}

/**
 * Clerk's `useAuth` throws outside a `ClerkProvider`, and a build with no
 * publishable key mounts no provider at all (see `CLERK_ENABLED`). Resolving the
 * branch once at module scope keeps hook order fixed for the lifetime of the
 * process - a conditional call inside the component would not.
 */
const useSessionToken: () => TokenGetter = CLERK_ENABLED
  ? useClerkToken
  : useNoToken;

// ------------------------------------------------------- derived content

/**
 * The literal prefix `build_normalized_text` writes in front of a block whose
 * text a vision model produced rather than the document containing.
 */
const DERIVED_MARKER = "[AI-described figure] ";

interface Span {
  start: number;
  end: number;
}

/**
 * Locate the model-generated runs in `normalized_text`.
 *
 * This scans for a delimiter the builder itself wrote - it is not a search for
 * cited text. Citation positions come from offsets and only from offsets; see
 * `buildSegments`.
 *
 * A run ends at the block separator after it, or at the next marker if two
 * descriptions were emitted back to back. Stopping at the blank line keeps the
 * "not from the document" styling on the description rather than bleeding it
 * across the document's own prose.
 */
function findDerivedRuns(text: string): Span[] {
  const runs: Span[] = [];
  let cursor = 0;

  for (;;) {
    const start = text.indexOf(DERIVED_MARKER, cursor);
    if (start === -1) break;

    const after = start + DERIVED_MARKER.length;
    const blockEnd = text.indexOf("\n\n", after);
    const nextMarker = text.indexOf(DERIVED_MARKER, after);
    const stops = [blockEnd, nextMarker].filter((index) => index !== -1);
    const end = stops.length > 0 ? Math.min(...stops) : text.length;

    runs.push({ start, end });
    cursor = end;
  }

  return runs;
}

// ---------------------------------------------------------- segmentation

interface Segment extends Span {
  /** Inside a model-generated run. */
  derived: boolean;
  /** The marker itself, rendered as its own inline label. */
  marker: boolean;
  /** Inside the citation span being highlighted. */
  highlighted: boolean;
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high);
}

/**
 * Cut the document at every boundary that matters, then flag each piece.
 *
 * The highlight is applied purely by offset - `text.slice(start, end)` - because
 * the offsets are authoritative (I5: they index `normalized_text`). Searching
 * for the cited string instead would land on the wrong occurrence the first time
 * a phrase repeats, and would silently disagree with the verifier.
 *
 * A single pass over shared cut points is what lets the highlight and a derived
 * run overlap without either one clobbering the other.
 */
function buildSegments(text: string, highlight: Span | null): Segment[] {
  const runs = findDerivedRuns(text);

  const cuts = new Set<number>([0, text.length]);
  for (const run of runs) {
    cuts.add(run.start);
    cuts.add(Math.min(run.start + DERIVED_MARKER.length, run.end));
    cuts.add(run.end);
  }
  if (highlight) {
    cuts.add(highlight.start);
    cuts.add(highlight.end);
  }

  const points = [...cuts]
    .filter((point) => point >= 0 && point <= text.length)
    .sort((a, b) => a - b);

  const segments: Segment[] = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    const start = points[i];
    const end = points[i + 1];
    if (end <= start) continue;

    const run = runs.find((item) => item.start <= start && end <= item.end);
    segments.push({
      start,
      end,
      derived: run !== undefined,
      marker:
        run !== undefined && end <= run.start + DERIVED_MARKER.length,
      highlighted:
        highlight !== null && highlight.start <= start && end <= highlight.end,
    });
  }

  return segments;
}

// -------------------------------------------------------------- component

export interface SourcePaneProps {
  documentId: string | null;
  /** Citation offsets into this document's `normalized_text`. */
  highlight: { char_start: number; char_end: number } | null;
  /** Present when this pane is a slide-over rather than the permanent
   * "nothing open yet" placeholder - renders a close button in the header. */
  onClose?: () => void;
  className?: string;
}

export function SourcePane({
  documentId,
  highlight,
  onClose,
  className,
}: SourcePaneProps) {
  const getToken = useSessionToken();
  const tokenRef = useRef<TokenGetter>(getToken);
  useEffect(() => {
    tokenRef.current = getToken;
  }, [getToken]);

  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [view, setView] = useState<"original" | "text">("original");

  const documentQuery = useQuery({
    queryKey: ["document", documentId],
    queryFn: async () =>
      api.getDocument(documentId as string, await tokenRef.current()),
    enabled: documentId !== null,
    // `normalized_text` is built once and never mutated ("one builder, one
    // string"), so a fetched document cannot go stale while it is open.
    staleTime: 5 * 60_000,
  });

  const filename = documentQuery.data?.filename ?? null;

  // The uploaded bytes, fetched only for the formats that need them. Markdown
  // and plain text render from `normalized_text`, so pulling a blob for them
  // would cost a download to show the same characters.
  const needsBlob = documentQuery.data?.mime === "application/pdf";
  const blobQuery = useQuery({
    queryKey: ["document-blob", documentId],
    queryFn: async () => {
      const { blob } = await api.downloadDocumentBlob(
        documentId as string,
        await tokenRef.current(),
      );
      return blob.arrayBuffer();
    },
    enabled: documentId !== null && needsBlob && view === "original",
    staleTime: 5 * 60_000,
  });

  const download = useCallback(async () => {
    if (!documentId) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const { blob, filename: blobFilename } = await api.downloadDocumentBlob(
        documentId,
        await tokenRef.current(),
      );
      triggerBrowserDownload(blob, blobFilename ?? filename ?? "document");
    } catch (error) {
      setDownloadError(
        error instanceof ApiError ? `${error.message} (${error.code})` : "Download failed",
      );
    } finally {
      setDownloading(false);
    }
  }, [documentId, filename]);

  const text = documentQuery.data?.normalized_text ?? "";

  const requestedStart = highlight?.char_start ?? null;
  const requestedEnd = highlight?.char_end ?? null;

  /** Out of range means the citation belongs to a different build of this text. */
  const highlightOutOfRange =
    highlight !== null &&
    text.length > 0 &&
    (highlight.char_start < 0 ||
      highlight.char_end > text.length ||
      highlight.char_end <= highlight.char_start);

  const segments = useMemo(() => {
    if (!text) return [];
    const span =
      requestedStart === null || requestedEnd === null
        ? null
        : {
            start: clamp(requestedStart, 0, text.length),
            end: clamp(requestedEnd, 0, text.length),
          };
    return buildSegments(text, span && span.end > span.start ? span : null);
  }, [text, requestedStart, requestedEnd]);

  const hasDerived = useMemo(
    () => segments.some((segment) => segment.derived),
    [segments],
  );

  const firstHighlightIndex = segments.findIndex(
    (segment) => segment.highlighted,
  );
  const highlightRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const node = highlightRef.current;
    if (!node) return;
    node.scrollIntoView({ block: "center", behavior: "smooth" });
    // `text` is a dependency because a highlight can arrive before the document
    // has loaded; the scroll then has to wait for the node to exist.
  }, [documentId, requestedStart, requestedEnd, text]);

  if (documentId === null) {
    return (
      <Card className={cn("h-full min-h-0", className)}>
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
          <PanelRight className="size-6 text-zinc-300 dark:text-zinc-700" />
          <p className="text-sm font-medium text-zinc-600 dark:text-zinc-300">
            No document open
          </p>
          <p className="max-w-xs text-xs leading-5 text-zinc-500 dark:text-zinc-400">
            Pick a document from the list, or click a citation in an answer to
            jump straight to the passage it came from.
          </p>
        </div>
      </Card>
    );
  }

  const kind = fileKind(filename ?? "", documentQuery.data?.mime ?? "");

  return (
    <Card className={cn("h-full min-h-0", className)}>
      <CardHeader className="flex-col items-stretch gap-2">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <span className={cn("flex size-6 shrink-0 items-center justify-center rounded-md", kind.swatchClassName)}>
              <kind.Icon className="size-3.5" aria-hidden />
            </span>
            <CardTitle className="truncate">{filename ?? "Source"}</CardTitle>
            {documentQuery.isFetching && (
              <LoaderCircle className="size-3.5 shrink-0 animate-spin text-zinc-400" />
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {documentQuery.isSuccess && (
              <Button
                variant="ghost"
                size="icon"
                title="Download"
                aria-label="Download original file"
                disabled={downloading}
                onClick={() => void download()}
              >
                {downloading ? (
                  <LoaderCircle className="size-3.5 animate-spin" />
                ) : (
                  <Download className="size-3.5" />
                )}
              </Button>
            )}
            {onClose && (
              <button
                type="button"
                onClick={onClose}
                aria-label="Close source"
                className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
              >
                <X className="size-4" aria-hidden />
              </button>
            )}
          </div>
        </div>

        {documentQuery.isSuccess && (
          <div
            role="tablist"
            aria-label="Source view"
            className="flex gap-1 border-b border-zinc-200 dark:border-zinc-800"
          >
            {(
              [
                ["original", "Original"],
                ["text", "Text"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={view === value}
                onClick={() => setView(value)}
                className={cn(
                  "-mb-px border-b-2 px-3 py-1.5 text-xs font-medium transition-colors",
                  view === value
                    ? "border-zinc-900 text-zinc-900 dark:border-zinc-100 dark:text-zinc-100"
                    : "border-transparent text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200",
                )}
              >
                {label}
              </button>
            ))}
            {/* Named rather than left to a tooltip. The Original tab locates the
                passage by matching text against the rendered page, so it can
                miss; the Text tab is driven by the stored offsets and cannot.
                A reader deciding whether to trust a highlight needs to know
                which one they are looking at. */}
            <span className="ml-auto self-center pr-1 text-[11px] text-zinc-400 dark:text-zinc-500">
              {view === "text" ? "exact offsets" : "as uploaded"}
            </span>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-1.5">
          {documentQuery.data && (
            <>
              <Badge variant="outline">{kind.label}</Badge>
              {downloadError && (
                <span className="text-xs text-red-600 dark:text-red-400">
                  {downloadError}
                </span>
              )}
            </>
          )}
          {highlight && !highlightOutOfRange && (
            <Badge variant="info">
              cited {highlight.char_start.toLocaleString("en-US")}-
              {highlight.char_end.toLocaleString("en-US")}
            </Badge>
          )}
          {hasDerived && (
            <Badge variant="derived">
              <WandSparkles className="size-2.5" />
              contains AI-described figures
            </Badge>
          )}
        </div>
      </CardHeader>

      {documentQuery.isPending && (
        <div className="flex flex-1 items-center justify-center gap-2 p-8 text-xs text-zinc-500 dark:text-zinc-400">
          <LoaderCircle className="size-3.5 animate-spin" />
          Loading document...
        </div>
      )}

      {documentQuery.isError && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 p-8 text-center">
          <TriangleAlert className="size-5 text-red-500" />
          <p className="text-sm font-medium text-red-700 dark:text-red-300">
            Could not load this document
          </p>
          <p className="max-w-xs text-xs text-zinc-500 dark:text-zinc-400">
            {documentQuery.error instanceof ApiError
              ? `${documentQuery.error.message} (${documentQuery.error.code})`
              : String(documentQuery.error)}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void documentQuery.refetch()}
          >
            Retry
          </Button>
        </div>
      )}

      {documentQuery.isSuccess && (
        <div className="flex min-h-0 flex-1 flex-col">
          {highlightOutOfRange && (
            <p className="flex items-start gap-1.5 border-b border-amber-300 bg-amber-50 px-4 py-2 text-xs leading-5 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200">
              <TriangleAlert className="mt-px size-3 shrink-0" />
              <span>
                This citation points at characters{" "}
                {highlight?.char_start.toLocaleString("en-US")}-
                {highlight?.char_end.toLocaleString("en-US")}, outside this
                document&rsquo;s {text.length.toLocaleString("en-US")}-character
                text. It was recorded against a different build and the
                highlight below may be wrong.
              </span>
            </p>
          )}

          {documentQuery.data.status !== "ready" && (
            <p className="border-b border-zinc-200 bg-zinc-50 px-4 py-2 text-xs text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900/60 dark:text-zinc-400">
              Ingest is still at &ldquo;{documentQuery.data.status}&rdquo;. This
              text may be incomplete.
            </p>
          )}

          {hasDerived && (
            <p className="flex items-start gap-1.5 border-b border-zinc-200 px-4 py-2 text-xs leading-5 text-zinc-600 dark:border-zinc-800 dark:text-zinc-400">
              <WandSparkles className="mt-px size-3 shrink-0 text-amber-600 dark:text-amber-400" />
              <span>
                Amber passages are a model&rsquo;s description of a figure, not
                text that appears in the document.
              </span>
            </p>
          )}

          {view === "original" ? (
            <DocumentView
              mime={documentQuery.data.mime}
              data={blobQuery.data ?? null}
              text={text}
              page={null}
              cited={
                highlight && !highlightOutOfRange
                  ? text.slice(highlight.char_start, highlight.char_end)
                  : null
              }
            />
          ) : (
          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            {text.length === 0 ? (
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                This document has no extracted text.
              </p>
            ) : (
              // Proportional font and document-width measure, not the monospace
              // code-block look this used before - reads like the document
              // rather than a raw text dump. Still `whitespace-pre-wrap` and
              // still exactly `normalized_text`: the offsets highlighting
              // depends on (I5) index this string precisely as extracted, and
              // reflowing PDF line-wrap artifacts would need the parser to mark
              // which single line breaks are wrapped prose versus a real list
              // or table row - data the frontend does not have, so this only
              // changes typography, not the characters shown.
              <pre className="mx-auto max-w-[42rem] whitespace-pre-wrap break-words font-sans text-[15px] leading-7 text-zinc-800 dark:text-zinc-200">
                {segments.map((segment, index) => {
                  const body = text.slice(segment.start, segment.end);

                  // The marker is rendered, never stripped: what the pane shows
                  // stays character-for-character `normalized_text`, which is
                  // what the offsets index.
                  const inner = segment.marker ? (
                    <span className="mx-0.5 whitespace-pre rounded border border-amber-400/60 bg-amber-100 px-1 align-baseline font-sans text-[10px] font-bold uppercase tracking-wide text-amber-900 dark:border-amber-400/30 dark:bg-amber-400/20 dark:text-amber-200">
                      {body}
                    </span>
                  ) : segment.derived ? (
                    <span className="bg-amber-100/70 text-amber-950 decoration-amber-500/50 underline-offset-2 dark:bg-amber-400/10 dark:text-amber-100">
                      {body}
                    </span>
                  ) : (
                    body
                  );

                  if (!segment.highlighted) {
                    return <Fragment key={index}>{inner}</Fragment>;
                  }

                  return (
                    <mark
                      key={index}
                      ref={
                        index === firstHighlightIndex ? highlightRef : undefined
                      }
                      className="rounded-sm bg-accent-200/80 text-inherit ring-1 ring-accent-400/60 dark:bg-accent-500/30 dark:ring-accent-400/40"
                    >
                      {inner}
                    </mark>
                  );
                })}
              </pre>
            )}
          </div>
          )}
        </div>
      )}
    </Card>
  );
}
