/**
 * Narrowing the retrieval trace down to an answer's actual bibliography.
 *
 * The regression these guard is the end-of-answer Sources footer listing every
 * document in the workspace: the citation list it was built from is one entry
 * per DATA block that reached the prompt, and retrieval reaches wide on
 * purpose. Only the markers the prose cites belong under that heading.
 */

import { describe, expect, it } from "vitest";

import { citedMarkers } from "./markdown-citations";

describe("citedMarkers", () => {
  it("returns only the markers the prose cites", () => {
    const answer =
      "Revenue grew 12% [1], driven by the enterprise tier [4][7]. " +
      "Margins were flat [1].";
    expect([...citedMarkers(answer)].sort((a, b) => a - b)).toEqual([1, 4, 7]);
  });

  it("is empty for an answer that cites nothing", () => {
    // A refusal or an abstain, which is exactly when a footer full of source
    // cards would be the most misleading thing on the screen.
    expect(citedMarkers("I could not find that in these documents.").size).toBe(
      0,
    );
  });

  it("does not read a four-digit number as a marker", () => {
    // Same three-digit bound `remarkCitationMarkers` renders against, so a
    // bracketed year cannot conjure a source card with no matching chip.
    expect(citedMarkers("The [2024] filing restated it [12].")).toEqual(
      new Set([12]),
    );
  });

  it("does not carry regex state between calls", () => {
    // `MARKER_RE` is a module-level /g regex shared with the remark plugin, so
    // a leftover `lastIndex` would make the second call skip the first marker.
    const answer = "First [1]. Second [2].";
    expect(citedMarkers(answer)).toEqual(citedMarkers(answer));
  });
});
