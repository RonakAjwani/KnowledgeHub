"""Parent-child chunking with offsets assigned against ``normalized_text``.

Two units, deliberately:

* the **child** is what gets embedded and BM25'd - small, so retrieval is precise
  and a match means the match is actually about the query;
* the **parent** is what the LLM receives - the enclosing section window, so the
  model has enough context to answer rather than a fragment torn out of a
  paragraph.

Picking one size means trading precision against context. Parent-child is
strictly better than compromising on a middle size, and the only cost is one more
pair of offsets per chunk.

**Tables bypass the prose splitter entirely.** A table split mid-row is worse than
no table at all: the numbers survive, their column headers do not, and the result
reads as authoritative while being unattributable. When a table exceeds the
ceiling it is split by row with the header repeated in every fragment, so each
fragment stands alone.

Every offset here indexes into ``normalized_text`` and is derived from the spans
the builder produced. Nothing in this module re-concatenates or re-measures the
document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.ingest.crossref import CrossReference, build_lead_line, extract_label
from app.ingest.tokens import count_tokens
from app.models.schemas import (
    Block,
    BlockSpan,
    BlockType,
    Chunk,
    NormalizedDocument,
    chunk_id,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class _Piece:
    """A candidate child span, before it becomes a Chunk."""

    start: int
    end: int
    span: BlockSpan


def _sentence_offsets(text: str, base: int) -> list[tuple[int, int]]:
    """Absolute sentence spans, found by scanning rather than by summing splits."""
    out: list[tuple[int, int]] = []
    cursor = 0
    for part in _SENTENCE_SPLIT_RE.split(text):
        if not part.strip():
            continue
        found = text.find(part, cursor)
        if found < 0:
            continue
        out.append((base + found, base + found + len(part)))
        cursor = found + len(part)
    return out


def _split_prose(
    doc: NormalizedDocument, span: BlockSpan, max_tokens: int
) -> list[_Piece]:
    """Break a prose block on sentence boundaries, packing up to the ceiling."""
    body = span.slice(doc.text)
    if count_tokens(body) <= max_tokens:
        return [_Piece(span.start, span.end, span)]

    pieces: list[_Piece] = []
    sentences = _sentence_offsets(body, span.start) or [(span.start, span.end)]

    cur_start = sentences[0][0]
    cur_end = sentences[0][0]
    cur_tokens = 0

    for s_start, s_end in sentences:
        s_tokens = count_tokens(doc.text[s_start:s_end])
        if cur_tokens and cur_tokens + s_tokens > max_tokens:
            pieces.append(_Piece(cur_start, cur_end, span))
            cur_start, cur_tokens = s_start, 0
        cur_end = s_end
        cur_tokens += s_tokens

    if cur_end > cur_start:
        pieces.append(_Piece(cur_start, cur_end, span))
    return pieces


def _packed_prose(
    doc: NormalizedDocument,
    spans: tuple[BlockSpan, ...],
    start: int,
    max_tokens: int,
) -> tuple[list[_Piece], int]:
    """Pack consecutive prose blocks up to the child ceiling.

    Splitting alone is only half a chunker. Measured over this corpus before
    packing existed, **61% of chunks came in under half the 250-token target and
    30% were under 25 tokens** - headings, one-line paragraphs and stray labels
    each becoming their own chunk. A 20-token fragment competes for a top-k slot
    against a 230-token passage and wins it on a lucky term match, which spends
    the context budget on something that cannot answer anything.

    So blocks are accumulated until the next one would breach the ceiling.
    Returns the pieces and how many spans were consumed.

    The boundaries are deliberate:

    * **Section.** Text under a different heading is a different subject; the
      same rule the parent window already follows.
    * **Tables.** They own the atomicity rules in ``_split_table`` - header
      repetition, never splitting mid-row - and folding one into a prose run
      would discard all of it.
    * **Derived content.** A VLM's description of a figure is marked as such in
      the text; packing it together with the document's own words would produce
      one chunk that is half quotation and half model output, with a single
      ``is_derived`` flag that must lie about one of them.

    A block that already exceeds the ceiling on its own still splits exactly as
    before - packing never makes a chunk bigger than splitting would allow.

    Offsets stay honest for free: the blocks are adjacent in ``normalized_text``,
    so ``[first.start, last.end]`` is one contiguous span (separators included)
    and still slices back out of the document verbatim (I5).
    """
    first = spans[start]
    if count_tokens(first.slice(doc.text)) > max_tokens:
        return _split_prose(doc, first, max_tokens), 1

    end_index = start
    total = count_tokens(first.slice(doc.text))

    for nxt in range(start + 1, len(spans)):
        candidate = spans[nxt]
        if (
            candidate.block_type is BlockType.TABLE
            or candidate.section != first.section
            or candidate.is_derived != first.is_derived
        ):
            break
        # Measured from the end of what is packed so far, so the separator
        # between the blocks is counted once and only once.
        extra = count_tokens(doc.text[spans[end_index].end : candidate.end])
        if total + extra > max_tokens:
            break
        end_index = nxt
        total += extra

    return [_Piece(first.start, spans[end_index].end, first)], end_index - start + 1


def _split_table(
    doc: NormalizedDocument, span: BlockSpan, max_tokens: int
) -> list[_Piece]:
    """Split a table by row, repeating the header in every fragment.

    The repeated header cannot come from ``normalized_text`` for fragments after
    the first - those rows are not adjacent to the header in the document. So the
    *offsets* cover only the rows the fragment actually contains, and the header
    is re-attached to the embedded text later, in :func:`_chunk_text_for`. Offsets
    stay honest; the embedding gets what it needs.
    """
    body = span.slice(doc.text)
    if count_tokens(body) <= max_tokens:
        return [_Piece(span.start, span.end, span)]

    lines: list[tuple[int, int]] = []
    cursor = span.start
    for line in body.split("\n"):
        lines.append((cursor, cursor + len(line)))
        cursor += len(line) + 1

    # A markdown table's first two lines are the header and its separator.
    header_lines = lines[:2] if len(lines) > 2 else lines[:1]
    header_tokens = sum(count_tokens(doc.text[s:e]) for s, e in header_lines)
    data_lines = lines[len(header_lines) :]
    if not data_lines:
        return [_Piece(span.start, span.end, span)]

    pieces: list[_Piece] = []
    cur_start = data_lines[0][0]
    cur_end = data_lines[0][0]
    cur_tokens = header_tokens

    for row_start, row_end in data_lines:
        row_tokens = count_tokens(doc.text[row_start:row_end])
        if cur_end > cur_start and cur_tokens + row_tokens > max_tokens:
            pieces.append(_Piece(cur_start, cur_end, span))
            cur_start, cur_tokens = row_start, header_tokens
        cur_end = row_end
        cur_tokens += row_tokens

    if cur_end > cur_start:
        pieces.append(_Piece(cur_start, cur_end, span))
    return pieces


def _enforce_ceiling(
    doc: NormalizedDocument, pieces: list[_Piece], max_tokens: int
) -> list[_Piece]:
    """Hard-split any piece still over the child ceiling.

    MEASURED, and the reason this is not redundant with the two splitters above:
    both divide on a natural boundary and neither can divide a unit larger than
    the ceiling. `_split_prose` keys on `.!?` followed by whitespace, and its
    packing loop takes the first sentence unconditionally (`if cur_tokens and
    ...`), so one oversized sentence becomes one oversized chunk; with no
    boundary anywhere in the block it falls back to the whole block as a single
    "sentence". `_split_table` has the same shape for a single huge row.

    Real documents hit this. Unpunctuated prose produced a 1,057-token child and
    CJK text - which terminates sentences with 。, not `.` - produced 2,906,
    against an embedding model that accepts **512**. Nothing errors: fastembed
    truncates, so the dense vector describes the opening of the chunk and the
    remainder is unreachable by the dense branch while BM25 still indexes all of
    it. The failure is a quiet recall hole, which is the worst kind.
    """
    out: list[_Piece] = []
    for piece in pieces:
        out.extend(_hard_split(doc, piece, max_tokens))
    return out


def _hard_split(
    doc: NormalizedDocument, piece: _Piece, max_tokens: int
) -> list[_Piece]:
    """Cut a piece into ceiling-sized parts at whitespace, ignoring semantics.

    A last resort with no pretence otherwise: there is no sentence to respect
    here, and a chunk the embedder silently truncates is worse than one cut at
    an arbitrary word. Falls back to a hard character cut when the text has no
    whitespace at all (a base64 blob, an unbroken CJK run), because at that
    point any cut is arbitrary and the only wrong answer is not cutting.
    """
    text = doc.text[piece.start : piece.end]
    if count_tokens(text) <= max_tokens:
        return [piece]

    density = len(text) / max(1, count_tokens(text))
    step = max(1, int(max_tokens * density))

    out: list[_Piece] = []
    cursor = piece.start
    while cursor < piece.end:
        cut = min(piece.end, cursor + step)

        if cut < piece.end:
            probe, budget = cut, 64
            while budget and probe > cursor + 1 and not doc.text[probe].isspace():
                probe -= 1
                budget -= 1
            if probe > cursor + 1:
                cut = probe

        # Verify rather than trust the density estimate; shrink if it ran high.
        guard = 0
        while (
            guard < 4
            and cut - cursor > 1
            and count_tokens(doc.text[cursor:cut]) > max_tokens
        ):
            cut = cursor + max(1, (cut - cursor) * 3 // 4)
            guard += 1

        out.append(_Piece(cursor, cut, piece.span))
        cursor = cut
    return out


def _snap_to_word(text: str, offset: int, *, lower: int, upper: int, inward: int) -> int:
    """Move ``offset`` to the nearest word boundary, shrinking the window.

    Always shrinks rather than grows: a window that starts mid-word feeds the
    model a fragment, and growing to fix it could re-breach the budget the
    caller just satisfied. Bounded so a long unbroken run (a URL, a base64
    blob, CJK text with no spaces) cannot make this scan far.
    """
    limit = 48
    while limit and lower < offset < upper and not text[offset - 1].isspace():
        offset += inward
        limit -= 1
    return max(lower, min(upper, offset))


def _bounded_window(
    doc: NormalizedDocument,
    piece: _Piece,
    block: BlockSpan,
    max_tokens: int,
) -> tuple[int, int]:
    """A parent for a child whose own block is already over the budget.

    Centred on the child and grown outward *within the block* until the budget
    is spent, rather than taking the whole block. The child is always contained,
    even if it alone exceeds the cap - a parent that omits the cited span breaks
    the source-pane scroll target.

    Sized by this block's own character density rather than by growing a token
    count in a loop: the loop would be one `count_tokens` call per step over a
    block that is by definition large, and this runs per chunk. The estimate is
    then verified and shrunk, so density being off costs accuracy, never the
    ceiling.
    """
    start, end = piece.start, piece.end
    child_tokens = count_tokens(doc.text[start:end])
    if child_tokens >= max_tokens:
        return start, end

    body = block.slice(doc.text)
    density = len(body) / max(1, count_tokens(body))
    spare = int((max_tokens - child_tokens) * density) // 2

    new_start = max(block.start, start - spare)
    new_end = min(block.end, end + spare)

    # Verify against the real counter and pull in if the estimate ran high.
    for _ in range(4):
        if count_tokens(doc.text[new_start:new_end]) <= max_tokens:
            break
        new_start += (start - new_start) // 2
        new_end -= (new_end - end) // 2

    new_start = _snap_to_word(doc.text, new_start, lower=block.start, upper=start, inward=1)
    new_end = _snap_to_word(doc.text, new_end, lower=end, upper=block.end, inward=-1)
    return min(new_start, start), max(new_end, end)


def _parent_window(
    doc: NormalizedDocument,
    piece: _Piece,
    spans: tuple[BlockSpan, ...],
    max_tokens: int,
) -> tuple[int, int]:
    """The enclosing section window around a child, capped.

    Grows outward from the child's own block through neighbours in the same
    section, taking both sides each pass so context is balanced rather than
    all-preceding. Never crosses a section boundary: text under a different
    heading is not context, it is a different subject.
    """
    index = next(
        (i for i, s in enumerate(spans) if s.block_index == piece.span.block_index), None
    )
    if index is None:
        return piece.start, piece.end

    section = piece.span.section
    # The window starts at the child's own *block*, and that is only safe while
    # the block fits the budget. A block bigger than `max_tokens` made every
    # parent in it the entire block - the growth loop below never runs, but the
    # starting value is already over. MEASURED on a 54 KB single-section
    # document: 41 of 42 parents breached the 1200-token ceiling and the largest
    # was ~10,400 tokens, 8.7x the cap. That is the string handed to the model,
    # so `fit_context` then trimmed the turn down to roughly one source instead
    # of five - a quality collapse that only appears once a section exceeds the
    # ceiling, which is to say on real documents and not on a short demo corpus.
    if count_tokens(spans[index].slice(doc.text)) > max_tokens:
        return _bounded_window(doc, piece, spans[index], max_tokens)

    start, end = spans[index].start, spans[index].end
    tokens = count_tokens(doc.text[start:end])

    lo = hi = index
    # Each pass tries both sides and stops the moment neither moved. The
    # termination condition is "no progress", not a set of boundary checks -
    # an earlier version alternated sides and broke only at section or array
    # edges, which looped forever whenever growth was blocked by the *token
    # budget* instead: `moved` stayed false, no break condition held, and
    # `tokens` never changed. It only showed up on real documents, where
    # sections are long enough to exhaust the budget mid-section.
    while True:
        grew = False

        if lo > 0 and spans[lo - 1].section == section:
            candidate = spans[lo - 1]
            extra = count_tokens(doc.text[candidate.start : start])
            if tokens + extra <= max_tokens:
                lo -= 1
                start = candidate.start
                tokens += extra
                grew = True

        if hi < len(spans) - 1 and spans[hi + 1].section == section:
            candidate = spans[hi + 1]
            extra = count_tokens(doc.text[end : candidate.end])
            if tokens + extra <= max_tokens:
                hi += 1
                end = candidate.end
                tokens += extra
                grew = True

        if not grew:
            break

    # The parent must always contain its child, even if the child alone exceeds
    # the cap - a parent that omits the cited span would break the scroll target.
    return min(start, piece.start), max(end, piece.end)


def _governing_label(
    doc: NormalizedDocument, spans: tuple[BlockSpan, ...], position: int
) -> str | None:
    """The label of the object this block *is*, looking at neighbours if needed.

    A caption is almost never inside the thing it captions: the usual layout is a
    caption block immediately above or below the table or figure. Reading only the
    block's own text finds captions and misses every table they describe, which
    silently drops the whole lead-line ladder for the objects that need it most.

    Order matters - own text first, then the block above, then below - because a
    table sandwiched between two captioned figures must not adopt the wrong one.
    """
    span = spans[position]
    own = extract_label(span.slice(doc.text))
    if own:
        return own

    if span.block_type not in (BlockType.TABLE, BlockType.FIGURE):
        return None

    for neighbour in (position - 1, position + 1):
        if not 0 <= neighbour < len(spans):
            continue
        adjacent = spans[neighbour]
        if adjacent.block_type is BlockType.TABLE:
            continue
        label = extract_label(adjacent.slice(doc.text))
        if label:
            return label
    return None


# A heading component longer than this is a sentence the font size happened to
# flag, not a title. MEASURED: the 360 ONE fact pages set the scheme description
# - "(An open ended equity scheme investing in maximum 30 multicap stocks)", 69
# characters - at 9pt over a 6pt body, so it passes the size test and lands in
# the path. Prepended to a 90-character AUM chunk it is most of the embedded
# text, shared verbatim with every other chunk on the page, and it pushed the
# chunk holding the answer from rank 28 down rather than up.
_MAX_HEADING_COMPONENT_CHARS = 48


def _embeddable_heading(section: str | None) -> str | None:
    """The part of a section path worth embedding: the title-like components.

    Dropping a component keeps the ones around it - "Fund > <description> >
    AUM" becomes "Fund > AUM" rather than being abandoned - because the outer
    title is the piece that identifies which of twenty near-identical pages a
    chunk belongs to, and it is usually the shortest.
    """
    if not section:
        return None
    kept = [
        part.strip()
        for part in section.split(" > ")
        if 0 < len(part.strip()) <= _MAX_HEADING_COMPONENT_CHARS
    ]
    return " > ".join(kept) or None


def _chunk_text_for(
    doc: NormalizedDocument,
    piece: _Piece,
    reference: CrossReference | None,
    table_header: str,
) -> str:
    """The string that actually gets embedded.

    For prose this is the span verbatim. For tables it is the author's caption and
    referencing narrative (ladder rungs 1 and 2), then the repeated header, then
    the rows - because raw markdown embeds badly as prose and the document's own
    words describe the table better than a model's would.
    """
    body = doc.text[piece.start : piece.end]

    # The section path is prepended to what gets embedded, never to the stored
    # span. Offsets keep indexing `normalized_text` exactly (I5) - this string
    # is the retrieval unit, not the citation unit.
    #
    # MEASURED: a chunk reading "Net AUM : 6,634.45 crore" is textually
    # identical to the same line on nineteen other fund pages, so a question
    # naming one fund could not select between them and the answer came back
    # with another fund's figure. The page title is the only thing that tells
    # them apart, and until now it was recorded as metadata the embedding never
    # saw.
    heading = _embeddable_heading(piece.span.section)
    prefix = f"{heading}\n" if heading and not body.lstrip().startswith(heading) else ""

    if piece.span.block_type is not BlockType.TABLE:
        return f"{prefix}{body}"

    parts: list[str] = []
    if prefix:
        parts.append(prefix.rstrip("\n"))
    lead = build_lead_line(doc, reference)
    if lead:
        parts.append(lead)
    if table_header and not body.lstrip().startswith(table_header.split("\n")[0]):
        parts.append(table_header)
    parts.append(body)
    return "\n".join(parts)


def chunk_document(
    doc: NormalizedDocument,
    *,
    doc_id: str,
    user_id: str,
    references: dict[str, CrossReference] | None = None,
    blocks: list[Block] | None = None,
    settings: Settings | None = None,
) -> list[Chunk]:
    """Turn a normalised document into chunks with offsets into its text."""
    cfg = settings or get_settings()
    references = references or {}
    child_max = cfg.child_tokens
    parent_max = cfg.parent_tokens

    chunks: list[Chunk] = []
    index = 0

    # Walked with an explicit cursor rather than `enumerate`, because a prose run
    # can consume several spans at once - see `_packed_prose`.
    position = 0
    while position < len(doc.spans):
        span = doc.spans[position]
        body = span.slice(doc.text)
        is_table = span.block_type is BlockType.TABLE

        if is_table:
            pieces = _split_table(doc, span, child_max)
            consumed = 1
            lines = body.split("\n")
            table_header = "\n".join(lines[:2]) if len(lines) > 2 else ""
        else:
            pieces, consumed = _packed_prose(doc, doc.spans, position, child_max)
            table_header = ""

        # Both splitters divide on a *natural* boundary - a sentence, a table
        # row - and neither can divide a unit that is itself over the ceiling.
        # This is the backstop for text that offers no such boundary.
        pieces = _enforce_ceiling(doc, pieces, child_max)

        # Which captioned object this block belongs to - its own caption if it
        # has one, otherwise an adjacent caption block.
        label = _governing_label(doc, doc.spans, position)
        reference = references.get(label) if label else None
        related = tuple(reference.mention_spans) if reference else ()

        for piece in pieces:
            text = _chunk_text_for(doc, piece, reference, table_header)
            p_start, p_end = _parent_window(doc, piece, doc.spans, parent_max)

            chunks.append(
                Chunk(
                    id=chunk_id(doc_id, index, text),
                    doc_id=doc_id,
                    user_id=user_id,
                    chunk_index=index,
                    text=text,
                    char_start=piece.start,
                    char_end=piece.end,
                    parent_text=doc.text[p_start:p_end],
                    parent_char_start=p_start,
                    parent_char_end=p_end,
                    section=span.section,
                    page=span.page,
                    token_count=count_tokens(text),
                    chunk_type=span.block_type,
                    is_derived=span.is_derived,
                    related_spans=related,
                )
            )
            index += 1

        position += consumed

    return chunks
