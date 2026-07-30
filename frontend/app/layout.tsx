import type { Metadata } from "next";
import { Geist_Mono, Inter } from "next/font/google";

import { Providers } from "./providers";
import "./globals.css";

// One font, everywhere - Inter, including headlines and the wordmark. An
// earlier pass split display text onto a serif face for "brand moments"; the
// user asked explicitly for a single, readable, balanced font throughout
// instead, so that split is gone. Geist Mono stays for the handful of
// monospace spots (token counts, ids).
const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "KnowledgeHub",
  description: "Ask questions across your own documents, with verifiable citations.",
};

// Runs before hydration so the `.dark`/`.sidebar-collapsed` classes are
// already correct on first paint - without this, the page renders
// light/expanded, then useTheme/useSidebarCollapsed's first read flips them a
// frame later, and every visit to a saved preference flashes the default
// first. Both live in one script (one blocking `<script>` tag, not two) since
// there's nothing to gain from splitting a two-line IIFE in half.
//
// Dark is the default for an unset/invalid preference (the reference this
// design follows is dark throughout, and a first-time visitor should land on
// the theme the app is actually designed around rather than whatever their OS
// happens to prefer) - "system" is a real, selectable third option once
// someone opens Settings, just not what a visitor with nothing stored yet
// gets defaulted into. `data-theme-pref` mirrors the resolved preference so
// `useTheme` (see hooks/useTheme.ts) can tell "explicitly dark" apart from
// "system, currently resolving dark" without a hydration mismatch.
const PREFERENCES_INIT_SCRIPT = `
(function () {
  try {
    var pref = localStorage.getItem("kh-theme");
    if (pref !== "light" && pref !== "dark" && pref !== "system") pref = "dark";
    var isDark = pref === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
      : pref === "dark";
    document.documentElement.setAttribute("data-theme-pref", pref);
    if (isDark) document.documentElement.classList.add("dark");
    if (localStorage.getItem("kh-sidebar-collapsed") === "true") {
      document.documentElement.classList.add("sidebar-collapsed");
    }
  } catch (e) {
    document.documentElement.setAttribute("data-theme-pref", "dark");
    document.documentElement.classList.add("dark");
  }
})();
`;

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${geistMono.variable} h-full antialiased`}
      // `PREFERENCES_INIT_SCRIPT` below mutates this element's classList
      // (adding "dark"/"sidebar-collapsed") before React hydrates it - an
      // expected, intentional difference from what the server rendered, not a
      // real mismatch. Without this, React logged a hydration-mismatch error
      // on every load, which triggered Next's dev-mode error overlay - and
      // that overlay's invisible portal was intercepting clicks on real UI
      // underneath it (confirmed via Playwright), which is what made the
      // sidebar and dropdowns intermittently unclickable.
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: PREFERENCES_INIT_SCRIPT }} />
      </head>
      <body className="flex min-h-full flex-col bg-zinc-50 font-sans text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
