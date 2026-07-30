"use client";

/**
 * Opened from the sidebar's account row. Two sections - General and Account -
 * because those are the two backed by something real in this app today:
 * General covers the profile fields Clerk provides plus the appearance
 * preference (which needs no auth at all), Account covers email and sign-out.
 * The reference this is modelled on has ~15 nav sections (Billing, Plugins,
 * Memory, Claude Code, ...); none of the other ~13 correspond to a
 * KnowledgeHub feature, so they're not here rather than being built as dead
 * nav - add a new entry to `SECTIONS` once a section actually has something
 * real behind it.
 *
 * Same hand-rolled backdrop pattern as `NewWorkspaceDialog` (no popover
 * primitive is installed in this project): backdrop click / Escape closes,
 * clicking the panel itself does not.
 */

import { LogOut, Monitor, Moon, Settings as SettingsIcon, Sun, User, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useState, type KeyboardEvent as ReactKeyboardEvent } from "react";

import { useAccount, type AccountInfo } from "@/hooks/useAccount";
import { useTheme, type Theme } from "@/hooks/useTheme";
import { CLERK_ENABLED } from "@/lib/utils";
import { cn } from "@/lib/utils";

type SectionId = "general" | "account";

const SECTIONS: { id: SectionId; label: string; Icon: LucideIcon }[] = [
  { id: "general", label: "General", Icon: SettingsIcon },
  { id: "account", label: "Account", Icon: User },
];

const APPEARANCE_OPTIONS: { value: Theme; label: string; Icon: LucideIcon }[] = [
  { value: "system", label: "System", Icon: Monitor },
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
];

// ------------------------------------------------------------------ panels

function Avatar({ imageUrl, name, className }: { imageUrl: string | null; name: string; className?: string }) {
  if (imageUrl) {
    // eslint-disable-next-line @next/next/no-img-element -- a remote Clerk-hosted avatar, not a local asset next/image would optimise
    return <img src={imageUrl} alt={name} className={cn("rounded-full object-cover", className)} />;
  }
  return (
    <span
      aria-label={name}
      className={cn(
        "flex items-center justify-center rounded-full bg-zinc-200 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400",
        className,
      )}
    >
      <User className="size-1/2" aria-hidden />
    </span>
  );
}

function SectionHeading({ children }: { children: string }) {
  return (
    <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
      {children}
    </h3>
  );
}

function AppearanceControl() {
  const { theme, setTheme } = useTheme();
  return (
    <div className="inline-flex rounded-lg border border-zinc-200 p-0.5 dark:border-zinc-800">
      {APPEARANCE_OPTIONS.map(({ value, label, Icon }) => (
        <button
          key={value}
          type="button"
          onClick={() => setTheme(value)}
          aria-label={label}
          aria-pressed={theme === value}
          title={label}
          className={cn(
            "flex size-8 items-center justify-center rounded-md",
            theme === value
              ? "bg-white text-zinc-900 shadow-sm dark:bg-zinc-700 dark:text-zinc-100"
              : "text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200",
          )}
        >
          <Icon className="size-4" aria-hidden />
        </button>
      ))}
    </div>
  );
}

function GeneralPanel({ account }: { account: AccountInfo }) {
  const [name, setName] = useState(account.fullName);
  const [saved, setSaved] = useState(false);

  const commitName = async () => {
    const trimmed = name.trim();
    if (!trimmed || trimmed === account.fullName) return;
    await account.updateName(trimmed);
    setSaved(true);
    window.setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="space-y-8">
      <div className="space-y-6">
        <SectionHeading>Profile</SectionHeading>

        {!CLERK_ENABLED ? (
          <p className="text-sm text-zinc-600 dark:text-zinc-400">
            Running without auth. Profile fields need a Clerk key
            (<code className="font-mono text-xs">NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY</code>)
            to have a real profile to edit.
          </p>
        ) : (
          <>
            <div>
              <span className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">
                Avatar
              </span>
              <Avatar imageUrl={account.imageUrl} name={account.fullName} className="mt-2 size-12" />
            </div>

            <label className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">
              Full name
              <div className="mt-1 flex items-center gap-2">
                <input
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  onBlur={commitName}
                  onKeyDown={(event: ReactKeyboardEvent<HTMLInputElement>) => {
                    if (event.key === "Enter") event.currentTarget.blur();
                  }}
                  className={cn(
                    "w-full max-w-xs rounded-md border border-zinc-300 bg-white px-2.5 py-1.5 text-sm",
                    "text-zinc-900",
                    "dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100",
                  )}
                />
                {saved && <span className="text-xs text-emerald-600 dark:text-emerald-400">Saved</span>}
              </div>
            </label>
          </>
        )}
      </div>

      {/* Unlike Profile, Appearance is a local preference (localStorage,
          not Clerk) - it works identically with or without auth, so it
          isn't gated behind `CLERK_ENABLED` the way the fields above are. */}
      <div className="space-y-3 border-t border-zinc-200 pt-6 dark:border-zinc-800">
        <SectionHeading>Preferences</SectionHeading>
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-zinc-700 dark:text-zinc-300">Appearance</span>
          <AppearanceControl />
        </div>
      </div>
    </div>
  );
}

function AccountPanel({ account }: { account: AccountInfo }) {
  if (!CLERK_ENABLED) {
    return (
      <p className="text-sm text-zinc-600 dark:text-zinc-400">
        Running without auth. The backend assigns every request the same dev
        user (<code className="font-mono text-xs">AUTH_MODE=dev</code>). There
        is no session to sign out of.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <span className="block text-xs font-medium text-zinc-600 dark:text-zinc-400">
          Email
        </span>
        <p className="mt-1 text-sm text-zinc-900 dark:text-zinc-100">
          {account.email ?? "-"}
        </p>
      </div>

      <button
        type="button"
        onClick={() => void account.signOut()}
        className="flex items-center gap-1.5 rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-900"
      >
        <LogOut className="size-3.5" aria-hidden />
        Sign out
      </button>
    </div>
  );
}

// ------------------------------------------------------------------ modal

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const [section, setSection] = useState<SectionId>("general");
  const account = useAccount();

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
      onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Settings"
        onClick={(event) => event.stopPropagation()}
        className="flex h-[32rem] w-full max-w-2xl overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-xl dark:border-zinc-800 dark:bg-zinc-950"
      >
        <nav className="w-44 shrink-0 border-r border-zinc-200 bg-zinc-50/60 p-2 dark:border-zinc-800 dark:bg-zinc-900/40">
          <p className="px-2 pb-1 text-[0.7rem] font-semibold uppercase tracking-wide text-zinc-400 dark:text-zinc-500">
            Settings
          </p>
          {SECTIONS.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setSection(item.id)}
              className={cn(
                "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm",
                section === item.id
                  ? "bg-zinc-200 font-medium text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
                  : "text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900",
              )}
            >
              <item.Icon className="size-3.5 shrink-0" aria-hidden />
              {item.label}
            </button>
          ))}
        </nav>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between border-b border-zinc-200 px-5 py-3 dark:border-zinc-800">
            <h2 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              {SECTIONS.find((item) => item.id === section)?.label}
            </h2>
            <button
              type="button"
              aria-label="Close settings"
              onClick={onClose}
              className="rounded p-1 text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-900"
            >
              <X className="size-4" aria-hidden />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            {section === "general" ? (
              <GeneralPanel account={account} />
            ) : (
              <AccountPanel account={account} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
