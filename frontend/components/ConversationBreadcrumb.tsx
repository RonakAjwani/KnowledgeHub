"use client";

/**
 *"Workspace name / Conversation label ▾"above the chat pane. A sibling of
 * `ChatPane`, not inside it - `ChatPane`'s own contract is deliberately
 * ids-in/stream-out (see its file header), and resolving workspace/
 * conversation *names* is sidebar-shaped data. Both queries here are already
 * running elsewhere in the tree (the active `WorkspaceRow`, the workspaces
 * list) and share the same query keys, so TanStack Query serves this from
 * cache rather than firing a second network request.
 */

import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Files, MessageSquare, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { useClickOutside } from "@/hooks/useClickOutside";
import { useSessionToken } from "@/hooks/useSessionToken";
import { api } from "@/lib/api";
import { WORKSPACES_KEY, conversationsKey } from "@/lib/queryKeys";
import { cn, conversationLabel } from "@/lib/utils";

export function ConversationBreadcrumb({
  workspaceId,
  conversationId,
  docsOpen,
  onToggleDocs,
}: {
  workspaceId: string;
  conversationId: string | null;
  /** Toggles the Artifacts-style documents panel - a sibling of this
   * breadcrumb rather than owned by it, since the page decides what shares
   * that panel's one slot (the document grid or an open citation's source). */
  docsOpen: boolean;
  onToggleDocs: () => void;
}) {
  const getToken = useSessionToken();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  useClickOutside([panelRef], () => setOpen(false));

  const workspacesQuery = useQuery({
    queryKey: WORKSPACES_KEY,
    queryFn: async () => api.listWorkspaces(await getToken()),
  });
  const conversationsQuery = useQuery({
    queryKey: conversationsKey(workspaceId),
    queryFn: async () => api.listConversations(workspaceId, await getToken()),
  });

  const workspace = workspacesQuery.data?.find((w) => w.id === workspaceId);
  const conversations = conversationsQuery.data ?? [];
  const activeConversation = conversations.find((c) => c.id === conversationId);
  const activeLabel = conversationId
    ? conversationLabel(activeConversation)
    : "New chat";

  const go = (nextConversationId: string | null) => {
    setOpen(false);
    router.push(
      nextConversationId
        ? `/workspace/${workspaceId}?c=${nextConversationId}`
        : `/workspace/${workspaceId}`,
    );
  };

  return (
    <div className="flex items-center justify-between gap-2 border-b border-zinc-200 px-4 py-2 dark:border-zinc-800">
      {/* `panelRef` wraps the toggle button too, not just the dropdown -
 otherwise a click on the button itself counts as"outside"the
 dropdown, `useClickOutside` closes it, and the button's own
 click-through handler immediately reopens it. */}
      <div
        ref={panelRef}
        className="relative flex min-w-0 items-center gap-1.5 text-sm"
      >
        <span className="truncate text-zinc-500 dark:text-zinc-400">
          {workspace?.name ?? "..."}
        </span>
        {/* zinc-400/500, not 300/700: as a real glyph (not a border) the
 separator rendered at 1.32:1 in light and 1.79:1 in dark -
 visually absent rather than subtle. */}
        <span className="text-zinc-400 dark:text-zinc-500">/</span>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1 truncate rounded px-1.5 py-0.5 font-medium text-zinc-900 hover:bg-zinc-100 dark:text-zinc-100 dark:hover:bg-zinc-900"
        >
          <span className="truncate">{activeLabel}</span>
          <ChevronDown
            className={cn(
              "size-3.5 shrink-0 transition-transform",
              open && "rotate-180",
            )}
            aria-hidden
          />
        </button>

        {open && (
          // Items are inset from this container's own edges (`p-1.5` here,
          // `rounded-md` per item below) rather than full-bleed - a
          // full-bleed hover item touching a rounded container's curve reads
          // as clipped, regardless of `overflow-hidden`. See AccountMenu for
          // the same fix.
          <div className="absolute left-0 top-full z-20 mt-1 w-64 rounded-md border border-zinc-200 bg-white p-1.5 dark:border-zinc-800 dark:bg-zinc-950">
            {conversations.map((conversation) => (
              <button
                key={conversation.id}
                type="button"
                onClick={() => go(conversation.id)}
                className={cn(
                  "flex w-full items-center gap-1.5 truncate rounded-md px-2.5 py-2 text-left text-sm",
                  conversation.id === conversationId
                    ? "bg-zinc-200 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
                    : "text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900",
                )}
              >
                <MessageSquare
                  className="size-3.5 shrink-0 opacity-60"
                  aria-hidden
                />
                <span className="truncate">
                  {conversationLabel(conversation)}
                </span>
              </button>
            ))}

            <button
              type="button"
              onClick={() => go(null)}
              className={cn(
                "flex w-full items-center gap-1.5 rounded-md px-2.5 py-2 text-left text-sm font-medium",
                conversationId === null
                  ? "text-zinc-900 dark:text-zinc-100"
                  : "text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900",
              )}
            >
              <Plus className="size-3.5 shrink-0" aria-hidden />
              New chat
            </button>
          </div>
        )}
      </div>

      <button
        type="button"
        onClick={onToggleDocs}
        aria-pressed={docsOpen}
        aria-label={docsOpen ? "Hide documents panel" : "Show documents panel"}
        title={docsOpen ? "Hide documents" : "Show documents"}
        className={cn(
          "shrink-0 rounded p-1.5",
          docsOpen
            ? "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
            : "text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:text-zinc-500 dark:hover:bg-zinc-900 dark:hover:text-zinc-200",
        )}
      >
        <Files className="size-4" aria-hidden />
      </button>
    </div>
  );
}
