"use client";

/**
 * The sidebar's bottom account row. Clicking it opens a small popover
 * (Settings, Log out) rather than the settings dialog directly, matching the
 * reference - "Settings" inside that popover is what opens `SettingsModal`.
 *
 * Dev mode (`CLERK_ENABLED === false`) has no session and no email to show,
 * so the popover drops both the email line and Log out rather than rendering
 * a control that does nothing - the one real item left, Settings, still
 * works (Profile -> Appearance is real regardless of auth).
 */

import { LogOut, Settings, User } from "lucide-react";
import { useRef, useState } from "react";

import { SettingsModal } from "@/components/SettingsModal";
import { useClickOutside } from "@/hooks/useClickOutside";
import { useAccount } from "@/hooks/useAccount";
import { CLERK_ENABLED, cn } from "@/lib/utils";

export function AccountMenu({ collapsed = false }: { collapsed?: boolean }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useClickOutside([menuRef], () => setMenuOpen(false));

  const { fullName, email, imageUrl, signOut } = useAccount();

  return (
    // `menuRef` wraps the toggle button too, not just the dropdown - otherwise
    // a click on the button counts as "outside" the dropdown, closes it via
    // `useClickOutside`, and the button's own click handler reopens it in the
    // same event.
    <div ref={menuRef} className="relative">
      {menuOpen && (
        <div
          role="menu"
          // `overflow-hidden`: the menu items below are full-bleed
          // (`w-full`, no margin or rounding of their own) so their
          // hover/focus highlight reaches edge-to-edge - without this, that
          // highlight on the first/last item is a square-cornered rectangle
          // sitting inside a rounded-lg container, which reads as the
          // highlight getting clipped by (or poking past) the curve. This
          // clips it to the container's own rounded shape instead.
          // `w-full`, not a fixed width: the sidebar is user-resizable from
          // 200px to 420px and its wrapper in `app/(app)/layout.tsx` is
          // `overflow-hidden` (that's what clips the collapse animation), so
          // any fixed width wider than the *current* sidebar got visibly
          // sliced off at the panel edge. Tracking the container width means
          // it fits at every size. Items are inset from the container's
          // rounded edge (`p-1.5` here + `rounded-md` per item) so a
          // full-bleed hover highlight never collides with the curve.
          className="absolute bottom-full left-0 z-40 mb-1.5 w-full overflow-hidden rounded-lg border border-zinc-200 bg-white p-1.5 shadow-lg dark:border-zinc-800 dark:bg-zinc-950"
        >
          {CLERK_ENABLED && email && (
            <p className="truncate border-b border-zinc-200 px-2.5 pb-2 pt-0.5 text-xs text-zinc-500 dark:border-zinc-800 dark:text-zinc-400">
              {email}
            </p>
          )}

          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setMenuOpen(false);
              setSettingsOpen(true);
            }}
            className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900"
          >
            <Settings className="size-3.5" aria-hidden />
            Settings
          </button>

          {CLERK_ENABLED && (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                void signOut();
              }}
              className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm text-zinc-700 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-900"
            >
              <LogOut className="size-3.5" aria-hidden />
              Log out
            </button>
          )}
        </div>
      )}

      <button
        type="button"
        onClick={() => setMenuOpen((v) => !v)}
        title={fullName}
        className={cn(
          "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left hover:bg-zinc-100 dark:hover:bg-zinc-900",
          collapsed && "justify-center px-0",
        )}
      >
        {imageUrl ? (
          // eslint-disable-next-line @next/next/no-img-element -- remote Clerk-hosted avatar
          <img src={imageUrl} alt="" className="size-6 shrink-0 rounded-full object-cover" />
        ) : (
          <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-zinc-200 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
            <User className="size-3.5" aria-hidden />
          </span>
        )}
        {!collapsed && (
          <span className="min-w-0 flex-1 truncate text-sm font-medium text-zinc-700 dark:text-zinc-300">
            {fullName}
          </span>
        )}
      </button>

      {settingsOpen && <SettingsModal onClose={() => setSettingsOpen(false)} />}
    </div>
  );
}
