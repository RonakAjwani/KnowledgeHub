import type { Metadata } from "next";
import { Geist_Mono, Inter } from "next/font/google";

import { Providers } from "./providers";
import "./globals.css";

// Inter over Geist: the app's whole job is to be read for long stretches —
// answers, source passages, citations — and Inter's tall x-height and open
// counters hold up better than a display-leaning sans at paragraph length and
// small sizes. Geist Mono stays for the handful of monospace spots (token
// counts, ids).
const inter = Inter({ variable: "--font-inter", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "KnowledgeHub",
  description: "Ask questions across your own documents, with verifiable citations.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-zinc-50 font-sans text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
