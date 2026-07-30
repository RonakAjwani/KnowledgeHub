"""Tier 1 parsing - local, always runs, no model involved.

Produces the ordered ``Block[]`` that ``build_normalized_text`` consumes. Three
input types reach the same shape: PDF via ``pdfplumber``, and markdown and plain
text via trivial splitters.

The PDF path is the one with judgment in it. Naive extraction flattens a table
into a stream of numbers - and the failure is worse than useless because it *looks*
like successful extraction: a column of revenue figures becomes a sentence, gets
embedded as prose, and retrieves for nothing. So tables are located first, carved
out of the page by bounding box, and emitted as atomic ``TABLE`` blocks in reading
order alongside the prose around them.

This module also produces the **complexity signal** that decides which pages get
escalated to a VLM in Tier 2. That decision is a cheap local heuristic - table
geometry, image coverage, text density - never a model. Spending an LLM call to
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
# Many PDFs - LaTeX output especially - encode no space glyphs at all: the space
# between two words is a horizontal jump, not a character. pdfplumber recovers
# word boundaries from those gaps, and its default absolute tolerance of 3pt is
# wider than the inter-word gap in a 9-10pt body font, so whole sentences come
# back as ``Regulatorycomplianceinindustrialmaintenance``. That is invisible
# downstream and catastrophic: BM25 tokenises the sentence as one term, so the
# sparse branch cannot match anything in the document, and the dense branch is
# left recovering meaning from wordpiece debris.
#
# Measured over this corpus, the ratio form (which scales with font size, unlike
# a fixed tolerance) eliminated every over-long token - 199, 98 and 181 merged
# runs in the three papers went to zero, recovering 1121 -> 2633 words in the
# worst case - while leaving the two documents that already extracted cleanly
# byte-for-byte identical. Short-word share moved under a percentage point, so
# nothing is being split that should not be.
_X_TOLERANCE_RATIO = 0.15

# Borderless-table recovery by column alignment. Right edges within this many
# points are the same column: measured, two rows of the 360 ONE macro table
# agree to within 1pt, so 3 is loose enough for rounding and far tighter than
# the ~50pt spacing between real columns.
_COLUMN_TOLERANCE = 3.0
# How near a word's right edge must be to an anchor to belong to that column.
# Wider than the clustering tolerance because a *header* is left-aligned over a
# right-aligned numeric column and sits ~10pt off.
_COLUMN_BIND_TOLERANCE = 16.0
# A column needs only two members. The sparse column is the whole point: June
# holds a value on just two rows of the macro table, and requiring three would
# discard the exact column whose emptiness is being recovered.
_MIN_COLUMN_MEMBERS = 2
# The table itself still needs three aligned rows and three columns, so a
# passing sentence with two figures in it cannot become a table.
_MIN_TABLE_ROWS = 3
_MIN_TABLE_COLUMNS = 3
# A line inside a table that binds no column is a section label ("Consumption",
# "Industrial Sector"). Allowed, but only when short - otherwise a paragraph
# sitting under a table would be swallowed into it.
_MAX_LABEL_WORDS = 4

# "Table 2", "TABLE II", "Tab. 3" at the start of a line - the author's own
# statement that a table is present. Anchored to line start so a passing mention
# mid-sentence ("as Table 2 shows") does not trigger the fallback detector.
_TABLE_CAPTION_RE = re.compile(r"^[ \t]*(?:Table|TABLE|Tab\.)\s*(?:[IVXLC]+|\d+)\b", re.M)

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
    """Raised when the bytes cannot be parsed at all - never a partial index."""


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


def _rescue_borderless_tables(page: pdfplumber.page.Page, text: str) -> list:
    """Find tables on a page that draws no ruling lines around them.

    pdfplumber's default detector keys on ruled lines, which LaTeX's `booktabs`
    style deliberately omits - measured across the corpus, one 5-page paper
    captions three tables and the default strategy finds **zero** of them. The
    text is still extracted (it lands in the prose stream), but it is never
    marked as a table, so it can be split mid-row by the chunker and never picks
    up the caption/lead-line ladder that makes a table's numbers attributable.

    Deliberately narrow, because the obvious fix is a regression. Measured over
    the same corpus, switching to the text strategy everywhere finds **16**
    tables in a paper containing one, and drops another page's 50 detections to
    9. So this runs only when two conditions hold at once:

    * the default detector found nothing on this page, and
    * the page's own text carries a table caption, i.e. the author says there is
      a table here.

    That second condition is what keeps the noisy strategy off pages that simply
    have none, and it is why this is scoped to captioned tables - an unlabelled
    borderless table stays missed, which is the honest trade for not
    hallucinating tables out of ordinary prose columns.
    """
    if not _TABLE_CAPTION_RE.search(text):
        return []
    try:
        return (
            page.find_tables(
                {"vertical_strategy": "text", "horizontal_strategy": "text"}
            )
            or []
        )
    except Exception:  # noqa: BLE001 - same contract as the default detector:
        return []  # a page that defeats detection still has readable prose


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
            # Ruling lines were found but most cells came back empty - the
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


def _blocks_from_words(
    words: list[dict],
    page_number: int,
    stack: list[tuple[int, str]],
    body_size: float,
) -> list[Block]:
    """Prose blocks from a set of words already assigned to one column.

    Takes words rather than a rectangle on purpose. Cropping a rectangle drops
    any character straddling the boundary, so a column edge cutting through
    "Macro-Economic" yields "mic" - and it needs a minimum-width constant to
    decide which strips are worth cropping at all, which is a number read off
    one document. Assigning each *whole* word to the column its centre falls in
    needs neither: an empty gutter simply gets no words.
    """
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
                    page=page_number,
                )
            )
        buffer = []

    # Find maximal runs of column-aligned lines before emitting anything. Longest
    # run first, so a table is not cut short by a shorter alignment inside it.
    # Anchors come from every numeric row in the region, computed once. Deriving
    # them per candidate run made a run that happened to exclude the two rows
    # carrying a June figure produce five columns instead of six - and the
    # missing column is exactly the sparse one this recovery exists for.
    anchors = _column_anchors(lines)
    table_at: dict[int, str] = {}
    consumed: set[int] = set()
    start = 0 if len(anchors) >= _MIN_TABLE_COLUMNS else len(lines)
    while start <= len(lines) - _MIN_TABLE_ROWS:
        for end in range(len(lines), start + _MIN_TABLE_ROWS - 1, -1):
            markdown = _aligned_table(lines[start:end], anchors)
            if markdown is not None:
                table_at[start] = markdown
                consumed.update(range(start, end))
                start = end
                break
        else:
            start += 1

    for index, (_, line_words) in enumerate(lines):
        if index in table_at:
            flush()
            blocks.append(
                Block(
                    text=table_at[index],
                    block_type=BlockType.TABLE,
                    section=_heading_path(stack),
                    page=page_number,
                )
            )
        if index in consumed:
            continue

        # Left to right, always. Grouping tolerates a 2.5pt baseline difference
        # so a row's label and its figures land on one line, but the enclosing
        # sort is by (top, x0) - so figures set a fraction higher than their own
        # label sort *ahead* of it. Measured on the 360 ONE macro table: label
        # top=145.264, figures top=143.878, and every row came out as
        # "14.9 28.4 19.3 35.2 26.2 Two-wheeler sales (%YoY)" - each row's
        # figures attached to the row above. Reading order within a line is a
        # property of x, not of a baseline that happens to jitter.
        line_words.sort(key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in line_words)
        size = max((w.get("size") or body_size) for w in line_words)

        # A caption delimits a block, the same way a heading does. PDF text
        # arrives line by line with no paragraph markers, so without this the
        # caption gets absorbed into the surrounding paragraph and stops being
        # recognisable as a caption at all - which silently costs every table its
        # lead line, the one thing that makes it findable.
        if extract_label(text) is not None:
            flush()
            blocks.append(
                Block(
                    text=text.strip(),
                    block_type=BlockType.PROSE,
                    section=_heading_path(stack),
                    page=page_number,
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
                    page=page_number,
                )
            )
            continue

        buffer.append(text)

    flush()
    return blocks


_NUMERIC_CELL_RE = re.compile(r"^[(\[]?-?[\d][\d,.]*%?[)\]]?\*?$")


def _numeric_count(words: list[dict]) -> int:
    return sum(1 for w in words if _NUMERIC_CELL_RE.match(w["text"]))


def _column_anchors(lines: list[tuple[float, list[dict]]]) -> list[float]:
    """Right-edge positions shared by enough lines to be table columns.

    Numeric tables are right-aligned, so a column's members agree on ``x1`` far
    more tightly than on ``x0``. MEASURED on the 360 ONE macro table: the
    Manufacturing PMI row ends its cells at 346/399/453/506/560 and the
    two-wheeler row at 346/400/453/507/560 - within a point of each other -
    while their left edges differ by up to 24pt because the numbers have
    different widths.

    A cluster has to appear on at least ``_MIN_COLUMN_ROWS`` lines. One line
    with several numbers is a sentence containing figures; three lines whose
    figures stop at the same x are a table.
    """
    edges = sorted(
        w["x1"]
        for _, ws in lines
        if _numeric_count(ws) >= _MIN_TABLE_COLUMNS
        for w in ws
        if _NUMERIC_CELL_RE.match(w["text"])
    )
    clusters: list[list[float]] = []
    for edge in edges:
        if clusters and edge - clusters[-1][-1] <= _COLUMN_TOLERANCE:
            clusters[-1].append(edge)
        else:
            clusters.append([edge])
    return [
        sum(c) / len(c) for c in clusters if len(c) >= _MIN_COLUMN_MEMBERS
    ]


def _as_table_row(words: list[dict], anchors: list[float]) -> tuple[list[str], int]:
    """Place each word in the column its right edge lands in.

    Returns the cells and how many words bound to a column. The first cell is
    everything that bound to none - the row label. Unbound words are put there
    rather than dropped, because losing a label is worse than an untidy one.
    """
    cells = [""] * (len(anchors) + 1)
    bound = 0
    for word in sorted(words, key=lambda w: w["x0"]):
        distances = [abs(word["x1"] - a) for a in anchors]
        best = min(range(len(anchors)), key=distances.__getitem__)
        if distances[best] <= _COLUMN_BIND_TOLERANCE:
            cells[best + 1] = (cells[best + 1] + " " + word["text"]).strip()
            bound += 1
        else:
            cells[0] = (cells[0] + " " + word["text"]).strip()
    return cells, bound


def _aligned_table(
    lines: list[tuple[float, list[dict]]], anchors: list[float]
) -> str | None:
    """Recover a borderless table from column alignment, or return None.

    This exists because a blank cell is invisible once a row is flattened to
    prose. MEASURED: the model answered 55.0 for February's PMI (that is May's
    figure) and invented a two-wheeler number for a June cell that is empty. The
    row reads "Two-wheeler sales (%YoY) 14.9 28.4 19.3 35.2 26.2" - five figures
    under six month headers, with nothing saying which month is missing. No
    prompt can recover that; the information is gone before the model sees it.

    Emitting the columns keeps the gap visible, so an absent value stays absent
    instead of shifting every later figure one month earlier.
    """
    if sum(1 for _, ws in lines if _numeric_count(ws) >= _MIN_TABLE_COLUMNS) < (
        _MIN_TABLE_ROWS - 1
    ):
        return None

    built = [(_as_table_row(ws, anchors), len(ws)) for _, ws in lines]
    aligned = sum(1 for (_, bound), _ in built if bound >= 2)
    if aligned < _MIN_TABLE_ROWS:
        return None
    # A line binding nothing is allowed only if it is short enough to be a
    # section label. A full sentence under a table is prose, not a row.
    if any(
        bound < 2 and count > _MAX_LABEL_WORDS for (_, bound), count in built
    ):
        return None

    rows = [cells for (cells, _), _ in built]
    width = len(anchors) + 1
    header, *body = rows
    out = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * width) + "|",
    ]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def _table_bands(tables: list) -> list[tuple[list, float, float]]:
    """Group tables that share a vertical range into one band.

    Two tables printed side by side are one horizontal stripe of the page, not
    two stacked ones. Walking them as if they were stacked makes the second
    table's band start below the first, so everything level with them is read in
    the wrong order or not at all.

    Returns ``(tables, top, bottom)`` per band, in top-to-bottom order.
    """
    bands: list[tuple[list, float, float]] = []
    for table in sorted(tables, key=lambda t: t.bbox[1]):
        _, top, _, bottom = table.bbox
        if bands and top < bands[-1][2]:
            members, band_top, band_bottom = bands[-1]
            members.append(table)
            bands[-1] = (members, min(band_top, top), max(band_bottom, bottom))
        else:
            bands.append(([table], top, bottom))
    return bands


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
            except Exception:  # noqa: BLE001 - a page whose ruling lines confuse
                tables = []  # the detector still has readable prose

            page_text = page.extract_text(x_tolerance_ratio=_X_TOLERANCE_RATIO) or ""
            tables = tables or _rescue_borderless_tables(page, page_text)
            assessments.append(_assess_page(page, tables, page_text))

            # Walk the page top to bottom, carving prose out of the gaps between
            # tables. This is what preserves reading order: a table's explanation
            # usually sits immediately above or below it, and emitting all prose
            # then all tables would separate them.
            #
            # Within a band, walk left to right as well. Skipping straight from a
            # table's top to its bottom discards whatever is printed *beside* it,
            # and on a multi-column page that is a whole column: measured on the
            # 360 ONE factsheet it deleted the entire "Fund Details" box -
            # manager, AUM, expense ratio, benchmark - from all 20 fund pages.
            page_words = (
                page.extract_words(
                    extra_attrs=["size"], x_tolerance_ratio=_X_TOLERANCE_RATIO
                )
                or []
            )
            n = page.page_number

            def band(lo: float, hi: float, words: list[dict] = page_words) -> list[dict]:
                return [w for w in words if lo <= (w["top"] + w["bottom"]) / 2 < hi]

            def column(words: list[dict], lo: float, hi: float) -> list[dict]:
                return [w for w in words if lo <= (w["x0"] + w["x1"]) / 2 < hi]

            cursor = 0.0
            for band_tables, band_top, band_bottom in _table_bands(tables):
                blocks.extend(
                    _blocks_from_words(band(cursor, band_top), n, stack, body_size)
                )

                beside = band(band_top, band_bottom)
                x_cursor = 0.0
                for table in sorted(band_tables, key=lambda t: t.bbox[0]):
                    x0, _, x1, _ = table.bbox
                    blocks.extend(
                        _blocks_from_words(
                            column(beside, x_cursor, x0), n, stack, body_size
                        )
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
                                page=n,
                            )
                        )
                    x_cursor = max(x_cursor, x1)

                blocks.extend(
                    _blocks_from_words(
                        column(beside, x_cursor, page.width + 1), n, stack, body_size
                    )
                )
                cursor = band_bottom

            blocks.extend(
                _blocks_from_words(
                    band(cursor, page.height + 1), n, stack, body_size
                )
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
