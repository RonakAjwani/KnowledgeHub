/**
 * Single source of truth for TanStack Query keys that more than one component
 * reads - `WorkspaceSidebar`, the `/workspaces` grid, and the breadcrumb all need
 * the workspaces list; the sidebar and the workspace home view both need a
 * workspace's conversations. Defining each key once here means all three
 * always share the same cache entry instead of drifting into near-duplicate
 * keys that silently stop deduping.
 */

export const WORKSPACES_KEY = ["workspaces"] as const;

/** `workspaceId: null` is its own cache slot ("no filter"), not an alias for
 * "everything" reusing another slot - same idiom `documentsKey` already used
 * for "no workspace open yet". */
export const conversationsKey = (workspaceId: string | null) =>
  ["conversations", workspaceId] as const;

export const documentsKey = (workspaceId: string | null) =>
  ["documents", workspaceId] as const;
