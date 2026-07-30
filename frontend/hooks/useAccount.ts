"use client";

/**
 * Shared account data - name, email, avatar, update/sign-out - read by both
 * `AccountMenu` (the sidebar popover) and `SettingsModal` (Profile/Account
 * panels). One definition so the two never quietly drift apart.
 *
 * Resolved once at module scope from a build-time constant, exactly like
 * `useSessionToken` - never a runtime-conditional hook call, so calling
 * `useUser()`/`useClerk()` unconditionally here never happens when no
 * `ClerkProvider` is mounted (dev mode).
 */

import { useClerk, useUser } from "@clerk/nextjs";

import { CLERK_ENABLED } from "@/lib/utils";

export interface AccountInfo {
  fullName: string;
  email: string | null;
  imageUrl: string | null;
  updateName: (name: string) => Promise<void>;
  signOut: () => Promise<void>;
}

function useClerkAccount(): AccountInfo {
  const { user } = useUser();
  const { signOut } = useClerk();
  return {
    fullName: user?.fullName || user?.username || "Account",
    email: user?.primaryEmailAddress?.emailAddress ?? null,
    imageUrl: user?.imageUrl ?? null,
    updateName: async (name: string) => {
      const [firstName, ...rest] = name.trim().split(/\s+/);
      await user?.update({ firstName, lastName: rest.join(" ") || undefined });
    },
    signOut: async () => {
      await signOut();
    },
  };
}

function useDevAccount(): AccountInfo {
  return {
    fullName: "Dev user",
    email: null,
    imageUrl: null,
    updateName: async () => {},
    signOut: async () => {},
  };
}

export const useAccount: () => AccountInfo = CLERK_ENABLED
  ? useClerkAccount
  : useDevAccount;
