"use client";

/**
 * The workspace shell: workspaces on the left, chat in the middle, the active
 * workspace's documents on the right. Citations open a slide-over rather than
 * a permanent fourth column — G5's source-pane verification still happens at
 * zero latency and zero LLM calls, it just does not cost a column of screen
 * width until someone actually clicks a citation.
 *
 * All three surfaces are client components. The workspace is a live,
 * streaming, authenticated single view — there is nothing here that server
 * rendering would improve, and pulling any of this into a server component
 * would only duplicate the auth path.
 */

import { useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useCallback, useState } from "react";

import { ChatPane } from "@/components/ChatPane";
import { DocumentManager } from "@/components/DocumentManager";
import { SourcePane } from "@/components/SourcePane";
import { WorkspaceSidebar } from "@/components/WorkspaceSidebar";
import type { Citation } from "@/lib/types";
import { CLERK_ENABLED } from "@/lib/utils";

interface Highlight {
  documentId: string;
  char_start: number;
  char_end: number;
}

export default function Workspace() {
  const queryClient = useQueryClient();

  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(null);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(
    null,
  );
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [highlight, setHighlight] = useState<Highlight | null>(null);

  const handleSelectWorkspaceChat = useCallback(
    (workspaceId: string, conversationId: string | null) => {
      setActiveWorkspaceId(workspaceId);
      setActiveConversationId(conversationId);
      setSelectedDocIds([]);
      setHighlight(null);
    },
    [],
  );

  /**
   * A citation click opens the drawer at the cited span. The offsets come from
   * the citation itself — they index into the same `normalized_text` the
   * source pane renders, which is the whole reason that string is the single
   * offset referent.
   */
  const handleCitationClick = useCallback((citation: Citation) => {
    setHighlight({
      documentId: citation.doc_id,
      char_start: citation.char_start,
      char_end: citation.char_end,
    });
  }, []);

  const handleOpenDocument = useCallback((docId: string) => {
    setHighlight({ documentId: docId, char_start: 0, char_end: 0 });
  }, []);

  // The list under the active workspace has nothing to show until this fires
  // — a brand-new conversation is invisible to Postgres (and so to the
  // sidebar's own query) until its first turn actually lands.
  const handleConversationStarted = useCallback(
    (conversationId: string) => {
      setActiveConversationId(conversationId);
      if (activeWorkspaceId) {
        void queryClient.invalidateQueries({
          queryKey: ["conversations", activeWorkspaceId],
        });
      }
    },
    [activeWorkspaceId, queryClient],
  );

  return (
    <div className="flex h-screen flex-col">
      {!CLERK_ENABLED && (
        <div className="border-b border-zinc-200 bg-accent-50 px-4 py-1 text-center text-xs text-accent-800 dark:border-zinc-800 dark:bg-accent-500/10 dark:text-accent-300">
          Running without auth — the backend assigns every request the same
          dev user (<code className="font-mono">AUTH_MODE=dev</code>).
        </div>
      )}

      <main className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)_320px]">
        <WorkspaceSidebar
          activeWorkspaceId={activeWorkspaceId}
          activeConversationId={activeConversationId}
          onSelect={handleSelectWorkspaceChat}
          className="border-r border-zinc-200 bg-zinc-100/60 dark:border-zinc-800 dark:bg-zinc-900/40"
        />

        {activeWorkspaceId ? (
          <section className="relative flex min-h-0 flex-col border-r border-zinc-200 dark:border-zinc-800">
            <ChatPane
              key={activeWorkspaceId}
              workspaceId={activeWorkspaceId}
              conversationId={activeConversationId}
              selectedDocIds={selectedDocIds}
              onCitationClick={handleCitationClick}
              onConversationStarted={handleConversationStarted}
            />
          </section>
        ) : (
          <section className="flex min-h-0 flex-col items-center justify-center border-r border-zinc-200 px-6 text-center dark:border-zinc-800">
            <p className="text-sm font-medium text-zinc-700 dark:text-zinc-200">
              Create or open a workspace to start chatting.
            </p>
            <p className="mt-1.5 max-w-xs text-sm text-zinc-500 dark:text-zinc-400">
              Upload your documents into a workspace once, then open as many
              conversations against them as you like.
            </p>
          </section>
        )}

        <div className="relative min-h-0 overflow-hidden">
          <DocumentManager
            workspaceId={activeWorkspaceId}
            selectedDocIds={selectedDocIds}
            onSelectionChange={setSelectedDocIds}
            onOpenDocument={handleOpenDocument}
            activeDocId={highlight?.documentId ?? null}
          />

          {/* Slide-over rather than a permanent column: G5's verification
              still costs zero LLM calls, it just does not spend screen width
              until a citation is actually clicked. */}
          {highlight ? (
            <div className="absolute inset-0 z-10 flex flex-col bg-white dark:bg-zinc-950">
              <div className="flex items-center justify-between border-b border-zinc-200 px-3 py-2 dark:border-zinc-800">
                <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
                  Source
                </span>
                <button
                  type="button"
                  onClick={() => setHighlight(null)}
                  aria-label="Close source"
                  className="rounded p-1 text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
                >
                  <X className="size-4" aria-hidden />
                </button>
              </div>
              <div className="min-h-0 flex-1">
                <SourcePane
                  documentId={highlight.documentId}
                  highlight={
                    highlight.char_end > highlight.char_start
                      ? {
                          char_start: highlight.char_start,
                          char_end: highlight.char_end,
                        }
                      : null
                  }
                />
              </div>
            </div>
          ) : null}
        </div>
      </main>
    </div>
  );
}
