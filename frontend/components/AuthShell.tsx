import type { ReactNode } from "react";

/** Centered card on a cream background, with the KnowledgeHub wordmark above
 * it - the shared frame both `/sign-in` and `/sign-up` render Clerk's
 * embedded form inside. Plain text wordmark, no icon: no "AI sparkle" marker
 * in this UI, and there's no real logo mark yet either. */
export function AuthShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-50 px-4 py-12 dark:bg-zinc-950">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center">
          <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
            KnowledgeHub
          </span>
        </div>
        {children}
      </div>
    </div>
  );
}
