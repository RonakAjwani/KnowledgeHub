"use client";

/**
 * True only after the client has actually mounted - `false` on the server
 * and, critically, on the client's *first* hydration-matching render too
 * (`useSyncExternalStore`'s `getServerSnapshot` is used for that first pass,
 * not just for real SSR).
 *
 * Exists for exactly one failure mode: a query whose `isLoading` the server
 * always sees as `true` (data-fetching effects don't run during SSR) can
 * already be resolved by the time the client hydrates, if the request is
 * fast enough (localhost, warm cache) - the two renders then disagree on
 * which branch to show, and React discards and re-renders the whole subtree
 * with a "Hydration failed" warning. Gating the data-dependent branch on
 * `hasMounted` as well forces both the server and the client's first paint
 * through the same loading branch; the real content only appears on the
 * ordinary post-hydration re-render once `hasMounted` flips true, which is
 * not a mismatch - just a state update.
 */

import { useSyncExternalStore } from "react";

function subscribe() {
  return () => {};
}
function getSnapshot() {
  return true;
}
function getServerSnapshot() {
  return false;
}

export function useHasMounted(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
