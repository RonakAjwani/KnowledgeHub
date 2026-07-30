"use client";

/**
 * Shared chrome for every signed-in page (`/workspaces`, `/workspace/[id]`):
 * the resizable workspace sidebar plus whatever the route renders beside it.
 * A route group (`(app)`, no URL segment) rather than duplicating the sidebar
 * in both `workspaces/page.tsx` and `workspace/[workspaceId]/page.tsx` - this
 * way the sidebar (and its collapse/width state) survives navigation between
 * them instead of remounting. **Both page files must live physically inside
 * this `(app)/` folder** - a route group's layout only wraps routes nested
 * inside the group's own directory; siblings of `(app)/` at the `app/` root
 * get only the root layout. (This is exactly the bug that shipped initially:
 * `app/dashboard/` and `app/workspace/` were created next to `(app)/` instead
 * of inside it, so this layout - sidebar, banner, everything - silently never
 * applied to either page.)
 *
 * Flex, not CSS Grid, for the sidebar/content row - collapsing needs to
 * *animate* a width down to 0, and grid-template-columns doesn't transition
 * smoothly without registering the custom property via `@property`; a plain
 * flex child's `width` does, for free. Flex's default `align-items: stretch`
 * also gives both children the row's full height with no extra utility
 * needed - the single-implicit-row auto-sizing quirk that CSS Grid has here
 * (an auto row track doesn't stretch to fill a taller container on its own)
 * doesn't exist in flexbox.
 */

import type { PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { Suspense, useCallback, useState } from "react";

import { SidebarToggleButton } from "@/components/SidebarToggleButton";
import { WorkspaceSidebar } from "@/components/WorkspaceSidebar";
import { useSidebarCollapsed } from "@/hooks/useSidebarCollapsed";
import { CLERK_ENABLED } from "@/lib/utils";

const SIDEBAR_MIN = 200;
const SIDEBAR_MAX = 420;

export default function AppLayout({ children }: { children: ReactNode }) {
  const [sidebarWidth, setSidebarWidth] = useState(260);
  const { collapsed } = useSidebarCollapsed();

  const startResize = useCallback(
    (e: ReactPointerEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startWidth = sidebarWidth;

      const onMove = (moveEvent: PointerEvent) => {
        const next = startWidth + (moveEvent.clientX - startX);
        setSidebarWidth(Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, next)));
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [sidebarWidth],
  );

  return (
    <div className="flex h-screen flex-col">
      {/* Neutral, not accent-tinted: this is a persistent dev-mode notice, and
          it spans the full width of every screen - the single loudest surface
          in the app if it carries a hue. The accent is reserved for the one
          primary action per screen (see ui/button.tsx). */}
      {!CLERK_ENABLED && (
        <div className="border-b border-zinc-200 bg-zinc-200 px-4 py-1 text-center text-xs text-zinc-700 dark:border-zinc-800 dark:bg-zinc-800 dark:text-zinc-300">
          Running without auth. The backend assigns every request the same
          dev user (<code className="font-mono">AUTH_MODE=dev</code>).
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        {/* Collapsing hides the sidebar entirely (no icon rail) - this
            wrapper's width animates to 0 and clips its (fixed-width) child,
            producing the slide-away effect, and the sidebar's own internal
            state/scroll position is preserved underneath since it stays
            mounted, just visually clipped, rather than unmounting. */}
        <div
          className="relative h-full shrink-0 overflow-hidden transition-[width] duration-200 ease-in-out"
          style={{ width: collapsed ? 0 : sidebarWidth }}
        >
          <div className="h-full" style={{ width: sidebarWidth }}>
            {/* `WorkspaceSidebar` reads the active conversation via
                `useSearchParams()`, which opts any ancestor into client-side
                rendering during static generation unless it's boundaried -
                without this, `/workspaces` (statically prerenderable
                otherwise) fails the build outright rather than degrading
                gracefully. */}
            <Suspense fallback={null}>
              {/* `bg-sidebar`, a named role rather than `dark:bg-zinc-900/40`:
                  `z900` is the general "lighter tint" step used for hover and
                  tile surfaces, so blending it over the page made the sidebar
                  *lighter* than the main panel - backwards, the sidebar should
                  read marginally darker. The token holds both themes' values
                  (see globals.css). */}
              <WorkspaceSidebar className="h-full border-r border-zinc-200 bg-sidebar dark:border-zinc-800" />
            </Suspense>
          </div>

          {!collapsed && (
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize workspace sidebar"
              onPointerDown={startResize}
              className="absolute inset-y-0 right-0 hidden w-1 -translate-x-1/2 cursor-col-resize touch-none bg-transparent hover:bg-accent-300/60 active:bg-accent-400 lg:block"
            />
          )}
        </div>

        <div className="relative min-h-0 flex-1">
          {/* Floating, not part of the sidebar: it has to stay reachable
              while the sidebar is fully hidden, since nothing inside a
              zero-width, unmounted-in-spirit panel can be clicked. */}
          {collapsed && (
            <div className="absolute left-3 top-3 z-30">
              <SidebarToggleButton className="bg-white/80 backdrop-blur-sm dark:bg-zinc-950/80" />
            </div>
          )}
          {children}
        </div>
      </div>
    </div>
  );
}
