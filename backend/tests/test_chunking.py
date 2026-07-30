"""Parsing, cross-reference resolution, and chunking.

The load-bearing assertion is the same one as Phase 1, carried one stage further:
a chunk's ``char_start``/``char_end`` must still slice its own text out of
``normalized_text``. That is the whole citation chain — click a marker, resolve a
chunk, highlight a span — and it is the thing that breaks silently.
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.ingest.chunk import chunk_document
from app.ingest.crossref import (
    build_lead_line,
    canonical_label,
    extract_label,
    resolve_cross_references,
)
from app.ingest.normalize import build_normalized_text
from app.ingest.parse import (
    UnsupportedDocument,
    parse_document,
    parse_markdown,
    parse_plain_text,
)
from app.ingest.tokens import count_tokens
from app.models.schemas import Block, BlockType

# Token counting uses the deterministic heuristic here rather than downloading
# 130 MB of ONNX weights; chunking logic is what is under test, not the tokenizer.
CFG = Settings(child_tokens=40, parent_tokens=200)


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    from app.ingest import tokens

    monkeypatch.setattr(tokens, "get_embedding_model", lambda: None)


# ------------------------------------------------------------------- parsing


def test_markdown_tracks_the_heading_path() -> None:
    result = parse_markdown("# Setup\n\nIntro text.\n\n## Auth\n\nToken rotation.\n")
    sections = [b.section for b in result.blocks]
    assert "Setup" in sections
    assert "Setup > Auth" in sections


def test_markdown_heading_pops_to_the_right_level() -> None:
    md = "# A\n\ntext\n\n## B\n\ntext\n\n# C\n\ntext\n"
    result = parse_markdown(md)
    assert result.blocks[-1].section == "C", "a level-1 heading must reset the path"


def test_markdown_tables_stay_whole() -> None:
    md = "# Data\n\n| q | rev |\n| --- | --- |\n| Q1 | 5 |\n| Q2 | 8 |\n\nAfter.\n"
    result = parse_markdown(md)
    tables = [b for b in result.blocks if b.block_type is BlockType.TABLE]
    assert len(tables) == 1
    assert "Q1" in tables[0].text and "Q2" in tables[0].text
    assert result.tables_found == 1


def test_plain_text_splits_on_blank_lines() -> None:
    result = parse_plain_text("First para.\n\nSecond para.\n\n\nThird.")
    assert [b.text for b in result.blocks] == ["First para.", "Second para.", "Third."]


def test_unsupported_mime_is_rejected() -> None:
    with pytest.raises(UnsupportedDocument, match="Unsupported media type"):
        parse_document(b"data", "application/zip")


def test_markdown_dispatches_by_mime() -> None:
    result = parse_document(b"# Title\n\nBody.", "text/markdown")
    assert result.blocks[0].text == "Title"


# --------------------------------------------------------- cross-references


def test_labels_are_canonicalised_across_spellings() -> None:
    assert canonical_label("Tab.", "2") == "table 2"
    assert canonical_label("TABLE", "2") == "table 2"
    assert canonical_label("Fig", "3") == "figure 3"
    assert extract_label("Table 2: Quarterly revenue") == "table 2"
    assert extract_label("Fig. 3 — Architecture") == "figure 3"
    assert extract_label("This mentions Table 2 mid-sentence") is None


def test_mentions_resolve_to_real_spans_in_the_document() -> None:
    blocks = [
        Block(text="Table 2: Quarterly revenue by segment", block_type=BlockType.PROSE),
        Block(text="| q | rev |\n| --- | --- |\n| Q3 | 8 |", block_type=BlockType.TABLE),
        Block(text="Revenue reached $8M in Q3, as Table 2 shows. Growth continued."),
    ]
    doc = build_normalized_text(blocks)
    refs = resolve_cross_references(doc)

    assert "table 2" in refs
    ref = refs["table 2"]
    assert ref.has_narrative

    # Every returned span must be a real offset into normalized_text — that is
    # what lets a table citation also highlight the paragraph explaining it.
    start, end = ref.mention_spans[0]
    assert doc.text[start:end] == "Revenue reached $8M in Q3, as Table 2 shows."


def test_the_caption_is_not_counted_as_a_mention_of_itself() -> None:
    blocks = [
        Block(text="Figure 3: System architecture"),
        Block(text="The pipeline is shown in Figure 3."),
    ]
    refs = resolve_cross_references(build_normalized_text(blocks))
    assert len(refs["figure 3"].mention_spans) == 1


def test_a_tables_own_cells_are_not_mistaken_for_narrative() -> None:
    """A table routinely repeats its label in a cell; that is not the author
    explaining it."""
    blocks = [
        Block(text="Table 1: Results"),
        Block(
            text="| ref | value |\n| --- | --- |\n| Table 1 | 5 |",
            block_type=BlockType.TABLE,
        ),
    ]
    refs = resolve_cross_references(build_normalized_text(blocks))
    assert refs["table 1"].mention_spans == ()


def test_lead_line_prefers_the_authors_words() -> None:
    blocks = [
        Block(text="Table 2: Quarterly revenue"),
        Block(text="As Table 2 shows, revenue grew 40% year over year."),
    ]
    doc = build_normalized_text(blocks)
    refs = resolve_cross_references(doc)
    lead = build_lead_line(doc, refs["table 2"])

    assert "Quarterly revenue" in lead
    assert "revenue grew 40%" in lead


def test_no_captions_means_no_references() -> None:
    doc = build_normalized_text([Block(text="Just ordinary prose here.")])
    assert resolve_cross_references(doc) == {}


# ------------------------------------------------------------------ chunking


def _chunks_for(blocks, **kw):
    doc = build_normalized_text(blocks)
    refs = resolve_cross_references(doc)
    chunks = chunk_document(
        doc, doc_id="d1", user_id="u1", references=refs, settings=CFG, **kw
    )
    return doc, chunks


def test_every_chunk_slices_back_out_of_normalized_text() -> None:
    """The citation chain, end to end. If this breaks, highlights point at the
    wrong sentence and nothing else fails."""
    blocks = [
        Block(text="Alpha section intro. " * 20, section="Alpha"),
        Block(text="Beta content here. " * 20, section="Beta"),
    ]
    doc, chunks = _chunks_for(blocks)

    assert len(chunks) > 2, "expected the long blocks to split"
    for chunk in chunks:
        sliced = doc.text[chunk.char_start : chunk.char_end]
        assert sliced, "empty chunk span"
        # Prose chunks embed their span verbatim.
        assert chunk.text == sliced


def test_parent_always_contains_its_child() -> None:
    blocks = [
        Block(text=f"Sentence number {i} in this section. " * 3, section="S")
        for i in range(8)
    ]
    doc, chunks = _chunks_for(blocks)
    for chunk in chunks:
        assert chunk.parent_char_start <= chunk.char_start
        assert chunk.parent_char_end >= chunk.char_end
        assert chunk.parent_text == doc.text[
            chunk.parent_char_start : chunk.parent_char_end
        ]


def test_parent_window_never_crosses_a_section_boundary() -> None:
    """Text under a different heading is a different subject, not context."""
    blocks = [
        Block(text="Auth details. " * 10, section="Setup > Auth"),
        Block(text="Billing details. " * 10, section="Setup > Billing"),
    ]
    doc, chunks = _chunks_for(blocks)
    for chunk in chunks:
        parent = doc.text[chunk.parent_char_start : chunk.parent_char_end]
        if chunk.section == "Setup > Auth":
            assert "Billing details" not in parent


def test_tables_are_never_split_mid_row() -> None:
    rows = "\n".join(f"| Q{i} | {i * 100} | segment-{i} |" for i in range(1, 40))
    table = f"| quarter | revenue | segment |\n| --- | --- | --- |\n{rows}"
    doc, chunks = _chunks_for([Block(text=table, block_type=BlockType.TABLE)])

    assert len(chunks) > 1, "expected the oversized table to split"
    for chunk in chunks:
        span = doc.text[chunk.char_start : chunk.char_end]
        for line in span.split("\n"):
            if line.strip():
                assert line.strip().startswith("|") and line.strip().endswith("|"), (
                    f"row was cut mid-line: {line!r}"
                )


def test_split_table_repeats_the_header_in_every_fragment() -> None:
    """A fragment without its header is numbers with no attribution."""
    rows = "\n".join(f"| Q{i} | {i * 100} |" for i in range(1, 40))
    table = f"| quarter | revenue |\n| --- | --- |\n{rows}"
    _, chunks = _chunks_for([Block(text=table, block_type=BlockType.TABLE)])

    assert len(chunks) > 1
    for chunk in chunks:
        assert "quarter" in chunk.text and "revenue" in chunk.text


def test_table_chunks_carry_the_authors_lead_line() -> None:
    blocks = [
        Block(text="Table 2: Quarterly revenue by segment"),
        Block(
            text="| quarter | revenue |\n| --- | --- |\n| Q3 | 8 |",
            block_type=BlockType.TABLE,
        ),
        Block(text="Revenue reached $8M in Q3, as Table 2 shows."),
    ]
    _, chunks = _chunks_for(blocks)
    table_chunk = next(c for c in chunks if c.chunk_type is BlockType.TABLE)

    # Rungs 1 and 2 of the ladder: caption, then referencing narrative.
    assert "Quarterly revenue by segment" in table_chunk.text
    assert "Revenue reached $8M" in table_chunk.text
    # Rung 3: the cells themselves survive for exact lookups.
    assert "| Q3 | 8 |" in table_chunk.text


def test_table_chunk_offsets_cover_only_the_table_itself() -> None:
    """The lead line is prepended to the embedded text but is not inside the
    table's span — a citation must highlight the table, not the paragraph."""
    blocks = [
        Block(text="Table 2: Revenue"),
        Block(text="| q | r |\n| --- | --- |\n| Q3 | 8 |", block_type=BlockType.TABLE),
        Block(text="As Table 2 shows, revenue grew."),
    ]
    doc, chunks = _chunks_for(blocks)
    table_chunk = next(c for c in chunks if c.chunk_type is BlockType.TABLE)

    span = doc.text[table_chunk.char_start : table_chunk.char_end]
    assert span.startswith("|")
    assert "As Table 2 shows" not in span


