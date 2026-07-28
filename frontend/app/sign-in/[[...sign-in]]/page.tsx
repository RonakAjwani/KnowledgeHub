import { SignIn } from "@clerk/nextjs";

import { AuthShell } from "@/components/AuthShell";
import { clerkAppearance } from "@/lib/clerk-appearance";
import { CLERK_ENABLED } from "@/lib/utils";

export default function SignInPage() {
  // `<SignIn>` needs a mounted `<ClerkProvider>`, which `Providers` only
  // mounts when a publishable key is configured — without one this route is
  // unreachable through the app's own middleware (AUTH_MODE=dev never
  // redirects here), but a direct visit would otherwise hard-crash with a 500
  // rather than explain itself.
  if (!CLERK_ENABLED) {
    return (
      <AuthShell>
        <p className="rounded-xl border border-zinc-200 bg-white p-6 text-center text-sm text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
          Sign-in is not configured for this deployment. Set
          <code className="mx-1 rounded bg-zinc-100 px-1 py-0.5 font-mono text-xs dark:bg-zinc-800">
            NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY
          </code>
          to enable it.
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell>
      <SignIn appearance={clerkAppearance} />
    </AuthShell>
  );
}
