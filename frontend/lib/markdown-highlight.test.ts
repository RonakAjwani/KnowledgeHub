/**
 * Citation highlighting in the Markdown Original view.
 *
 * Both cases here were reported from the deployed app against a real document,
 * and neither is catchable by reading the code: the highlight renders, it is
 * just the wrong length, and nothing errors.
 */

import { describe, expect, it } from "vitest";

import { HIGHLIGHT_NODE_TAG, remarkHighlightSnippet } from "./markdown-highlight";
import { ANCHOR_CHARS, anchorOf, normalise } from "./text-match";

interface MdNode {
  type: string;
  value?: string;
  children?: MdNode[];
  data?: { hName?: string };
}

/** A paragraph tree, the shape remark hands the plugin. */
function paragraph(text: string): MdNode {
  return { type: "root", children: [{ type: "paragraph", children: [{ type: "text", value: text }] }] };
}

/** The concatenated text of every highlighted node, in document order. */
function highlighted(node: MdNode): string {
  if (node.data?.hName === HIGHLIGHT_NODE_TAG) {
    return (node.children ?? []).map((c) => c.value ?? "").join("");
  }
  return (node.children ?? []).map(highlighted).join("");
}

// The real section from `01_architecture_and_api_reference.md`, and the exact
// passage the screenshot showed being mis-highlighted.
const SECTION =
  "4.4 Rate Limits The API rate limit is 300 requests per minute per Workspace " +
  "on the Free and Starter plans, and 1,200 requests per minute on the Team plan " +
  "and above. Exceeding the limit returns HTTP 429 with a Retry-After header " +
  "indicating the number of seconds to wait.";

describe("citation highlighting", () => {
  it("highlights the whole cited passage, not just the search anchor", () => {
    // Reported: a 272-character citation showed ~180 characters highlighted -
    // the anchor length. The anchor is how the passage is found; it was being
    // used as how much to light.
    expect(SECTION.length).toBeGreaterThan(ANCHOR_CHARS);

    const tree = paragraph(SECTION);
    remarkHighlightSnippet(SECTION)(tree);

    expect(normalise(highlighted(tree))).toBe(normalise(SECTION));
  });

  it("never ends the highlight mid-word", () => {
    // Reported: the highlight stopped inside "limit", leaving "l". 180 is a raw
    // character count and lands inside a word far more often than between two.
    const tree = paragraph(SECTION);
    remarkHighlightSnippet(SECTION)(tree);

    const lit = highlighted(tree);
    const rest = SECTION.slice(lit.length);
    expect(
      rest === "" || /^\s/.test(rest) || /\s$/.test(lit),
      `highlight ended mid-word: ...${lit.slice(-25)}|${rest.slice(0, 15)}...`,
    ).toBe(true);
  });

  it("stops at the point the rendering stops agreeing", () => {
    // The shared opening has to exceed ANCHOR_CHARS, or the anchor *is* the
    // whole cited string - including the divergent tail - and the search
    // correctly finds nothing. Only a passage long enough for the anchor to be
    // a proper prefix exercises the extension at all.
    const shared = `${SECTION} Rules are evaluated within roughly one second of the triggering event.`;
    expect(normalise(shared).length).toBeGreaterThan(ANCHOR_CHARS);

    const cited = `${shared} A sentence that the rendering does not contain anywhere.`;
    const tree = paragraph(`${shared} A completely unrelated closing sentence.`);
    remarkHighlightSnippet(cited)(tree);

    const lit = normalise(highlighted(tree));
    expect(lit.length).toBeGreaterThan(ANCHOR_CHARS);
    expect(lit).not.toContain("unrelated closing");
    expect(lit).not.toContain("does not contain");
  });

  it("skips the search entirely when the passage is too short to anchor", () => {
    const tree = paragraph("Rate limits apply.");
    remarkHighlightSnippet("429")(tree);
    expect(highlighted(tree)).toBe("");
  });
});

describe("anchorOf", () => {
  it("cuts at a word boundary rather than at a raw character count", () => {
    const long = "alpha bravo charlie delta echo foxtrot golf hotel india juliet ".repeat(6);
    const anchor = anchorOf(long);

    expect(anchor.length).toBeLessThanOrEqual(ANCHOR_CHARS);
    expect(long.startsWith(anchor)).toBe(true);
    // The character after the anchor must be a boundary, not the middle of a word.
    expect(/\s/.test(long[anchor.length])).toBe(true);
  });

  it("falls back to a raw cut when there is no boundary to find", () => {
    // CJK and long identifiers have no spaces; cutting mid-run is correct there.
    const unbroken = "这是一个没有空格的句子".repeat(60);
    expect(anchorOf(unbroken).length).toBe(ANCHOR_CHARS);
  });

  it("returns a short passage unchanged", () => {
    expect(anchorOf("  Rate  limits   apply.  ")).toBe("rate limits apply.");
  });
});