def test_related_spans_link_a_table_to_its_explanation() -> None:
    blocks = [
        Block(text="Table 5: Latency"),
        Block(text="| p | ms |\n| --- | --- |\n| p50 | 12 |", block_type=BlockType.TABLE),
        Block(text="Latency stayed under 20ms, see Table 5."),
    ]
    doc, chunks = _chunks_for(blocks)
    caption_chunk = next(c for c in chunks if c.related_spans)

    start, end = caption_chunk.related_spans[0]
    assert "Latency stayed under 20ms" in doc.text[start:end]


def test_chunk_ids_are_stable_across_reruns() -> None:
    """Idempotent re-ingest: the same document must produce the same ids, or
    every re-upload doubles the vectors."""
    blocks = [Block(text="Stable content for hashing. " * 5)]
    _, first = _chunks_for(blocks)
    _, second = _chunks_for(blocks)
    assert [c.id for c in first] == [c.id for c in second]


def test_chunk_indexes_are_contiguous() -> None:
    blocks = [Block(text=f"Block {i} content. " * 6, section="S") for i in range(5)]
    _, chunks = _chunks_for(blocks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_derived_flag_and_type_survive_into_chunks() -> None:
    blocks = [Block(text="A rising revenue chart.", is_derived=True, block_type=BlockType.FIGURE)]
    _, chunks = _chunks_for(blocks)
    assert chunks[0].is_derived is True
    assert chunks[0].chunk_type is BlockType.FIGURE


def test_metadata_propagates_to_chunks() -> None:
    blocks = [Block(text="Rotation is automatic.", section="Setup > Auth", page=12)]
    _, chunks = _chunks_for(blocks)
    assert chunks[0].section == "Setup > Auth"
    assert chunks[0].page == 12
    assert chunks[0].token_count > 0


def test_empty_document_yields_no_chunks() -> None:
    _, chunks = _chunks_for([Block(text="   ")])
    assert chunks == []


def test_parent_window_terminates_when_budget_blocks_growth() -> None:
    """Regression: the window loop must stop on 'no progress', not on reaching a
    section or array edge.

    An earlier version alternated sides and broke only at those edges, so when a
    neighbouring block was too large for the remaining token budget — mid-section,
    away from both array ends — nothing moved, no break fired, and the token count
    never changed. It spun forever. Short test documents never reached the budget,
    so only a real 40k-character paper surfaced it.

    The reproducing shape is narrow, which is why it survived the suite: the
    child's own block must fit under the cap (or the loop is never entered at
    all), while every neighbour must be too large to append. ~165 tokens a block
    against a 200-token parent gives exactly that.
    """
    blocks = [
        Block(text=f"Paragraph {i}. " + ("filler words here. " * 40), section="One")
        for i in range(30)
    ]
    doc = build_normalized_text(blocks)
    tight = Settings(child_tokens=40, parent_tokens=200)

    first = doc.spans[0]
    own = count_tokens(doc.text[first.start : first.end])
    assert own < tight.parent_tokens < own * 2, (
        "test no longer reproduces the hang: the block must fit alone but not in pairs"
    )

    chunks = chunk_document(doc, doc_id="d1", user_id="u1", settings=tight)

    assert chunks
    for chunk in chunks:
        assert chunk.parent_char_start <= chunk.char_start
        assert chunk.parent_char_end >= chunk.char_end


def test_borderless_rescue_needs_a_caption_to_fire() -> None:
    """The fallback detector is gated on the author saying a table is there.

    Measured over the corpus, running pdfplumber's text strategy unconditionally
    finds 16 tables in a paper containing one, and drops another page from 50
    detections to 9 — so the rescue is scoped to pages whose own text carries a
    table caption *and* where the default detector found nothing. Ordinary prose,
    and a passing mention mid-sentence, must not trigger it.
    """
    from app.ingest.parse import _TABLE_CAPTION_RE

    # Real captions, at line start — these are the author declaring a table.
    for caption in ("Table 1: Results", "TABLE II  Latency", "Tab. 3 — Sizes"):
        assert _TABLE_CAPTION_RE.search(caption), caption

    # A mention inside a sentence is not a caption; matching it would point the
    # noisy strategy at pages of plain prose.
    assert not _TABLE_CAPTION_RE.search("as Table 2 shows, revenue grew")
    assert not _TABLE_CAPTION_RE.search("no tabular content here at all")


def test_small_adjacent_blocks_are_packed_not_left_as_fragments() -> None:
    """Splitting alone is half a chunker.

    Measured before packing existed: 61% of corpus chunks came in under half the
    250-token target and 30% were under 25 tokens — headings and one-line
    paragraphs each becoming their own chunk, competing for a top-k slot against
    real passages.
    """
    blocks = [Block(text=f"Short line {i}.", section="One") for i in range(12)]
    doc = build_normalized_text(blocks)
    chunks = chunk_document(doc, doc_id="d1", user_id="u1", settings=CFG)

    assert len(chunks) < len(blocks), "adjacent short blocks should merge"
    for chunk in chunks:
        assert chunk.text == doc.text[chunk.char_start : chunk.char_end], (
            "a packed span must still slice its own text out of normalized_text"
        )


def test_packing_never_crosses_a_section_boundary() -> None:
    blocks = [
        Block(text="Auth line.", section="Setup > Auth"),
        Block(text="Billing line.", section="Setup > Billing"),
    ]
    doc = build_normalized_text(blocks)
    chunks = chunk_document(doc, doc_id="d1", user_id="u1", settings=CFG)

    assert len(chunks) == 2, "different sections are different subjects"


def test_packing_never_absorbs_a_table_or_derived_content() -> None:
    """A table owns its own atomicity rules, and a VLM's figure description
    cannot share one `is_derived` flag with the document's own words."""
    blocks = [
        Block(text="Lead in.", section="S"),
        Block(text="| a | b |\n| --- | --- |\n| 1 | 2 |", block_type=BlockType.TABLE, section="S"),
        Block(text="After.", section="S"),
        Block(text="A described chart.", section="S", is_derived=True),
    ]
    doc = build_normalized_text(blocks)
    chunks = chunk_document(doc, doc_id="d1", user_id="u1", settings=CFG)

    kinds = [(c.chunk_type, c.is_derived) for c in chunks]
    assert (BlockType.TABLE, False) in kinds, "the table must survive as a table"
    assert (BlockType.PROSE, True) in kinds, "derived content stays separately flagged"
    # Four distinct blocks, none of which may merge with another.
    assert len(chunks) == 4


def test_an_oversized_block_still_splits() -> None:
    """Packing must never make a chunk larger than splitting would allow."""
    blocks = [Block(text="Sentence here. " * 200, section="S")]
    doc = build_normalized_text(blocks)
    chunks = chunk_document(doc, doc_id="d1", user_id="u1", settings=CFG)

    assert len(chunks) > 1
