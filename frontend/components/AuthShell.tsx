import type { ReactNode } from "react";

import { Logo } from "@/components/ui/logo";

/**
 * The frame both `/sign-in` and `/sign-up` render Clerk's embedded form
 * inside. This is the design that used to live at `/login` as a static mock;
 * that route has been deleted, because keeping a second, drifting copy of the
 * same screen is exactly how the real Clerk form ended up painted in a retired
 * accent colour across three palette rewrites without anyone noticing.
 *
 * Composition: wordmark, a headline and subtitle on the left above the auth
 * card, and a large media panel on the right. Clerk's `<SignIn>`/`<SignUp>`
 * drops into the card slot with its own card chrome stripped (see
 * `lib/clerk-appearance.ts`), so the card rendered here is the only card on
 * screen. Clerk keeps every bit of the actual auth logic - OAuth, one-time
 * codes, MFA, validation, error and rate-limit states - which is why the form
 * is wrapped rather than rebuilt.
 *
 * Deliberately NOT theme-fixed, unlike the `/login` mock it replaces. That
 * mock pinned itself to dark because it predated the light theme. This is a
 * real signed-out entry point, and a visitor who has chosen light mode should
 * not be dropped onto a black screen and then returned to a cream one. Both
 * themes were finished to the same bar, so both get used.
 */
export function AuthShell({
  title = "Ask your documents anything",
  subtitle = "Your documents, with answers you can verify",
  children,
}: {
  title?: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex h-dvh flex-col overflow-y-auto bg-zinc-50 px-6 py-4 sm:px-10 sm:py-5 dark:bg-zinc-950">
      {/* Wordmark is page-centred, not corner-anchored: it reads as the page's
          own masthead sitting above the headline, rather than a nav-bar logo. */}
      <div className="flex shrink-0 items-center justify-center gap-2">
        <Logo className="size-5 shrink-0 text-zinc-900 dark:text-zinc-100" />
        <span className="text-base font-semibold text-zinc-900 sm:text-lg dark:text-zinc-100">
          KnowledgeHub
        </span>
      </div>

      <div className="mx-auto grid w-full max-w-6xl flex-1 min-h-0 items-center-safe gap-6 lg:grid-cols-2 lg:gap-14">
        {/* `items-center-safe`, not `items-center`: on a short viewport (small
            laptop, sign-up's extra password field) the column can be taller
            than the row's assigned space. Plain centering then overflows
            symmetrically - upward as much as downward - which pushed the
            headline up into the wordmark above it. The `-safe` variant falls
            back to start-alignment exactly when centering would overflow the
            start edge, so it only ever grows downward into the scroll
            fallback, never overlaps upward. */}
        {/* Content is centre-aligned in its own column at every breakpoint:
            headline, subtitle and the card share one vertical axis, which is
            what gives the composition its poise. Left-aligning the headline
            (an earlier attempt) broke that axis and read as a landing page. */}
        <div className="flex min-h-0 flex-col items-center text-center">
          <h1 className="text-2xl font-semibold leading-[1.15] tracking-tight text-zinc-900 sm:text-[2.25rem] dark:text-zinc-100">
            {title}
          </h1>
          <p className="mt-2.5 max-w-sm text-sm text-zinc-500 sm:text-base dark:text-zinc-400">
            {subtitle}
          </p>

          {/*
            No card wrapper here on purpose. Clerk's `<SignIn>`/`<SignUp>`
            already renders its own card, and nesting ours around it produced
            two visibly stacked cards. Clerk's card is instead *restyled* into
            the one we want (28px radius, our surface, border and shadow) in
            `lib/clerk-appearance.ts`, so there is exactly one card on screen
            and Clerk keeps control of its own internal layout.
          */}
          <div className="mt-4 w-full max-w-[25rem]">{children}</div>
        </div>

        {/* 16:9, because a product walkthrough clip goes here — the frame
            is already the shape of the thing that fills it, so the layout
            does not shift as the video loads. Capped by height too: on a
            short viewport the row's height is set by the card column, and
            without a height cap the video box would render at its full
            aspect-ratio width regardless, overflowing sideways. */}
        <div className="order-first aspect-video max-h-full w-full overflow-hidden rounded-[28px] border border-zinc-300 bg-zinc-900 dark:border-zinc-800 lg:order-none">
          <Walkthrough />
        </div>
      </div>
    </div>
  );
}

/**
 * A short, silent, looping capture of the real product: create a workspace,
 * upload a document, ask a question, watch the cited answer stream in. Muted
 * autoplay with `loop` is what lets it play the instant the page loads with
 * no controls chrome competing with the sign-in card beside it - the same
 * pattern product landing pages use for a hero clip.
 *
 * MP4 (H.264) listed first: Safari has no VP8/webm decoder at all, so a
 * webm-only source silently renders nothing there - `<source>` picks the
 * first type the browser can actually play, not the first in file-size
 * order. The MP4 also carries `+faststart` (moov atom at the front) so
 * playback can begin from a partial download instead of waiting on the
 * whole file, and is ~85% smaller than the raw Playwright-recorded webm.
 *
 * `poster` is the clip's own first frame, not a separate graphic - without
 * it, a `<video>` with nothing decoded yet paints as whatever the element's
 * background resolves to, and for the instant before that resolves the
 * browser's own default (white) shows through, which read as a flash on
 * load. The parent's `bg-zinc-900` is the same fix's second layer, in case
 * the poster itself is still loading.
 */
function Walkthrough() {
  return (
    <video
      className="h-full w-full bg-zinc-900 object-cover"
      poster="/media/walkthrough-poster.jpg"
      autoPlay
      loop
      muted
      playsInline
      preload="auto"
      aria-label="KnowledgeHub product walkthrough: uploading a document and asking a question with a verified citation"
    >
      <source src="/media/walkthrough.mp4" type="video/mp4" />
      <source src="/media/walkthrough.webm" type="video/webm" />
    </video>
  );
}
