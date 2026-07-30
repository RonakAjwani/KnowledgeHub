"use client";

/**
 * Design-only mock of the login page - not wired to Clerk yet (there's no key
 * configured to test against), and deliberately not reachable through normal
 * navigation (no link points here). Visit `/login` directly to review it.
 * Once Clerk is actually connected, this visual design moves into
 * `app/sign-in`'s real `<SignIn>`-wrapping page instead of living as its own
 * static route.
 *
 * Matches the reference: wordmark top-left, a large display headline and
 * sans subtitle (both Inter, per the single-font decision in layout.tsx), a
 * bordered auth card (Google / email), and a large panel on the right - here
 * a video-walkthrough placeholder instead of the reference's photo, since a
 * real walkthrough clip will replace it later.
 */

import { Play } from "lucide-react";
import { useState, type CSSProperties } from "react";

function GoogleIcon() {
  return (
    <svg viewBox="0 0 24 24" className="size-4" aria-hidden>
      <path
        fill="#4285F4"
        d="M23.49 12.27c0-.79-.07-1.54-.19-2.27H12v4.51h6.47c-.29 1.48-1.14 2.73-2.4 3.58v3h3.86c2.26-2.09 3.56-5.17 3.56-8.82z"
      />
      <path
        fill="#34A853"
        d="M12 24c3.24 0 5.95-1.08 7.93-2.91l-3.86-3c-1.08.72-2.45 1.16-4.07 1.16-3.13 0-5.78-2.11-6.73-4.96H1.29v3.09C3.26 21.3 7.31 24 12 24z"
      />
      <path
        fill="#FBBC05"
        d="M5.27 14.29c-.25-.72-.38-1.49-.38-2.29s.14-1.57.38-2.29V6.62H1.29A11.96 11.96 0 0 0 0 12c0 1.92.46 3.74 1.29 5.38l3.98-3.09z"
      />
      <path
        fill="#EA4335"
        d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.42-3.42C17.95 1.19 15.24 0 12 0 7.31 0 3.26 2.7 1.29 6.62l3.98 3.09C6.22 6.86 8.87 4.75 12 4.75z"
      />
    </svg>
  );
}

function WalkthroughPlaceholder() {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-3 rounded-[28px] border border-dashed border-white/15 bg-white/[0.02]">
      <span className="flex size-14 items-center justify-center rounded-full bg-white/10 text-zinc-200">
        <Play className="size-6 translate-x-0.5" aria-hidden />
      </span>
      <p className="text-sm font-medium text-zinc-300">Product walkthrough</p>
      <p className="max-w-[16rem] text-center text-xs text-zinc-500">
        A short video showing how to upload documents and ask questions goes
        here.
      </p>
    </div>
  );
}

/**
 * `--z*`/`--surface-*` are indirected so `dark:`-paired utilities can differ
 * by theme (see globals.css) - which means the *bare* `zinc-*`/`black`/`white`
 * classes below would resolve differently depending on which theme the app
 * happens to be in, and this page is a deliberately theme-*fixed* dark design
 * (no `dark:` siblings anywhere in it, unlike every other file). Left to the
 * app's own tokens it rendered as illegible grey-on-grey under `.dark`, and
 * as warm-ink-on-warm-ink once light mode's ramp went warm.
 *
 * So this subtree pins its own self-contained dark palette, mirroring the
 * app's dark theme (neutral-black grounds, warm off-white text) written out
 * explicitly. Pinned rather than inherited so the page looks identical
 * regardless of the visitor's theme preference - a signed-out visitor has no
 * preference yet - and so it cannot silently drift if either ramp is retuned.
 *
 * NOTE the ordering: this page uses `text-zinc-50`/`text-zinc-100` for its
 * *headline and body*, so the low-numbered steps here must be near-WHITE -
 * the opposite of the app's dark theme, where `z50` is a near-black ground.
 * The off-white values are shared with the app so the two feel related; only
 * the direction of the scale differs, because here the steps are read as text
 * on a dark ground rather than as surfaces. That includes the app's near-zero
 * text warmth (R-B = 2-3): this page's headline is `text-5xl`, the largest
 * type in the product and the single worst place for a tint to show.
 */
