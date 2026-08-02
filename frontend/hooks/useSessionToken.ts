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
 *
 * Also exposes `isLoaded`/`isSignedIn`, so a query that needs auth can hold
 * off firing until Clerk has actually hydrated a session. Without this, a
 * `useQuery` that fires on mount can call `getToken()` before Clerk is ready
 * and get back a token the backend rejects as expired/invalid - indistinguishable
 * from a real expiry, but really just a race between the fetch and the SDK.
 * Dev mode has no session to wait on, so it reports itself as always loaded
 * and signed in.
 */

import { useAuth } from "@clerk/nextjs";
import { useCallback } from "react";

import { CLERK_ENABLED } from "@/lib/utils";

export type TokenGetter = () => Promise<string | null>;

export interface SessionToken {
  getToken: TokenGetter;
  isLoaded: boolean;
  isSignedIn: boolean;
}

function useClerkSessionToken(): SessionToken {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  return { getToken, isLoaded, isSignedIn: isSignedIn ?? false };
}

function useAnonymousSessionToken(): SessionToken {
  const getToken = useCallback(async () => null, []);
  return { getToken, isLoaded: true, isSignedIn: true };
}

const useSessionTokenState: () => SessionToken = CLERK_ENABLED
  ? useClerkSessionToken
  : useAnonymousSessionToken;

/** Back-compat shorthand for callers that only need the token getter. */
export function useSessionToken(): TokenGetter {
  return useSessionTokenState().getToken;
}

export { useSessionTokenState };
