"use client";

/**
 * Three-way preference (system/light/dark), like the reference's own
 * Appearance control - but "system" is opt-in, not the default: dark stays
 * the fallback for a visitor with nothing stored yet (see `layout.tsx`'s
 * blocking init script), since the reference this design follows is dark
 * throughout and a first-time visitor should land on the theme the app is
 * actually designed around, not whatever their OS happens to prefer.
 *
 * The *resolved* light/dark value drives `.dark` on `<html>`, exactly as
 * before. The *preference* itself (which of the three the user actually
 * picked) has to be tracked separately - `.dark`'s presence alone can't
 * distinguish "explicitly dark" from "system, currently resolving dark" -
 * so it's mirrored into a `data-theme-pref` attribute on the same element by
 * the same blocking script, which is what makes it readable via
 * `useSyncExternalStore` without a hydration mismatch.
 */

import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "kh-theme";
const PREF_ATTR = "data-theme-pref";

function isTheme(value: string | null): value is Theme {
  return value === "light" || value === "dark" || value === "system";
}

function systemPrefersDark(): boolean {
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
  return isTheme(attr) ? attr : "dark";
}

// The server never knows the visitor's stored preference - it always renders
// "dark", matching the blocking script's default when nothing is in
// localStorage yet (or localStorage is unreadable).
function getServerSnapshot(): Theme {
  return "dark";
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
