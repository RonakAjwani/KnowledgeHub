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
import {
  CircleCheck,
  Download,
  LoaderCircle,
  ShieldAlert,
  Trash2,
  TriangleAlert,
  Upload,
  X,
} from "lucide-react";

import { useHasMounted } from "@/hooks/useHasMounted";
import { useSessionToken, type TokenGetter } from "@/hooks/useSessionToken";
import { API_URL, ApiError, api, triggerBrowserDownload } from "@/lib/api";
import { fileKind } from "@/lib/fileKind";
import { documentsKey } from "@/lib/queryKeys";
import { streamIngest } from "@/lib/sse";
import { cn } from "@/lib/utils";
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
import { useToast } from "@/components/ui/toast";

// -------------------------------------------------------------- constants

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
 * `extraction` is either the signal or `{}` - an empty object still satisfies
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

function formatKinds(kinds: Record<string, number>): string {
  return Object.entries(kinds)
    .map(([kind, count]) => `${count} ${kind.replace(/_/g, "")}`)
    .join(",");
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
  /** The workspace whose files this panel shows and uploads into. `null`
   * means no workspace is open yet - the panel renders an empty placeholder
   * and every mutation is disabled, since there is nowhere to attach a file. */
  workspaceId: string | null;
  /** Documents retrieval is restricted to. Empty means"search everything". */
  selectedDocIds: string[];
  onSelectionChange: (ids: string[]) => void;
  onOpenDocument: (docId: string) => void;
  /** Document currently shown in the source pane, so its row can mark itself. */
  activeDocId?: string | null;
  className?: string;
}

