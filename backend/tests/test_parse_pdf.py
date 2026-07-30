"""Tier-1 PDF parsing, against real generated PDFs.

Fixtures are authored rather than committed as binaries, so each test states what
the document actually contains and a parser regression reads as a behaviour
change instead of a diff against an opaque blob.

The case that matters most is the ruled table. Naive extraction turns a revenue
table into a stream of numbers, and that failure is worse than a crash because it
*looks* like success: the numbers get embedded as prose, retrieve for nothing, and
nobody notices until an answer cites a column header as a sentence.
"""

from __future__ import annotations

import io

import pytest

from app.ingest.chunk import chunk_document
from app.ingest.crossref import resolve_cross_references
from app.ingest.normalize import build_normalized_text
from app.ingest.parse import UnsupportedDocument, parse_document
from app.models.schemas import BlockType

reportlab = pytest.importorskip("reportlab")

from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.config import Settings  # noqa: E402

CFG = Settings(child_tokens=120, parent_tokens=400)


@pytest.fixture(autouse=True)
def _no_model(monkeypatch):
    from app.ingest import tokens

    monkeypatch.setattr(tokens, "get_embedding_model", lambda: None)


def _draw_table(c, x, y, rows, col_width=110, row_height=20):
    """A table with real ruling lines, which is what find_tables() keys on."""
    width = col_width * len(rows[0])
    for r, row in enumerate(rows):
        top = y - r * row_height
        c.line(x, top, x + width, top)
        for i, cell in enumerate(row):
            c.drawString(x + i * col_width + 4, top - 14, str(cell))
    c.line(x, y - len(rows) * row_height, x + width, y - len(rows) * row_height)
    for i in range(len(rows[0]) + 1):
        c.line(x + i * col_width, y, x + i * col_width, y - len(rows) * row_height)


def make_report_pdf() -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)

    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 720, "Quarterly Financial Report")

    c.setFont("Helvetica", 11)
    c.drawString(72, 690, "This report summarises revenue across all business segments.")
    c.drawString(72, 674, "Revenue reached $8M in Q3, as Table 1 shows.")

    c.drawString(72, 640, "Table 1: Quarterly revenue by segment")
    _draw_table(
        c,
        72,
        625,
        [
            ["quarter", "revenue", "segment"],
            ["Q1", "5200000", "cloud"],
            ["Q2", "6400000", "cloud"],
            ["Q3", "8000000", "cloud"],
        ],
    )

    c.setFont("Helvetica", 11)
    c.drawString(72, 500, "Growth was driven primarily by enterprise contracts.")

    c.showPage()
    c.setFont("Helvetica-Bold", 18)
    c.drawString(72, 720, "Outlook")
    c.setFont("Helvetica", 11)
    c.drawString(72, 690, "We expect continued expansion in the next fiscal year.")
    c.showPage()
    c.save()
    return buf.getvalue()


# ------------------------------------------------------------------- parsing


def test_parses_multi_page_pdf() -> None:
    result = parse_document(make_report_pdf(), "application/pdf")
    assert result.page_count == 2
    assert result.blocks


def test_table_is_recovered_as_an_atomic_block_not_flattened_prose() -> None:
    """The whole reason Tier 1 uses pdfplumber rather than raw text extraction."""
    result = parse_document(make_report_pdf(), "application/pdf")
    tables = [b for b in result.blocks if b.block_type is BlockType.TABLE]

    assert len(tables) == 1, "the ruled table should be found exactly once"
    body = tables[0].text
    assert body.startswith("|"), "table should render as markdown"
    assert "quarter" in body and "revenue" in body, "header row must survive"
    assert "8000000" in body, "cell values must survive"

    # And the numbers must not also appear as loose prose.
    prose = " ".join(b.text for b in result.blocks if b.block_type is BlockType.PROSE)
    assert "5200000" not in prose


def test_headings_are_detected_and_scope_the_section_path() -> None:
    result = parse_document(make_report_pdf(), "application/pdf")
    sections = {b.section for b in result.blocks if b.section}
    assert "Quarterly Financial Report" in sections
    assert "Outlook" in sections


def test_page_numbers_are_recorded() -> None:
    result = parse_document(make_report_pdf(), "application/pdf")
    pages = {b.page for b in result.blocks}
    assert pages == {1, 2}


