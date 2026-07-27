"""Cross-reference resolution: find the author's own explanation of a table or figure.

A cross-reference has two sides. The **target** is where the object lives — the
caption, ``Table 2: Quarterly revenue by segment``. The **sources** are the places
the body text points at it — ``as Table 2 shows, revenue grew 40%``. Linking them
gives every table and figure the document's own description of itself.

This exists because the obvious alternative is worse. Asking a VLM to describe a
chart produces text that is not in the document, so it has no offsets, cannot be
cited, and — decisively — **a model reading values off a chart axis is a
hallucination dressed as extraction.** If a user asks "what was Q3 revenue" and
the figure came from a model squinting at a bar chart, that is a fabricated number
with a citation attached, which is the single worst failure available to a system
whose whole pitch is verifiable grounding. Retrieving the author's sentence
"Q3 revenue reached $8M (Figure 3)" is real evidence with a real offset.

It is also free: no LLM call, no quota, no latency. Solving this with a regex
where the obvious move is a model is the point, not a shortcut.

**Where it does not work, stated plainly:** bare appendix tables and raw financial
statements often have no surrounding narrative; terse captions like
``Table 2: Results`` carry little signal; and narrative states *conclusions*
rather than *contents* — "Table 3 shows our method outperforms baselines" will not
answer "what was the F1 for BERT on SQuAD?". That is why the table's own markdown
stays in the chunk (rung 3 of the ladder) and why BM25 matters for tables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.schemas import BlockSpan, BlockType, NormalizedDocument

# Label forms seen in real documents: "Table 2", "Tab. 2", "TABLE II",
# "Figure 3", "Fig. 3", "Fig 3a", "Exhibit 4". Roman numerals included because
# older papers and legal documents use them.
_KIND = r"(?P<kind>tables?|tab\.|figs?\.|figures?|fig|exhibits?|charts?)"
_NUMBER = r"(?P<number>\d+[a-z]?|[IVXLC]+)"
_LABEL_RE = re.compile(rf"\b{_KIND}\s*{_NUMBER}\b", re.IGNORECASE)

# A caption is a label at the very start of a block, usually followed by a
# separator. Distinguishing caption from mention matters: the caption defines the
# object, a mention refers to it.
_CAPTION_RE = re.compile(rf"^\s*{_KIND}\s*{_NUMBER}\s*[:.—-]?\s*", re.IGNORECASE)

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

_KIND_CANON = {
    "table": "table",
    "tables": "table",
    "tab.": "table",
    "figure": "figure",
    "figures": "figure",
    "fig": "figure",
    "fig.": "figure",
    "figs.": "figure",
    "exhibit": "exhibit",
    "exhibits": "exhibit",
    "chart": "chart",
    "charts": "chart",
}


def canonical_label(kind: str, number: str) -> str:
    """``Tab. 2`` and ``TABLE 2`` and ``table 2`` all name the same object."""
    return f"{_KIND_CANON.get(kind.lower(), kind.lower())} {number.lower()}"


@dataclass(frozen=True)
class CrossReference:
    label: str
    caption: str | None
    caption_span: tuple[int, int] | None
    # Spans of the sentences elsewhere in the document that mention this label.
    mention_spans: tuple[tuple[int, int], ...]

    @property
    def has_narrative(self) -> bool:
        return bool(self.mention_spans)


def extract_label(text: str) -> str | None:
    """The label a block *is*, if the block opens with a caption."""
    match = _CAPTION_RE.match(text)
    if not match:
        return None
    return canonical_label(match.group("kind"), match.group("number"))


def _sentence_spans(text: str, offset: int) -> list[tuple[int, int, str]]:
    """Absolute spans of each sentence in a block.

    Offsets are computed by walking the original string rather than by summing
    split fragments, so a multi-character separator cannot shift them.
    """
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    for part in _SENTENCE_END_RE.split(text):
        if not part:
            continue
        start = text.find(part, cursor)
        if start < 0:
            continue
        end = start + len(part)
        spans.append((offset + start, offset + end, part))
        cursor = end
    return spans


def resolve_cross_references(doc: NormalizedDocument) -> dict[str, CrossReference]:
    """Map every captioned object to its caption and its body-text mentions.

    Consumes ``doc.spans`` rather than re-deriving positions — the builder is the
    only thing that knows where blocks live, and every returned span is a real
    offset into ``doc.text``, directly usable as a citation highlight.
    """
    captions: dict[str, tuple[BlockSpan, str]] = {}
    caption_block_indexes: dict[str, int] = {}

    for span in doc.spans:
        body = span.slice(doc.text)
        label = extract_label(body)
        if label and label not in captions:
            captions[label] = (span, body)
            caption_block_indexes[label] = span.block_index

    if not captions:
        return {}

    mentions: dict[str, list[tuple[int, int]]] = {label: [] for label in captions}

    for span in doc.spans:
        body = span.slice(doc.text)
        # A table's own markdown routinely repeats its label in a cell; that is
        # not the author explaining it.
        if span.block_type is BlockType.TABLE:
            continue

        for sent_start, sent_end, sentence in _sentence_spans(body, span.start):
            for match in _LABEL_RE.finditer(sentence):
                label = canonical_label(match.group("kind"), match.group("number"))
                if label not in mentions:
                    continue
                # Skip the caption itself: it is the target, not a source.
                if span.block_index == caption_block_indexes[label]:
                    continue
                pair = (sent_start, sent_end)
                if pair not in mentions[label]:
                    mentions[label].append(pair)

    return {
        label: CrossReference(
            label=label,
            caption=body,
            caption_span=(span.start, span.end),
            mention_spans=tuple(sorted(mentions[label])),
        )
        for label, (span, body) in captions.items()
    }


def build_lead_line(
    doc: NormalizedDocument,
    reference: CrossReference | None,
    *,
    max_chars: int = 400,
) -> str:
    """The escalation ladder, rungs 1 and 2 — the author's words, never a model's.

    Prepended to a table or figure chunk before embedding, because raw markdown
    retrieves badly as prose. Rung 3 (the table's own cells) is the chunk body and
    is BM25's job; rung 4 (VLM synthesis) is the fallback handled in Tier 2 and
    marked ``is_derived``.
    """
    if reference is None:
        return ""

    parts: list[str] = []
    if reference.caption:
        parts.append(reference.caption.strip())

    for start, end in reference.mention_spans:
        sentence = doc.text[start:end].strip()
        if not sentence:
            continue
        candidate = " ".join([*parts, sentence])
        if len(candidate) > max_chars:
            break
        parts.append(sentence)

    return " ".join(parts).strip()
