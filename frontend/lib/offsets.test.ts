/**
 * Code point <-> code unit offset conversion.
 *
 * The bug this exists for is invisible on any document that happens to be all
 * BMP - which is every document in the demo corpus, measured - so only an
 * astral fixture exercises it at all.
 */

import { describe, expect, it } from "vitest";

import { codePointLength, toCodeUnitOffsets } from "./offsets";

/** How Python would index this string: by code point. */
function pythonSlice(text: string, start: number, end: number): string {
  return Array.from(text).slice(start, end).join("");
}

describe("toCodeUnitOffsets", () => {
  it("is the identity on an all-BMP document", () => {
    // Accented Latin, CJK and BMP symbols are all one code unit each, so the
    // two indexings already agree and nothing should move.
    const text = "Café — 第三季度 ✅ £29/month, “Team” plan.";
    expect(toCodeUnitOffsets(text, [0, 5, 12, text.length])).toEqual([
      0,
      5,
      12,
      text.length,
    ]);
  });

  it("recovers the passage Python meant when astral characters precede it", () => {
    // MEASURED against the real thing: one 🚀 ahead of the cited sentence made
    // the browser slice `"\nThe Team plan carries a 99.9% uptime commitment"` -
    // starting on the newline, losing the final period - from offsets that
    // Python resolved correctly.
    const text = "Status: ✅ shipped. 🚀 Launch went well.\nThe Team plan carries a 99.9% uptime commitment.";
    const start = Array.from(text).indexOf("T", 30);
    const end = Array.from(text).length;

    const naive = text.slice(start, end);
    const [fixedStart, fixedEnd] = toCodeUnitOffsets(text, [start, end]);
    const fixed = text.slice(fixedStart, fixedEnd);

    expect(fixed).toBe(pythonSlice(text, start, end));
    expect(fixed).toBe("The Team plan carries a 99.9% uptime commitment.");
    expect(naive).not.toBe(fixed); // the bug reproduces without the conversion
  });

  it("drifts by one unit per astral character, so it accumulates", () => {
    const text = `${"🚀".repeat(5)}TARGET`;
    const start = Array.from(text).indexOf("T");
    const [converted] = toCodeUnitOffsets(text, [start]);

    expect(start).toBe(5); // five code points
    expect(converted).toBe(10); // ten code units
    expect(text.slice(converted)).toBe("TARGET");
  });

  it("clamps an offset past the end, matching slice()", () => {
    const text = "🚀 short";
    const [converted] = toCodeUnitOffsets(text, [999]);
    expect(converted).toBe(text.length);
  });

  it("handles an offset landing exactly on an astral character", () => {
    const text = "ab🚀cd";
    const [converted] = toCodeUnitOffsets(text, [2]);
    expect(text.slice(converted)).toBe("🚀cd");
  });

  it("returns an empty request untouched", () => {
    expect(toCodeUnitOffsets("🚀", [])).toEqual([]);
  });
});

describe("codePointLength", () => {
  it("matches what the backend's len() would report", () => {
    expect(codePointLength("abc")).toBe(3);
    expect(codePointLength("🚀🚀")).toBe(2);
    expect("🚀🚀".length).toBe(4); // the code-unit count the naive check used
    expect(codePointLength("Café ✅")).toBe(6);
  });

  it("is what the out-of-range guard must compare against", () => {
    // A code point count is always <= the code unit count, so comparing an
    // offset against `text.length` lets a genuinely out-of-range citation
    // through on a document containing astral characters.
    const text = "🚀".repeat(10); // 10 code points, 20 code units
    const citationEnd = 15; // past the end in code points
    expect(citationEnd).toBeLessThan(text.length); // would pass the naive check
    expect(citationEnd).toBeGreaterThan(codePointLength(text)); // correctly rejected
  });
});
