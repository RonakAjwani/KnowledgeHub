"use client";

/**
 * Workspaces on the left: upload once, open as many chats as you like inside
 * one without re-attaching the files.
 *
 * A workspace is the organising unit - its own document set - and a
 * conversation always belongs to exactly one. Only the *active* workspace's
 * conversations are ever fetched; the others stay collapsed rows with just a
 * name and a count, which is what keeps opening the sidebar an O(1) request
 * rather than one per workspace.
 *
 * Selection is real routing, not local state: this component reads which
 * workspace/conversation is active straight off the URL (`useParams`/
 * `useSearchParams`) and navigates with `useRouter`, rather than taking
 * `activeWorkspaceId`/`onSelect` props from a parent. That's what makes a
 * specific chat a real, shareable, back-button-safe URL - a plain client-state
 *"which workspace is open"variable, the previous design, could not do that.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronRight,
  FileText,
  LayoutGrid,
  LoaderCircle,
  MessageSquare,
  Pencil,
  Pin,
  Plus,
  Search,
  Trash2,
  Upload,
  X,
} from "lucide-react";
import {
  useParams,
  usePathname,
  useRouter,
  useSearchParams,
} from "next/navigation";
import {
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
} from "react";

import { AccountMenu } from "@/components/AccountMenu";
import { SidebarToggleButton } from "@/components/SidebarToggleButton";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/ui/logo";
import { useToast } from "@/components/ui/toast";
import { useSessionToken, useSessionTokenState } from "@/hooks/useSessionToken";
import { useConversationMutations } from "@/hooks/useConversationMutations";
import { useHasMounted } from "@/hooks/useHasMounted";
import { usePinnedWorkspaces } from "@/hooks/usePinnedWorkspaces";
import { useWorkspaceMutations } from "@/hooks/useWorkspaceMutations";
import { api, ApiError } from "@/lib/api";
import { WORKSPACES_KEY, conversationsKey } from "@/lib/queryKeys";
import type { Conversation, Workspace } from "@/lib/types";
import { cn, conversationLabel } from "@/lib/utils";

const ACCEPTED_EXTENSIONS = [".pdf", ".txt", ".md", ".markdown"];

function extensionOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot === -1 ? "" : filename.slice(dot).toLowerCase();
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) return `${error.message} (${error.code})`;
  return error instanceof Error ? error.message : "Request failed";
}

// ------------------------------------------------------ inline delete confirm

/**
 * A single-line delete confirmation: the item's fate stated once, inline,
 * with the two actions right beside it - no bordered card, no wrapped
 * two-sentence warning. The previous version (a full-width red-bordered box
 * with the message on its own line above stacked buttons) took up as much
 * vertical space as three rows for a decision that's one sentence. "This
 * can't be undone" is dropped as boilerplate: every delete in this sidebar
 * is permanent, so the label already implies it without repeating it below.
 */
