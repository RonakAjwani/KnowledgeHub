"use client";

/**
 * Pinning a workspace, persisted to localStorage. There is no backend column
 * for this (confirmed - no migration, no endpoint), and building one was
 * explicitly deferred as out of scope for this pass. A device-local pin is
 * the honest middle ground: it's a real, working feature (not a dead button),
 * it just doesn't sync across devices the way a backend-backed one would.
 *
 * `useSyncExternalStore`, same as `useTheme`/`useSidebarCollapsed` - reading
 * localStorage into `useState` inside an effect trips this codebase's
 * `react-hooks/set-state-in-effect` lint rule, and more importantly is the
 * wrong tool: this is external state (localStorage), not state derived from
 * props, so it belongs behind a subscription, not a state-in-effect sync.
 * There is no native "did this key change" event for same-tab writes, so
 * `toggle` dispatches its own event alongside the write.
 */

import { useCallback, useSyncExternalStore } from "react";

const STORAGE_KEY = "kh-pinned-workspaces";
const CHANGE_EVENT = "kh-pinned-workspaces-changed";
const EMPTY: ReadonlySet<string> = new Set();

function readPinned(): Set<string> {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? new Set(JSON.parse(raw) as string[]) : new Set();
  } catch {
    return new Set();
  }
}

function subscribe(callback: () => void) {
  window.addEventListener(CHANGE_EVENT, callback);
  // Same key changed from another tab.
  window.addEventListener("storage", callback);
  return () => {
    window.removeEventListener(CHANGE_EVENT, callback);
    window.removeEventListener("storage", callback);
  };
}

// Cached so repeated `getSnapshot()` calls between actual changes return the
// same reference - `useSyncExternalStore` re-renders whenever the snapshot
// isn't `Object.is`-equal to the last one, so recomputing a fresh `Set` every
// call would re-render on every unrelated render of every subscriber.
let cache: Set<string> | null = null;

function getSnapshot(): ReadonlySet<string> {
  cache ??= readPinned();
  return cache;
}

function getServerSnapshot(): ReadonlySet<string> {
  return EMPTY;
}

export function usePinnedWorkspaces() {
  const pinned = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const toggle = useCallback((workspaceId: string) => {
    const next = readPinned();
    if (next.has(workspaceId)) next.delete(workspaceId);
    else next.add(workspaceId);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...next]));
    } catch {
      // Storage disabled - pinning just won't persist across reloads.
    }
    cache = next;
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  return { pinned, toggle };
}
