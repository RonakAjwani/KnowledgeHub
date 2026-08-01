import type { ComponentProps } from "react";

/**
 * The KnowledgeHub mark ("The Facet") - a solid circle with a diamond cut as
 * true negative space (an SVG mask, not a background-colour trick), plus a
 * small gold centre dot. Replaces the Next.js default favicon and the plain
 * text-only wordmark everywhere it appeared.
 *
 * Fill is `currentColor`, not a baked-in ink/cream pair - the source kit
 * shipped two static files (one per theme) because the diamond has to read
 * against *whatever* is behind it, but this codebase already has a working
 * mechanism for exactly that (`text-zinc-900 dark:text-zinc-100`, the same
 * pattern every lucide icon in this app uses), so one `currentColor` SVG
 * does the job of both files. The gold accent dot is the one fixed colour:
 * it's deliberately outside the app's monochrome-plus-platform-blue system
 * (see the accent-hue comment in globals.css) so the mark reads as a brand
 * mark rather than another blue system-affordance, and it never appears
 * anywhere else on screen to clash with.
 */
export function Logo({ className, ...props }: ComponentProps<"svg">) {
  return (
    <svg
      viewBox="0 0 100 100"
      role="img"
      aria-label="KnowledgeHub"
      className={className}
      {...props}
    >
      <mask id="kh-facet-mask">
        <rect width="100" height="100" fill="#fff" />
        <polygon points="50,8 80,50 50,92 20,50" fill="#000" />
      </mask>
      <circle cx="50" cy="50" r="34" fill="currentColor" mask="url(#kh-facet-mask)" />
      <circle cx="50" cy="50" r="6.5" fill="#b8863b" />
    </svg>
  );
}
