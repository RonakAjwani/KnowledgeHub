"""The one builder. ``normalized_text`` is constructed here and nowhere else.

Contract §1: ``build_normalized_text`` is the single function that turns parsed
blocks into the document string, and it returns both the string *and* every
block's span within it. Sanitisation (G3) and derived-block rendering happen
inside it, before spans are assigned, because offsets computed against
un-sanitised text are wrong the moment sanitisation runs.

**Nothing else may ever concatenate blocks.** This is not a style preference. The
reference project carries the failure in embryo: ``LoadedDocument.full_text``
joins blocks with ``"\\n\\n"``, while its chunker separately rebuilds a
``combined`` string using hand-rolled ``cursor += len(block.text) + 2``
arithmetic. Nothing depended on the two agreeing, so nothing noticed they had
drifted. Here, offsets are load-bearing - that same drift is a citation
highlighting the wrong sentence, and it fails silently and convincingly.

The defence is structural rather than documentary: this module owns the separator,
owns the derived-block rendering, and hands back spans. Downstream stages consume
``spans`` and never see a list of blocks to re-join.

After this function returns, ``text`` is immutable. A later ``.strip()``,
whitespace collapse, or re-clean invalidates every offset in the document.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.ingest.sanitize import merge_removals, sanitize_text
from app.models.schemas import (
    Block,
    BlockSpan,
    NormalizedDocument,
    SanitizationReport,
)

# The separator between blocks. Owned here so exactly one place knows its width.
BLOCK_SEPARATOR = "\n\n"

# Derived content is marked in the text itself, not only in chunk metadata. The
# contract requires a reader to be able to tell "this sentence is in the document"
# from "this is a model's description of a picture in the document", and metadata
# alone cannot carry that into a copied-and-pasted answer or a rendered source
# pane. The marker is inside the block's span, so highlighting a derived citation
# highlights its provenance too.
DERIVED_PREFIX = "[AI-described figure] "


def render_block_text(clean_text: str, *, is_derived: bool) -> str:
    """The exact string a block contributes to ``normalized_text``.

    Split out so that the span-assignment loop below and any test asserting the
    round-trip agree by construction rather than by coincidence.
    """
    if is_derived:
        return f"{DERIVED_PREFIX}{clean_text}"
    return clean_text


def build_normalized_text(blocks: Sequence[Block]) -> NormalizedDocument:
    """Build the document string and every block's span within it.

    Guarantees, all of which the property tests pin:

    * ``result.text[span.start:span.end]`` is exactly that block's rendered,
      post-sanitisation content - for every span, with no exceptions.
    * Spans appear in document order and never overlap.
    * ``span.block_index`` indexes into the *input* sequence, so a span can always
      be traced back to the block it came from even though empty blocks are
      dropped.
    * The separator between blocks lies outside every span, so no highlight ever
      includes padding the author did not write.
    """
    pieces: list[str] = []
    spans: list[BlockSpan] = []
    removed: dict[str, int] = {}
    dropped_blocks = 0
    cursor = 0

    for block_index, block in enumerate(blocks):
        result = sanitize_text(block.text)
        merge_removals(removed, result.removed)

        rendered = render_block_text(result.text, is_derived=block.is_derived)

        # A block that sanitised away to nothing contributes no text and therefore
        # no span. Emitting a zero-length span would satisfy the round-trip
        # property while giving the chunker and the highlighter an empty range to
        # trip over, so it is dropped and counted instead.
        if not rendered.strip():
            dropped_blocks += 1
            continue

        if pieces:
            cursor += len(BLOCK_SEPARATOR)
            pieces.append(BLOCK_SEPARATOR)

        start = cursor
        pieces.append(rendered)
        cursor += len(rendered)

        spans.append(
            BlockSpan(
                block_index=block_index,
                start=start,
                end=cursor,
                section=block.section,
                page=block.page,
                block_type=block.block_type,
                is_derived=block.is_derived,
            )
        )

    if dropped_blocks:
        removed["empty_blocks"] = removed.get("empty_blocks", 0) + dropped_blocks

    report = SanitizationReport(
        removed_spans=sum(1 for v in removed.values() if v),
        kinds=removed,
    )

    return NormalizedDocument(
        text="".join(pieces),
        spans=tuple(spans),
        report=report,
    )


def span_for_offset(
    doc: NormalizedDocument, offset: int
) -> BlockSpan | None:
    """The span containing ``offset``, or None if it falls in a separator.

    Used by the citation resolver to answer "which section and page is this
    character in?" without re-deriving positions.
    """
    for span in doc.spans:
        if span.start <= offset < span.end:
            return span
    return None