export function InlineDeleteConfirm({
  label,
  onConfirm,
  onCancel,
  className,
  size = "xs",
}: {
  label: string;
  onConfirm: () => void;
  onCancel: () => void;
  className?: string;
  /** `sm` for the workspace grid's cards, `xs` for the sidebar's rows. */
  size?: "xs" | "sm";
}) {
  const sm = size === "sm";
  return (
    // Two rows, not one. The single-line version put the question and both
    // buttons on the same line with `truncate` on the label - which fits
    // nowhere it is actually used. A sidebar row and a workspace card are both
    // ~250-300px wide, and "Delete this workspace?" plus two buttons needs
    // ~330px, so the label truncated to "Delete t..." and the control read as
    // broken. Wrapping the label onto its own line costs one line of height
    // and is the only layout that holds at these widths without a container
    // query. `line-clamp-2` bounds it, so a very long workspace name cannot
    // grow the row without limit either.
    <div
      className={cn(
        "mt-1 rounded-md bg-red-50 dark:bg-red-950/30",
        sm ? "rounded-lg px-3 py-2" : "px-2 py-1.5",
        className,
      )}
    >
      <p
        className={cn(
          "line-clamp-2 font-medium break-words text-red-800 dark:text-red-200",
          sm ? "text-sm" : "text-xs",
        )}
      >
        {label}
      </p>
      <div className={cn("flex items-center justify-end gap-1", sm ? "mt-1.5" : "mt-1")}>
        <button
          type="button"
          onClick={onConfirm}
          className={cn(
            "shrink-0 rounded font-semibold text-red-700 hover:bg-red-100 dark:text-red-300 dark:hover:bg-red-900/50",
            sm ? "rounded-md px-2.5 py-1 text-sm" : "px-1.5 py-0.5 text-xs",
          )}
        >
          Delete
        </button>
        <button
          type="button"
          onClick={onCancel}
          className={cn(
            "shrink-0 rounded text-zinc-500 hover:bg-zinc-200 dark:text-zinc-400 dark:hover:bg-zinc-800",
            sm ? "rounded-md px-2.5 py-1 text-sm" : "px-1.5 py-0.5 text-xs",
          )}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------- inline naming

/**
 * A single-line text field that replaces itself with a button on blur/Enter -
 * used for rename. Inline rather than `window.prompt`: a native prompt
 * cannot be styled and reads as a jarring escape from the rest of the UI for
 * something this small.
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
        "text-zinc-900",
        "dark:border-accent-500 dark:bg-zinc-900 dark:text-zinc-100",
      )}
    />
  );
}

// ---------------------------------------------------------- new workspace

/**
 *"New workspace"opens this instead of the old rename-style inline field:
 * a workspace is nearly always created *to hold something*, so asking for the
 * name and the first documents in one step avoids the create -> reopen ->
 * upload round trip. Uploads run after the workspace exists (the API has
 * nowhere to attach a file before that), sequentially for the same reason
 * `DocumentManager` uploads sequentially - the API process shares its 512 MB
 * with in-process ingest tasks, so concurrent multipart bodies risk an OOM.
 */
export function NewWorkspaceDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (workspace: Workspace) => void;
}) {
  const getToken = useSessionToken();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const [name, setName] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [rejected, setRejected] = useState<string[]>([]);
  const [uploadingName, setUploadingName] = useState<string | null>(null);
  const [uploadErrors, setUploadErrors] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dragDepthRef = useRef(0);

  const submitMutation = useMutation({
    mutationFn: async () => {
      const token = await getToken();
      const workspace = await api.createWorkspace(name.trim(), token);

      for (const file of files) {
        setUploadingName(file.name);
        try {
          await api.uploadDocument(file, workspace.id, await getToken());
        } catch (error) {
          setUploadErrors((prev) => [
            ...prev,
            `${file.name}: ${describeError(error)}`,
          ]);
        }
      }
      setUploadingName(null);
      return workspace;
    },
    onSuccess: (workspace) => {
      void queryClient.invalidateQueries({ queryKey: WORKSPACES_KEY });
      showToast(`Workspace "${workspace.name}" created`);
      onCreated(workspace);
    },
  });

  const addFiles = (incoming: File[]) => {
    const accepted: File[] = [];
    const bad: string[] = [];
    for (const file of incoming) {
      if (ACCEPTED_EXTENSIONS.includes(extensionOf(file.name))) {
        accepted.push(file);
      } else {
        bad.push(`${file.name}: only PDF, TXT and Markdown are accepted`);
      }
    }
    setRejected(bad);
    if (accepted.length > 0) {
      setFiles((prev) => [
        ...prev,
        ...accepted.filter((file) => !prev.some((p) => p.name === file.name)),
      ]);
    }
  };

  const removeFile = (fileName: string) => {
    setFiles((prev) => prev.filter((file) => file.name !== fileName));
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragDepthRef.current = 0;
    setDragging(false);
    addFiles(Array.from(event.dataTransfer.files));
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

  const busy = submitMutation.isPending;
  const canSubmit = name.trim().length > 0 && !busy;

  // Bounded by construction, and that is the point. This label used to read
  // `Uploading ${uploadingName}...`, which put an arbitrary-length filename
  // inside a `whitespace-nowrap shrink-0` button: a real 66-character
  // factsheet name grew the submit button to 714px inside a 576px dialog,
  // and because the footer is `justify-end` the overflow went left - Cancel
  // was rendered 274px outside the dialog, floating over the page behind it.
  // The filename was redundant anyway: the file list right above already
  // spins on the row being uploaded, so a counter says the one thing the
  // list cannot ("how many are left") in a fixed number of characters.
  const uploadIndex = uploadingName
    ? files.findIndex((file) => file.name === uploadingName) + 1
    : 0;
  const submitLabel = !busy
    ? "Create workspace"
    : uploadIndex > 0
      ? `Uploading ${uploadIndex} of ${files.length}...`
      : "Creating...";

  return (
    <div
      // True-black scrim via an explicit rgba, NOT `bg-black/40`:
      // `--color-black` is indirected to `--surface-black`, which inverts to
      // near-white in dark mode, so `bg-black/40` painted a WHITE veil over
      // the dark app instead of dimming it (measured at oklab L=0.94).
      className="fixed inset-0 z-50 flex items-center justify-center bg-[rgb(0_0_0/0.45)] p-4"
      onClick={() => !busy && onClose()}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-workspace-title"
        onClick={(event) => event.stopPropagation()}
        className="w-full max-w-lg rounded-2xl border border-zinc-200 bg-white p-6 sm:p-8 dark:border-zinc-800 dark:bg-zinc-950"
      >
        <div className="flex items-center justify-between">
          <h2
            id="new-workspace-title"
            className="text-2xl font-semibold text-zinc-900 dark:text-zinc-100"
          >
            New workspace
          </h2>
          <button
            type="button"
            aria-label="Close"
            disabled={busy}
            onClick={onClose}
            className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-zinc-900"
          >
            <X className="size-5" aria-hidden />
          </button>
        </div>

        <label className="mt-6 block text-sm font-semibold text-zinc-700 dark:text-zinc-300">
          Name
          <input
            autoFocus
            value={name}
            disabled={busy}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && canSubmit) submitMutation.mutate();
            }}
            placeholder="e.g. Product Launch"
            className={cn(
              "mt-2 w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-base",
              "text-zinc-900",
              "dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100",
              "disabled:opacity-60",
            )}
          />
        </label>

        <div className="mt-6">
          <span className="block text-sm font-semibold text-zinc-700 dark:text-zinc-300">
            Initial documents{" "}
            <span className="font-normal text-zinc-400 dark:text-zinc-500">
              (optional)
            </span>
          </span>

          <input
            ref={fileInputRef}
            type="file"
            multiple
            disabled={busy}
            accept=".pdf,.txt,.md,.markdown,application/pdf,text/plain,text/markdown"
            className="hidden"
            onChange={(event) => {
              addFiles(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />

          <div
            onDrop={busy ? undefined : handleDrop}
            onDragOver={(event) => event.preventDefault()}
            onDragEnter={busy ? undefined : handleDragEnter}
            onDragLeave={busy ? undefined : handleDragLeave}
            className={cn(
              "mt-2 rounded-xl border border-dashed px-4 py-6 text-center transition-colors",
              busy && "opacity-60",
              dragging
                ? "border-accent-500 bg-accent-50 dark:border-accent-400 dark:bg-accent-500/10"
                : "border-zinc-300 bg-zinc-50/60 dark:border-zinc-700 dark:bg-zinc-900/40",
            )}
          >
            <Upload className="mx-auto size-5 text-zinc-400 dark:text-zinc-500" />
            <p className="mt-2 text-sm text-zinc-500 dark:text-zinc-400">
              Drop files here ·{" "}
              <button
                type="button"
                disabled={busy}
                className="font-medium text-zinc-900 underline underline-offset-2 dark:text-zinc-100"
                onClick={() => fileInputRef.current?.click()}
              >
                browse
              </button>
            </p>
          </div>

          {files.length > 0 && (
            <ul className="mt-3 space-y-1.5">
              {files.map((file) => (
                <li
                  key={file.name}
                  className="flex items-center gap-2.5 rounded-lg bg-zinc-100 px-3 py-2 text-sm text-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
                >
                  <FileText className="size-4 shrink-0 text-zinc-400 dark:text-zinc-500" />
                  <span className="min-w-0 flex-1 truncate">{file.name}</span>
                  {uploadingName === file.name ? (
                    <LoaderCircle className="size-3.5 shrink-0 animate-spin" />
                  ) : (
                    <button
                      type="button"
                      aria-label={`Remove ${file.name}`}
                      disabled={busy}
                      onClick={() => removeFile(file.name)}
                      className="shrink-0 rounded p-0.5 text-zinc-400 hover:bg-zinc-200 hover:text-zinc-700 disabled:opacity-40 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
                    >
                      <X className="size-3.5" aria-hidden />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {rejected.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-xs text-red-700 dark:text-red-300">
              {rejected.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          )}

          {uploadErrors.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-xs text-red-700 dark:text-red-300">
              {uploadErrors.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          )}

          {submitMutation.isError && (
            <p className="mt-2 text-xs text-red-700 dark:text-red-300">
              {describeError(submitMutation.error)}
            </p>
          )}
        </div>

        <div className="mt-8 flex justify-end gap-3">
          <Button variant="outline" size="md" disabled={busy} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="accent"
            size="md"
            disabled={!canSubmit}
            onClick={() => submitMutation.mutate()}
            // `min-w-0` overrides the base `shrink-0`'s effect on this one
            // instance so a label that somehow still outgrows the row
            // truncates instead of pushing Cancel out. The label below is
            // already bounded; this is the guard that makes the row safe
            // regardless of what any future label says.
            className="min-w-0 shrink"
          >
            {busy && <LoaderCircle className="size-3.5 shrink-0 animate-spin" />}
            <span className="truncate">{submitLabel}</span>
          </Button>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------ conversation row

/**
 * One chat in the expanded workspace's list. The same hover-reveal-icon,
 * click-to-arm, inline-confirm shape as `WorkspaceRow`'s own delete flow -
 * intentionally, since this is meant to read as"chats delete the same way
 * workspaces do,"not as a second, differently-behaved delete control the
 * user has to learn separately. Minus rename/pin: a conversation's label is
 * derived from its first message, not a user-set field, so there is nothing
 * for a rename control to edit.
 *
 * A `role="button"` div, not a `<button>`, for the same reason `WorkspaceRow`
 * uses one: the delete icon nested inside it is itself interactive, and a
 * `<button>` cannot contain another `<button>`.
 */
function ConversationRow({
  conversation,
  active,
  onNavigate,
  onDelete,
}: {
  conversation: Conversation;
  active: boolean;
  onNavigate: () => void;
  onDelete: () => void;
}) {
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onClick={onNavigate}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onNavigate();
          }
        }}
        className={cn(
          "group flex w-full cursor-pointer items-center gap-1.5 truncate rounded-md px-2 py-1 text-left text-[0.8rem]",
          active
            ? "bg-zinc-200 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
            : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900",
        )}
      >
        <MessageSquare className="size-3 shrink-0 opacity-60" aria-hidden />
        <span className="min-w-0 flex-1 truncate">
          {conversationLabel(conversation)}
        </span>
        <button
          type="button"
          aria-label="Delete chat"
          onClick={(event) => {
            event.stopPropagation();
            setConfirmingDelete(true);
          }}
          className="hidden shrink-0 rounded p-0.5 text-zinc-400 hover:bg-red-100 hover:text-red-700 group-hover:block dark:hover:bg-red-950/50 dark:hover:text-red-300"
        >
          <Trash2 className="size-3" aria-hidden />
        </button>
      </div>

      {confirmingDelete ? (
        <InlineDeleteConfirm
          label="Delete this chat?"
          onConfirm={() => {
            setConfirmingDelete(false);
            onDelete();
          }}
          onCancel={() => setConfirmingDelete(false)}
        />
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------- workspace row

function WorkspaceRow({
  workspace,
  active,
  activeConversationId,
  pinned,
  onTogglePin,
  onNavigate,
  onDelete,
  onRename,
}: {
  workspace: Workspace;
  active: boolean;
  activeConversationId: string | null;
  pinned: boolean;
  onTogglePin: () => void;
  onNavigate: (workspaceId: string, conversationId: string | null) => void;
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

  // Navigate off a conversation the moment it's the one just deleted - the
  // alternative is a URL that still points at `?c=<deleted id>`, which
  // `WorkspacePage` would then try to load history for and fail.
  const { deleteMutation: deleteConversationMutation } =
    useConversationMutations(workspace.id, (deletedId) => {
      if (deletedId === activeConversationId) onNavigate(workspace.id, null);
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
        onClick={() => onNavigate(workspace.id, null)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            onNavigate(workspace.id, null);
          }
        }}
        className={cn(
          "group flex w-full cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 text-sm",
          // Selected state is a neutral tint, not an accent wash - a
          // persistently-visible surface shouldn't carry the one hue that's
          // supposed to mean"this is the primary action."In dark mode the
          // fill stops at z900 rather than going a further step to z800:
          // z800 (#333) is light enough to squeeze the muted count text
          // below AA, and there is no muted step bright enough to sit on it
          // without inverting the ramp's own order.
          active
            ? "bg-zinc-200 text-zinc-900 dark:bg-zinc-900 dark:text-zinc-100"
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
        <span className="min-w-0 flex-1 truncate font-medium">
          {workspace.name}
        </span>
        {/* One step stronger than the usual muted pair (`400/dark:500`):
 this count sits on the *selected* row fill as well as the plain
 sidebar, and the lighter pair measured 3.3:1 there. */}
        <span className="shrink-0 text-xs text-zinc-500 dark:text-zinc-400">
          {workspace.document_count}{" "}
          {workspace.document_count === 1 ? "file" : "files"}
        </span>
        <span className="hidden shrink-0 items-center gap-0.5 group-hover:flex">
          <button
            type="button"
            aria-label={pinned ? "Unpin workspace" : "Pin workspace"}
            onClick={(event) => {
              event.stopPropagation();
              onTogglePin();
            }}
            className={cn(
              "rounded p-0.5 hover:bg-zinc-200 dark:hover:bg-zinc-800",
              // A pinned mark is a near-white fill in dark mode, not the
              // accent color - sampled off the real app (#F9F9F7,
              // neutralized). The accent stays reserved for the one primary
              // action per screen; a pin isn't that.
              pinned
                ? "text-zinc-900 dark:text-zinc-100"
                : "text-zinc-300 hover:text-zinc-700 dark:hover:text-zinc-200",
            )}
          >
            <Pin
              className={cn("size-3", pinned && "fill-current")}
              aria-hidden
            />
          </button>
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
        <InlineDeleteConfirm
          label={`Delete "${workspace.name}"?`}
          className="ml-5"
          onConfirm={() => {
            setConfirmingDelete(false);
            onDelete(workspace);
          }}
          onCancel={() => setConfirmingDelete(false)}
        />
      ) : null}

      {active ? (
        <div className="ml-5 mt-0.5 space-y-0.5 border-l border-zinc-200 pl-2 dark:border-zinc-800">
          {(conversationsQuery.data ?? []).map((conversation: Conversation) => (
            <ConversationRow
              key={conversation.id}
              conversation={conversation}
              active={conversation.id === activeConversationId}
              onNavigate={() => onNavigate(workspace.id, conversation.id)}
              onDelete={() => deleteConversationMutation.mutate(conversation.id)}
            />
          ))}

          <button
            type="button"
            onClick={() => onNavigate(workspace.id, null)}
            className={cn(
              "flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[0.8rem] font-medium",
              activeConversationId === null
                ? "text-zinc-900 dark:text-zinc-100"
                : "text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900",
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

export function WorkspaceSidebar({ className }: { className?: string }) {
  const { getToken, isLoaded, isSignedIn } = useSessionTokenState();
  const router = useRouter();
  const pathname = usePathname();
  const params = useParams<{ workspaceId?: string }>();
  const searchParams = useSearchParams();
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [search, setSearch] = useState("");

  // `useParams()` reflects whichever leaf route is currently active - `{}` on
  // `/workspaces`, `{workspaceId}` on `/workspace/[workspaceId]` - since this
  // component renders once, shared, inside the `(app)` layout that wraps both.
  const activeWorkspaceId = pathname.startsWith("/workspace/")
    ? (params.workspaceId ?? null)
    : null;
  const activeConversationId = searchParams.get("c");

  const navigate = (workspaceId: string, conversationId: string | null) => {
    router.push(
      conversationId
        ? `/workspace/${workspaceId}?c=${conversationId}`
        : `/workspace/${workspaceId}`,
    );
  };

  const workspacesQuery = useQuery({
    queryKey: WORKSPACES_KEY,
    queryFn: async () => api.listWorkspaces(await getToken()),
    // Firing before Clerk has hydrated a session gets a token the backend
    // rejects as expired - a race, not a real expiry. Waiting for `isLoaded`
    // (and a signed-in session) means this fetches once, with a real token,
    // instead of failing immediately on mount and needing the 401 retry to
    // paper over it.
    enabled: isLoaded && isSignedIn,
  });

  const { pinned, toggle: togglePin } = usePinnedWorkspaces();

  // Forces the server-matching first client paint through the loading
  // branch even if the query has already resolved by then (a fast localhost
  // fetch can beat hydration) - see hooks/useHasMounted.ts for why this is
  // the fix for the"isLoading differs between SSR and hydration"mismatch,
  // not a cosmetic workaround.
  const hasMounted = useHasMounted();
  // `isPending`, not `isLoading`: a query held off by `enabled: isLoaded &&
  // isSignedIn` never starts fetching, so `isLoading` (`isPending &&
  // isFetching`) would read `false` while still waiting on Clerk and this
  // would flash the empty state before the first real fetch even starts.
  const showLoading = !hasMounted || workspacesQuery.isPending;

  const workspaces = workspacesQuery.data ?? [];
  const filteredWorkspaces = useMemo(() => {
    const q = search.trim().toLowerCase();
    const all = workspacesQuery.data ?? [];
    if (!q) return all;
    return all.filter((w) => w.name.toLowerCase().includes(q));
  }, [workspacesQuery.data, search]);

  // Pinned workspaces get their own section, same grouping the reference
  // uses - shares `usePinnedWorkspaces` with the /workspaces grid, so
  // pinning from either surface shows up in both.
  const pinnedWorkspaces = useMemo(
    () => filteredWorkspaces.filter((w) => pinned.has(w.id)),
    [filteredWorkspaces, pinned],
  );
  const unpinnedWorkspaces = useMemo(
    () => filteredWorkspaces.filter((w) => !pinned.has(w.id)),
    [filteredWorkspaces, pinned],
  );

  const { renameMutation, deleteMutation } = useWorkspaceMutations(
    (deletedId) => {
      if (deletedId !== activeWorkspaceId) return;
      const remaining = workspaces.filter((w) => w.id !== deletedId);
      if (remaining[0]) navigate(remaining[0].id, null);
      else router.push("/workspaces");
    },
  );

  // No icon-rail collapsed state: collapsing hides the sidebar entirely
  // (matching the reference), and `(app)/layout.tsx` is what decides whether
  // to mount this component at all - a floating toggle button there (not
  // owned by this component, since it has to stay reachable when this
  // component isn't rendered) brings it back.

  return (
    <aside className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center gap-1.5 px-3 py-3">
        <span className="flex flex-1 items-center gap-1.5 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          <Logo className="size-4 shrink-0" />
          KnowledgeHub
        </span>
        <SidebarToggleButton />
      </div>

      <div className="px-2 pb-2">
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-zinc-400 dark:text-zinc-500"
            aria-hidden
          />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search workspaces"
            aria-label="Search workspaces"
            className={cn(
              "w-full rounded-md border border-zinc-200 bg-white py-1.5 pl-7 pr-2 text-sm",
              "text-zinc-900 placeholder:text-zinc-400",
              "dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-100 dark:placeholder:text-zinc-500",
            )}
          />
        </div>

        <button
          type="button"
          onClick={() => setShowCreateDialog(true)}
          className="mt-2 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900"
        >
          <Plus
            className="size-3.5 text-zinc-400 dark:text-zinc-500"
            aria-hidden
          />
          New workspace
        </button>

        <button
          type="button"
          onClick={() => router.push("/workspaces")}
          className={cn(
            "mt-0.5 flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium",
            pathname === "/workspaces"
              ? "bg-zinc-200 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
              : "text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900",
          )}
        >
          <LayoutGrid
            className="size-3.5 text-zinc-400 dark:text-zinc-500"
            aria-hidden
          />
          Workspaces
        </button>
      </div>

      {showCreateDialog && (
        <NewWorkspaceDialog
          onClose={() => setShowCreateDialog(false)}
          onCreated={(workspace) => {
            setShowCreateDialog(false);
            navigate(workspace.id, null);
          }}
        />
      )}

      {/* `pt-1.5` is not cosmetic: `overflow-y-auto` clips at this element's
 padding box, and the focus ring (globals.css) is drawn 4px *outside*
 a row's border box. With no top padding the first row sat flush
 against that boundary and the top of its ring was sliced off. 6px of
 padding gives the ring its 4px of clearance. Any scroll container
 holding focusable children needs >= 4px of padding on every side for
 the same reason. */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3 pt-1.5">
        {showLoading ? (
          <p className="shimmer-text px-2 py-1 text-sm">Loading...</p>
        ) : null}

        {!showLoading && workspaces.length === 0 ? (
          <p className="px-2 py-1 text-xs text-zinc-500 dark:text-zinc-400">
            Create a workspace to upload documents and start chatting.
          </p>
        ) : null}

        {!showLoading &&
        workspaces.length > 0 &&
        filteredWorkspaces.length === 0 ? (
          <p className="px-2 py-1 text-xs text-zinc-500 dark:text-zinc-400">
            No workspaces match &ldquo;{search}&rdquo;.
          </p>
        ) : null}

        {!showLoading && pinnedWorkspaces.length > 0 && (
          <div className="mb-3">
            <p className="px-2 pb-1 text-[0.7rem] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
              Pinned
            </p>
            <div className="space-y-0.5">
              {pinnedWorkspaces.map((workspace) => (
                <WorkspaceRow
                  key={workspace.id}
                  workspace={workspace}
                  active={workspace.id === activeWorkspaceId}
                  activeConversationId={
                    workspace.id === activeWorkspaceId
                      ? activeConversationId
                      : null
                  }
                  pinned={true}
                  onTogglePin={() => togglePin(workspace.id)}
                  onNavigate={navigate}
                  onDelete={(w) => deleteMutation.mutate(w.id)}
                  onRename={(w, name) =>
                    renameMutation.mutate({ id: w.id, name })
                  }
                />
              ))}
            </div>
          </div>
        )}

        {!showLoading && unpinnedWorkspaces.length > 0 && (
          <div>
            {pinnedWorkspaces.length > 0 && (
              <p className="px-2 pb-1 text-[0.7rem] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
                Workspaces
              </p>
            )}
            <div className="space-y-0.5">
              {unpinnedWorkspaces.map((workspace) => (
                <WorkspaceRow
                  key={workspace.id}
                  workspace={workspace}
                  active={workspace.id === activeWorkspaceId}
                  activeConversationId={
                    workspace.id === activeWorkspaceId
                      ? activeConversationId
                      : null
                  }
                  pinned={false}
                  onTogglePin={() => togglePin(workspace.id)}
                  onNavigate={navigate}
                  onDelete={(w) => deleteMutation.mutate(w.id)}
                  onRename={(w, name) =>
                    renameMutation.mutate({ id: w.id, name })
                  }
                />
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="border-t border-zinc-200 px-2 py-2 dark:border-zinc-800">
        <AccountMenu />
      </div>
    </aside>
  );
}
