"use client";

/**
 * Delete mutation for a conversation - the sidebar's per-chat equivalent of
 * `useWorkspaceMutations`'s `deleteMutation`. Rename has no counterpart here:
 * a conversation's name is derived (`_derive_title` on the backend, from its
 * first message), not a user-set field the way a workspace's `name` is, so
 * there is nothing for a rename affordance to edit.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { useToast } from "@/components/ui/toast";
import { useSessionToken } from "@/hooks/useSessionToken";
import { api } from "@/lib/api";
import { conversationsKey } from "@/lib/queryKeys";

export function useConversationMutations(
  workspaceId: string,
  onDeleted?: (id: string) => void,
) {
  const getToken = useSessionToken();
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  const deleteMutation = useMutation({
    mutationFn: async (id: string) =>
      api.deleteConversation(id, await getToken()),
    onSuccess: (_data, id) => {
      void queryClient.invalidateQueries({
        queryKey: conversationsKey(workspaceId),
      });
      showToast("Chat deleted");
      onDeleted?.(id);
    },
  });

  return { deleteMutation };
}
