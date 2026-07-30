"use client";

/**
 * Whether the workspace sidebar is collapsed to an icon rail. Same
 * persisted-DOM-class pattern as useTheme.ts (see that file's header for the
 * full rationale): the class lives on <html>, set by a blocking script in
 * layout.tsx before hydration, so a collapsed session doesn't flash expanded
 * on every reload the way a plain `useState(false)` would.
 */

import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "kh-sidebar-collapsed";
const CLASS_NAME = "sidebar-collapsed";

function subscribe(callback: () => void) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class"],
  });
  return () => observer.disconnect();
}

function getSnapshot(): boolean {
  return document.documentElement.classList.contains(CLASS_NAME);
}

// The server never knows the visitor's stored preference - always render
// expanded, matching the blocking script's default when nothing is stored.
function getServerSnapshot(): boolean {
  return false;
}

export function useSidebarCollapsed() {
  const collapsed = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setCollapsed = useCallback((next: boolean) => {
    document.documentElement.classList.toggle(CLASS_NAME, next);
    try {
      window.localStorage.setItem(STORAGE_KEY, String(next));
    } catch {
      // Storage disabled - collapse state just won't survive a reload.
    }
  }, []);

  const toggle = useCallback(() => {
    setCollapsed(!getSnapshot());
  }, [setCollapsed]);

  return { collapsed, setCollapsed, toggle };
}
