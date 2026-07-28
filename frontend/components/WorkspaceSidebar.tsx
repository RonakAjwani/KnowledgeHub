"use client";

/**
 * Workspaces on the left: upload once, open as many chats as you like inside
 * one without re-attaching the files.
 *
 * A workspace is the organising unit — its own document set — and a
 * conversation always belongs to exactly one. Only the *active* workspace's
 * conversations are ever fetched; the others stay collapsed rows with just a
 * name and a count, which is what keeps opening the sidebar an O(1) request
 * rather than one per workspace.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  MessageSquare,
  Pencil,
  Plus,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useRef, useState, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { useSessionToken } from "@/hooks/useSessionToken";
import { api } from "@/lib/api";
import type { Conversation, Workspace } from "@/lib/types";
import { cn } from "@/lib/utils";

const WORKSPACES_KEY = ["workspaces"] as const;
const conversationsKey = (workspaceId: string) =>
  ["conversations", workspaceId] as const;

export interface WorkspaceSidebarProps {
  activeWorkspaceId: string | null;
  activeConversationId: string | null;
  /** `conversationId: null` selects "new chat" inside that workspace. */
  onSelect: (workspaceId: string, conversationId: string | null) => void;
  className?: string;
}

// ---------------------------------------------------------- inline naming

/**
 * A single-line text field that replaces itself with a button on blur/Enter —
 * used for both "create" (empty starting value) and "rename" (pre-filled).
 * Inline rather than `window.prompt`: a native prompt cannot be styled and
 * reads as a jarring escape from the rest of the UI for something this small.
 */
function InlineNameField({
  initialValue = "",
  placeholder,
  onCommit,
  onCancel,
}: {
  initialValue?: string;
  placeholder: string;
  onCommit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initialValue);
  const ref = useRef<HTMLInputElement>(null);

  const commit = () => {
    const trimmed = value.trim();
    if (trimmed) onCommit(trimmed);
    else onCancel();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commit();
    } else if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
    }
  };

  return (
    <input
      ref={ref}
      autoFocus
      value={value}
      placeholder={placeholder}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={onKeyDown}
      onBlur={commit}
      onClick={(event) => event.stopPropagation()}
      className={cn(
        "w-full rounded-md border border-accent-400 bg-white px-2 py-1 text-sm",
        "text-zinc-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500",
        "dark:border-accent-500 dark:bg-zinc-900 dark:text-zinc-100",
      )}
    />
  );
}

// --------------------------------------------------------------- workspace row

