"""Tier 1 parsing — local, always runs, no model involved.

Produces the ordered ``Block[]`` that ``build_normalized_text`` consumes. Three
input types reach the same shape: PDF via ``pdfplumber``, and markdown and plain
text via trivial splitters.

The PDF path is the one with judgment in it. Naive extraction flattens a table
into a stream of numbers — and the failure is worse than useless because it *looks*
like successful extraction: a column of revenue figures becomes a sentence, gets
embedded as prose, and retrieves for nothing. So tables are located first, carved
out of the page by bounding box, and emitted as atomic ``TABLE`` blocks in reading
order alongside the prose around them.

This module also produces the **complexity signal** that decides which pages get
escalated to a VLM in Tier 2. That decision is a cheap local heuristic — table
geometry, image coverage, text density — never a model. Spending an LLM call to
decide whether to spend an LLM call is not a saving.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field

import pdfplumber

from app.ingest.crossref import extract_label
from app.models.schemas import Block, BlockType

logger = logging.getLogger(__name__)

SUPPORTED_MIMES = {
    "application/pdf": "pdf",
    "text/plain": "text",
    "text/markdown": "markdown",
    "text/x-markdown": "markdown",
}

# A page yielding less text than this per unit area is probably scanned rather
# than born-digital, and Tier 1 has nothing useful to say about it.
_SPARSE_TEXT_CHARS_PER_KILOPIXEL = 0.02

# Word-boundary tolerance as a fraction of font size, passed to every pdfplumber
# text call.
#
# Many PDFs — LaTeX output especially — encode no space glyphs at all: the space
# between two words is a horizontal jump, not a character. pdfplumber recovers
# word boundaries from those gaps, and its default absolute tolerance of 3pt is
# wider than the inter-word gap in a 9–10pt body font, so whole sentences come
# back as ``Regulatorycomplianceinindustrialmaintenance``. That is invisible
# downstream and catastrophic: BM25 tokenises the sentence as one term, so the
# sparse branch cannot match anything in the document, and the dense branch is
# left recovering meaning from wordpiece debris.
#
# Measured over this corpus, the ratio form (which scales with font size, unlike
# a fixed tolerance) eliminated every over-long token — 199, 98 and 181 merged
# runs in the three papers went to zero, recovering 1121 → 2633 words in the
# worst case — while leaving the two documents that already extracted cleanly
# byte-for-byte identical. Short-word share moved under a percentage point, so
# nothing is being split that should not be.
_X_TOLERANCE_RATIO = 0.15

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:-]*-{3,}[\s:|-]*\|?\s*$")


@dataclass
class PageAssessment:
    """Why a page might need Tier-2 escalation. Reasons are surfaced, not just counted."""

    page: int
    reasons: list[str] = field(default_factory=list)

    @property
    def is_complex(self) -> bool:
        return bool(self.reasons)


@dataclass
class ParseResult:
    blocks: list[Block]
    page_count: int
    assessments: list[PageAssessment] = field(default_factory=list)
    tables_found: int = 0

    @property
    def complex_pages(self) -> list[int]:
        return [a.page for a in self.assessments if a.is_complex]


class UnsupportedDocument(Exception):
    """Raised when the bytes cannot be parsed at all — never a partial index."""


# ------------------------------------------------------------------- markdown


def _heading_path(stack: list[tuple[int, str]]) -> str | None:
    return " > ".join(title for _, title in stack) if stack else None


def parse_markdown(text: str) -> ParseResult:
    """Split on headings and blank lines, tracking the heading path.

    Markdown tables are kept whole rather than split into paragraphs, so the
    "tables are atomic" rule holds for markdown input too and a pipe table is not
    shredded into meaningless prose lines.
    """
    blocks: list[Block] = []
    stack: list[tuple[int, str]] = []
    buffer: list[str] = []
    in_table = False
    tables = 0

    def flush(block_type: BlockType = BlockType.PROSE) -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if body:
            blocks.append(
                Block(text=body, block_type=block_type, section=_heading_path(stack))
            )
        buffer = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        heading = _MD_HEADING_RE.match(line)

        if heading:
            flush(BlockType.TABLE if in_table else BlockType.PROSE)
            if in_table:
                tables += 1
            in_table = False
            level, title = len(heading.group(1)), heading.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            # The heading is itself document text and must be citable, so it is a
            # block rather than metadata only.
            blocks.append(
                Block(text=title, block_type=BlockType.PROSE, section=_heading_path(stack))
            )
            continue

        looks_like_table_row = line.strip().startswith("|") and line.strip().endswith("|")
        if looks_like_table_row or (in_table and _MD_TABLE_SEP_RE.match(line)):
            if not in_table:
                flush()
                in_table = True
            buffer.append(line)
            continue

        if in_table and not line.strip():
            flush(BlockType.TABLE)
            tables += 1
            in_table = False
            continue

        if not line.strip():
            flush()
            continue

        if in_table:
            flush(BlockType.TABLE)
            tables += 1
            in_table = False
        buffer.append(line)

    flush(BlockType.TABLE if in_table else BlockType.PROSE)
    if in_table:
        tables += 1

    return ParseResult(blocks=blocks, page_count=1, tables_found=tables)


def parse_plain_text(text: str) -> ParseResult:
    blocks = [
        Block(text=para.strip(), block_type=BlockType.PROSE)
        for para in re.split(r"\n\s*\n", text)
        if para.strip()
    ]
    return ParseResult(blocks=blocks, page_count=1)


# ------------------------------------------------------------------------ PDF


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    """Render an extracted table as markdown.

    Markdown rather than the raw cell grid because the header row survives it,
    and the header row is what makes a split table's fragments interpretable.
    """
    cleaned = [
        [(cell or "").replace("\n", " ").strip() for cell in row]
        for row in rows
        if any((cell or "").strip() for cell in row)
    ]
    if not cleaned:
        return ""

    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]

    header, *body = cleaned
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _looks_like_heading(line: str, size: float, body_size: float) -> bool:
    if not line.strip() or len(line) > 120:
        return False
    return size >= body_size * 1.15


def _assess_page(page: pdfplumber.page.Page, tables: list, text: str) -> PageAssessment:
    """The Tier-2 escalation heuristic. Cheap, local, explainable.

    Deliberately not a model. The whole justification for Tier 2 costing nothing
    extra is that it runs only on pages a free local check has already flagged.
    """
    assessment = PageAssessment(page=page.page_number)

    area_kpx = max((page.width * page.height) / 1000.0, 1.0)
    if len(text.strip()) / area_kpx < _SPARSE_TEXT_CHARS_PER_KILOPIXEL:
        # Almost no extractable text: a scan, or a page that is one large image.
        assessment.reasons.append("sparse_text")

    for table in tables:
        rows = table.extract(x_tolerance_ratio=_X_TOLERANCE_RATIO)
        if not rows:
            assessment.reasons.append("unreadable_table")
            continue
        cells = [c for row in rows for c in row]
        if cells and sum(1 for c in cells if not (c or "").strip()) / len(cells) > 0.5:
            # Ruling lines were found but most cells came back empty — the
            # classic signature of a table Tier 1 can see but cannot read.
            assessment.reasons.append("degenerate_table")

    images = getattr(page, "images", []) or []
    image_area = sum(
        max(0.0, (im.get("x1", 0) - im.get("x0", 0)))
        * max(0.0, (im.get("bottom", 0) - im.get("top", 0)))
        for im in images
    )
    if image_area > 0.25 * page.width * page.height:
        assessment.reasons.append("large_figure")

    return assessment


def _blocks_from_region(
    page: pdfplumber.page.Page,
    top: float,
    bottom: float,
    stack: list[tuple[int, str]],
    body_size: float,
) -> list[Block]:
    """Prose blocks from a vertical slice of a page, tracking headings."""
    if bottom - top < 2:
        return []

    region = page.crop((0, max(top, 0), page.width, min(bottom, page.height)))
    words = (
        region.extract_words(
            extra_attrs=["size"], x_tolerance_ratio=_X_TOLERANCE_RATIO
        )
        or []
    )
    if not words:
        return []

    # Group words into lines by their vertical position.
    lines: list[tuple[float, list[dict]]] = []
    for word in sorted(words, key=lambda w: (round(w["top"], 1), w["x0"])):
        if lines and abs(lines[-1][0] - word["top"]) <= 2.5:
            lines[-1][1].append(word)
        else:
            lines.append((word["top"], [word]))

    blocks: list[Block] = []
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer
        body = " ".join(buffer).strip()
        if body:
            blocks.append(
                Block(
                    text=body,
                    block_type=BlockType.PROSE,
                    section=_heading_path(stack),
                    page=page.page_number,
                )
            )
        buffer = []

    for _, line_words in lines:
        text = " ".join(w["text"] for w in line_words)
        size = max((w.get("size") or body_size) for w in line_words)

        # A caption delimits a block, the same way a heading does. PDF text
        # arrives line by line with no paragraph markers, so without this the
        # caption gets absorbed into the surrounding paragraph and stops being
        # recognisable as a caption at all — which silently costs every table its
        # lead line, the one thing that makes it findable.
        if extract_label(text) is not None:
            flush()
            blocks.append(
                Block(
                    text=text.strip(),
                    block_type=BlockType.PROSE,
                    section=_heading_path(stack),
                    page=page.page_number,
                )
            )
            continue

        if _looks_like_heading(text, size, body_size):
            flush()
            # Font size gives no real nesting depth, so headings are treated as a
            # flat single level. A wrong guess at hierarchy is worse than none:
            # it would put "Appendix" under "Introduction".
            stack.clear()
            stack.append((1, text.strip()))
            blocks.append(
                Block(
                    text=text.strip(),
                    block_type=BlockType.PROSE,
                    section=_heading_path(stack),
                    page=page.page_number,
                )
            )
            continue

        buffer.append(text)

    flush()
    return blocks


def parse_pdf(data: bytes) -> ParseResult:
    blocks: list[Block] = []
    assessments: list[PageAssessment] = []
    stack: list[tuple[int, str]] = []
    tables_found = 0

    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001
        raise UnsupportedDocument(f"Could not open PDF: {exc}") from exc

    with pdf:
        # One pass to learn the document's body font size, so "larger than body
        # text" means something relative to this document rather than an absolute
        # threshold that breaks on any unusual template.
        sizes: list[float] = []
        for page in pdf.pages[:10]:
            sizes.extend(
                round(c["size"], 1) for c in (page.chars or []) if c.get("size")
            )
        body_size = max(set(sizes), key=sizes.count) if sizes else 10.0

        for page in pdf.pages:
            try:
                tables = page.find_tables() or []
            except Exception:  # noqa: BLE001 — a page whose ruling lines confuse
                tables = []  # the detector still has readable prose

            page_text = page.extract_text(x_tolerance_ratio=_X_TOLERANCE_RATIO) or ""
            assessments.append(_assess_page(page, tables, page_text))

            # Walk the page top to bottom, carving prose out of the gaps between
            # tables. This is what preserves reading order: a table's explanation
            # usually sits immediately above or below it, and emitting all prose
            # then all tables would separate them.
            cursor = 0.0
            for table in sorted(tables, key=lambda t: t.bbox[1]):
                x0, top, x1, bottom = table.bbox
                blocks.extend(
                    _blocks_from_region(page, cursor, top, stack, body_size)
                )

                rows = table.extract(x_tolerance_ratio=_X_TOLERANCE_RATIO) or []
                markdown = _table_to_markdown(rows)
                if markdown:
                    tables_found += 1
                    blocks.append(
                        Block(
                            text=markdown,
                            block_type=BlockType.TABLE,
                            section=_heading_path(stack),
                            page=page.page_number,
                        )
                    )
                cursor = bottom

            blocks.extend(
                _blocks_from_region(page, cursor, page.height, stack, body_size)
            )

        page_count = len(pdf.pages)

    if not blocks:
        raise UnsupportedDocument(
            "No extractable text found. The document may be a scan without an "
            "embedded text layer."
        )

    return ParseResult(
        blocks=blocks,
        page_count=page_count,
        assessments=assessments,
        tables_found=tables_found,
    )


# ------------------------------------------------------------------ entrypoint


def parse_document(data: bytes, mime: str) -> ParseResult:
    """Parse raw bytes into ordered blocks. Raises ``UnsupportedDocument`` on failure.

    A parse failure fails the whole document rather than indexing what it managed
    to read. A partial index is the worst outcome available: the user believes
    their document is searchable and it silently is not.
    """
    kind = SUPPORTED_MIMES.get(mime)
    if kind is None:
        raise UnsupportedDocument(f"Unsupported media type: {mime}")

    if kind == "pdf":
        return parse_pdf(data)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")

    return parse_markdown(text) if kind == "markdown" else parse_plain_text(text)
