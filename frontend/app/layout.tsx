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
// **"system" is the default for an unset/invalid preference**, so a
// first-time visitor - including on the landing and sign-in pages, which is
// the first thing anyone sees - lands on whatever their OS asks for. This
// used to default to dark on the reasoning that the design is dark-first and a
// visitor should see the theme the app was built around; that was overridden
// deliberately, because respecting the OS setting is what a visitor expects
// and a light-mode user being handed a dark app reads as the app ignoring
// them. Light and dark are both fully supported here, so there is nothing to
// protect by forcing one. Dark remains selectable in Settings, and an explicit
// choice still wins over the OS.
//
// The localStorage read is the only part that can throw (private browsing,
// storage disabled), so only it is wrapped - resolving "system" must still
// happen in that case rather than dropping the visitor into a hardcoded
// fallback. `data-theme-pref` mirrors the preference so `useTheme` (see
// hooks/useTheme.ts) can tell "explicitly dark" apart from "system, currently
// resolving dark" without a hydration mismatch.
const PREFERENCES_INIT_SCRIPT = `
(function () {
  var pref = "system";
  var collapsed = false;
  try {
    var stored = localStorage.getItem("kh-theme");
    if (stored === "light" || stored === "dark" || stored === "system") pref = stored;
    collapsed = localStorage.getItem("kh-sidebar-collapsed") === "true";
  } catch (e) {}

  var isDark = pref === "dark";
  if (pref === "system") {
    try {
      isDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (e) {}
  }
  document.documentElement.setAttribute("data-theme-pref", pref);
  if (isDark) document.documentElement.classList.add("dark");
  if (collapsed) document.documentElement.classList.add("sidebar-collapsed");
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