const FIXED_DARK_TOKENS = {
  "--z50": "#f7f7f6",
  "--z100": "#edecea",
  "--z200": "#d6d5d3",
  "--z300": "#bab9b7",
  "--z400": "#9d9c9a",
  "--z500": "#8a8987",
  "--z600": "#666664",
  "--z700": "#3a3a3a",
  "--z800": "#262626",
  "--z900": "#1b1b1b",
  "--z950": "#0f0f0f",
  "--surface-white": "#ffffff",
  "--surface-black": "#0b0b0b",
  "--focus-ring": "#60a5fa",
  "--focus-gap": "#0b0b0b",
  /* Same reason as every token above: this page is dark even when the app
     theme is light, so it has to pin the scrollbar's inverted thumb itself -
     the global light-mode value is dark ink at 22% alpha, which is invisible
     on a near-black ground. `colorScheme` covers the native parts of the
     widget that CSS can't reach. */
  colorScheme: "dark",
  "--scrollbar-thumb": "rgb(240 240 240 / 0.22)",
  "--scrollbar-thumb-hover": "rgb(240 240 240 / 0.38)",
} as CSSProperties;

export default function LoginMockPage() {
  const [email, setEmail] = useState("");

  return (
    <div
      className="flex min-h-screen flex-col bg-black px-8 py-6 text-zinc-100"
      style={FIXED_DARK_TOKENS}
    >
      <div className="flex justify-center">
        <span className="text-lg font-semibold">KnowledgeHub</span>
      </div>

      <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col items-center justify-center gap-12 lg:flex-row lg:items-center">
        <div className="flex w-full max-w-md shrink-0 flex-col items-center text-center lg:items-start lg:text-left">
          <h1 className="text-4xl font-semibold leading-tight text-zinc-50 sm:text-5xl">
            Ask your documents anything
          </h1>
          <p className="mt-4 text-base text-zinc-400">
            Your documents, with answers you can verify
          </p>

          {/*
            The flat-rectangle look was two things: literal zinc-800/900 fills
            (opaque grays read as "outlined box"), and every radius the same
            size (no nested-radius relationship, which is what makes a card
            read as containing its own elements rather than just framing
            them). Fixed with white-alpha layers instead of gray fills - depth
            comes from translucency stacking against the pure black page, the
            same technique most refined dark UIs on a true-black canvas use -
            a shadow to lift the card off the page, and an outer radius
            visibly larger than the inner elements' (28px card, 14px
            buttons/input) rather than matching them 1:1.
          */}
          <div className="mt-8 w-full rounded-[28px] border border-white/10 bg-white/[0.03] p-8 shadow-2xl shadow-black/50">
            <button
              type="button"
              className="flex w-full items-center justify-center gap-2 rounded-2xl border border-white/15 bg-white/5 py-3 text-sm font-medium text-zinc-100 transition-colors duration-150 hover:bg-white/10"
            >
              <GoogleIcon />
              Continue with Google
            </button>

            <div className="my-5 flex items-center gap-3">
              <div className="h-px flex-1 bg-white/10" />
              <span className="text-xs font-medium tracking-wide text-zinc-500">OR</span>
              <div className="h-px flex-1 bg-white/10" />
            </div>

            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="Enter your email"
              className="w-full rounded-2xl border border-white/15 bg-white/5 px-4 py-3 text-sm text-zinc-100 placeholder:text-zinc-500 transition-colors duration-150 focus-visible:border-white/30 focus-visible:outline-none"
            />

            <button
              type="button"
              className="mt-3 w-full rounded-2xl bg-white py-3 text-sm font-semibold text-black shadow-lg shadow-black/20 transition-colors duration-150 hover:bg-zinc-200"
            >
              Continue with email
            </button>
          </div>
        </div>

        <div className="h-[28rem] w-full lg:h-[32rem] lg:flex-1">
          <WalkthroughPlaceholder />
        </div>
      </div>
    </div>
  );
}
