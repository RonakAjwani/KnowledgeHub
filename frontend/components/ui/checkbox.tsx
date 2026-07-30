"use client";

import { type ComponentProps, useEffect, useRef } from "react";

import { cn } from "@/lib/utils";

export interface CheckboxProps
  extends Omit<ComponentProps<"input">, "type" | "ref"> {
  /** Mixed state for a "select all" control. */
  indeterminate?: boolean;
}

/**
 * A native checkbox rather than a Radix primitive - no Radix in the dependency
 * set, and `indeterminate` is DOM-only state that has to be set imperatively
 * either way.
 */
export function Checkbox({
  className,
  indeterminate = false,
  ...props
}: CheckboxProps) {
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);

  return (
    <input
      ref={ref}
      type="checkbox"
      className={cn(
        "size-4 shrink-0 cursor-pointer rounded border-zinc-300 [accent-color:var(--color-accent-600)]",
        "dark:border-zinc-600 dark:[accent-color:var(--color-accent-500)]",
        "disabled:cursor-not-allowed disabled:opacity-40",
        className,
      )}
      {...props}
    />
  );
}
