/**
 * Shared Clerk `appearance` config for the embedded sign-in/sign-up pages.
 *
 * Clerk still owns every bit of auth logic — form validation, OAuth, session
 * handling — this only retargets its rendered classes onto the app's own
 * design tokens (the warm cream/terracotta palette from `globals.css`) so the
 * auth pages read as part of KnowledgeHub rather than a visibly different
 * product bolted on. `elements` keys are Clerk's own internal class names,
 * documented at https://clerk.com/docs/customization/overview.
 */

import type { SignIn } from "@clerk/nextjs";
import type { ComponentProps } from "react";

// Derived from the component itself rather than imported from `@clerk/types`
// directly: that package is one of Clerk's own transitive dependencies, not
// one of this project's, so its resolved path is not guaranteed. `SignIn` and
// `SignUp` share the same `appearance` shape.
type ClerkAppearance = ComponentProps<typeof SignIn>["appearance"];

export const clerkAppearance: ClerkAppearance = {
  variables: {
    colorPrimary: "#b8583a", // accent-600
    colorPrimaryForeground: "#fffdfa", // white (warm) — text on the filled button
    colorForeground: "#2b2620", // zinc-900 — default text
    colorMutedForeground: "#756a58", // zinc-600 — subtitles, helper text
    colorBackground: "#fffdfa", // white (warm) — card background
    colorInput: "#fffdfa", // white (warm) — input background
    colorInputForeground: "#2b2620", // zinc-900 — text typed into inputs
    colorNeutral: "#2b2620", // zinc-900 — borders, hover backgrounds
    borderRadius: "0.5rem",
    fontFamily: "var(--font-inter), ui-sans-serif, system-ui, sans-serif",
  },
  elements: {
    rootBox: "w-full",
    card: "shadow-none border border-zinc-200 rounded-xl p-0 w-full bg-white",
    header: "hidden", // KnowledgeHub's own heading replaces Clerk's default one
    footer: "px-8 pb-8",
    footerActionText: "text-zinc-600",
    footerActionLink: "text-accent-600 hover:text-accent-700 font-medium",
    formButtonPrimary:
      "bg-accent-600 hover:bg-accent-700 text-white text-sm normal-case shadow-none",
    formFieldInput:
      "border-zinc-300 focus:border-accent-500 focus:ring-accent-500 rounded-lg",
    formFieldLabel: "text-zinc-700 font-medium",
    socialButtonsBlockButton:
      "border-zinc-300 hover:bg-zinc-50 text-zinc-800 rounded-lg",
    socialButtonsBlockButtonText: "text-sm font-medium",
    dividerLine: "bg-zinc-200",
    dividerText: "text-zinc-400",
    identityPreviewEditButton: "text-accent-600",
    formResendCodeLink: "text-accent-600",
    otpCodeFieldInput: "border-zinc-300",
  },
};
