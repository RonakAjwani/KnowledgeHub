/**
 * Best-effort "find this passage inside a differently-structured rendering of
 * the same text" matching, shared by `DocumentView.tsx`'s pdf.js text-layer
 * search and `markdown-highlight.ts`'s mdast text-node search.
 *
 * Both callers face the same problem for the same reason: `normalized_text`
 * carries the only offsets that are ever authoritative (I5), but neither a
 * PDF page nor a parsed Markdown tree exposes a position that those offsets
 * map onto - a PDF has coordinates instead of character indices, and
 * Markdown's parser consumes the very syntax characters (`**`, `#`) an offset
 * would have to cross. Search is the fallback both settle on, and it has to
 * be the *same* search, or "the passage is highlighted in the PDF but not in
 * the Markdown view" becomes a real, confusing difference between two things
 * that are supposed to agree.
 */

/** Words shorter than this carry too little signal to anchor a match -"the"// appears on every page. Below it, the search is skipped rather than risk
 * highlighting an unrelated line. */
export const MIN_ANCHOR_CHARS = 12;

/** How much of the cited passage to search for. The whole span often crosses
 * a column/page break (PDF) or several Markdown nodes and then matches
 * nothing; its opening is enough to place the highlight. */
export const ANCHOR_CHARS = 180;

/**
 * Collapse whitespace and case so two independently-extracted texts of the
 * same passage compare fairly - both the whole `needle` and each individual
 * haystack item/node, on both sides of every call site below.
 *
 * Collapses to a single space, and that specific choice is the one that
 * makes this whole scheme work: it is what makes every kind of boundary -
 * a plain word gap, a Markdown paragraph break, a pdf.js line-item split -
 * normalise to *exactly the same thing*, one space, regardless of which of
 * those it originally was or how many raw whitespace characters it spanned.
 * That equivalence is what lets a haystack built by joining separate
 * items/nodes with an inserted single space agree with a `needle` that was
 * one continuous slice of `normalized_text`, no matter where the needle's
 * boundaries happen to fall relative to the haystack's own item/node
 * boundaries.
 *
 * An earlier version of this function stripped whitespace to nothing rather
 * than collapsing it to a space - a genuine bug, not a deliberate choice; the
 * comment above it already said "collapse", just not the code. Stripping
 * loses exactly the boundary information collapsing preserves: a `needle`
 * spanning two haystack items normalised to `...performancecapacityfactor...`
 * (no separator at all) while the haystack's own join produces
 * `...performance capacityfactor...` (one space) - one character apart,
 * forever failing to match. That one bug was enough to fail the PDF search
 * for almost any real citation of more than a few words, since `ANCHOR_CHARS`
 * (180 characters) crosses at least one pdf.js line-item boundary in nearly
 * every case - confirmed against a real citation and a real page, not
 * theorised.
 */
export function normalise(text: string): string {
  return text.replace(/\s+/g, " ").trim().toLowerCase();
}
