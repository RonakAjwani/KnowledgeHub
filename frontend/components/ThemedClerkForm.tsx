"use client";

/**
 * Picks the light/dark `clerk-appearance.ts` object off the app's own theme
 * state, and re-renders when it changes. This has to be a client component -
 * `useTheme()` reads the `.dark` class on `<html>` - even though the
 * sign-in/sign-up *pages* themselves don't otherwise need to be.
 *
 * `resolvedTheme`, not `theme`: Clerk's own appearance API only understands
 * light/dark, and the preference itself can be "system" - that has to be
 * resolved to one of the two before it means anything to `getClerkAppearance`.
 */

import { SignIn, SignUp } from "@clerk/nextjs";

import { useTheme } from "@/hooks/useTheme";
import { getClerkAppearance } from "@/lib/clerk-appearance";

export function ThemedClerkForm({ mode }: { mode: "sign-in" | "sign-up" }) {
  const { resolvedTheme } = useTheme();
  const appearance = getClerkAppearance(resolvedTheme);
  return mode === "sign-in" ? (
    <SignIn appearance={appearance} />
  ) : (
    <SignUp appearance={appearance} />
  );
}