def test_reading_order_keeps_prose_around_its_table() -> None:
    """A table's explanation usually sits directly above or below it; emitting
    all prose then all tables would separate them."""
    result = parse_document(make_report_pdf(), "application/pdf")
    types = [b.block_type for b in result.blocks]
    table_at = types.index(BlockType.TABLE)

    before = " ".join(b.text for b in result.blocks[:table_at])
    after = " ".join(b.text for b in result.blocks[table_at + 1 :])
    assert "Table 1" in before
    assert "enterprise contracts" in after


def make_two_column_pdf() -> bytes:
    """A narrow table with a text column printed beside it, at the same height.

    This is the shape of every fund fact-page in the 360 ONE factsheet: a
    portfolio table down the middle, a "Fund Details" box to its left. Carving
    prose only out of the vertical gaps *between* tables skips the whole column.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)

    c.setFont("Helvetica", 11)
    # Left column - level with the table, not above or below it.
    c.drawString(60, 600, "Fund Manager Ms. Ada Lovelace")
    c.drawString(60, 584, "Net AUM 6634.45 crore")
    c.drawString(60, 568, "Expense Ratio 1.54%")
    # Right column, also level with the table.
    c.drawString(430, 600, "Sector Allocation 32.16%")

    _draw_table(
        c,
        220,
        620,
        [["holding", "weight"], ["ICICI Bank", "9.25"], ["Bharti Airtel", "6.03"]],
        col_width=90,
    )
    c.showPage()
    c.save()
    return buf.getvalue()


def test_text_beside_a_table_is_not_dropped() -> None:
    """Measured on the real corpus: this discarded 50% of the 360 ONE factsheet,
    including every fund's manager, AUM and expense ratio."""
    result = parse_document(make_two_column_pdf(), "application/pdf")
    text = " ".join(b.text for b in result.blocks)

    assert "Ada Lovelace" in text
    assert "6634.45" in text
    assert "Expense Ratio 1.54%" in text
    assert "Sector Allocation 32.16%" in text


def test_a_side_column_is_read_as_its_own_column_not_merged_across_the_page() -> None:
    """Recovering the text is not enough if it comes back interleaved.

    Lines are grouped by vertical position, so a full-width crop would join
    "Fund Manager Ms. Ada Lovelace" to "Sector Allocation 32.16%" - they share a
    baseline. Each column has to be cropped before the grouping happens.
    """
    result = parse_document(make_two_column_pdf(), "application/pdf")
    prose = [b.text for b in result.blocks if b.block_type is BlockType.PROSE]

    assert any("Ada Lovelace" in b and "Sector Allocation" not in b for b in prose)


