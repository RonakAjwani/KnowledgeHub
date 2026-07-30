import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

type BadgeVariant =
  | "default"
  | "outline"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "derived";

const VARIANTS: Record<BadgeVariant, string> = {
  default:
    "bg-zinc-100 text-zinc-700 ring-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:ring-zinc-700",
  outline:
    "bg-transparent text-zinc-600 ring-zinc-300 dark:text-zinc-400 dark:ring-zinc-700",
  success:
    "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/30",
  warning:
    "bg-amber-50 text-amber-800 ring-amber-300 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/30",
  danger:
    "bg-red-50 text-red-700 ring-red-200 dark:bg-red-500/10 dark:text-red-300 dark:ring-red-500/30",
  // Neutral, not accent-tinted: this labels a *neutral fact* (which character
  // range is cited), and it persists in the source-pane header the whole time
  // a citation is open. Semantic colour is reserved for the states that carry
  // real risk - success / warning / danger / derived below - and the accent
  // for the one primary action per screen.
  info: "bg-zinc-100 text-zinc-700 ring-zinc-300 dark:bg-zinc-800 dark:text-zinc-200 dark:ring-zinc-600",
  // Reserved for model-generated content, so "not from the document" reads the
  // same everywhere it appears.
  derived:
    "bg-amber-100 text-amber-900 ring-amber-400/60 dark:bg-amber-400/15 dark:text-amber-200 dark:ring-amber-400/30",
};

export interface BadgeProps extends ComponentProps<"span"> {
  variant?: BadgeVariant;
}

export function Badge({
  className,
  variant = "default",
  ...props
}: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium leading-4 ring-1 ring-inset",
        VARIANTS[variant],
        className,
      )}
      {...props}
    />
  );
}
