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
 * WHY LITERAL HEX AND NOT `var(--primary)`: Clerk's colour engine derives
 * hover/active/alpha shades from the base colours it is given, by RGB-channel
 * maths in JS rather than by the browser's cascade. A raw `var(...)` string
 * survives passthrough but not derivation, so the derived states silently
 * break. Literal hex is the only form guaranteed correct.
 *
 * THE COST OF THAT, AND THE GUARD: because these are literals, they are the
 * one place in the codebase that can drift out of sync with `globals.css`
 * without anything failing - and they did, badly. This file kept rendering the
 * retired terracotta accent (and neutrals from two ramps ago) through several
 * palette changes, because it is a `.ts` in `lib/` and every audit was scoped
 * to `.tsx` under `components/`. `ThemedClerkForm` now asserts these values
 * against the live CSS variables in development and logs a warning on
 * mismatch, so the next drift is caught the first time the page renders.
 *
 * Keep `TOKENS` below as the single sync point: it mirrors `:root` and
 * `:root.dark` in `app/globals.css`. Nothing else in this file should hold a
 * colour.
 */

import type { SignIn } from "@clerk/nextjs";
import type { ComponentProps } from "react";

// Derived from the component itself rather than imported from `@clerk/types`
// directly: that package is one of Clerk's own transitive dependencies, not
// one of this project's, so its resolved path is not guaranteed. `SignIn` and
// `SignUp` share the same `appearance` shape.
type ClerkAppearance = ComponentProps<typeof SignIn>["appearance"];

/**
 * Mirror of the design tokens in `app/globals.css`. The CSS-variable name each
 * value corresponds to is in the comment, and that pairing is what
 * `ThemedClerkForm`'s dev-mode check reads.
 */
export const TOKENS = {
  light: {
    primary: "#24211c", // --primary ink, the filled primary button
    primaryFg: "#fbfaf7", // --primary-fg cream label on that button
    foreground: "#24211c", // --z900 body text
    muted: "#56514a", // --z500 subtitles, helper text
    surface: "#fefdfb", // --surface-white elevated card
    input: "#fefdfb", // --surface-white input fill
    border: "#e0dcd3", // --z300 borders
    focus: "#2563eb", // --focus-ring platform blue
  },
  dark: {
    primary: "#edecea", // --primary off-white, the filled primary button
    primaryFg: "#0f0f0f", // --primary-fg near-black label on that button
    foreground: "#edecea", // --z100 body text
    muted: "#9d9c9a", // --z400 subtitles, helper text
    surface: "#1b1b1b", // --z900 elevated card over the z950 page
    input: "#262626", // --z800 input fill, a step above the card
    border: "#262626", // --z800 borders
    focus: "#60a5fa", // --focus-ring platform blue, lifted for dark
  },
} as const;

const SHARED_VARIABLES = {
  borderRadius: "0.5rem",
  fontFamily: "var(--font-inter), ui-sans-serif, system-ui, sans-serif",
  // Clerk's own internal gap scale (header–fields–footer spacing), default
  // 1rem. Tightened so the sign-up card - header, Google button, divider,
  // email + password fields, submit, footer - fits AuthShell's single-page,
  // no-scroll layout. Documented at
  // https://clerk.com/docs/nextjs/guides/customizing-clerk/appearance-prop/variables
  //
  // This scales Clerk's *box* metrics and not its type: font sizes come off a
  // separate `fontSize` variable. Anything Clerk sizes to fit its text at the
  // default 1rem therefore ends up ~30% too short for that text here, and
  // `lastAuthenticationStrategyBadge` below is the element with no slack to
  // absorb it. Raising this back to 1rem is the other fix and is rejected -
  // it puts the sign-up card back into a scroll.
  spacing: "0.7rem",
} as const;

/**
 * The "Last used" pill on whichever sign-in option the visitor picked last.
 *
 * MEASURED against the live form (its element descriptor is
 * `lastAuthenticationStrategyBadge`, read out of the installed
 * `@clerk/ui@1.27.2` bundle's own descriptor list - it is not in the docs'
 * elements table): Clerk gives it a hard `height` derived from `spacing`,
 * which at 0.7rem resolves to 14.17px, and `display: block` with no vertical
 * padding. Its text line box is 15px tall (12.375px type on an 18px root), so
 * the label overhung the pill's bottom edge by 2.8px - `scrollHeight` 18
 * against `clientHeight` 12.
 *
 * `h-auto` drops the inherited fixed height so the box is sized by its
 * content, and `inline-flex` + `items-center` centres the label in it rather
 * than letting a line box taller than the container decide where the text
 * sits. `!` on each for the reason documented on `cardBox` below: Clerk's
 * `cl-internal-*` classes tie with a utility on specificity and win on order.
 */
const LAST_USED_BADGE =
  "inline-flex! h-auto! items-center px-2! py-1! leading-none!";