function WorkspaceRow({
  workspace,
  active,
  activeConversationId,
  onSelect,
  onDelete,
  onRename,
}: {
  workspace: Workspace;
  active: boolean;
  activeConversationId: string | null;
  onSelect: (workspaceId: string, conversationId: string | null) => void;
  onDelete: (workspace: Workspace) => void;
  onRename: (workspace: Workspace, name: string) => void;
}) {
  const [renaming, setRenaming] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const getToken = useSessionToken();

  const conversationsQuery = useQuery({
    queryKey: conversationsKey(workspace.id),
    queryFn: async () => api.listConversations(workspace.id, await getToken()),
    enabled: active,
  });

  if (renaming) {
    return (
      <div className="px-2 py-1">
        <InlineNameField
          initialValue={workspace.name}
          placeholder="Workspace name"
          onCommit={(name) => {
            setRenaming(false);
            onRename(workspace, name);
          }}
          onCancel={() => setRenaming(false)}
        />
      </div>
    );
  }

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onClick={() => onSelect(workspace.id, null)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onSelect(workspace.id, null);
          }
        }}
        className={cn(
          "group flex w-full cursor-pointer items-center gap-1.5 rounded-md px-2 py-1.5 text-sm",
          active
            ? "bg-accent-50 text-zinc-900 dark:bg-accent-500/10 dark:text-zinc-100"
            : "text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900",
        )}
      >
        <ChevronRight
          className={cn(
            "size-3.5 shrink-0 text-zinc-400 transition-transform dark:text-zinc-500",
            active && "rotate-90",
          )}
          aria-hidden
        />
        <span className="min-w-0 flex-1 truncate font-medium">{workspace.name}</span>
        <span className="shrink-0 text-xs text-zinc-400 dark:text-zinc-500">
          {workspace.document_count} {workspace.document_count === 1 ? "file" : "files"}
        </span>
        <span className="hidden shrink-0 items-center gap-0.5 group-hover:flex">
          <button
            type="button"
            aria-label="Rename workspace"
            onClick={(event) => {
              event.stopPropagation();
              setRenaming(true);
            }}
            className="rounded p-0.5 text-zinc-400 hover:bg-zinc-200 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
          >
            <Pencil className="size-3" aria-hidden />
          </button>
          <button
            type="button"
            aria-label="Delete workspace"
            onClick={(event) => {
              event.stopPropagation();
              setConfirmingDelete(true);
            }}
            className="rounded p-0.5 text-zinc-400 hover:bg-red-100 hover:text-red-700 dark:hover:bg-red-950/50 dark:hover:text-red-300"
          >
            <Trash2 className="size-3" aria-hidden />
          </button>
        </span>
      </div>

      {confirmingDelete ? (
        <div className="ml-5 mt-1 rounded-md border border-red-200 bg-red-50 p-2 text-xs dark:border-red-900 dark:bg-red-950/30">
          <p className="text-red-800 dark:text-red-200">
            Delete &ldquo;{workspace.name}&rdquo; and its {workspace.document_count}{" "}
            {workspace.document_count === 1 ? "document" : "documents"}? This
            cannot be undone.
          </p>
          <div className="mt-1.5 flex gap-1.5">
            <Button
              variant="destructive"
              size="sm"
              onClick={() => {
                setConfirmingDelete(false);
                onDelete(workspace);
              }}
            >
              Delete
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirmingDelete(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}

      {active ? (
        <div className="ml-5 mt-0.5 space-y-0.5 border-l border-zinc-200 pl-2 dark:border-zinc-800">
          {(conversationsQuery.data ?? []).map((conversation: Conversation) => (
            <button
              key={conversation.id}
              type="button"
              onClick={() => onSelect(workspace.id, conversation.id)}
              className={cn(
                "flex w-full items-center gap-1.5 truncate rounded-md px-2 py-1 text-left text-[0.8rem]",
                conversation.id === activeConversationId
                  ? "bg-accent-100 text-zinc-900 dark:bg-accent-500/15 dark:text-zinc-100"
                  : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900",
              )}
            >
              <MessageSquare className="size-3 shrink-0 opacity-60" aria-hidden />
              <span className="truncate">
                {conversation.title || "Untitled chat"}
              </span>
            </button>
          ))}

          <button
            type="button"
            onClick={() => onSelect(workspace.id, null)}
            className={cn(
              "flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[0.8rem] font-medium",
              activeConversationId === null
                ? "text-accent-700 dark:text-accent-400"
                : "text-accent-600 hover:bg-accent-50 dark:text-accent-500 dark:hover:bg-accent-500/10",
            )}
          >
            <Plus className="size-3 shrink-0" aria-hidden />
            New chat
          </button>
        </div>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------- sidebar

export function WorkspaceSidebar({
  activeWorkspaceId,
  activeConversationId,
  onSelect,
  className,
}: WorkspaceSidebarProps) {
  const getToken = useSessionToken();
  const queryClient = useQueryClient();
  const [creating, setCreating] = useState(false);

  const workspacesQuery = useQuery({
    queryKey: WORKSPACES_KEY,
    queryFn: async () => api.listWorkspaces(await getToken()),
  });

  const createMutation = useMutation({
    mutationFn: async (name: string) => api.createWorkspace(name, await getToken()),
    onSuccess: (workspace) => {
      void queryClient.invalidateQueries({ queryKey: WORKSPACES_KEY });
      onSelect(workspace.id, null);
    },
  });

  const renameMutation = useMutation({
    mutationFn: async ({ id, name }: { id: string; name: string }) =>
      api.renameWorkspace(id, name, await getToken()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: WORKSPACES_KEY });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => api.deleteWorkspace(id, await getToken()),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({ queryKey: WORKSPACES_KEY });
      if (id === activeWorkspaceId) {
        const remaining = (workspacesQuery.data ?? []).filter((w) => w.id !== id);
        if (remaining[0]) onSelect(remaining[0].id, null);
      }
    },
  });

  const workspaces = workspacesQuery.data ?? [];

  return (
    <aside className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center gap-1.5 px-3 py-3">
        <Sparkles className="size-4 text-accent-600 dark:text-accent-400" aria-hidden />
        <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          KnowledgeHub
        </span>
      </div>

      <div className="px-2">
        {creating ? (
          <div className="px-1 pb-2">
            <InlineNameField
              placeholder="Workspace name"
              onCommit={(name) => {
                setCreating(false);
                createMutation.mutate(name);
              }}
              onCancel={() => setCreating(false)}
            />
          </div>
        ) : (
          <Button
            variant="accent"
            size="sm"
            className="mb-2 w-full justify-center"
            onClick={() => setCreating(true)}
          >
            <Plus className="size-3.5" aria-hidden />
            New workspace
          </Button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        <p className="px-2 pb-1 text-[0.7rem] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
          Workspaces
        </p>

        {workspacesQuery.isLoading ? (
          <p className="shimmer-text px-2 py-1 text-sm">Loading…</p>
        ) : null}

        {!workspacesQuery.isLoading && workspaces.length === 0 ? (
          <p className="px-2 py-1 text-xs text-zinc-500 dark:text-zinc-400">
            Create a workspace to upload documents and start chatting.
          </p>
        ) : null}

        <div className="space-y-0.5">
          {workspaces.map((workspace) => (
            <WorkspaceRow
              key={workspace.id}
              workspace={workspace}
              active={workspace.id === activeWorkspaceId}
              activeConversationId={
                workspace.id === activeWorkspaceId ? activeConversationId : null
              }
              onSelect={onSelect}
              onDelete={(w) => deleteMutation.mutate(w.id)}
              onRename={(w, name) => renameMutation.mutate({ id: w.id, name })}
            />
          ))}
        </div>
      </div>
    </aside>
  );
}
