/**
 * REST client. Calls FastAPI **directly** from the browser.
 *
 * Deliberately not proxied through Next route handlers: a proxy hop would add
 * buffering risk to a token stream for no benefit, and the backend already
 * verifies Clerk JWTs itself. The only thing a proxy would add here is a place
 * for the stream to stall.
 */

import type {
  ApiErrorBody,
  Conversation,
  DocumentDetail,
  DocumentSummary,
  PersistedMessage,
  Workspace,
} from "./types";

export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  token?: string | null,
): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...(init.body instanceof FormData
        ? {}
        : { "content-type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    let body: ApiErrorBody | null = null;
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(
      response.status,
      body?.error.code ?? "dependency_unavailable",
      body?.error.message ?? `Request failed with ${response.status}`,
      body?.error.request_id,
    );
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  listDocuments: (workspaceId?: string | null, token?: string | null) =>
    request<DocumentSummary[]>(
      `/documents${workspaceId ? `?workspace_id=${workspaceId}` : ""}`,
      {},
      token,
    ),

  getDocument: (id: string, token?: string | null) =>
    request<DocumentDetail>(`/documents/${id}`, {}, token),

  /**
   * Upload returns 201 for a new document and **200 with the existing one** when
   * the same bytes were already uploaded — re-uploading is not an error. The
   * same bytes uploaded into a *different* workspace is a distinct document,
   * though: the dedup key includes `workspace_id`, so this never silently
   * reparents a file that already lives somewhere else.
   */
  uploadDocument: async (
    file: File,
    workspaceId?: string | null,
    token?: string | null,
  ) => {
    const form = new FormData();
    form.append("file", file);
    if (workspaceId) form.append("workspace_id", workspaceId);
    return request<DocumentSummary>(
      "/documents",
      { method: "POST", body: form },
      token,
    );
  },

  deleteDocument: (id: string, token?: string | null) =>
    request<void>(`/documents/${id}`, { method: "DELETE" }, token),

  listConversations: (workspaceId?: string | null, token?: string | null) =>
    request<Conversation[]>(
      `/conversations${workspaceId ? `?workspace_id=${workspaceId}` : ""}`,
      {},
      token,
    ),

  listWorkspaces: (token?: string | null) =>
    request<Workspace[]>("/workspaces", {}, token),

  createWorkspace: (name: string, token?: string | null) =>
    request<Workspace>(
      "/workspaces",
      { method: "POST", body: JSON.stringify({ name }) },
      token,
    ),

  renameWorkspace: (id: string, name: string, token?: string | null) =>
    request<Workspace>(
      `/workspaces/${id}`,
      { method: "PUT", body: JSON.stringify({ name }) },
      token,
    ),

  deleteWorkspace: (id: string, token?: string | null) =>
    request<void>(`/workspaces/${id}`, { method: "DELETE" }, token),

  getConversation: (id: string, token?: string | null) =>
    request<{ id: string; title: string | null; messages: PersistedMessage[] }>(
      `/conversations/${id}`,
      {},
      token,
    ),

  /**
   * The disconnect-recovery path. The stream is not resumable by design, so a
   * client that dropped mid-turn refetches final state — including verification
   * verdicts that landed after it stopped listening.
   */
  getMessage: (id: string, token?: string | null) =>
    request<PersistedMessage>(`/messages/${id}`, {}, token),
};
