"use client";

/**
 * Three-way preference (system/light/dark), like the reference's own
 * Appearance control - and **"system" is the default**, not an opt-in. A
 * visitor with nothing stored gets whatever their OS asks for, on the landing
 * page as much as inside the app. (This previously defaulted to dark on the
 * argument that the design is dark-first; that was overridden deliberately -
 * both themes are fully supported, so there is nothing to protect by
 * overriding the OS, and doing so reads as the app ignoring the setting.)
 * Keep this in step with `layout.tsx`'s blocking init script: the two agree on
 * the default, and a mismatch would flash the wrong theme on first paint.
 *
 * The *resolved* light/dark value drives `.dark` on `<html>`. The *preference*
 * itself has to be tracked separately - `.dark`'s presence alone can't
 * distinguish "explicitly dark" from "system, currently resolving dark" - so
 * it's mirrored into a `data-theme-pref` attribute on the same element by that
 * same script, which is what makes it readable via `useSyncExternalStore`
 * without a hydration mismatch.
 */

import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "kh-theme";
const PREF_ATTR = "data-theme-pref";

function isTheme(value: string | null): value is Theme {
  return value === "light" || value === "dark" || value === "system";
}

function systemPrefersDark(): boolean {
  // Guarded for the server render. `resolve("system")` is now reachable during
  // SSR - it was not while the default was "dark", which never touched
  // `matchMedia` - and an unguarded read here would crash the render rather
  // than fall back. The value returned server-side is immaterial: the blocking
  // script in `layout.tsx` puts the real class on `<html>` before first paint,
  // and `useSyncExternalStore` swaps to the live snapshot on hydration.
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function resolve(preference: Theme): "light" | "dark" {
  return preference === "system" ? (systemPrefersDark() ? "dark" : "light") : preference;
}

function apply(preference: Theme) {
  document.documentElement.classList.toggle("dark", resolve(preference) === "dark");
  document.documentElement.setAttribute(PREF_ATTR, preference);
}

function getSnapshot(): Theme {
  const attr = document.documentElement.getAttribute(PREF_ATTR);
  return isTheme(attr) ? attr : "system";
}

// The server never knows the visitor's stored preference, so it renders the
// same default the blocking script applies when nothing is in localStorage.
function getServerSnapshot(): Theme {
  return "system";
}

function subscribe(callback: () => void) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class", PREF_ATTR],
  });
  // Only changes anything while the stored preference is "system", but it's
  // cheap to keep live regardless - re-applying the current preference when
  // the OS flips is what makes "system" track without a reload.
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const onMediaChange = () => apply(getSnapshot());
  media.addEventListener("change", onMediaChange);
  return () => {
    observer.disconnect();
    media.removeEventListener("change", onMediaChange);
  };
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setTheme = useCallback((next: Theme) => {
    apply(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Storage disabled (private browsing, quota) - theme just won't
      // persist across reloads, which is not worth surfacing an error for.
    }
  }, []);

  return { theme, resolvedTheme: resolve(theme), setTheme };
}