export function DocumentManager({
  workspaceId,
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
  const { showToast } = useToast();
  const DOCUMENTS_KEY = documentsKey(workspaceId);

  const documentsQuery = useQuery({
    queryKey: DOCUMENTS_KEY,
    queryFn: async () =>
      api.listDocuments(workspaceId, await tokenRef.current()),
    enabled: workspaceId !== null,
  });

  // Forces the server-matching first client paint through the loading branch
  // even if the query has already resolved by then (a fast localhost fetch
  // can beat hydration) - see hooks/useHasMounted.ts. Same fix as
  // WorkspaceSidebar/WorkspaceGrid's identical race, applied here
  // proactively since this component has the exact same `isPending`-gated
  // render pattern inside the same server-rendered page.
  const hasMounted = useHasMounted();
  const showLoading = !hasMounted || documentsQuery.isPending;

  const [live, setLive] = useState<Record<string, LiveState>>({});
  const [uploadErrors, setUploadErrors] = useState<string[]>([]);
  const [uploadingNames, setUploadingNames] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [downloadingAll, setDownloadingAll] = useState(false);
  const [downloadAllErrors, setDownloadAllErrors] = useState<string[]>([]);
  const [confirmingBulkDelete, setConfirmingBulkDelete] = useState(false);
  const [bulkDeleteErrors, setBulkDeleteErrors] = useState<string[]>([]);

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
    () =>
      documents.filter((doc) => doc.status === "ready").map((doc) => doc.id),
    [documents],
  );

  // ------------------------------------------------------ ingest streams

  const streamsRef = useRef(new Map<string, AbortController>());

  const subscribe = useCallback(
    (docId: string) => {
      const streams = streamsRef.current;
      // Entries are never removed except on unmount, which doubles as the
      //"already attempted"guard: a stream that died mid-ingest must not be
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
              const doc = queryClient
                .getQueryData<DocumentSummary[]>(DOCUMENTS_KEY)
                ?.find((row) => row.id === docId);
              showToast(`${doc?.filename ?? "Document"} is ready`);
            } else {
              setLive((prev) => ({
                ...prev,
                [docId]: { status: "failed", error: event.data.message },
              }));
            }
            // Exactly one terminal event arrives. Stop reading here instead of
            // waiting for the server to hang up, and let `finally` close the
            // connection - a free-tier instance should not hold an idle stream.
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
    [queryClient, DOCUMENTS_KEY, showToast],
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
          rejected.push(
            `${file.name}: only PDF, TXT and Markdown are accepted`,
          );
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
          const doc = await api.uploadDocument(
            file,
            workspaceId,
            await tokenRef.current(),
          );
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
            `${file.name}: ${describeError(error)}`,
          ]);
        } finally {
          setUploadingNames((prev) =>
            prev.filter((name) => name !== file.name),
          );
        }
      }

      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
    },
    [queryClient, subscribe, workspaceId, DOCUMENTS_KEY],
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

  // ----------------------------------------------------------- download

  /**
   * The original bytes are stored at upload time (`document.blob_ref = data`,
   * before ingest even starts), so this works regardless of ingest status -
   * unlike search/citation,"download the file I gave you"has no dependency
   * on parsing having finished.
   */
  const downloadDocument = useCallback(async (doc: DocumentSummary) => {
    const { blob, filename } = await api.downloadDocumentBlob(
      doc.id,
      await tokenRef.current(),
    );
    triggerBrowserDownload(blob, filename ?? doc.filename);
  }, []);

  const downloadAll = useCallback(async () => {
    setDownloadingAll(true);
    setDownloadAllErrors([]);
    // Sequential: bursting many programmatic downloads at once is throttled
    // or silently blocked by most browsers, not just an OOM concern this time.
    for (const doc of documents) {
      try {
        await downloadDocument(doc);
      } catch (error) {
        setDownloadAllErrors((prev) => [
          ...prev,
          `${doc.filename}: ${describeError(error)}`,
        ]);
      }
    }
    setDownloadingAll(false);
  }, [documents, downloadDocument]);

  // ------------------------------------------------------------- delete

  /** Shared by the single-tile delete and the bulk delete below, so both leave
   * the ingest-stream guard, the live-status map, and the query cache in the
   * same state instead of drifting into two slightly different cleanups. */
  const forgetDocument = useCallback(
    (docId: string) => {
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
    },
    [queryClient, DOCUMENTS_KEY],
  );

  const deleteMutation = useMutation({
    mutationFn: async (docId: string) => {
      await api.deleteDocument(docId, await tokenRef.current());
      return docId;
    },
    onSuccess: (docId) => {
      const filename = documents.find((doc) => doc.id === docId)?.filename;
      forgetDocument(docId);
      onSelectionChange(selectedDocIds.filter((id) => id !== docId));
      setConfirmingDelete(null);
      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
      showToast(`${filename ?? "Document"} deleted`);
    },
  });

  /** Removes every currently-selected (retrieval-scope) document from the
   * corpus. Sequential for the same reason upload and download-all are: the
   * API process shares its 512 MB with in-process ingest tasks. */
  const bulkDeleteMutation = useMutation({
    mutationFn: async (ids: string[]) => {
      const failures: string[] = [];
      for (const id of ids) {
        try {
          await api.deleteDocument(id, await tokenRef.current());
          forgetDocument(id);
        } catch (error) {
          failures.push(describeError(error));
        }
      }
      return { ids, failures };
    },
    onSuccess: ({ ids, failures }) => {
      onSelectionChange(selectedDocIds.filter((id) => !ids.includes(id)));
      setConfirmingBulkDelete(false);
      setBulkDeleteErrors(failures);
      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
      const succeeded = ids.length - failures.length;
      if (succeeded > 0) {
        showToast(
          `${succeeded} document${succeeded === 1 ? "" : "s"} deleted`,
        );
      }
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

  if (workspaceId === null) {
    return (
      <Card
        className={cn("h-full min-h-0 bg-zinc-50 dark:bg-zinc-950", className)}
      >
        <CardHeader>
          <CardTitle>Context</CardTitle>
        </CardHeader>
        <CardContent className="flex min-h-0 flex-1 items-center justify-center p-3">
          <p className="max-w-[16rem] text-center text-xs text-zinc-500 dark:text-zinc-400">
            Open a workspace to see and upload the documents it searches.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card
      className={cn("h-full min-h-0 bg-zinc-50 dark:bg-zinc-950", className)}
    >
      <CardHeader className="py-4">
        <div className="flex min-w-0 items-center gap-2">
          <CardTitle className="text-base">Context</CardTitle>
          <Badge variant="outline">{documents.length}</Badge>
        </div>
        {documents.length > 0 && (
          // `ghost`, not a filled pill: in the reference this is a plain
          // icon + label in the panel header (its icon sampled at #F4F4F4,
          // i.e. near-white *on* the panel, which a filled light pill could
          // not be). `dark:text-zinc-100` is that near-white step.
          <Button
            variant="ghost"
            size="sm"
            className="dark:text-zinc-100"
            disabled={downloadingAll}
            onClick={() => void downloadAll()}
          >
            {downloadingAll ? (
              <LoaderCircle className="size-3.5 animate-spin" />
            ) : (
              <Download className="size-3.5" />
            )}
            Download all
          </Button>
        )}
      </CardHeader>

      <CardContent
        onDrop={handleDrop}
        onDragOver={(event) => event.preventDefault()}
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4"
      >
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

        {/*
 Drag-and-drop and the file picker both work anywhere over this
 panel (the handlers above sit on the whole scroll container), but
 the *resting-state* affordance stays a single slim row rather than
 a permanent dashed box - the reference's own Artifacts panel has no
 upload chrome at all, since Claude generates those files itself;
 KnowledgeHub's documents are user-supplied, so some affordance has
 to exist, but it doesn't need to dominate the panel to be
 discoverable. The full dropzone still appears, briefly, as the drop
 target the instant a drag actually enters the panel.
 */}
        {dragging ? (
          <div className="rounded-lg border border-dashed border-accent-500 bg-accent-50 px-4 py-5 text-center dark:border-accent-400 dark:bg-accent-500/10">
            <Upload className="mx-auto size-5 text-accent-600 dark:text-accent-400" />
            <p className="mt-2 text-sm font-medium text-zinc-700 dark:text-zinc-200">
              Drop files here
            </p>
            <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
              PDF, TXT or Markdown
            </p>
          </div>
        ) : (
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
          >
            <Upload
              className="size-3.5 text-zinc-400 dark:text-zinc-500"
              aria-hidden
            />
            Add files
            <span className="font-normal text-zinc-400 dark:text-zinc-500">
              PDF, TXT or Markdown
            </span>
          </button>
        )}

        {uploadingNames.length > 0 && (
          <ul className="space-y-1">
            {uploadingNames.map((name) => (
              <li
                key={name}
                className="flex items-center gap-2 rounded-md bg-zinc-100 px-2.5 py-1.5 text-xs text-zinc-600 dark:bg-zinc-800/60 dark:text-zinc-300"
              >
                <LoaderCircle className="size-3 animate-spin" />
                <span className="truncate">Uploading {name}...</span>
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

        {downloadAllErrors.length > 0 && (
          <div className="rounded-md border border-red-200 bg-red-50 p-2.5 dark:border-red-500/30 dark:bg-red-500/10">
            <div className="flex items-center justify-between gap-2">
              <p className="flex items-center gap-1.5 text-xs font-semibold text-red-800 dark:text-red-300">
                <TriangleAlert className="size-3.5" />
                Some downloads failed
              </p>
              <Button
                variant="ghost"
                size="sm"
                className="h-5 px-1.5 text-red-700 hover:bg-red-100 dark:text-red-300 dark:hover:bg-red-500/20"
                onClick={() => setDownloadAllErrors([])}
              >
                Dismiss
              </Button>
            </div>
            <ul className="mt-1 space-y-0.5 text-xs text-red-700 dark:text-red-300">
              {downloadAllErrors.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Retrieval scope. Kept above the grid and always visible once there
 is something to scope - which documents get searched is the
 single thing this checkbox changes, and that question doesn't
 exist yet for an empty workspace. */}
        {!showLoading && documents.length > 0 && (
          <>
            <div
              className={cn(
                "flex items-center gap-2.5 rounded-md border px-3 py-2.5",
                scopeNarrowed
                  ? "border-zinc-300 bg-zinc-200 dark:border-zinc-600 dark:bg-zinc-800"
                  : "border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/50",
              )}
            >
              <Checkbox
                checked={allReadySelected}
                indeterminate={scopeNarrowed}
                disabled={readyIds.length === 0}
                aria-label="Select all ready documents"
                onChange={(event) =>
                  onSelectionChange(event.target.checked ? [...readyIds] : [])
                }
              />
              <p className="flex-1 text-sm font-medium text-zinc-700 dark:text-zinc-200">
                Select All
              </p>
              {selectedReady.length > 0 && !confirmingBulkDelete && (
                <Button
                  variant="destructive"
                  size="sm"
                  className="h-6 px-2"
                  onClick={() => {
                    bulkDeleteMutation.reset();
                    setBulkDeleteErrors([]);
                    setConfirmingBulkDelete(true);
                  }}
                >
                  <Trash2 className="size-3.5" />
                  Delete {selectedReady.length}
                </Button>
              )}
            </div>

            {confirmingBulkDelete && selectedReady.length > 0 && (
              <div className="flex items-center gap-1.5 rounded-md bg-red-50 py-1.5 pl-3 pr-1.5 dark:bg-red-950/30">
                <span className="min-w-0 flex-1 truncate text-xs font-medium text-red-800 dark:text-red-200">
                  Delete {selectedReady.length} document
                  {selectedReady.length === 1 ? "" : "s"}?
                </span>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs font-semibold text-red-700 hover:bg-red-100 dark:text-red-300 dark:hover:bg-red-900/50"
                  disabled={bulkDeleteMutation.isPending}
                  onClick={() => bulkDeleteMutation.mutate([...selectedReady])}
                >
                  {bulkDeleteMutation.isPending && (
                    <LoaderCircle className="size-3 animate-spin" />
                  )}
                  Delete
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 px-2 text-xs text-zinc-500 hover:bg-zinc-200 dark:text-zinc-400 dark:hover:bg-zinc-800"
                  disabled={bulkDeleteMutation.isPending}
                  onClick={() => setConfirmingBulkDelete(false)}
                >
                  Cancel
                </Button>
                {bulkDeleteErrors.length > 0 && (
                  <ul className="mt-1.5 basis-full space-y-0.5 text-xs text-red-700 dark:text-red-300">
                    {bulkDeleteErrors.map((message) => (
                      <li key={message}>{message}</li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </>
        )}

        {showLoading && (
          <p className="flex items-center gap-2 px-1 py-6 text-xs text-zinc-500 dark:text-zinc-400">
            <LoaderCircle className="size-3.5 animate-spin" />
            Loading documents...
          </p>
        )}

        {!showLoading && documentsQuery.isError && (
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

        {!showLoading && documentsQuery.isSuccess && documents.length === 0 && (
          <p className="px-1 py-6 text-center text-xs text-zinc-500 dark:text-zinc-400">
            No documents yet. Upload one to start asking questions.
          </p>
        )}

        <div className="flex flex-col gap-2">
          {!showLoading &&
            documents.map((doc) => (
              <DocumentRow
                key={doc.id}
                doc={doc}
                live={live[doc.id]}
                selected={selectedDocIds.includes(doc.id)}
                active={activeDocId === doc.id}
                confirming={confirmingDelete === doc.id}
                deleting={
                  deleteMutation.isPending &&
                  deleteMutation.variables === doc.id
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
        </div>
      </CardContent>
    </Card>
  );
}

// ------------------------------------------------------------------- row

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

/**
 * A full-width row - matching the reference's Artifacts panel, which is a
 * stacked list, not a tile grid (that grid belongs to a *different* screen in
 * the reference product, the one this was mistakenly modelled on earlier).
 * Icon, filename and status/format subtitle on the left; the retrieval-scope
 * checkbox (always visible - the user asked to keep this discoverable, not
 * hover-only like the reference's own checkbox) and the escalation-cap/
 * sanitization warnings (trust signals, same non-dismissible principle
 * `DegradationBanner` applies) on the right. Hover-revealed delete"X"sits
 * at the far right, past the checkbox, so it never overlaps it.
 */
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
  const pending = !ready && !failed;
  const extraction = extractionOf(doc);
  const sanitization = sanitizationOf(doc);
  const capped =
    !!extraction && extraction.pages_flagged > extraction.pages_escalated;
  const kind = fileKind(doc.filename, doc.mime);

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" && event.key !== "") return;
    event.preventDefault();
    onOpen();
  };

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={active}
      onClick={onOpen}
      onKeyDown={handleKeyDown}
      className={cn(
        "group relative flex cursor-pointer items-center gap-3 rounded-xl border p-3 text-left transition-colors",
        active
          ? "border-zinc-400 bg-zinc-200 dark:border-zinc-600 dark:bg-zinc-800"
          : "border-zinc-200 bg-white hover:bg-zinc-50 dark:border-transparent dark:bg-zinc-900 dark:hover:bg-zinc-800",
      )}
    >
      <span
        className={cn(
          "flex size-10 shrink-0 items-center justify-center rounded-lg",
          kind.swatchClassName,
        )}
      >
        <kind.Icon className="size-5" aria-hidden />
      </span>

      <div className="min-w-0 flex-1">
        {/* dark:text-zinc-100, not -50 - this ramp's `z50` stays deliberately
 dark in dark mode (a background-role value, not a text one; see
 globals.css), so `dark:text-zinc-50` here was near-black text on
 a near-black row. `z100` is the ramp's actual"brightest text"step in dark mode. */}
        <p className="truncate text-sm font-semibold leading-snug text-zinc-900 dark:text-zinc-100">
          {doc.filename}
        </p>

        {pending &&
          (live?.progress ? (
            <>
              <p className="mt-0.5 text-xs tabular-nums text-zinc-500 dark:text-zinc-400">
                {kind.label} · {STATUS_LABEL[doc.status]} {live.progress.done}/
                {live.progress.total} {live.progress.unit}
              </p>
              <Progress
                value={live.progress.done}
                max={live.progress.total}
                aria-label={`${STATUS_LABEL[doc.status]} progress`}
                className="mt-1 max-w-xs"
              />
            </>
          ) : (
            <>
              <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                {kind.label} · {STATUS_LABEL[doc.status]}
              </p>
              <StageTrail status={doc.status} className="mt-1 max-w-xs" />
            </>
          ))}

        {live?.streamLost && pending && (
          <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-400">
            Stream dropped, refresh
          </p>
        )}

        {ready && (
          <p className="mt-0.5 flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400">
            <CircleCheck
              className="size-3.5 text-emerald-600 dark:text-emerald-400"
              aria-hidden
            />
            {kind.label} · {doc.chunk_count} chunks
          </p>
        )}

        {failed && (
          <p
            className="mt-0.5 flex items-center gap-1.5 truncate text-xs text-red-600 dark:text-red-400"
            title={doc.error ?? undefined}
          >
            <TriangleAlert className="size-3.5 shrink-0" aria-hidden />
            {kind.label} · {doc.error ?? "Failed"}
          </p>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-2 empty:hidden">
        {sanitization && sanitization.removed_spans > 0 && (
          <span
            className="shrink-0"
            title={`${sanitization.removed_spans} hidden span${sanitization.removed_spans === 1 ? "" : "s"} stripped at ingest${Object.keys(sanitization.kinds ?? {}).length > 0 ? ` (${formatKinds(sanitization.kinds)})` : ""}. This content is not searchable and cannot be cited.`}
          >
            <ShieldAlert
              className="size-4 text-amber-600 dark:text-amber-400"
              aria-hidden
            />
          </span>
        )}
        {capped && extraction && (
          <span
            className="shrink-0"
            title={`Escalation cap reached: ${extraction.pages_flagged - extraction.pages_escalated} page(s) parsed locally only and may be incomplete.`}
          >
            <TriangleAlert
              className="size-4 text-amber-600 dark:text-amber-400"
              aria-hidden
            />
          </span>
        )}
      </div>

      {/* The checkbox controls retrieval scope, the row controls the source
 pane - so it must not fall through to the row's click handler.
 Wrapped in its own fixed-size, centered box rather than just relying
 on the parent row's `items-center`, because a native checkbox's
 intrinsic box model is inconsistent enough across browsers that it
 can sit a couple of pixels off its neighbours'baseline otherwise. */}
      <span
        onClick={(event) => event.stopPropagation()}
        onKeyDown={(event) => event.stopPropagation()}
        className="flex size-6 shrink-0 items-center justify-center"
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

      {/* Quick delete, hover-revealed. A confirm step still guards it despite
 the low-friction"X": deleting a document takes its chunks,
 citation records, and any past answer's sources with it. */}
      <button
        type="button"
        aria-label={`Delete ${doc.filename}`}
        onClick={(event) => {
          event.stopPropagation();
          onRequestDelete();
        }}
        className="hidden size-6 shrink-0 items-center justify-center rounded-full bg-zinc-200 text-zinc-500 hover:bg-red-100 hover:text-red-700 group-hover:flex dark:bg-zinc-700 dark:text-zinc-400 dark:hover:bg-red-950/50 dark:hover:text-red-300"
      >
        <X className="size-3.5" aria-hidden />
      </button>

      {confirming && (
        <div
          role="dialog"
          aria-label="Confirm delete"
          className="absolute inset-0 z-10 flex items-center gap-1.5 rounded-xl bg-white pl-3.5 pr-1.5 dark:bg-zinc-950"
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => event.stopPropagation()}
        >
          <span className="min-w-0 flex-1 truncate text-xs font-medium text-red-800 dark:text-red-200">
            {deleteError ?? "Delete this document?"}
          </span>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2.5 text-xs font-semibold text-red-700 hover:bg-red-100 dark:text-red-300 dark:hover:bg-red-950/50"
            disabled={deleting}
            onClick={onConfirmDelete}
          >
            {deleting && <LoaderCircle className="size-3 animate-spin" />}
            Delete
          </Button>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 px-2.5 text-xs"
            disabled={deleting}
            onClick={onCancelDelete}
          >
            Cancel
          </Button>
        </div>
      )}
    </div>
  );
}

/** queued -> parsing -> chunking -> embedding, drawn as four filling segments. */
function StageTrail({
  status,
  className,
}: {
  status: DocumentStatus;
  className?: string;
}) {
  const current = PIPELINE_STEPS.indexOf(status);
  return (
    <div className={cn("flex items-center gap-1", className)} aria-hidden>
      {PIPELINE_STEPS.map((step, index) => (
        <span
          key={step}
          // Neutral fill, matching `ui/progress.tsx` - progress is conveyed
          // by *how many* segments are filled plus the pulse on the active
          // one, so it doesn't need a hue to be legible, and these sit on
          // screen for the whole duration of an ingest.
          className={cn(
            "h-1 flex-1 rounded-full",
            index < current
              ? "bg-primary"
              : index === current
                ? "animate-pulse bg-primary"
                : "bg-zinc-200 dark:bg-zinc-800",
          )}
        />
      ))}
    </div>
  );
}
