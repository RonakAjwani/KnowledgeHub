import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

import type { Conversation } from "./types";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * `Conversation.title` is always `null` today - no backend path ever sets it
 * (confirmed against backend/app/api/chat.py). Anything that displays *or
 * searches* a conversation must go through this fallback rather than the raw
 * field, or it silently shows/matches nothing.
 */
export function conversationLabel(
  conversation: Pick<Conversation, "title"> | null | undefined,
): string {
  return conversation?.title || "Untitled chat";
}

/**
 * Whether auth is enabled on this build.
 *
 * The backend's `AUTH_MODE=dev` assigns every caller a fixed user so Compose
 * runs with no Clerk account. The frontend mirrors that: without a publishable
 * key it skips `ClerkProvider` entirely rather than rendering a broken sign-in
 * wall, which is what makes `docker compose up` work on a clean clone.
 */
export const CLERK_ENABLED = Boolean(
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
);
