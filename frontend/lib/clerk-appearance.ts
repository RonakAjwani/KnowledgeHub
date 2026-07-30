/**
 * Clerk `appearance` config for the embedded sign-in/sign-up pages - one
 * object per theme, picked at render time by `ThemedClerkForm`
 * (components/ThemedClerkForm.tsx) off `useTheme()`.
 *
 * Clerk still owns every bit of auth logic - form validation, OAuth, session
 * handling - this only retargets its rendered classes onto the app's own
 * design tokens so the auth pages read as part of KnowledgeHub rather than a
 * visibly different product bolted on. `elements` keys are Clerk's own
 * internal class names, documented at
 * https://clerk.com/docs/customization/overview.
 *
 * Two literal objects, not one object built from `var(--color-*)` strings:
 * Clerk's colour engine may derive hover/focus shades from the base colours
 * it's given (RGB-channel math, not just CSS passthrough), which a raw
 * `var(...)` reference can't survive if that derivation happens in JS rather
 * than the browser's own cascade. Literal hex per theme is the version that's
 * guaranteed correct regardless of how Clerk's internals handle colour.
 * Values are pulled from the same ramp `app/globals.css` defines - keep the
 * two in sync if that ramp changes.
 */

import type { SignIn } from "@clerk/nextjs";
import type { ComponentProps } from "react";

// Derived from the component itself rather than imported from `@clerk/types`
// directly: that package is one of Clerk's own transitive dependencies, not
// one of this project's, so its resolved path is not guaranteed. `SignIn` and
// `SignUp` share the same `appearance` shape.
type ClerkAppearance = ComponentProps<typeof SignIn>["appearance"];

const LIGHT_APPEARANCE = {
  variables: {
    colorPrimary: "#ac5636", // accent-600
    colorPrimaryForeground: "#ffffff", // white - text on the filled button
    colorForeground: "#2b2620", // zinc-900 - default text
    colorMutedForeground: "#8c7d64", // zinc-500 (light) - subtitles, helper text
    colorBackground: "#ffffff", // white - card background
    colorInput: "#ffffff",
    colorInputForeground: "#2b2620",
    colorNeutral: "#2b2620",
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
} satisfies ClerkAppearance;

const DARK_APPEARANCE = {
  variables: {
    colorPrimary: "#c96a45", // accent-500 - a touch brighter than accent-600 for a dark background
    colorPrimaryForeground: "#ffffff",
    colorForeground: "#ececea", // dark-mode zinc-100 - near-white text
    colorMutedForeground: "#8a8a86", // dark-mode zinc-400 - subtitles, helper text
    colorBackground: "#1c1813", // zinc-950 - matches every other modal/card surface in dark mode
    colorInput: "#2b2620", // zinc-900 - matches dialog input backgrounds elsewhere
    colorInputForeground: "#ececea",
    colorNeutral: "#ececea",
    borderRadius: "0.5rem",
    fontFamily: "var(--font-inter), ui-sans-serif, system-ui, sans-serif",
  },
  elements: {
    rootBox: "w-full",
    card: "shadow-none border border-zinc-800 rounded-xl p-0 w-full bg-zinc-950",
    header: "hidden",
    footer: "px-8 pb-8",
    footerActionText: "text-zinc-400",
    footerActionLink: "text-accent-400 hover:text-accent-300 font-medium",
    formButtonPrimary:
      "bg-accent-500 hover:bg-accent-400 text-white text-sm normal-case shadow-none",
    formFieldInput:
      "border-zinc-700 bg-zinc-900 text-zinc-100 focus:border-accent-500 focus:ring-accent-500 rounded-lg",
    formFieldLabel: "text-zinc-300 font-medium",
    socialButtonsBlockButton:
      "border-zinc-700 hover:bg-zinc-900 text-zinc-200 rounded-lg",
    socialButtonsBlockButtonText: "text-sm font-medium",
    dividerLine: "bg-zinc-800",
    dividerText: "text-zinc-500",
    identityPreviewEditButton: "text-accent-400",
    formResendCodeLink: "text-accent-400",
    otpCodeFieldInput: "border-zinc-700 bg-zinc-900 text-zinc-100",
  },
} satisfies ClerkAppearance;

export function getClerkAppearance(theme: "light" | "dark"): ClerkAppearance {
  return theme === "dark" ? DARK_APPEARANCE : LIGHT_APPEARANCE;
}
