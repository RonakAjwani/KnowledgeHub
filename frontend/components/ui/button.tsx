import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

type ButtonVariant =
  | "default"
  | "accent"
  | "subtle"
  | "outline"
  | "ghost"
  | "destructive";

type ButtonSize = "sm" | "md" | "icon";

// No focus utilities here: the focus ring is a single global rule in
// globals.css keyed on `button:focus-visible`, so every button gets the
// identical indicator and a new one cannot forget it.
const BASE =
  "inline-flex shrink-0 items-center justify-center whitespace-nowrap font-medium transition-colors " +
  "disabled:pointer-events-none disabled:opacity-45";

// `PRIMARY` is a single semantic triplet with no `dark:` variants and no `!`.
// Each token resolves per theme in globals.css, so there is no light/dark pair
// to tie-break - which is what previously required `dark:bg-zinc-100!` here
// (see the semantic-token comment in globals.css for the full explanation).
// Deliberately monochrome: this product carries no brand accent on its
// buttons, so "primary" is expressed by full-strength contrast against the
// ground rather than by hue.
const PRIMARY = "bg-primary text-primary-fg hover:bg-primary-hover";

const VARIANTS: Record<ButtonVariant, string> = {
  default: PRIMARY,
  // Kept as a distinct name for the call sites that mean "this is *the* action
  // on this screen" (send, upload, create workspace), but it renders
  // identically to `default`. The palette is near-monochrome by decision, so
  // there is no separate accent treatment to point at; the name still
  // documents intent and gives one place to diverge again later.
  accent: PRIMARY,
  subtle:
    "bg-zinc-100 text-zinc-900 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-100 dark:hover:bg-zinc-700",
  outline:
    "border border-zinc-300 bg-transparent text-zinc-800 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800",
  ghost:
    "bg-transparent text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100",
  destructive:
    "bg-red-600 text-white hover:bg-red-700 dark:bg-red-600 dark:hover:bg-red-500",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-7 gap-1.5 rounded-md px-2.5 text-xs",
  md: "h-9 gap-2 rounded-md px-3.5 text-sm",
  icon: "size-7 rounded-md",
};

export interface ButtonProps extends ComponentProps<"button"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

export function Button({
  className,
  variant = "default",
  size = "md",
  // Defaulting to "button" rather than the HTML default "submit": these buttons
  // sit inside forms often enough that an accidental submit is the likelier bug.
  type = "button",
  ...props
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(BASE, VARIANTS[variant], SIZES[size], className)}
      {...props}
    />
  );
}
