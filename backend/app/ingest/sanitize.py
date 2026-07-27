"""G3, ingest half: strip what a human reader cannot see.

Retrieved document text is an untrusted input channel. Users upload arbitrary
PDFs, and the classic indirect-injection payload — "ignore previous instructions
and…" — is hidden as white-on-white text, a zero-opacity run, or an HTML comment.
It is invisible to the person who uploaded the file and extracted verbatim by the
chunking pipeline, which is exactly what makes it dangerous: the model treats
retrieved text as trustworthy because it arrived through the system's own
retrieval path.

This module is one layer of the defence, not the whole of it. The other half is
in the prompt: chunk content goes into delimited DATA blocks with the delimiter
escaped inside the content, so text cannot break out of its own wrapper. Neither
layer is complete alone; the blast radius stays small because this application
gives the model no write tools and no outbound calls.

**Non-fatal by design.** Suspicious content is removed and counted, never
rejected — a document full of zero-width characters is far more likely to be a
bad PDF export than an attack, and refusing it would be wrong either way. What
matters is that the removal is *visible*, which is why every call returns a
report that is persisted on the Document and surfaced in the document manager.

Ordering note: this runs **inside** ``build_normalized_text``, before any offset
is assigned. Sanitising after offsets exist would invalidate every one of them.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# Zero-width and invisible formatting characters. These render as nothing but
# survive text extraction, so they can carry a payload or split a keyword to
# defeat naive matching ("ig​nore" reads as "ignore" to a tokenizer).
_ZERO_WIDTH = (
    "​"  # zero-width space
    "‌"  # zero-width non-joiner
    "‍"  # zero-width joiner
    "⁠"  # word joiner
    "﻿"  # zero-width no-break space / BOM
    "­"  # soft hyphen
    "᠎"  # Mongolian vowel separator
)
_ZERO_WIDTH_RE = re.compile(f"[{_ZERO_WIDTH}]")

# Bidirectional overrides can visually reorder text so that what a reviewer reads
# is not what a tokenizer consumes — the "Trojan Source" trick. There is no
# legitimate reason for these to appear in extracted document prose.
_BIDI_RE = re.compile("[‪-‮⁦-⁩]")

# HTML/XML comments. Never rendered, frequently carry instructions in documents
# converted from web sources.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# C0/C1 control characters other than tab and newline, which are meaningful.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Runs of whitespace long enough to push visible content off a rendered page.
_LONG_WHITESPACE_RE = re.compile(r"[ \t]{40,}")


class SanitizeResult(NamedTuple):
    text: str
    # kind -> number of characters removed
    removed: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.removed.values())


def sanitize_text(text: str) -> SanitizeResult:
    """Strip invisible content, returning the cleaned text and what was removed.

    Deliberately does **not** strip or collapse ordinary whitespace at the edges
    of the string: the caller assigns offsets against exactly what comes back, so
    any cosmetic tidying here has to be something the caller can rely on being
    stable. Long *runs* of horizontal whitespace are collapsed because they are an
    evasion technique, not formatting.
    """
    removed: dict[str, int] = {}

    def _strip(pattern: re.Pattern[str], kind: str, source: str, repl: str = "") -> str:
        matches = pattern.findall(source)
        if matches:
            dropped = sum(len(m) for m in matches) - (len(repl) * len(matches))
            if dropped > 0:
                removed[kind] = removed.get(kind, 0) + dropped
        return pattern.sub(repl, source)

    # HTML comments first: their bodies may themselves contain zero-width or
    # control characters, and removing the whole comment is cheaper than cleaning
    # its interior and then discarding it.
    text = _strip(_HTML_COMMENT_RE, "html_comment", text)
    text = _strip(_ZERO_WIDTH_RE, "zero_width", text)
    text = _strip(_BIDI_RE, "bidi_control", text)
    text = _strip(_CONTROL_RE, "control_char", text)
    text = _strip(_LONG_WHITESPACE_RE, "long_whitespace", text, repl=" ")

    return SanitizeResult(text=text, removed=removed)


def merge_removals(target: dict[str, int], source: dict[str, int]) -> None:
    for kind, count in source.items():
        target[kind] = target.get(kind, 0) + count
