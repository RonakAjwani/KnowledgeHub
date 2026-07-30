import type { ComponentProps } from "react";

import { cn } from "@/lib/utils";

export interface ProgressProps extends Omit<ComponentProps<"div">, "children"> {
  value: number;
  max?: number;
  /** Class applied to the filled bar, so callers can colour by meaning. */
  barClassName?: string;
  /** Renders a pulsing full-width bar for work with no known total. */
  indeterminate?: boolean;
}

export function Progress({
  value,
  max = 100,
  className,
  barClassName,
  indeterminate = false,
  ...props
}: ProgressProps) {
  const safeMax = max > 0 ? max : 1;
  const ratio = Math.min(Math.max(value / safeMax, 0), 1);

  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={indeterminate ? undefined : safeMax}
      aria-valuenow={indeterminate ? undefined : value}
      className={cn(
        "h-1.5 w-full overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800",
        className,
      )}
      {...props}
    >
      <div
        className={cn(
          // `bg-primary` resolves per theme, so the filled bar needs no
          // `dark:` pair (and none of the specificity workarounds it required).
          "h-full rounded-full bg-primary transition-[width] duration-300 ease-out",
          indeterminate && "animate-pulse",
          barClassName,
        )}
        style={{ width: indeterminate ? "100%" : `${ratio * 100}%` }}
      />
    </div>
  );
}
