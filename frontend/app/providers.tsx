"use client";

/**
 * Client providers.
 *
 * `ClerkProvider` is mounted **conditionally**. Without a publishable key the app
 * runs unauthenticated and the backend's `AUTH_MODE=dev` assigns every caller a
 * fixed user - which is what lets `docker compose up` work on a clean clone with
 * no Clerk account. Mounting the provider regardless would render a sign-in wall
 * that nothing can satisfy.
 */

import { ClerkProvider } from "@clerk/nextjs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { CLERK_ENABLED } from "@/lib/utils";

export function Providers({ children }: { children: ReactNode }) {
  // Created in state, not at module scope: a module-level client would be shared
  // across requests during SSR and leak one user's cache into another's render.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            retry: (failureCount, error) => {
              // Don't retry the 4xx cases - a 404 or 415 will not become a 200.
              const status = (error as { status?: number })?.status;
              if (status && status >= 400 && status < 500) return false;
              return failureCount < 2;
            },
          },
        },
      }),
  );

  const tree = <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;

  return CLERK_ENABLED ? <ClerkProvider>{tree}</ClerkProvider> : tree;
}
