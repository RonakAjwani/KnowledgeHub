"use client";

/**
 * Click-outside-or-Escape-to-close, shared by every hand-rolled popover in
 * this codebase (no popover/menu primitive is installed - `NewWorkspaceDialog`
 * already hand-rolls a backdrop; the breadcrumb dropdown and account menu
 * need the same "click outside" behaviour without a full-screen backdrop).
 *
 * Takes an array of refs, not one, because the workspace page's Artifacts
 * toggle button and the panel it opens live in two different components (the
 * header and the page) - "outside" has to mean "outside both," or the click
 * that opens the panel would immediately register as the click that closes
 * it. A single-ref caller just passes a one-element array.
 */

import { useEffect } from "react";

/**
 * Read-only, not `RefObject<HTMLElement | null>` - a button ref and a div
 * ref are each some *specific* element subtype, and TypeScript's mutable-ref
 * variance won't let two different concrete subtypes share one array slot
 * typed as their common supertype. Declaring the field `readonly` here is
 * what makes that widening legal: read-only properties are covariant, so a
 * `RefObject<HTMLButtonElement | null>` (or any other element ref) is
 * assignable here without a cast at the call site.
 */
interface ReadableRef {
  readonly current: HTMLElement | null;
}

export function useClickOutside(
  refs: ReadableRef[],
  onOutside: () => void,
): void {
  useEffect(() => {
    function handlePointer(event: PointerEvent) {
      const target = event.target as Node;
      const inside = refs.some((ref) => ref.current && ref.current.contains(target));
      if (!inside) onOutside();
    }
    function handleKey(event: KeyboardEvent) {
      if (event.key === "Escape") onOutside();
    }
    document.addEventListener("pointerdown", handlePointer);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("pointerdown", handlePointer);
      document.removeEventListener("keydown", handleKey);
    };
  }, [refs, onOutside]);
}