def make_jittered_row_pdf() -> bytes:
    """A row label and its figures set 1.4pt apart vertically.

    Real financial tables do this constantly - figures in a tabular font sit on
    a marginally different baseline from the label beside them. The measured
    case is the 360 ONE macro table: label top=145.264, figures top=143.878.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 11)
    c.drawString(60, 600, "Two-wheeler sales (%YoY)")
    for i, value in enumerate(["14.9", "28.4", "19.3"]):
        c.drawString(300 + i * 60, 601.4, value)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_a_rows_figures_stay_with_their_own_label() -> None:
    """Lines group on a 2.5pt tolerance, so label and figures land on one line -
    but sorting the line by baseline first puts the figures ahead of the label,
    and every row silently inherits the values of the row above it."""
    result = parse_document(make_jittered_row_pdf(), "application/pdf")
    text = " ".join(b.text for b in result.blocks)

    assert "Two-wheeler sales (%YoY) 14.9 28.4 19.3" in text


def test_empty_pdf_fails_loudly_rather_than_indexing_nothing() -> None:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.showPage()
    c.save()

    with pytest.raises(UnsupportedDocument, match="No extractable text"):
        parse_document(buf.getvalue(), "application/pdf")


def test_corrupt_bytes_fail_loudly() -> None:
    with pytest.raises(UnsupportedDocument):
        parse_document(b"this is not a pdf", "application/pdf")


# ------------------------------------------------------ full chain on a PDF


def test_offsets_survive_the_whole_pdf_chain() -> None:
    """parse -> normalize -> crossref -> chunk, with the citation chain intact."""
    result = parse_document(make_report_pdf(), "application/pdf")
    doc = build_normalized_text(result.blocks)
    refs = resolve_cross_references(doc)
    chunks = chunk_document(
        doc, doc_id="d1", user_id="u1", references=refs, settings=CFG
    )

    assert chunks
    for chunk in chunks:
        sliced = doc.text[chunk.char_start : chunk.char_end]
        assert sliced.strip(), "chunk points at empty text"
        assert doc.text[chunk.parent_char_start : chunk.parent_char_end] == chunk.parent_text
        assert chunk.parent_char_start <= chunk.char_start
        assert chunk.parent_char_end >= chunk.char_end


def test_table_in_a_pdf_gets_the_authors_narrative_as_its_lead_line() -> None:
    """End to end: the caption and the referencing sentence both come from the
    document, so the table is findable without a model inventing a description."""
    result = parse_document(make_report_pdf(), "application/pdf")
    doc = build_normalized_text(result.blocks)
    refs = resolve_cross_references(doc)
    chunks = chunk_document(
        doc, doc_id="d1", user_id="u1", references=refs, settings=CFG
    )

    table_chunk = next(c for c in chunks if c.chunk_type is BlockType.TABLE)
    assert "Quarterly revenue by segment" in table_chunk.text  # rung 1: caption
    assert "Revenue reached $8M" in table_chunk.text  # rung 2: narrative
    assert "8000000" in table_chunk.text  # rung 3: cells
    assert table_chunk.is_derived is False  # no model involved


def test_table_citation_span_covers_the_table_only() -> None:
    result = parse_document(make_report_pdf(), "application/pdf")
    doc = build_normalized_text(result.blocks)
    refs = resolve_cross_references(doc)
    chunks = chunk_document(
        doc, doc_id="d1", user_id="u1", references=refs, settings=CFG
    )

    table_chunk = next(c for c in chunks if c.chunk_type is BlockType.TABLE)
    span = doc.text[table_chunk.char_start : table_chunk.char_end]
    assert span.lstrip().startswith("|")
    assert "Revenue reached $8M" not in span


def test_complexity_assessment_runs_on_every_page() -> None:
    """The Tier-2 escalation signal - a local heuristic, never a model."""
    result = parse_document(make_report_pdf(), "application/pdf")
    assert len(result.assessments) == 2
    # A clean born-digital page with a readable table needs no escalation.
    assert result.complex_pages == []


def make_sparse_table_pdf() -> bytes:
    """A right-aligned numeric table whose first data row has a gap.

    The shape of the 360 ONE macro table: six month columns, and a row that
    carries a value for only five of them. Which month is missing is visible
    only from the column positions.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica", 9)

    columns = [300, 360, 420, 480, 540]
    for x, month in zip(columns, ["Jun", "May", "Apr", "Mar", "Feb"], strict=True):
        c.drawRightString(x, 700, month)

    c.drawString(60, 686, "Two-wheeler sales")
    # No June figure. The remaining values sit under May..Feb.
    for x, value in zip(columns[1:], ["14.9", "28.4", "19.3", "35.2"], strict=True):
        c.drawRightString(x, 686, value)

    c.drawString(60, 672, "Manufacturing PMI")
    for x, value in zip(columns, ["54.2", "55.0", "54.7", "53.9", "56.9"], strict=True):
        c.drawRightString(x, 672, value)

    c.drawString(60, 658, "Energy Consumption")
    for x, value in zip(columns, ["10.9", "11.0", "4.4", "0.7", "4.9"], strict=True):
        c.drawRightString(x, 658, value)

    c.showPage()
    c.save()
    return buf.getvalue()


def test_a_blank_cell_survives_as_a_blank_cell() -> None:
    """MEASURED: flattened to prose, this row read "Two-wheeler sales 14.9 28.4
    19.3 35.2" and the model reported 14.9 as June's figure - it is May's. Every
    later month shifted with it, and a blank cell became a confident wrong
    answer no prompt could prevent."""
    result = parse_document(make_sparse_table_pdf(), "application/pdf")
    text = " ".join(b.text for b in result.blocks)

    row = next(line for line in text.splitlines() if "Two-wheeler" in line)
    cells = [c.strip() for c in row.strip("|").split("|")]

    assert cells[1] == "", f"June must stay empty, got {cells[1]!r}"
    assert cells[2] == "14.9", "14.9 belongs to May, the second column"


def test_the_recovered_table_is_an_atomic_table_block() -> None:
    """A recovered table has to reach the chunker as a TABLE, or the row-atomic
    splitting and header repetition never apply to it."""
    result = parse_document(make_sparse_table_pdf(), "application/pdf")

    tables = [b for b in result.blocks if b.block_type is BlockType.TABLE]
    assert tables, "the aligned rows should be recovered as a table block"
    assert "Manufacturing PMI" in " ".join(b.text for b in tables)
