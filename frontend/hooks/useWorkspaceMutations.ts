"use client";

/**
 * Rename/delete mutations for a workspace, shared by `WorkspaceSidebar`'s
 * compact list and `/workspaces`'s card grid - both need the exact same
 * optimistic-invalidation behaviour, and duplicating it per-surface is how the
 * two would eventually drift (one gets a bugfix, the other doesn't).
 *
 * Create is deliberately *not* here: `NewWorkspaceDialog` already owns create,
 * bundled with its own optional initial-document upload - a plain "create a
 * workspace" mutation would be a second, thinner path to the same action.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useSessionToken } from "@/hooks/useSessionToken";
import { api } from "@/lib/api";
import { WORKSPACES_KEY } from "@/lib/queryKeys";

export function useWorkspaceMutations(onDeleted?: (id: string) => void) {
  const getToken = useSessionToken();
  const queryClient = useQueryClient();

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
      onDeleted?.(id);
    },
  });

  return { renameMutation, deleteMutation };
}
