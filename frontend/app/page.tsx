"use client";

/**
 * The three-pane workspace.
 *
 * Documents on the left, chat in the middle, source on the right — and the
 * source pane is not decoration. It is guardrail **G5**: clicking a citation
 * scrolls to and highlights the exact span the answer drew on, which is
 * human-in-the-loop verification at zero latency and zero LLM calls. It degrades
 * gracefully too: even if every automated check fails, the user can still see
 * the receipt.
 *
 * All three panes are client components. The workspace is a live, streaming,
 * authenticated single view — there is nothing here that server rendering would
 * improve, and pulling the document list into a server component would only
 * duplicate the auth path.
 */

import { useState } from "react";

import { ChatPane } from "@/components/ChatPane";
import { DocumentManager } from "@/components/DocumentManager";
import { SourcePane } from "@/components/SourcePane";
import type { Citation } from "@/lib/types";
import { CLERK_ENABLED } from "@/lib/utils";

interface Highlight {
  char_start: number;
  char_end: number;
}

export default function Workspace() {
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [openDocumentId, setOpenDocumentId] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<Highlight | null>(null);

  /**
   * A citation click drives both panes at once: it opens the cited document and
   * highlights the cited span. The offsets come from the citation itself — they
   * index into the same `normalized_text` the source pane renders, which is the
   * whole reason that string is the single offset referent.
   */
  function handleCitationClick(citation: Citation) {
    setOpenDocumentId(citation.doc_id);
    setHighlight({
      char_start: citation.char_start,
      char_end: citation.char_end,
    });
  }

  function handleOpenDocument(docId: string) {
    setOpenDocumentId(docId);
    setHighlight(null);
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between border-b border-neutral-200 px-4 py-2 dark:border-neutral-800">
        <div className="flex items-baseline gap-2">
          <h1 className="text-sm font-semibold">KnowledgeHub</h1>
          <span className="text-xs text-neutral-500">
            multi-document RAG with verifiable citations
          </span>
        </div>
        {!CLERK_ENABLED && (
          <span
            className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-900 dark:bg-amber-900/40 dark:text-amber-200"
            title="The backend is running with AUTH_MODE=dev, which assigns every caller the same user id."
          >
            dev auth
          </span>
        )}
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[280px_minmax(0,1fr)_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-y-auto border-r border-neutral-200 dark:border-neutral-800">
          <DocumentManager
            selectedDocIds={selectedDocIds}
            onSelectionChange={setSelectedDocIds}
            onOpenDocument={handleOpenDocument}
            // Marks the row whose text the source pane is showing. Matters most
            // after a citation click, which changes the source pane without the
            // user having touched the list.
            activeDocId={openDocumentId}
          />
        </aside>

        <section className="flex min-h-0 flex-col border-r border-neutral-200 dark:border-neutral-800">
          <ChatPane
            selectedDocIds={selectedDocIds}
            onCitationClick={handleCitationClick}
          />
        </section>

        <section className="min-h-0 overflow-hidden">
          <SourcePane documentId={openDocumentId} highlight={highlight} />
        </section>
      </main>
    </div>
  );
}
