"use client";

/**
 * Get a fresh Clerk session token, or `null` in dev mode.
 *
 * Picked once at module scope, not per render: `useAuth()` throws outside a
 * `ClerkProvider`, and `CLERK_ENABLED` is a build-time constant, so branching
 * here is a stable choice of hook rather than a conditional hook call. Calling
 * `useAuth()` unconditionally would crash the dev-mode build (no publishable
 * key, no provider mounted) on the first render of anything that needs a token.
 *
 * Shared by every component that calls the API with auth - originally lived
 * only inside `useChatStream`, duplicated here rather than imported so a
 * workspace sidebar (or anything else) doesn't have to render a chat stream
 * just to get a token.
 */

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

import { CLERK_ENABLED } from "@/lib/utils";

export type TokenGetter = () => Promise<string | null>;

function useClerkToken(): TokenGetter {
  const { getToken } = useAuth();
  return getToken;
}

function useAnonymousToken(): TokenGetter {
  return useCallback(async () => null, []);
}

export const useSessionToken: () => TokenGetter = CLERK_ENABLED
  ? useClerkToken
  : useAnonymousToken;
