import { SignUp } from "@clerk/nextjs";

import { AuthShell } from "@/components/AuthShell";
import { clerkAppearance } from "@/lib/clerk-appearance";
import { CLERK_ENABLED } from "@/lib/utils";

export default function SignUpPage() {
  // See app/sign-in/[[...sign-in]]/page.tsx: `<SignUp>` needs a mounted
  // `<ClerkProvider>`, which is only mounted when a publishable key exists.
  if (!CLERK_ENABLED) {
    return (
      <AuthShell>
        <p className="rounded-xl border border-zinc-200 bg-white p-6 text-center text-sm text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300">
          Sign-up is not configured for this deployment. Set
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
      <SignUp appearance={clerkAppearance} />
    </AuthShell>
  );
}
