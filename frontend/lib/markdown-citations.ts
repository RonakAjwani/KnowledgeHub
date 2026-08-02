/**
 * A `remark` plugin that turns every `[n]` citation marker inside prose into
 * its own inline AST node, so `AnswerBody` can render a real `<CitationChip>`
 * there instead of a literal bracket - without ever touching raw markdown
 * syntax. It does the same for `CURSOR_SENTINEL`, the streaming caret, for
 * the identical reason (see below).
 *
 * The marker has to be found *after* the text is parsed into an AST, not
 * before: an earlier approach split the raw answer string around `[n]` and
 * ran each half through Markdown independently, which corrupted any run that
 * happened to straddle a marker (`**bold [1] text**` split into two
 * separately-parsed halves, each missing one `**`). Splitting a `text` node
 * post-parse instead never crosses a syntax boundary, since anything that
 * would have been syntax was already resolved into its own node type by then.
 */

/** Structural, not `mdast`'s own types: this file only ever touches `type`,
 * `value` and `children`, and typing against that directly sidesteps pulling
 * in `@types/mdast` for three fields. */
interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: Record<string, unknown>;
}

/** `[12]` - bounded to three digits so `[2024]` in prose is not eaten as a
 * marker. `AnswerBody` uses the same shape (see its `PARTIAL_MARKER_RE`) to
 * strip a marker the stream has only half-delivered before this ever runs. */
const MARKER_RE = /\[(\d{1,3})\]/g;

/** A Private Use Area character, appended to the *end* of the answer text
 * only while a turn is still streaming - `AnswerBody`'s stand-in for "the
 * caret belongs right here." Chosen over a literal character (`|`, `_`)
 * because nothing a model would ever generate collides with it, so the
 * split below never fires on the model's own prose by accident.
 *
 * Splicing it into the *markdown source* and letting this plugin turn it
 * into its own node, rather than rendering a cursor `<span>` as a sibling
 * after `<ReactMarkdown>`'s output, is what keeps it inline at the true end
 * of a live sentence instead of on its own line: `ReactMarkdown` renders
 * top-level content as block elements (`<p>`, `<li>`, ...), so anything
 * appended *after* that output lands after the closing block tag, not
 * inside it.
 */
export const CURSOR_SENTINEL = "";

const INLINE_RE = new RegExp(`${MARKER_RE.source}|${CURSOR_SENTINEL}`, "g");

/** The hast tag names this plugin emits - `AnswerBody` renders them via
 * `ReactMarkdown`'s `components` prop, keyed on these exact strings. */
export const CITATION_NODE_TAG = "citation-marker";
export const CURSOR_NODE_TAG = "streaming-cursor";

function splitTextNode(node: MdNode): MdNode[] {
  const value = node.value ?? "";
  INLINE_RE.lastIndex = 0;
  if (!INLINE_RE.test(value)) return [node];
  INLINE_RE.lastIndex = 0;

  const out: MdNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  while ((match = INLINE_RE.exec(value)) !== null) {
    if (match.index > cursor) {
      out.push({ type: "text", value: value.slice(cursor, match.index) });
    }
    out.push(
      match[1] !== undefined
        ? {
            type: "citationMarker",
            data: {
              hName: CITATION_NODE_TAG,
              hProperties: { marker: Number(match[1]) },
            },
          }
        : {
            type: "streamingCursor",
            data: { hName: CURSOR_NODE_TAG, hProperties: {} },
          },
    );
    cursor = match.index + match[0].length;
  }
  if (cursor < value.length) {
    out.push({ type: "text", value: value.slice(cursor) });
  }
  return out;
}

function walk(node: MdNode): void {
  if (!node.children) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (child.type === "text") {
      next.push(...splitTextNode(child));
    } else {
      walk(child);
      next.push(child);
    }
  }
  node.children = next;
}

export function remarkCitationMarkers() {
  return (tree: MdNode) => {
    walk(tree);
  };
}

/**
 * The marker numbers an answer actually cites.
 *
 * The citation list the API returns is the *retrieval trace* - one entry per
 * DATA block that reached the prompt, numbered by its position there. That is
 * the right payload (it is what makes `[n]` resolvable, and it is the eval
 * dataset), but it is not the answer's bibliography: retrieval routinely pulls
 * a chunk from every document in the workspace, and the model cites a handful.
 * Anything rendering "the sources for this answer" has to narrow the list to
 * the markers present in the prose, and this is that narrowing.
 *
 * Scanning the raw string rather than the parsed AST means a `[1]` inside a
 * code fence counts here while `remarkCitationMarkers` would leave it as
 * literal text. That is a deliberate direction to be wrong in: the result is a
 * superset of the rendered chips, so the failure mode is listing a source the
 * reader cannot click back to - never dropping one they can.
 */
export function citedMarkers(text: string): Set<number> {
  const markers = new Set<number>();
  MARKER_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = MARKER_RE.exec(text)) !== null) {
    markers.add(Number(match[1]));
  }
  return markers;
}
