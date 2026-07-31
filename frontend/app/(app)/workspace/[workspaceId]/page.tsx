"use client";

/**
 * One workspace: chat on the left, its documents in a panel on the right that
 * reserves its own grid column - opening it resizes the chat to make room,
 * it does not overlay on top of it. Open by default on the workspace-home
 * view (matching the reference's project-home Context card), toggled from
 * the header's Artifacts-style button otherwise. The panel has two "views"
 * sharing one slot: the document grid (`DocumentManager`, opened by the
 * header button or by closing the source pane) and a single document
 * (`SourcePane`, opened by a citation click or by clicking a tile in the
 * grid) - a citation click always wins the slot outright, since jumping to
 * the cited passage is the more specific intent than "browse everything."
 * Closing the source pane falls back to the grid if the panel was opened via
 * the header button, or closes the whole panel if it was opened only by the
 * citation click that's now being dismissed.
 *
 * The workspace and the active conversation both come from the URL
 * (`useParams`/`useSearchParams`), not local state - that's what makes a
 * specific chat a real, bookmarkable, back-button-safe link. The sidebar
 * (now living in `app/(app)/layout.tsx`, shared with `/workspaces`) navigates
 * here the same way; this page only ever reacts to the URL, it never owns
 * "which workspace is open."
 */

import { useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

import { ChatPane } from "@/components/ChatPane";
import { ConversationBreadcrumb } from "@/components/ConversationBreadcrumb";
import { DocumentManager } from "@/components/DocumentManager";
import { SourcePane } from "@/components/SourcePane";
import { WorkspaceHomeHeader } from "@/components/WorkspaceHomeHeader";
import { conversationsKey } from "@/lib/queryKeys";
import type { Citation } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Highlight {
  documentId: string;
  char_start: number;
  char_end: number;
  /** The citation's own recorded page, for `DocumentView`'s PDF fallback -
   * text-search misses land on this page with nothing highlighted rather
   * than wherever the viewer happened to be scrolled to. `null` for a
   * citation with no page (any non-PDF source) or `handleOpenDocument`'s
   * "just open it" case, where there is no cited passage at all. */
  page: number | null;
}

export default function WorkspacePage() {
  const { workspaceId } = useParams<{ workspaceId: string }>();
  const searchParams = useSearchParams();
  const conversationId = searchParams.get("c");
  const router = useRouter();
  const queryClient = useQueryClient();

  // Lazy-initialized open, not closed: landing on a workspace with no
  // conversation selected yet is the reference's project-home screen, whose
  // Context card is always visible rather than gated behind a click. Once a
  // conversation is active this same flag still starts closed on a fresh
  // mount of that state (see below) - the reference's own chat view opens
  // its Artifacts panel on demand, not by default.
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [showDocsPanel, setShowDocsPanel] = useState(() => conversationId === null);
  const [expanded, setExpanded] = useState(false);
  const panelVisible = showDocsPanel || highlight !== null;

  const closeDocsPanel = useCallback(() => {
    setShowDocsPanel(false);
    setHighlight(null);
    setExpanded(false);
  }, []);

  // Closes only from this same toggle being clicked again - not on an
  // outside click. The panel has to stay open while the user clicks *into*
  // the chat behind it (reading a source while typing a follow-up question
  // is the common case), so treating every outside click as a dismissal
  // fought the one thing this panel is for.
  const toggleDocsPanel = useCallback(() => {
    if (panelVisible) closeDocsPanel();
    else setShowDocsPanel(true);
  }, [panelVisible, closeDocsPanel]);

  const goToConversation = useCallback(
    (nextConversationId: string | null) => {
      router.push(
        nextConversationId
          ? `/workspace/${workspaceId}?c=${nextConversationId}`
          : `/workspace/${workspaceId}`,
      );
    },
    [router, workspaceId],
  );

  /**
   * A citation click opens the drawer at the cited span. The offsets come from
   * the citation itself - they index into the same `normalized_text` the
   * source pane renders, which is the whole reason that string is the single
   * offset referent.
   */
  const handleCitationClick = useCallback((citation: Citation) => {
    setHighlight({
      documentId: citation.doc_id,
      char_start: citation.char_start,
      char_end: citation.char_end,
      page: citation.page,
    });
  }, []);

  const handleOpenDocument = useCallback((docId: string) => {
    setHighlight({ documentId: docId, char_start: 0, char_end: 0, page: null });
  }, []);

  // A brand-new conversation is invisible to Postgres (and so to the
  // sidebar's/breadcrumb's own query) until its first turn actually lands -
  // once it does, adopt its id into the URL and invalidate so both refetch.
  const handleConversationStarted = useCallback(
    (newConversationId: string) => {
      router.replace(`/workspace/${workspaceId}?c=${newConversationId}`);
      void queryClient.invalidateQueries({ queryKey: conversationsKey(workspaceId) });
    },
    [router, workspaceId, queryClient],
  );

  return (
    // `content-stretch`: a single auto row doesn't stretch to fill a
    // definite-height grid container on its own, and without it this
    // column's `h-full` resolves against a shrunk, content-sized parent
    // instead of the real viewport (same fix as app/(app)/layout.tsx's own
    // grid). The documents column only exists in the grid template at all
    // while the panel is open - closed, the chat alone fills the row.
    //
    // `minmax(26rem,38%)`, not a flat `24rem`: a fixed pixel column reads as
    // a narrow sidebar bolted onto the side of the real content, which is
    // exactly what it was - a reference artifact panel claims a real
    // proportion of the window, generous enough that its own text doesn't
    // need to be small to fit. Percentage-based so it actually scales with
    // window width instead of eating a shrinking fraction of a wide monitor
    // and a too-large one of a narrow laptop; `26rem` floors it so it never
    // gets so narrow the reasoning above stops holding.
    <div
      className={cn(
        "grid h-full min-h-0 content-stretch grid-cols-1",
        panelVisible &&
          (expanded
            ? "lg:grid-cols-1"
            : "lg:grid-cols-[minmax(0,1fr)_minmax(26rem,38%)]"),
      )}
    >
      {/* `hidden` rather than unmounted while expanded - a full re-mount on
          every expand/collapse would drop the chat's scroll position and
          restart `ChatPane`'s own history fetch for no reason; the grid
          column it would occupy is simply not there while expanded, so
          `hidden` costs nothing layout-wise. */}
      <section
        className={cn(
          "relative flex min-h-0 flex-col border-r border-zinc-200 dark:border-zinc-800",
          expanded && "hidden",
        )}
      >
        {/* The richer header (back link, title, pin, rename/delete) only
            makes sense before a specific chat is open - once one is, the
            breadcrumb's job (show which workspace *and which chat*, with a
            quick switcher) takes over. Both get the same Artifacts-style
            toggle button. */}
        {conversationId === null ? (
          <WorkspaceHomeHeader
            workspaceId={workspaceId}
            docsOpen={panelVisible}
            onToggleDocs={toggleDocsPanel}
          />
        ) : (
          <ConversationBreadcrumb
            workspaceId={workspaceId}
            conversationId={conversationId}
            docsOpen={panelVisible}
            onToggleDocs={toggleDocsPanel}
          />
        )}
        <ChatPane
          key={workspaceId}
          workspaceId={workspaceId}
          conversationId={conversationId}
          selectedDocIds={selectedDocIds}
          onCitationClick={handleCitationClick}
          onConversationStarted={handleConversationStarted}
          onOpenConversation={goToConversation}
        />
      </section>

      {/* A real grid column, not an overlay - opening this resizes the chat
          to make room rather than covering it. It closes only from the
          header's own toggle button being clicked again (see
          `toggleDocsPanel`), not from a click elsewhere on the page. A
          citation click always wins the slot: jumping to the cited passage
          is more specific than "browse everything," so the grid only shows
          once there is no particular document being pointed at. */}
      {panelVisible && (
        <div className="relative min-h-0 overflow-hidden">
          {highlight ? (
            <SourcePane
              documentId={highlight.documentId}
              highlight={
                highlight.char_end > highlight.char_start
                  ? {
                      char_start: highlight.char_start,
                      char_end: highlight.char_end,
                      page: highlight.page,
                    }
                  : null
              }
              expanded={expanded}
              onToggleExpand={() => setExpanded((v) => !v)}
              onClose={() => {
                setHighlight(null);
                setExpanded(false);
              }}
            />
          ) : (
            <DocumentManager
              workspaceId={workspaceId}
              selectedDocIds={selectedDocIds}
              onSelectionChange={setSelectedDocIds}
              onOpenDocument={handleOpenDocument}
            />
          )}
        </div>
      )}
    </div>
  );
}
