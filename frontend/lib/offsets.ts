/**
 * Citation offsets are **code point** indices; JavaScript strings are indexed
 * in **UTF-16 code units**. This converts between them.
 *
 * I5 says every offset indexes `normalized_text` and nothing may introduce a
 * second referent. The backend honours that exactly - measured, its spans
 * round-trip under zero-width injection, bidi overrides, control characters and
 * blocks that sanitise away to nothing. But Python counts a character outside
 * the Basic Multilingual Plane as **one** index and JavaScript counts it as
 * **two**, so the browser and the backend disagree about what the same number
 * means the moment a document contains an emoji, a mathematical alphanumeric
 * (𝐀), a rare CJK extension character, or any other astral codepoint.
 *
 * MEASURED with a single 🚀 ahead of the cited sentence: offsets 40..88 sliced
 * to `"The Team plan carries a 99.9% uptime commitment."` in Python and
 * `"\nThe Team plan carries a 99.9% uptime commitment"` in the browser - the
 * highlight starts on the preceding newline and loses its final character. The
 * drift is one unit per astral character before the span, so it accumulates
 * down a document rather than staying constant.
 *
 * It also cannot trip the existing out-of-range guard: code point counts are
 * always **≤** code unit counts, so a shifted offset stays inside the string
 * and the highlight is silently wrong rather than visibly absent.
 */

/** True when every character is in the BMP, so the two indexings coincide. */
function isCodeUnitSafe(text: string): boolean {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    // High surrogate: the only way an astral character can appear.
    if (code >= 0xd800 && code <= 0xdbff) return false;
  }
  return true;
}

/**
 * Convert code-point offsets into code-unit offsets for `text`.
 *
 * Converts every offset in one pass rather than one pass each, because the
 * caller always needs a start and an end and the scan is the expensive part.
 * Returns the input untouched for the overwhelmingly common all-BMP document,
 * so this is the identity function on every document that has no astral
 * characters in it - which is most of them.
 *
 * An offset past the end of the string clamps to the end, matching what
 * `String.prototype.slice` already does with an over-long index.
 */
export function toCodeUnitOffsets(text: string, offsets: number[]): number[] {
  if (offsets.length === 0 || isCodeUnitSafe(text)) return offsets;

  const out = offsets.slice();
  const pending = offsets.map((_, i) => i);
  let codePoint = 0;

  for (let unit = 0; unit < text.length; codePoint += 1) {
    for (let p = pending.length - 1; p >= 0; p -= 1) {
      if (offsets[pending[p]] === codePoint) {
        out[pending[p]] = unit;
        pending.splice(p, 1);
      }
    }
    if (pending.length === 0) return out;

    const code = text.charCodeAt(unit);
    unit += code >= 0xd800 && code <= 0xdbff ? 2 : 1;
  }

  // Anything not reached lies at or past the end of the string.
  for (const index of pending) out[index] = text.length;
  return out;
}

/** The number of code points in `text` - what the backend's `len()` returns. */
export function codePointLength(text: string): number {
  if (isCodeUnitSafe(text)) return text.length;
  let count = 0;
  for (let unit = 0; unit < text.length; count += 1) {
    const code = text.charCodeAt(unit);
    unit += code >= 0xd800 && code <= 0xdbff ? 2 : 1;
  }
  return count;
}
