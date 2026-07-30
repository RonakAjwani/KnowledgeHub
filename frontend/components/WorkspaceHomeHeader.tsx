"use client";

/**
 * The workspace-home header - shown instead of `ConversationBreadcrumb` when
 * no conversation is selected yet, matching the reference's project-page
 * chrome: a back link, the workspace name, a pin toggle, and a rename/delete
 * menu. Reuses the exact mutations/pin-state the sidebar and `/workspaces`
 * grid already use, so an action taken here shows up identically everywhere
 * else - nothing here is a separate, drifting implementation of "rename a
 * workspace."
 */

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Files, MoreHorizontal, Pin, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { useClickOutside } from "@/hooks/useClickOutside";
import { usePinnedWorkspaces } from "@/hooks/usePinnedWorkspaces";
import { useSessionToken } from "@/hooks/useSessionToken";
import { useWorkspaceMutations } from "@/hooks/useWorkspaceMutations";
import { api } from "@/lib/api";
import { WORKSPACES_KEY } from "@/lib/queryKeys";
import { cn } from "@/lib/utils";

export function WorkspaceHomeHeader({
  workspaceId,
  docsOpen,
  onToggleDocs,
}: {
  workspaceId: string;
  /** Toggles the Artifacts-style documents panel - see the workspace page,
   * which owns the panel's actual content (document grid vs. open citation). */
  docsOpen: boolean;
  onToggleDocs: () => void;
}) {
  const getToken = useSessionToken();
  const router = useRouter();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useClickOutside([menuRef], () => setMenuOpen(false));

  const workspacesQuery = useQuery({
    queryKey: WORKSPACES_KEY,
    queryFn: async () => api.listWorkspaces(await getToken()),
  });
  const workspace = workspacesQuery.data?.find((w) => w.id === workspaceId);

  const { pinned, toggle: togglePin } = usePinnedWorkspaces();
  const isPinned = pinned.has(workspaceId);

  const { renameMutation, deleteMutation } = useWorkspaceMutations(() => {
    router.push("/workspaces");
  });

  if (renaming && workspace) {
    return (
      <div className="flex items-center gap-3 border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
        <input
          autoFocus
          defaultValue={workspace.name}
          onChange={(event) => setName(event.target.value)}
          onBlur={() => {
            setRenaming(false);
            const trimmed = name.trim();
            if (trimmed && trimmed !== workspace.name) {
              renameMutation.mutate({ id: workspaceId, name: trimmed });
            }
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") event.currentTarget.blur();
            if (event.key === "Escape") setRenaming(false);
          }}
          className="w-full max-w-md rounded-md border border-accent-400 bg-white px-2 py-1 text-2xl font-semibold text-zinc-900 dark:border-accent-500 dark:bg-zinc-900 dark:text-zinc-100"
        />
      </div>
    );
  }

  return (
    <div className="border-b border-zinc-200 px-6 py-4 dark:border-zinc-800">
      <Link
        href="/workspaces"
        className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200"
      >
        <ArrowLeft className="size-3.5" aria-hidden />
        All workspaces
      </Link>

      <div className="mt-1 flex items-center gap-2">
        <h1 className="min-w-0 truncate text-2xl font-semibold text-zinc-900 dark:text-zinc-100">
          {workspace?.name ?? "..."}
        </h1>

        <button
          type="button"
          aria-label={isPinned ? "Unpin workspace" : "Pin workspace"}
          onClick={() => togglePin(workspaceId)}
          className={cn(
            "shrink-0 rounded p-1",
            // Near-white fill in dark mode, not the accent color - sampled
            // off the real app. See WorkspaceSidebar's identical pin toggle
            // for why.
            isPinned
              ? "text-zinc-900 dark:text-zinc-100"
              : "text-zinc-300 hover:text-zinc-500 dark:text-zinc-700 dark:hover:text-zinc-400",
          )}
        >
          <Pin className={cn("size-4", isPinned && "fill-current")} aria-hidden />
        </button>

        <div className="ml-auto flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={onToggleDocs}
            aria-pressed={docsOpen}
            aria-label={docsOpen ? "Hide documents panel" : "Show documents panel"}
            title={docsOpen ? "Hide documents" : "Show documents"}
            className={cn(
              "rounded p-1",
              docsOpen
                ? "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
                : "text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:text-zinc-500 dark:hover:bg-zinc-900 dark:hover:text-zinc-300",
            )}
          >
            <Files className="size-4" aria-hidden />
          </button>

          <div ref={menuRef} className="relative">
            <button
              type="button"
              aria-label="Workspace options"
              onClick={() => setMenuOpen((v) => !v)}
              className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-600 dark:text-zinc-500 dark:hover:bg-zinc-900 dark:hover:text-zinc-300"
            >
              <MoreHorizontal className="size-4" aria-hidden />
            </button>

            {menuOpen && (
              <div
                role="menu"
                // Items are inset from this container's own edges (`p-1.5`
                // here, `rounded-md` per item below) rather than full-bleed -
                // see AccountMenu for why.
                className="absolute right-0 top-full z-20 mt-1 w-40 rounded-md border border-zinc-200 bg-white p-1.5 shadow-lg dark:border-zinc-800 dark:bg-zinc-950"
              >
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    setName(workspace?.name ?? "");
                    setRenaming(true);
                  }}
                  className="block w-full rounded-md px-2.5 py-2 text-left text-sm text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900"
                >
                  Rename
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setMenuOpen(false);
                    setConfirmingDelete(true);
                  }}
                  className="flex w-full items-center gap-1.5 rounded-md px-2.5 py-2 text-left text-sm text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/30"
                >
                  <Trash2 className="size-3.5" aria-hidden />
                  Delete
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {confirmingDelete && workspace && (
        <div className="mt-3 max-w-md rounded-md border border-red-200 bg-red-50 p-2.5 text-xs dark:border-red-900 dark:bg-red-950/30">
          <p className="text-red-800 dark:text-red-200">
            Delete &ldquo;{workspace.name}&rdquo; and its {workspace.document_count}{" "}
            {workspace.document_count === 1 ? "document" : "documents"}? This
            cannot be undone.
          </p>
          <div className="mt-1.5 flex gap-1.5">
            <button
              type="button"
              onClick={() => {
                setConfirmingDelete(false);
                deleteMutation.mutate(workspaceId);
              }}
              className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-700"
            >
              Delete
            </button>
            <button
              type="button"
              onClick={() => setConfirmingDelete(false)}
              className="rounded-md px-2 py-1 text-xs font-medium text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
