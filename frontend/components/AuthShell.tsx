import { Play } from "lucide-react";
import type { ReactNode } from "react";

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
      <div className="flex shrink-0 justify-center">
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
            should already be the shape of the thing that will fill it, so the
            layout does not shift when the real video lands. Capped by height
            too: on a short viewport the row's height is set by the card
            column, and without a height cap the video box would render at its
            full aspect-ratio width regardless, overflowing sideways. */}
        <div className="order-first aspect-video max-h-full w-full lg:order-none">
          <WalkthroughPlaceholder />
        </div>
      </div>
    </div>
  );
}

/**
 * Stands in for a short product walkthrough clip. Deliberately an explicit,
 * labelled placeholder rather than stock imagery: an unrelated photo would
 * read as filler, and this states plainly what belongs here.
 */
function WalkthroughPlaceholder() {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-3 rounded-[28px] border border-dashed border-zinc-300 bg-zinc-100/60 dark:border-zinc-800 dark:bg-zinc-900/40">
      <span className="flex size-14 items-center justify-center rounded-full bg-zinc-200 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-300">
        <Play className="size-6 translate-x-0.5" aria-hidden />
      </span>
      <p className="text-sm font-medium text-zinc-700 dark:text-zinc-200">
        Product walkthrough
      </p>
      <p className="max-w-[16rem] text-center text-xs text-zinc-500 dark:text-zinc-400">
        A short video showing how to upload documents and ask questions goes
        here.
      </p>
    </div>
  );
}
