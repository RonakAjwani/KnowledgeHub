/**
 * A `remark` plugin that highlights a cited passage inside rendered Markdown -
 * the Markdown/plain-text equivalent of `DocumentView.tsx`'s pdf.js text-layer
 * search, and built the same way on purpose: `normalized_text` has no
 * coordinates to look the passage up by (that is exactly why the PDF path has
 * to search pdf.js's own text layer instead of using the stored offsets
 * directly), and Markdown syntax characters (`**`, `#`, `` ` ``) are consumed
 * by the parser and never appear in a rendered text node, so a raw
 * `char_start`/`char_end` slice cannot be mapped onto the parsed tree either.
 * Both views end up doing the same best-effort thing: normalise, concatenate,
 * search, map back - `locate()` over pdf.js text items there, this over mdast
 * text nodes here. Reuses that function's exact `normalise`/`ANCHOR_CHARS`/
 * `MIN_ANCHOR_CHARS` so the two searches agree on what counts as a match.
 *
 * Before this, the Original view had highlighting for PDFs (search) and for
 * plain text via the Text tab (exact offsets, always correct) - but nothing
 * for Markdown's Original view, which just rendered the document with no
 * indication of which passage a citation had pointed at.
 */

import { ANCHOR_CHARS, MIN_ANCHOR_CHARS, normalise } from "@/lib/text-match";

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: Record<string, unknown>;
}

/** The hast tag name this plugin emits. */
export const HIGHLIGHT_NODE_TAG = "cited-mark";

interface Piece {
  raw: string;
  start: number;
  end: number;
}

/** Depth-first, in document order - the same order a second traversal of the
 * same (unmodified-in-between) tree will visit nodes in, which is what lets
 * `applyHighlight` correlate "the Nth text node" between the two passes
 * without threading parent pointers through. */
function collectTextNodes(node: MdNode, out: MdNode[]): void {
  if (node.type === "text") {
    out.push(node);
    return;
  }
  for (const child of node.children ?? []) collectTextNodes(child, out);
}

/**
 * Inverts `normalise` (whitespace collapsed to one space, case folded): the
 * raw index one past whatever raw character produced the
 * `normalizedIndex`-th emitted character. Lowercasing does not change length
 * or position, so it needs no inverse; whitespace collapsing does, since one
 * emitted space can correspond to a run of several raw characters (or to
 * leading whitespace `normalise`'s own `.trim()` emits nothing for at all).
 */
function rawIndexAt(raw: string, normalizedIndex: number): number {
  if (normalizedIndex <= 0) return 0;
  let emitted = 0;
  let i = 0;
  while (i < raw.length && /\s/.test(raw[i])) i += 1; // `.trim()`'s leading half
  let inRun = false;
  for (; i < raw.length; i += 1) {
    if (/\s/.test(raw[i])) {
      if (!inRun) {
        emitted += 1; // the whole run collapses to this one emitted space
        inRun = true;
        if (emitted === normalizedIndex) return i + 1;
      }
    } else {
      emitted += 1;
      inRun = false;
      if (emitted === normalizedIndex) return i + 1;
    }
  }
  return raw.length;
}

function splitTextNode(node: MdNode, rawStart: number, rawEnd: number): MdNode[] {
  const raw = node.value ?? "";
  const out: MdNode[] = [];
  if (rawStart > 0) out.push({ type: "text", value: raw.slice(0, rawStart) });
  out.push({
    type: "citedHighlight",
    data: { hName: HIGHLIGHT_NODE_TAG, hProperties: {} },
    children: [{ type: "text", value: raw.slice(rawStart, rawEnd) }],
  });
  if (rawEnd < raw.length) out.push({ type: "text", value: raw.slice(rawEnd) });
  return out;
}

function applyHighlight(node: MdNode, pieces: Piece[], at: number, until: number): void {
  if (!node.children) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text") {
      const piece = pieces.shift();
      const overlaps = piece && piece.start < until && piece.end > at;
      if (!overlaps) {
        next.push(child);
        continue;
      }
      const localStart = Math.max(at, piece!.start) - piece!.start;
      const localEnd = Math.min(until, piece!.end) - piece!.start;
      const raw = child.value ?? "";
      next.push(
        ...splitTextNode(child, rawIndexAt(raw, localStart), rawIndexAt(raw, localEnd)),
      );
    } else {
      applyHighlight(child, pieces, at, until);
      next.push(child);
    }
  }
  node.children = next;
}

export function remarkHighlightSnippet(cited: string | null) {
  return (tree: MdNode) => {
    if (!cited) return;
    const target = normalise(cited).slice(0, ANCHOR_CHARS);
    if (target.length < MIN_ANCHOR_CHARS) return;

    const textNodes: MdNode[] = [];
    collectTextNodes(tree, textNodes);

    const pieces: Piece[] = [];
    let haystack = "";
    for (const node of textNodes) {
      const piece = normalise(node.value ?? "");
      if (!piece) {
        // A placeholder keeps `pieces` aligned 1:1 with every text node the
        // second pass will visit, whitespace-only ones included - dropping it
        // here would desync the `pieces.shift()` correlation in
        // `applyHighlight` from the second traversal's first empty-piece node
        // onward.
        pieces.push({ raw: "", start: -1, end: -1 });
        continue;
      }
      const start = haystack.length ? haystack.length + 1 : 0;
      haystack = haystack.length ? `${haystack} ${piece}` : piece;
      pieces.push({ raw: node.value ?? "", start, end: haystack.length });
    }

    const at = haystack.indexOf(target);
    if (at < 0) return;
    const until = at + target.length;

    applyHighlight(tree, pieces, at, until);
  };
}
