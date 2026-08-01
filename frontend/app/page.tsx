/**
 * `/` - public marketing landing page, or a straight redirect into the app.
 *
 * Signed-in visitors (and every visitor at all when Clerk isn't configured -
 * `AUTH_MODE=dev` has no real signed-out state to market to) skip straight to
 * `/workspaces`. Only a Clerk-enabled, signed-out visitor sees this page, which
 * is why `middleware.ts` had to add `/` to its public-route matcher - every
 * other route stays behind `auth.protect()`.
 */

import { auth } from "@clerk/nextjs/server";
import Link from "next/link";
import { redirect } from "next/navigation";

import { Logo } from "@/components/ui/logo";
import { CLERK_ENABLED } from "@/lib/utils";

export default async function LandingPage() {
  if (!CLERK_ENABLED) redirect("/workspaces");

  const { userId } = await auth();
  if (userId) redirect("/workspaces");

  return (
    <div className="flex min-h-screen flex-col bg-zinc-50 dark:bg-zinc-950">
      <header className="flex items-center gap-2 px-6 py-5">
        <Logo className="size-5 shrink-0 text-zinc-900 dark:text-zinc-100" />
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          KnowledgeHub
        </span>
      </header>

      <main className="flex flex-1 flex-col items-center justify-center px-6 text-center">
        <h1 className="max-w-xl text-3xl font-semibold text-zinc-900 dark:text-zinc-100">
          Ask questions across your own documents, with verifiable citations.
        </h1>
        <p className="mt-3 max-w-md text-sm text-zinc-600 dark:text-zinc-400">
          Upload your documents into a workspace once, then open as many
          conversations against them as you like. Every answer links back to
          the exact passage it came from.
        </p>

        <div className="mt-8 flex items-center gap-3">
          {/* Monochrome primary, same semantic tokens as `Button`'s
              `default`/`accent` variants: ink on cream, inverting to warm
              off-white on black, resolved per theme in globals.css. */}
          <Link
            href="/sign-up"
            className="rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-fg hover:bg-primary-hover"
          >
            Sign up
          </Link>
          <Link
            href="/sign-in"
            className="rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-800 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900"
          >
            Log in
          </Link>
        </div>
      </main>
    </div>
  );
}
