"use client";

/**
 * App-wide toast notifications - a workspace/chat/document create or delete
 * gets a brief, bottom-right confirmation instead of just a silent list
 * update, since the previous only feedback for those actions was the item
 * itself appearing or disappearing from a list the user might not be looking
 * at. Auto-dismisses after a few seconds; never blocks interaction
 * underneath it (no backdrop, `pointer-events-none` on the stack's own
 * padding box so only the cards themselves are clickable).
 *
 * One `ToastProvider` mounted once in `app/providers.tsx`; call sites reach
 * it through `useToast()` rather than importing a component, since a toast
 * is fired from an event handler (a mutation's `onSuccess`), not rendered
 * into the tree at the point it's triggered.
 */

import { CircleCheck, TriangleAlert, X } from "lucide-react";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

type ToastVariant = "success" | "error" | "info";

interface Toast {
  id: string;
  message: string;
  variant: ToastVariant;
}

interface ToastOptions {
  variant?: ToastVariant;
  /** Milliseconds before auto-dismiss. */
  duration?: number;
}

interface ToastContextValue {
  showToast: (message: string, options?: ToastOptions) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const DEFAULT_DURATION = 4000;

const ICON: Record<ToastVariant, typeof CircleCheck> = {
  success: CircleCheck,
  error: TriangleAlert,
  info: CircleCheck,
};

// Icon colour is the only thing that varies by variant - the card itself
// stays the app's neutral elevated surface (see `--surface-white`/`black` in
// globals.css), matching every other transient affordance in this codebase
// (focus rings aside) rather than painting the whole toast red/green.
const ICON_CLASSNAME: Record<ToastVariant, string> = {
  success: "text-emerald-600 dark:text-emerald-400",
  error: "text-red-600 dark:text-red-400",
  info: "text-accent-600 dark:text-accent-400",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timersRef = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: string) => {
    const timer = timersRef.current.get(id);
    if (timer) {
      clearTimeout(timer);
      timersRef.current.delete(id);
    }
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const showToast = useCallback(
    (message: string, options?: ToastOptions) => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const variant = options?.variant ?? "success";
      setToasts((prev) => [...prev, { id, message, variant }]);
      const timer = setTimeout(
        () => dismiss(id),
        options?.duration ?? DEFAULT_DURATION,
      );
      timersRef.current.set(id, timer);
    },
    [dismiss],
  );

  const value = useMemo(() => ({ showToast }), [showToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}

      {/* Fixed to the viewport, not a page container, so it survives
          route changes and sits above the sidebar/panels regardless of
          which screen fired it. `items-end` right-aligns the stack while
          letting each card's own width stay content-sized. */}
      <div
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-full max-w-sm flex-col items-end gap-2"
      >
        {toasts.map((toast) => {
          const Icon = ICON[toast.variant];
          return (
            <div
              key={toast.id}
              role="status"
              className={cn(
                "pointer-events-auto flex w-full items-start gap-2.5 rounded-xl border border-zinc-200 bg-white p-3 pr-2.5 shadow-lg",
                "dark:border-zinc-800 dark:bg-zinc-900",
                "animate-toast-in",
              )}
            >
              <Icon
                className={cn(
                  "mt-0.5 size-4 shrink-0",
                  ICON_CLASSNAME[toast.variant],
                )}
                aria-hidden
              />
              <p className="min-w-0 flex-1 text-sm leading-5 text-zinc-800 dark:text-zinc-100">
                {toast.message}
              </p>
              <button
                type="button"
                aria-label="Dismiss notification"
                onClick={() => dismiss(toast.id)}
                className="shrink-0 rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
              >
                <X className="size-3.5" aria-hidden />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context;
}
