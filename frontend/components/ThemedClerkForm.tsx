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
import { useEffect } from "react";

import { useTheme } from "@/hooks/useTheme";
import { TOKENS, getClerkAppearance } from "@/lib/clerk-appearance";

/**
 * Which CSS custom property each `TOKENS` entry is supposed to mirror. Clerk
 * needs literal hex (it derives hover/alpha shades in JS, which a `var(...)`
 * cannot survive), so these literals are the one place in the codebase that
 * can silently fall out of step with `globals.css`.
 *
 * They did exactly that: this file kept rendering the retired terracotta accent
 * through several palette rewrites, and nobody saw it because `/sign-in` is
 * unreachable without a Clerk key - so it appeared in no screenshot and no
 * audit. The check below closes that gap by comparing the literals against the
 * live computed values on first render.
 */
const MIRRORS: Record<keyof typeof TOKENS.light, string> = {
  primary: "--primary",
  primaryFg: "--primary-fg",
  foreground: "--z900-or-z100",
  muted: "--z500-or-z400",
  surface: "--surface-white-or-z900",
  input: "--surface-white-or-z800",
  border: "--z300-or-z800",
  focus: "--focus-ring",
};

/** `#RRGGBB` / `rgb(r, g, b)` -> a comparable `r,g,b` string. */
function normalise(value: string): string | null {
  const v = value.trim();
  const hex = /^#([0-9a-f]{6})$/i.exec(v);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return `${(n >> 16) & 255},${(n >> 8) & 255},${n & 255}`;
  }
  const rgb = v.match(/\d+/g);
  return rgb && rgb.length >= 3 ? `${rgb[0]},${rgb[1]},${rgb[2]}` : null;
}

function useAppearanceDriftCheck(resolvedTheme: "light" | "dark") {
  useEffect(() => {
    if (process.env.NODE_ENV === "production") return;

    // Read the mode from the DOM, not from `resolvedTheme`. On the first pass
    // `useTheme()` still reports its server snapshot ("dark") while the
    // blocking init script has already put the real class on <html>, so
    // trusting the React value here compared the dark token set against
    // light-mode CSS and reported drift that did not exist. The whole point of
    // this check is to be trustworthy; a false positive is worse than none.
    const root = document.documentElement;
    const mode = root.classList.contains("dark") ? "dark" : "light";
    const css = getComputedStyle(root);
    const expected = TOKENS[mode];

    // Only the two tokens with a stable one-to-one CSS variable are asserted.
    // The rest map to a different ramp step per theme (see `MIRRORS`), so a
    // name-based lookup would compare the wrong thing and cry wolf.
    for (const key of ["primary", "primaryFg", "focus"] as const) {
      const live = normalise(css.getPropertyValue(MIRRORS[key]));
      const literal = normalise(expected[key]);
      if (live && literal && live !== literal) {
        console.warn(
          `[clerk-appearance] ${resolvedTheme}.${key} is stale: this file says ` +
            `${expected[key]} (rgb ${literal}) but ${MIRRORS[key]} in globals.css ` +
            `is rgb(${live}). Clerk's forms are now painted a colour the rest of ` +
            `the app no longer uses. Update TOKENS in lib/clerk-appearance.ts.`,
        );
      }
    }
  }, [resolvedTheme]);
}

export function ThemedClerkForm({ mode }: { mode: "sign-in" | "sign-up" }) {
  const { resolvedTheme } = useTheme();
  useAppearanceDriftCheck(resolvedTheme);
  const appearance = getClerkAppearance(resolvedTheme);
  return mode === "sign-in" ? (
    <SignIn appearance={appearance} />
  ) : (
    <SignUp appearance={appearance} />
  );
}