const LIGHT_APPEARANCE = {
  variables: {
    colorPrimary: TOKENS.light.primary,
    colorPrimaryForeground: TOKENS.light.primaryFg,
    colorForeground: TOKENS.light.foreground,
    colorMutedForeground: TOKENS.light.muted,
    colorBackground: TOKENS.light.surface,
    colorInput: TOKENS.light.input,
    colorInputForeground: TOKENS.light.foreground,
    colorNeutral: TOKENS.light.foreground,
    ...SHARED_VARIABLES,
  },
  elements: {
    rootBox: "w-full",
    // Clerk's own card IS the card - `AuthShell` deliberately wraps it in
    // nothing, since nesting produced two stacked cards. Restyled here into
    // the shell's language: 28px outer radius against the controls'0.5rem.
    //
    // The `!` suffixes are required, not stylistic. Clerk ships internal
    // `cl-internal-*` classes at the same specificity as a utility, emitted
    // later in the cascade, so a plain `hidden` or `bg-white` loses the tie -
    // verified by reading computed styles, where `cl-header hidden` still
    // resolved to `display: flex`. The important modifier is what makes these
    // deterministic.
    // NB the literal rgba shadow: `shadow-black/*` cannot be used here.
    // `--color-black` is indirected to `--surface-black`, which INVERTS to
    // near-white in dark mode, so it painted a white halo under the card
    // rather than a shadow. Same trap as `dark:hover:bg-white`. Shadows need
    // a real black, so they take an explicit rgba.
    //
    // The surface lives on `cardBox`, NOT on `card`: Clerk renders the footer
    // ("Don't have an account? Sign up") as a sibling of `card` inside
    // `cardBox`, so painting `card` left the footer outside the rounded shape
    // and the whole thing read as two detached slabs. `overflow-hidden` clips
    // the footer to the 28px corners so it becomes one object.
    cardBox:
      "w-full overflow-hidden rounded-[28px]! border! border-zinc-200! bg-white!",
    card: "w-full bg-transparent! border-0! shadow-none! px-7! pt-5! pb-3!",
    // Clerk's own header ("Sign in to KnowledgeHub") is left visible. Hiding
    // it needed `hidden!` to beat Clerk's internal classes, and the result
    // read as a headline with no form label under it; keeping it also means
    // sign-in and sign-up are distinguishable at a glance.
    headerTitle: "text-base font-semibold",
    headerSubtitle: "text-xs",
    footer: "bg-transparent! m-0! px-7 py-2.5! border-t border-zinc-200!",
    footerActionText: "text-zinc-600",
    // Links are ink + underline, not a coloured accent: this product is
    // near-monochrome and reserves its one hue for system affordances.
    footerActionLink: "text-zinc-900 underline underline-offset-2 font-medium",
    formButtonPrimary:
      "bg-primary hover:bg-primary-hover text-primary-fg text-sm normal-case shadow-none",
    formFieldInput: "border-zinc-300 rounded-lg",
    formFieldLabel: "text-zinc-700 font-medium",
    socialButtonsBlockButton:
      "border-zinc-300 hover:bg-zinc-100 text-zinc-800 rounded-lg",
    socialButtonsBlockButtonText: "text-sm font-medium",
    lastAuthenticationStrategyBadge: LAST_USED_BADGE,
    dividerLine: "bg-zinc-200",
    dividerText: "text-zinc-500",
    identityPreviewEditButton: "text-zinc-900 underline underline-offset-2",
    formResendCodeLink: "text-zinc-900 underline underline-offset-2",
    otpCodeFieldInput: "border-zinc-300",
  },
} satisfies ClerkAppearance;

const DARK_APPEARANCE = {
  variables: {
    colorPrimary: TOKENS.dark.primary,
    colorPrimaryForeground: TOKENS.dark.primaryFg,
    colorForeground: TOKENS.dark.foreground,
    colorMutedForeground: TOKENS.dark.muted,
    colorBackground: TOKENS.dark.surface,
    colorInput: TOKENS.dark.input,
    colorInputForeground: TOKENS.dark.foreground,
    colorNeutral: TOKENS.dark.foreground,
    ...SHARED_VARIABLES,
  },
  elements: {
    rootBox: "w-full",
    // See the light config for why every override carries `!`.
    // See the light config: surface on `cardBox` so the footer is enclosed.
    cardBox:
      "w-full overflow-hidden rounded-[28px]! border! border-zinc-800! bg-zinc-900!",
    card: "w-full bg-transparent! border-0! shadow-none! px-7! pt-5! pb-3!",
    headerTitle: "text-base font-semibold",
    headerSubtitle: "text-xs",
    footer: "bg-transparent! m-0! px-7 py-2.5! border-t border-zinc-800!",
    footerActionText: "text-zinc-400",
    footerActionLink: "text-zinc-100 underline underline-offset-2 font-medium",
    formButtonPrimary:
      "bg-primary hover:bg-primary-hover text-primary-fg text-sm normal-case shadow-none",
    formFieldInput: "border-zinc-800 bg-zinc-800 text-zinc-100 rounded-lg",
    formFieldLabel: "text-zinc-300 font-medium",
    socialButtonsBlockButton:
      "border-zinc-800 hover:bg-zinc-800 text-zinc-200 rounded-lg",
    socialButtonsBlockButtonText: "text-sm font-medium",
    lastAuthenticationStrategyBadge: LAST_USED_BADGE,
    dividerLine: "bg-zinc-800",
    dividerText: "text-zinc-400",
    identityPreviewEditButton: "text-zinc-100 underline underline-offset-2",
    formResendCodeLink: "text-zinc-100 underline underline-offset-2",
    otpCodeFieldInput: "border-zinc-800 bg-zinc-800 text-zinc-100",
  },
} satisfies ClerkAppearance;

export function getClerkAppearance(theme: "light" | "dark"): ClerkAppearance {
  return theme === "dark" ? DARK_APPEARANCE : LIGHT_APPEARANCE;
}
