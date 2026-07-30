"use client";

/**
 * The one control that brings the sidebar back once it's collapsed - it
 * can't live inside `WorkspaceSidebar`'s own header, since that component
 * isn't mounted at all while collapsed (see `app/(app)/layout.tsx`). Also
 * wires the actual Ctrl/Cmd+B shortcut its tooltip advertises, rather than
 * showing a hint for a shortcut that doesn't do anything.
 */

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useEffect, useSyncExternalStore } from "react";

import { useSidebarCollapsed } from "@/hooks/useSidebarCollapsed";
import { cn } from "@/lib/utils";

// The platform never changes mid-session, so `subscribe` is a no-op - but
// `useSyncExternalStore` (not a lazy useState initializer) is still the
// right tool: its `getServerSnapshot` is what keeps the server-rendered HTML
// (which has no `navigator` to check) from mismatching the client's first
// hydration pass, which a plain `useState(() => ...)` initializer would not.
function subscribe() {
  return () => {};
}
function getSnapshot() {
  return /Mac|iPhone|iPod|iPad/.test(window.navigator.platform);
}
function getServerSnapshot() {
  return false;
}

export function SidebarToggleButton({ className }: { className?: string }) {
  const { collapsed, toggle } = useSidebarCollapsed();
  const isMac = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      const modifier = isMac ? event.metaKey : event.ctrlKey;
      if (modifier && event.key.toLowerCase() === "b") {
        event.preventDefault();
        toggle();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isMac, toggle]);

  return (
    <div className="group/tooltip relative inline-flex">
      <button
        type="button"
        onClick={toggle}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className={cn(
          "flex size-8 items-center justify-center rounded-md text-zinc-500 hover:bg-zinc-200/70 dark:text-zinc-400 dark:hover:bg-zinc-800",
          className,
        )}
      >
        {collapsed ? (
          <PanelLeftOpen className="size-4" aria-hidden />
        ) : (
          <PanelLeftClose className="size-4" aria-hidden />
        )}
      </button>
      <div
        className={cn(
          "pointer-events-none absolute top-full z-50 mt-1.5 hidden items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium shadow-lg group-hover/tooltip:flex",
          // Inverted pill: `bg-primary`/`text-primary-fg` resolve per theme in
          // globals.css, so this needs no `dark:` pair.
          "bg-primary text-primary-fg",
          collapsed ? "left-0" : "right-0",
        )}
      >
        {collapsed ? "Expand sidebar" : "Collapse sidebar"}
        <kbd className="rounded bg-white/15 px-1 py-0.5 font-sans text-[10px] dark:bg-black/10">
          {isMac ? "⌘B" : "Ctrl+B"}
        </kbd>
      </div>
    </div>
  );
}
