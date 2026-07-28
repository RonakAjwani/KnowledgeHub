"use client";

import {
  type DragEvent,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@clerk/nextjs";
import {
  CircleCheck,
  FileText,
  Info,
  LoaderCircle,
  RefreshCw,
  ScanText,
  ShieldAlert,
  Table2,
  Trash2,
  TriangleAlert,
  Upload,
  WandSparkles,
} from "lucide-react";

import { API_URL, ApiError, api } from "@/lib/api";
import { streamIngest } from "@/lib/sse";
import { CLERK_ENABLED, cn } from "@/lib/utils";
import type {
  DocumentStatus,
  DocumentSummary,
  ExtractionSignal,
  SanitizationReport,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";

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
 * process — a conditional call inside the component would not.
 *
 * Duplicated verbatim in `SourcePane.tsx` rather than shared, to keep the two
 * panes independently mountable while the app shell is still being wired.
 */
const useSessionToken: () => TokenGetter = CLERK_ENABLED
  ? useClerkToken
  : useNoToken;

// -------------------------------------------------------------- constants

const DOCUMENTS_KEY = ["documents"] as const;
const ACCEPTED_EXTENSIONS = [".pdf", ".txt", ".md", ".markdown"];
const TERMINAL_STATUSES: DocumentStatus[] = ["ready", "failed"];

const STATUS_LABEL: Record<DocumentStatus, string> = {
  queued: "Queued",
  parsing: "Parsing",
  chunking: "Chunking",
  embedding: "Embedding",
  ready: "Ready",
  failed: "Failed",
};

/** Ingest stage order, used to draw how far a document has travelled. */
const PIPELINE_STEPS: DocumentStatus[] = [
  "queued",
  "parsing",
  "chunking",
  "embedding",
];

// ---------------------------------------------------------------- helpers

/**
 * `extraction` is either the signal or `{}` — an empty object still satisfies
 * `in` narrowing because of its index signature, so probe a real field.
 */
function extractionOf(doc: DocumentSummary): ExtractionSignal | null {
  const value = doc.extraction as Partial<ExtractionSignal>;
  return typeof value.pages_total === "number"
    ? (value as ExtractionSignal)
    : null;
}

function sanitizationOf(doc: DocumentSummary): SanitizationReport | null {
  const direct = doc.sanitization_report as Partial<SanitizationReport>;
  if (typeof direct.removed_spans === "number") {
    return direct as SanitizationReport;
  }
  const nested = extractionOf(doc)?.sanitization;
  return nested && typeof nested.removed_spans === "number" ? nested : null;
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} (${error.code})`;
  return error instanceof Error ? error.message : "Request failed";
}

function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? "" : filename.slice(dot).toLowerCase();
}

/**
 * ISO prefix rather than `toLocaleString`: this component pre-renders on the
 * server, and a locale-formatted date is the classic hydration mismatch.
 */
function formatCreated(created: string | null): string | null {
  return created ? created.slice(0, 10) : null;
}

function formatKinds(kinds: Record<string, number>): string {
  return Object.entries(kinds)
    .map(([kind, count]) => `${count} ${kind.replace(/_/g, " ")}`)
    .join(", ");
}

// ------------------------------------------------------------- live state

interface LiveState {
  status: DocumentStatus;
  progress?: { done: number; total: number; unit: string };
  error?: string | null;
  /** The event stream dropped before a terminal frame; progress here is stale. */
  streamLost?: boolean;
}

// ------------------------------------------------------------- component

export interface DocumentManagerProps {
  /** Documents retrieval is restricted to. Empty means "search everything". */
  selectedDocIds: string[];
  onSelectionChange: (ids: string[]) => void;
  onOpenDocument: (docId: string) => void;
  /** Document currently shown in the source pane, so its row can mark itself. */
  activeDocId?: string | null;
  className?: string;
}

export function DocumentManager({
  selectedDocIds,
  onSelectionChange,
  onOpenDocument,
  activeDocId = null,
  className,
}: DocumentManagerProps) {
  const getToken = useSessionToken();
  const tokenRef = useRef<TokenGetter>(getToken);
  useEffect(() => {
    tokenRef.current = getToken;
  }, [getToken]);

  const queryClient = useQueryClient();

  const documentsQuery = useQuery({
    queryKey: DOCUMENTS_KEY,
    queryFn: async () => api.listDocuments(await tokenRef.current()),
  });

  const [live, setLive] = useState<Record<string, LiveState>>({});
  const [uploadErrors, setUploadErrors] = useState<string[]>([]);
  const [uploadingNames, setUploadingNames] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  // Drag events fire on every child; a depth counter keeps the drop zone from
  // flickering as the pointer crosses row boundaries.
  const dragDepthRef = useRef(0);

  /** Server list, overlaid with whatever the ingest stream has said since. */
  const documents = useMemo<DocumentSummary[]>(() => {
    const rows = documentsQuery.data ?? [];
    return rows.map((doc) => {
      const state = live[doc.id];
      if (!state) return doc;
      return { ...doc, status: state.status, error: state.error ?? doc.error };
    });
  }, [documentsQuery.data, live]);

  const pendingIds = useMemo(
    () =>
      documents
        .filter((doc) => !TERMINAL_STATUSES.includes(doc.status))
        .map((doc) => doc.id),
    [documents],
  );

  const readyIds = useMemo(
    () => documents.filter((doc) => doc.status === "ready").map((doc) => doc.id),
    [documents],
  );

  // ------------------------------------------------------ ingest streams

  const streamsRef = useRef(new Map<string, AbortController>());

  const subscribe = useCallback(
    (docId: string) => {
      const streams = streamsRef.current;
      // Entries are never removed except on unmount, which doubles as the
      // "already attempted" guard: a stream that died mid-ingest must not be
      // retried on every render.
      if (streams.has(docId)) return;

      const controller = new AbortController();
      streams.set(docId, controller);

      void (async () => {
        try {
          const token = await tokenRef.current();
          for await (const event of streamIngest(API_URL, docId, {
            token,
            signal: controller.signal,
          })) {
            if (event.type === "document.status") {
              setLive((prev) => ({
                ...prev,
                [docId]: {
                  status: event.data.status,
                  progress: event.data.progress,
                },
              }));
              continue;
            }

            if (event.type === "document.complete") {
              setLive((prev) => ({ ...prev, [docId]: { status: "ready" } }));
            } else {
              setLive((prev) => ({
                ...prev,
                [docId]: { status: "failed", error: event.data.message },
              }));
            }
            // Exactly one terminal event arrives. Stop reading here instead of
            // waiting for the server to hang up, and let `finally` close the
            // connection — a free-tier instance should not hold an idle stream.
            break;
          }
          // Refetch for the fields the stream does not carry: chunk_count and
          // the extraction signal.
          void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
        } catch (error) {
          if (controller.signal.aborted) return;
          console.warn(`ingest stream for ${docId} dropped`, error);
          setLive((prev) => ({
            ...prev,
            [docId]: {
              ...(prev[docId] ?? { status: "queued" }),
              streamLost: true,
            },
          }));
        } finally {
          controller.abort();
        }
      })();
    },
    [queryClient],
  );

  useEffect(() => {
    for (const id of pendingIds) subscribe(id);
  }, [pendingIds, subscribe]);

  useEffect(() => {
    const streams = streamsRef.current;
    return () => {
      for (const controller of streams.values()) controller.abort();
      streams.clear();
    };
  }, []);

  // ------------------------------------------------------------- upload

  const uploadFiles = useCallback(
    async (files: File[]) => {
      const accepted: File[] = [];
      const rejected: string[] = [];

      for (const file of files) {
        if (ACCEPTED_EXTENSIONS.includes(extensionOf(file.name))) {
          accepted.push(file);
        } else {
          rejected.push(`${file.name} — only PDF, TXT and Markdown are accepted`);
        }
      }

      setUploadErrors(rejected);
      if (accepted.length === 0) return;

      setUploadingNames(accepted.map((file) => file.name));

      // Sequential rather than Promise.all: the API process shares its 512 MB
      // with the in-process ingest tasks, so concurrent multipart bodies are the
      // likeliest way to OOM it.
      for (const file of accepted) {
        try {
          const doc = await api.uploadDocument(file, await tokenRef.current());
          queryClient.setQueryData<DocumentSummary[]>(DOCUMENTS_KEY, (prev) => [
            doc,
            ...(prev ?? []).filter((row) => row.id !== doc.id),
          ]);
          // A duplicate upload answers 200 with the finished document; there is
          // no ingest left to watch in that case.
          if (!TERMINAL_STATUSES.includes(doc.status)) subscribe(doc.id);
        } catch (error) {
          setUploadErrors((prev) => [
            ...prev,
            `${file.name} — ${describeError(error)}`,
          ]);
        } finally {
          setUploadingNames((prev) => prev.filter((name) => name !== file.name));
        }
      }

      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
    },
    [queryClient, subscribe],
  );

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragging(false);
    void uploadFiles(Array.from(event.dataTransfer.files));
  };

  const handleDragEnter = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragDepthRef.current += 1;
    setDragging(true);
  };

  const handleDragLeave = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragDepthRef.current -= 1;
    if (dragDepthRef.current <= 0) {
      dragDepthRef.current = 0;
      setDragging(false);
    }
  };

  // ------------------------------------------------------------- delete

  const deleteMutation = useMutation({
    mutationFn: async (docId: string) => {
      await api.deleteDocument(docId, await tokenRef.current());
      return docId;
    },
    onSuccess: (docId) => {
      streamsRef.current.get(docId)?.abort();
      // Drop the guard entry too: re-uploading the same bytes reuses this id,
      // and that ingest does need a fresh subscription.
      streamsRef.current.delete(docId);
      setLive((prev) => {
        const next = { ...prev };
        delete next[docId];
        return next;
      });
      queryClient.setQueryData<DocumentSummary[]>(DOCUMENTS_KEY, (prev) =>
        (prev ?? []).filter((row) => row.id !== docId),
      );
      onSelectionChange(selectedDocIds.filter((id) => id !== docId));
      setConfirmingDelete(null);
      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
    },
  });

  // ---------------------------------------------------------- selection

  // A selected document that was deleted or is no longer ready must not stay in
  // the retrieval scope. Guarded on `isSuccess` so an in-flight first fetch does
  // not wipe a selection the parent restored from storage.
  const listLoaded = documentsQuery.isSuccess;
  useEffect(() => {
    if (!listLoaded) return;
    const pruned = selectedDocIds.filter((id) => readyIds.includes(id));
    if (pruned.length !== selectedDocIds.length) onSelectionChange(pruned);
  }, [listLoaded, readyIds, selectedDocIds, onSelectionChange]);

  const selectedReady = selectedDocIds.filter((id) => readyIds.includes(id));
  const allReadySelected =
    readyIds.length > 0 && selectedReady.length === readyIds.length;
  const scopeNarrowed =
    selectedReady.length > 0 && selectedReady.length < readyIds.length;

  const toggleDocument = (docId: string) => {
    onSelectionChange(
      selectedDocIds.includes(docId)
        ? selectedDocIds.filter((id) => id !== docId)
        : [...selectedDocIds, docId],
    );
  };

  const busy = uploadingNames.length > 0;

  return (
    <Card className={cn("h-full min-h-0", className)}>
      <CardHeader>
        <div className="flex min-w-0 items-center gap-2">
          <CardTitle>Documents</CardTitle>
          <Badge variant="outline">{documents.length}</Badge>
        </div>
        <div className="flex items-center gap-1.5">
          <Button
            variant="ghost"
            size="icon"
            title="Refresh"
            aria-label="Refresh document list"
            disabled={documentsQuery.isFetching}
            onClick={() => void documentsQuery.refetch()}
          >
            <RefreshCw
              className={cn(
                "size-3.5",
                documentsQuery.isFetching && "animate-spin",
              )}
            />
          </Button>
          <Button size="sm" disabled={busy} onClick={() => fileInputRef.current?.click()}>
            {busy ? (
              <LoaderCircle className="size-3.5 animate-spin" />
            ) : (
              <Upload className="size-3.5" />
            )}
            Upload
          </Button>
        </div>
      </CardHeader>

      <CardContent className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.txt,.md,.markdown,application/pdf,text/plain,text/markdown"
          className="hidden"
          onChange={(event) => {
            void uploadFiles(Array.from(event.target.files ?? []));
            // Reset so re-picking the same file fires `change` again.
            event.target.value = "";
          }}
        />

        <div
          onDrop={handleDrop}
          onDragOver={(event) => event.preventDefault()}
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          className={cn(
            "rounded-lg border border-dashed px-4 py-5 text-center transition-colors",
            dragging
              ? "border-blue-500 bg-blue-50 dark:border-blue-400 dark:bg-blue-500/10"
              : "border-zinc-300 bg-zinc-50/60 dark:border-zinc-700 dark:bg-zinc-900/40",
          )}
        >
          <Upload className="mx-auto size-5 text-zinc-400 dark:text-zinc-500" />
          <p className="mt-2 text-sm font-medium text-zinc-700 dark:text-zinc-200">
            Drop files here
          </p>
          <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
            PDF, TXT or Markdown ·{" "}
            <button
              type="button"
              className="font-medium text-blue-600 underline-offset-2 hover:underline dark:text-blue-400"
              onClick={() => fileInputRef.current?.click()}
            >
              browse
            </button>
          </p>
        </div>

        {uploadingNames.length > 0 && (
          <ul className="space-y-1">
            {uploadingNames.map((name) => (
              <li
                key={name}
                className="flex items-center gap-2 rounded-md bg-zinc-100 px-2.5 py-1.5 text-xs text-zinc-600 dark:bg-zinc-800/60 dark:text-zinc-300"
              >
                <LoaderCircle className="size-3 animate-spin" />
                <span className="truncate">Uploading {name}…</span>
              </li>
            ))}
          </ul>
        )}

        {uploadErrors.length > 0 && (
          <div className="rounded-md border border-red-200 bg-red-50 p-2.5 dark:border-red-500/30 dark:bg-red-500/10">
            <div className="flex items-center justify-between gap-2">
              <p className="flex items-center gap-1.5 text-xs font-semibold text-red-800 dark:text-red-300">
                <TriangleAlert className="size-3.5" />
                Upload rejected
              </p>
              <Button
                variant="ghost"
                size="sm"
                className="h-5 px-1.5 text-red-700 hover:bg-red-100 dark:text-red-300 dark:hover:bg-red-500/20"
                onClick={() => setUploadErrors([])}
              >
                Dismiss
              </Button>
            </div>
            <ul className="mt-1 space-y-0.5 text-xs text-red-700 dark:text-red-300">
              {uploadErrors.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Retrieval scope. Kept above the list and always visible: which
            documents get searched is the single thing a checkbox here changes. */}
        <div
          className={cn(
            "rounded-md border px-2.5 py-2",
            scopeNarrowed
              ? "border-blue-300 bg-blue-50 dark:border-blue-500/40 dark:bg-blue-500/10"
              : "border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/50",
          )}
        >
          <div className="flex items-center gap-2">
            <Checkbox
              checked={allReadySelected}
              indeterminate={scopeNarrowed}
              disabled={readyIds.length === 0}
              aria-label="Select all ready documents"
              onChange={(event) =>
                onSelectionChange(event.target.checked ? [...readyIds] : [])
              }
            />
            <p className="flex-1 text-xs font-medium text-zinc-700 dark:text-zinc-200">
              {readyIds.length === 0
                ? "No documents are ready to search yet"
                : scopeNarrowed
                  ? `Searching ${selectedReady.length} of ${readyIds.length} documents`
                  : `Searching all ${readyIds.length} ready document${readyIds.length === 1 ? "" : "s"}`}
            </p>
            {selectedReady.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                className="h-5 px-1.5"
                onClick={() => onSelectionChange([])}
              >
                Clear
              </Button>
            )}
          </div>
          <p className="mt-1 pl-6 text-[11px] leading-4 text-zinc-500 dark:text-zinc-400">
            {scopeNarrowed
              ? `The other ${readyIds.length - selectedReady.length} document${readyIds.length - selectedReady.length === 1 ? " is" : "s are"} excluded — nothing in them can be retrieved or cited.`
              : "Tick individual documents to narrow retrieval to just those."}
          </p>
        </div>

        {documentsQuery.isPending && (
          <p className="flex items-center gap-2 px-1 py-6 text-xs text-zinc-500 dark:text-zinc-400">
            <LoaderCircle className="size-3.5 animate-spin" />
            Loading documents…
          </p>
        )}

        {documentsQuery.isError && (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 text-xs dark:border-red-500/30 dark:bg-red-500/10">
            <p className="font-semibold text-red-800 dark:text-red-300">
              Could not load documents
            </p>
            <p className="mt-0.5 text-red-700 dark:text-red-300">
              {describeError(documentsQuery.error)}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-2"
              onClick={() => void documentsQuery.refetch()}
            >
              Retry
            </Button>
          </div>
        )}

        {documentsQuery.isSuccess && documents.length === 0 && (
          <p className="px-1 py-6 text-center text-xs text-zinc-500 dark:text-zinc-400">
            No documents yet. Upload one to start asking questions.
          </p>
        )}

        <ul className="space-y-2">
          {documents.map((doc) => (
            <DocumentRow
              key={doc.id}
              doc={doc}
              live={live[doc.id]}
              selected={selectedDocIds.includes(doc.id)}
              active={activeDocId === doc.id}
              confirming={confirmingDelete === doc.id}
              deleting={
                deleteMutation.isPending && deleteMutation.variables === doc.id
              }
              deleteError={
                confirmingDelete === doc.id && deleteMutation.isError
                  ? describeError(deleteMutation.error)
                  : null
              }
              onToggle={() => toggleDocument(doc.id)}
              onOpen={() => onOpenDocument(doc.id)}
              onRequestDelete={() => {
                deleteMutation.reset();
                setConfirmingDelete(doc.id);
              }}
              onCancelDelete={() => setConfirmingDelete(null)}
              onConfirmDelete={() => deleteMutation.mutate(doc.id)}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

// -------------------------------------------------------------------- row

interface DocumentRowProps {
  doc: DocumentSummary;
  live: LiveState | undefined;
  selected: boolean;
  active: boolean;
  confirming: boolean;
  deleting: boolean;
  deleteError: string | null;
  onToggle: () => void;
  onOpen: () => void;
  onRequestDelete: () => void;
  onCancelDelete: () => void;
  onConfirmDelete: () => void;
}

function DocumentRow({
  doc,
  live,
  selected,
  active,
  confirming,
  deleting,
  deleteError,
  onToggle,
  onOpen,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
}: DocumentRowProps) {
  const ready = doc.status === "ready";
  const failed = doc.status === "failed";
  const extraction = extractionOf(doc);
  const sanitization = sanitizationOf(doc);
  const created = formatCreated(doc.created_at);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    onOpen();
  };

  return (
    <li>
      <div
        role="button"
        tabIndex={0}
        aria-pressed={active}
        onClick={onOpen}
        onKeyDown={handleKeyDown}
        className={cn(
          "group w-full cursor-pointer rounded-lg border p-2.5 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500",
          active
            ? "border-blue-400 bg-blue-50/70 dark:border-blue-500/50 dark:bg-blue-500/10"
            : "border-zinc-200 bg-white hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950 dark:hover:bg-zinc-900",
        )}
      >
        <div className="flex items-start gap-2.5">
          {/* The checkbox controls retrieval scope, the row controls the source
              pane — so it must not fall through to the row's click handler. */}
          <span
            className="pt-0.5"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            <Checkbox
              checked={selected}
              disabled={!ready}
              onChange={onToggle}
              aria-label={
                ready
                  ? `Include ${doc.filename} in retrieval`
                  : `${doc.filename} is not ready to search`
              }
              title={
                ready
                  ? "Include this document in retrieval"
                  : "Only ready documents can be searched"
              }
            />
          </span>

          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <FileText className="size-3.5 shrink-0 text-zinc-400 dark:text-zinc-500" />
              <span className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
                {doc.filename}
              </span>
            </div>

            <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-zinc-500 dark:text-zinc-400">
              <StatusBadge status={doc.status} />
              {ready && <span>{doc.chunk_count} chunks</span>}
              {created && <span>{created}</span>}
              {selected && (
                <Badge variant="info">
                  <ScanText className="size-2.5" />
                  in search scope
                </Badge>
              )}
            </div>

            {live?.progress && !ready && !failed && (
              <div className="mt-2">
                <Progress
                  value={live.progress.done}
                  max={live.progress.total}
                  aria-label={`${STATUS_LABEL[doc.status]} progress`}
                />
                <p className="mt-1 text-[11px] tabular-nums text-zinc-500 dark:text-zinc-400">
                  {live.progress.done} / {live.progress.total}{" "}
                  {live.progress.unit}
                </p>
              </div>
            )}

            {!ready && !failed && !live?.progress && (
              <div className="mt-2">
                <StageTrail status={doc.status} />
              </div>
            )}

            {live?.streamLost && !ready && !failed && (
              <p className="mt-1.5 flex items-center gap-1 text-[11px] text-amber-700 dark:text-amber-400">
                <TriangleAlert className="size-3" />
                Live progress stream dropped — refresh to check the real status.
              </p>
            )}

            {failed && doc.error && (
              <p className="mt-1.5 rounded border border-red-200 bg-red-50 px-2 py-1 text-[11px] text-red-700 dark:border-red-500/30 dark:bg-red-500/10 dark:text-red-300">
                {doc.error}
              </p>
            )}

            {ready && extraction && <ExtractionQuality extraction={extraction} />}

            {sanitization && sanitization.removed_spans > 0 && (
              <p className="mt-1.5 flex items-start gap-1.5 rounded border border-zinc-200 bg-zinc-50 px-2 py-1 text-[11px] leading-4 text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900/60 dark:text-zinc-400">
                <ShieldAlert className="mt-px size-3 shrink-0" />
                <span>
                  {sanitization.removed_spans} hidden span
                  {sanitization.removed_spans === 1 ? "" : "s"} stripped at
                  ingest
                  {Object.keys(sanitization.kinds ?? {}).length > 0 && (
                    <> ({formatKinds(sanitization.kinds)})</>
                  )}
                  . This content is not searchable and cannot be cited.
                </span>
              </p>
            )}
          </div>

          <span
            className="shrink-0"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            {!confirming && (
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Delete ${doc.filename}`}
                title="Delete"
                className="text-zinc-400 hover:text-red-600 dark:hover:text-red-400"
                onClick={onRequestDelete}
              >
                <Trash2 className="size-3.5" />
              </Button>
            )}
          </span>
        </div>

        {confirming && (
          <div
            className="mt-2 rounded-md border border-red-200 bg-red-50 p-2.5 dark:border-red-500/30 dark:bg-red-500/10"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={(event) => event.stopPropagation()}
          >
            <p className="text-xs font-medium text-red-800 dark:text-red-300">
              Delete “{doc.filename}”?
            </p>
            <p className="mt-0.5 text-[11px] leading-4 text-red-700 dark:text-red-300">
              Its chunks, vectors and citation records go with it. Past answers
              that cited it will lose their sources. This cannot be undone.
            </p>
            {deleteError && (
              <p className="mt-1 text-[11px] font-medium text-red-800 dark:text-red-200">
                {deleteError}
              </p>
            )}
            <div className="mt-2 flex gap-2">
              <Button
                variant="destructive"
                size="sm"
                disabled={deleting}
                onClick={onConfirmDelete}
              >
                {deleting && <LoaderCircle className="size-3 animate-spin" />}
                Delete
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={deleting}
                onClick={onCancelDelete}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}

// ----------------------------------------------------------- status bits

function StatusBadge({ status }: { status: DocumentStatus }) {
  if (status === "ready") {
    return (
      <Badge variant="success">
        <CircleCheck className="size-2.5" />
        Ready
      </Badge>
    );
  }
  if (status === "failed") {
    return (
      <Badge variant="danger">
        <TriangleAlert className="size-2.5" />
        Failed
      </Badge>
    );
  }
  return (
    <Badge variant="info">
      <LoaderCircle className="size-2.5 animate-spin" />
      {STATUS_LABEL[status]}
    </Badge>
  );
}

/** queued → parsing → chunking → embedding, drawn as four filling segments. */
function StageTrail({ status }: { status: DocumentStatus }) {
  const current = PIPELINE_STEPS.indexOf(status);
  return (
    <div className="flex items-center gap-1" aria-hidden>
      {PIPELINE_STEPS.map((step, index) => (
        <span
          key={step}
          className={cn(
            "h-1 flex-1 rounded-full",
            index < current
              ? "bg-blue-500"
              : index === current
                ? "animate-pulse bg-blue-500"
                : "bg-zinc-200 dark:bg-zinc-800",
          )}
        />
      ))}
    </div>
  );
}

// ------------------------------------------------------ extraction panel

function ExtractionQuality({ extraction }: { extraction: ExtractionSignal }) {
  const {
    pages_total,
    pages_escalated,
    pages_flagged,
    tables_recovered,
    figures_described,
    confidence,
  } = extraction;

  // The cap: pages the heuristic flagged as complex but that never reached the
  // VLM. Those pages carry Tier-1 text only, which is exactly the case where a
  // quiet UI would let a user trust a half-parsed table.
  const capped = pages_flagged > pages_escalated;
  const missed = pages_flagged - pages_escalated;

  const pct = Math.round(Math.min(Math.max(confidence, 0), 1) * 100);
  const tone =
    pct >= 80
      ? "text-emerald-700 dark:text-emerald-400"
      : pct >= 55
        ? "text-amber-700 dark:text-amber-400"
        : "text-red-700 dark:text-red-400";
  const bar =
    pct >= 80
      ? "bg-emerald-500"
      : pct >= 55
        ? "bg-amber-500"
        : "bg-red-500";

  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
          Extraction
        </span>
        <Progress
          value={pct}
          barClassName={bar}
          className="h-1 flex-1"
          aria-label="Extraction confidence"
        />
        <span className={cn("text-[11px] font-semibold tabular-nums", tone)}>
          {pct}%
        </span>
      </div>

      <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-zinc-500 dark:text-zinc-400">
        <span title="Pages sent to the vision model because a local heuristic flagged them as complex">
          {pages_escalated}/{pages_total} pages via VLM
        </span>
        <span className="inline-flex items-center gap-1">
          <Table2 className="size-2.5" />
          {tables_recovered} table{tables_recovered === 1 ? "" : "s"}
        </span>
        {figures_described > 0 && (
          <span className="inline-flex items-center gap-1">
            <WandSparkles className="size-2.5" />
            {figures_described} figure
            {figures_described === 1 ? "" : "s"} described
          </span>
        )}
      </div>

      {figures_described > 0 && (
        <p className="flex items-start gap-1.5 text-[11px] leading-4 text-zinc-500 dark:text-zinc-400">
          <Info className="mt-px size-3 shrink-0" />
          Figure descriptions are model-written, not document text. They are
          marked as such in the source pane.
        </p>
      )}

      {capped && (
        <div className="rounded-md border border-amber-400 bg-amber-50 p-2 dark:border-amber-500/40 dark:bg-amber-500/10">
          <p className="flex items-center gap-1.5 text-[11px] font-semibold text-amber-900 dark:text-amber-200">
            <TriangleAlert className="size-3.5 shrink-0" />
            Escalation cap reached — {missed} page
            {missed === 1 ? "" : "s"} parsed locally only
          </p>
          <p className="mt-1 text-[11px] leading-4 text-amber-800 dark:text-amber-300">
            {pages_flagged} page{pages_flagged === 1 ? "" : "s"} looked too
            complex for the local parser but only {pages_escalated} could be sent
            to the vision model before this document hit its cap. Text from the
            remaining {missed} may be incomplete, and tables on them are likely
            garbled. Answers drawing on those pages can be thin without saying
            so.
          </p>
        </div>
      )}
    </div>
  );
}
