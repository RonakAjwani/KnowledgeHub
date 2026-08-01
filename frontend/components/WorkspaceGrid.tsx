"use client";

/**
 * The `/workspaces` picker - a fuller, card-based view of the same list the
 * sidebar shows compactly. Both read `WORKSPACES_KEY`, so opening this page
 * after using the sidebar (or vice versa) costs no extra request, and both
 * share `useWorkspaceMutations` so rename/delete never drifts between the
 * two surfaces.
 *
 * Layout follows the reference's Projects grid: title + search + sort +
 *"New"in one header row, cards with a pin toggle. Search and sort are both
 * real (client-side filter/sort over the already-fetched list, no backend
 * call) - the pin is real too, just device-local (see
 * hooks/usePinnedWorkspaces.ts for why there's no backend column for it).
 */

import { useQuery } from "@tanstack/react-query";
import {
  ChevronDown,
  FileText,
  MessageSquare,
  Pencil,
  Pin,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";

import { NewWorkspaceDialog } from "@/components/WorkspaceSidebar";
import { useClickOutside } from "@/hooks/useClickOutside";
import { useHasMounted } from "@/hooks/useHasMounted";
import { usePinnedWorkspaces } from "@/hooks/usePinnedWorkspaces";
import { useSessionToken } from "@/hooks/useSessionToken";
import { useWorkspaceMutations } from "@/hooks/useWorkspaceMutations";
import { api } from "@/lib/api";
import { WORKSPACES_KEY } from "@/lib/queryKeys";
import type { Workspace } from "@/lib/types";
import { cn } from "@/lib/utils";

type SortKey = "updated" | "name";

const SORT_LABEL: Record<SortKey, string> = {
  updated: "Last updated",
  name: "Name",
};

/** ISO prefix, not `toLocaleString`: this can pre-render on the server, and a
 * locale-formatted date is the classic hydration mismatch (same convention
 * DocumentManager's `formatCreated` already uses). */
function formatUpdated(value: string | null): string | null {
  return value ? value.slice(0, 10) : null;
}

function WorkspaceCard({
  workspace,
  pinned,
  onTogglePin,
  onOpen,
  onRename,
  onDelete,
}: {
  workspace: Workspace;
  pinned: boolean;
  onTogglePin: () => void;
  onOpen: () => void;
  onRename: (name: string) => void;
  onDelete: () => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState(workspace.name);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const updated = formatUpdated(workspace.updated_at);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => !renaming && !confirmingDelete && onOpen()}
      onKeyDown={(event) => {
        if (
          (event.key === "Enter" || event.key === " ") &&
          !renaming &&
          !confirmingDelete
        ) {
          event.preventDefault();
          onOpen();
        }
      }}
      className="group flex cursor-pointer flex-col rounded-xl border border-zinc-200 bg-white p-5 text-left hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950 dark:hover:bg-zinc-900"
    >
      <div className="flex items-start justify-between gap-2">
        {renaming ? (
          <input
            autoFocus
            value={name}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => setName(event.target.value)}
            onBlur={() => {
              setRenaming(false);
              const trimmed = name.trim();
              if (trimmed && trimmed !== workspace.name) onRename(trimmed);
              else setName(workspace.name);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
              if (event.key === "Escape") {
                setName(workspace.name);
                setRenaming(false);
              }
            }}
            className="w-full rounded-md border border-accent-400 bg-white px-2 py-1 text-base font-medium text-zinc-900 dark:border-accent-500 dark:bg-zinc-900 dark:text-zinc-100"
          />
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <h3 className="min-w-0 truncate text-base font-semibold text-zinc-900 dark:text-zinc-100">
              {workspace.name}
            </h3>
            <button
              type="button"
              aria-label={pinned ? "Unpin workspace" : "Pin workspace"}
              onClick={(event) => {
                event.stopPropagation();
                onTogglePin();
              }}
              className={cn(
                "shrink-0 rounded p-0.5",
                // Near-white fill in dark mode, not the accent color -
                // sampled off the real app. See WorkspaceSidebar's identical
                // pin toggle for why.
                pinned
                  ? "text-zinc-900 dark:text-zinc-100"
                  : "text-zinc-300 opacity-0 group-hover:opacity-100 hover:text-zinc-500 dark:text-zinc-700 dark:hover:text-zinc-400",
              )}
            >
              <Pin
                className={cn("size-4", pinned && "fill-current")}
                aria-hidden
              />
            </button>
          </div>
        )}

        <span className="hidden shrink-0 items-center gap-0.5 group-hover:flex">
          <button
            type="button"
            aria-label="Rename workspace"
            onClick={(event) => {
              event.stopPropagation();
              setRenaming(true);
            }}
            className="rounded p-1 text-zinc-400 hover:bg-zinc-200 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          >
            <Pencil className="size-4" aria-hidden />
          </button>
          <button
            type="button"
            aria-label="Delete workspace"
            onClick={(event) => {
              event.stopPropagation();
              setConfirmingDelete(true);
            }}
            className="rounded p-1 text-zinc-400 hover:bg-red-100 hover:text-red-700 dark:hover:bg-red-950/50 dark:hover:text-red-300"
          >
            <Trash2 className="size-4" aria-hidden />
          </button>
        </span>
      </div>

      <div className="mt-4 flex items-center gap-3 text-sm text-zinc-500 dark:text-zinc-400">
        <span className="flex items-center gap-1.5">
          <FileText className="size-3.5" aria-hidden />
          {workspace.document_count}{" "}
          {workspace.document_count === 1 ? "file" : "files"}
        </span>
        <span className="flex items-center gap-1.5">
          <MessageSquare className="size-3.5" aria-hidden />
          {workspace.conversation_count}{" "}
          {workspace.conversation_count === 1 ? "chat" : "chats"}
        </span>
      </div>
      {updated && (
        <p className="mt-2 text-xs text-zinc-400 dark:text-zinc-500">
          Updated {updated}
        </p>
      )}

      {confirmingDelete && (
        <div
          onClick={(event) => event.stopPropagation()}
          className="mt-4 flex items-center gap-1.5 rounded-lg bg-red-50 py-1.5 pl-3 pr-1.5 dark:bg-red-950/30"
        >
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-red-800 dark:text-red-200">
            Delete this workspace?
          </span>
          <button
            type="button"
            onClick={() => {
              setConfirmingDelete(false);
              onDelete();
            }}
            className="shrink-0 rounded-md px-2.5 py-1 text-sm font-semibold text-red-700 hover:bg-red-100 dark:text-red-300 dark:hover:bg-red-900/50"
          >
            Delete
          </button>
          <button
            type="button"
            onClick={() => setConfirmingDelete(false)}
            className="shrink-0 rounded-md px-2.5 py-1 text-sm text-zinc-500 hover:bg-zinc-200 dark:text-zinc-400 dark:hover:bg-zinc-800"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

export function WorkspaceGrid() {
  const getToken = useSessionToken();
  const router = useRouter();
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("updated");
  const [sortMenuOpen, setSortMenuOpen] = useState(false);
  const sortMenuRef = useRef<HTMLDivElement>(null);
  useClickOutside([sortMenuRef], () => setSortMenuOpen(false));

  const workspacesQuery = useQuery({
    queryKey: WORKSPACES_KEY,
    queryFn: async () => api.listWorkspaces(await getToken()),
  });
  const { renameMutation, deleteMutation } = useWorkspaceMutations();
  const { pinned, toggle: togglePin } = usePinnedWorkspaces();

  // Forces the server-matching first client paint through the loading branch
  // even if the query has already resolved by then (a fast localhost fetch
  // can beat hydration) - see hooks/useHasMounted.ts. Same fix as
  // WorkspaceSidebar's identical race, since this page is server-rendered
  // the same way.
  const hasMounted = useHasMounted();
  const showLoading = !hasMounted || workspacesQuery.isLoading;

  const workspaces = useMemo(() => {
    const all = workspacesQuery.data ?? [];
    const q = search.trim().toLowerCase();
    const filtered = q
      ? all.filter((w) => w.name.toLowerCase().includes(q))
      : all;

    // Pinned workspaces float to the top regardless of sort key, same as the
    // reference's separate"Pinned"section - here it's one grid, so the
    // grouping has to happen via sort rather than a second list.
    const byPin = (a: Workspace, b: Workspace) =>
      Number(pinned.has(b.id)) - Number(pinned.has(a.id));

    return [...filtered].sort((a, b) => {
      const pinDelta = byPin(a, b);
      if (pinDelta !== 0) return pinDelta;
      if (sortKey === "name") return a.name.localeCompare(b.name);
      return (b.updated_at ?? "").localeCompare(a.updated_at ?? "");
    });
  }, [workspacesQuery.data, search, sortKey, pinned]);

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100">
          Workspaces
        </h1>
        <div className="flex items-center gap-2">
          {showSearch ? (
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-zinc-400 dark:text-zinc-500"
                aria-hidden
              />
              <input
                autoFocus
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                onBlur={() => {
                  if (!search) setShowSearch(false);
                }}
                placeholder="Search workspaces"
                aria-label="Search workspaces"
                className="w-48 rounded-md border border-zinc-200 bg-white py-1.5 pl-7 pr-2 text-sm text-zinc-900 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100"
              />
            </div>
          ) : (
            <button
              type="button"
              aria-label="Search workspaces"
              onClick={() => setShowSearch(true)}
              className="rounded-md p-2 text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
            >
              <Search className="size-4" aria-hidden />
            </button>
          )}

          <div ref={sortMenuRef} className="relative">
            <button
              type="button"
              onClick={() => setSortMenuOpen((v) => !v)}
              className="flex items-center gap-1 rounded-md border border-zinc-200 px-3 py-1.5 text-sm text-zinc-600 hover:bg-zinc-100 dark:border-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-900"
            >
              Sort by{" "}
              <span className="font-medium text-zinc-900 dark:text-zinc-100">
                {SORT_LABEL[sortKey]}
              </span>
              <ChevronDown
                className={cn(
                  "size-3.5 transition-transform",
                  sortMenuOpen && "rotate-180",
                )}
                aria-hidden
              />
            </button>
            {sortMenuOpen && (
              // Items are inset from this container's own edges (`p-1.5`
              // here, `rounded-md` per item below) rather than full-bleed -
              // see AccountMenu for why.
              <div className="absolute right-0 top-full z-10 mt-1 w-36 rounded-md border border-zinc-200 bg-white p-1.5 dark:border-zinc-800 dark:bg-zinc-950">
                {(Object.keys(SORT_LABEL) as SortKey[]).map((key) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => {
                      setSortKey(key);
                      setSortMenuOpen(false);
                    }}
                    className={cn(
                      "block w-full rounded-md px-2.5 py-2 text-left text-sm",
                      key === sortKey
                        ? "text-zinc-900 dark:text-zinc-100"
                        : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900",
                    )}
                  >
                    {SORT_LABEL[key]}
                  </button>
                ))}
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={() => setShowCreateDialog(true)}
            // Same semantic primary triplet as `Button` - one token per role,
            // resolved per theme, so no `dark:` pair and no specificity hacks.
            className="flex items-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-fg hover:bg-primary-hover"
          >
            <Plus className="size-3.5" aria-hidden />
            New workspace
          </button>
        </div>
      </div>

      {showLoading && <p className="shimmer-text mt-6 text-sm">Loading...</p>}

      {/* A failed fetch is not an empty account. Without this branch the error
 falls through to the empty state below and the page says"create a
 workspace to get started"to someone who already has several - the
 same class of failure I1 exists to prevent on the backend, where a
 degraded path must never look like a healthy one. The likeliest cause
 is an expired session, so the copy says so and offers a retry rather
 than leaving the only route a manual refresh. */}
      {!showLoading && workspacesQuery.isError && (
        <div className="mt-6 space-y-2" role="alert">
          <p className="text-sm text-zinc-700 dark:text-zinc-300">
            Could not load your workspaces.
          </p>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Your session may have expired, or the server may be unreachable.
            Your workspaces have not been lost.
          </p>
          <button
            type="button"
            onClick={() => workspacesQuery.refetch()}
            disabled={workspacesQuery.isFetching}
            className="rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium hover:bg-zinc-100 disabled:opacity-60 dark:border-zinc-700 dark:hover:bg-zinc-800"
          >
            {workspacesQuery.isFetching ? "Retrying..." : "Try again"}
          </button>
        </div>
      )}

      {!showLoading &&
        !workspacesQuery.isError &&
        (workspacesQuery.data ?? []).length === 0 && (
          <p className="mt-6 text-sm text-zinc-500 dark:text-zinc-400">
            Create a workspace to upload documents and start chatting.
          </p>
        )}

      {!showLoading &&
        (workspacesQuery.data ?? []).length > 0 &&
        workspaces.length === 0 && (
          <p className="mt-6 text-sm text-zinc-500 dark:text-zinc-400">
            No workspaces match &ldquo;{search}&rdquo;.
          </p>
        )}

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {!showLoading &&
          workspaces.map((workspace) => (
            <WorkspaceCard
              key={workspace.id}
              workspace={workspace}
              pinned={pinned.has(workspace.id)}
              onTogglePin={() => togglePin(workspace.id)}
              onOpen={() => router.push(`/workspace/${workspace.id}`)}
              onRename={(name) =>
                renameMutation.mutate({ id: workspace.id, name })
              }
              onDelete={() => deleteMutation.mutate(workspace.id)}
            />
          ))}
      </div>

      {showCreateDialog && (
        <NewWorkspaceDialog
          onClose={() => setShowCreateDialog(false)}
          onCreated={(workspace) => {
            setShowCreateDialog(false);
            router.push(`/workspace/${workspace.id}`);
          }}
        />
      )}
    </div>
  );
}
