"""Tier 2 — escalate visually complex pages to a VLM through the existing adapter.

Tier 1 handles prose-only pages perfectly at near-zero cost. It fails on scanned
pages, on tables whose ruling lines it can see but whose cells it cannot read,
and on pages that are mostly figure. Those pages are rendered to an image and
sent to the same LLM adapter the rest of the system uses — no GPU, no new
dependency, no RAM cost, and provider-swappability inherited for free.

**What bounds this is tokens, not requests.** There is no separate image
allowance on the free tier: a page image draws on the same TPM budget as text and
drains it far faster — one tiled page can cost several hundred to a couple of
thousand tokens before the prompt is read. So the queue is paced on a token
budget, and ``VLM_RENDER_DPI`` is a real tuning constant rather than an
implementation detail: at or below 384 px on both sides an image is a flat 258
tokens; above that it tiles and the cost climbs.

**The per-document cap is not optional, and it is not silent.** A 200-page scan
where every page escalates would exhaust the minute's budget and stall ingest.
Hitting the cap emits a ``Degradation`` and shows up in the document's extraction
signal, so a user learns that pages 40+ were parsed by Tier 1 only. Truncating
quietly would produce exactly the fails-convincingly outcome this design exists to
avoid.

**This is VLM extraction, not VLM summarisation.** Turning pixels into a markdown
table is the model's job. Making that table *findable* is the author's narrative —
see :mod:`app.ingest.crossref`. A model reading values off a chart axis is a
hallucination dressed as extraction, and this module never asks it to.
"""

from __future__ import annotations

import base64
import io
import logging

import pypdfium2 as pdfium

from app.config import Settings, get_settings
from app.ingest.parse import PageAssessment
from app.llm.client import ImagePart, LLMClient, LLMError, Message, TextPart
from app.llm.limiter import TokenBudgetLimiter
from app.models.schemas import (
    Block,
    BlockType,
    Degradation,
    DegradationReason,
    DegradationStage,
)

logger = logging.getLogger(__name__)

# Gemini free-tier TPM sits somewhere in the 250K–1M range depending on model.
# Pacing against the conservative end costs a little throughput on ingest — which
# is async and off the request path — and avoids a 429 storm if the real ceiling
# is the lower one.
DEFAULT_TPM_BUDGET = 200_000

# A flat 258 tokens when both dimensions are within 384 px; above that the image
# is tiled at roughly 768 px per tile and each tile adds its own cost.
_FLAT_TOKEN_SIZE = 384
_FLAT_TOKEN_COST = 258
_TILE_PX = 768
_TILE_COST = 258

_PROMPT = """\
You are converting a single page of a document into markdown.

Rules:
- Transcribe ONLY what is visibly present on the page. Do not infer, summarise, \
or add commentary.
- Render tables as markdown tables, preserving the header row and every cell value \
exactly as printed.
- Render mathematical formulae as LaTeX between $ delimiters.
- For a chart or diagram, describe its type, axis labels and legend. Do NOT read \
data values off the plot area — state the labels and say the values are shown \
graphically.
- Output the markdown only. No preamble, no code fence, no explanation.
"""


def estimate_image_tokens(width: int, height: int) -> int:
    """Token cost of an image at the given pixel dimensions.

    The step at 384 px is why render DPI is a tuning constant: crossing it turns
    a flat cost into a tiled one that scales with area.
    """
    if width <= _FLAT_TOKEN_SIZE and height <= _FLAT_TOKEN_SIZE:
        return _FLAT_TOKEN_COST
    tiles_w = max(1, -(-width // _TILE_PX))
    tiles_h = max(1, -(-height // _TILE_PX))
    return tiles_w * tiles_h * _TILE_COST


def render_page_png(pdf_bytes: bytes, page_number: int, dpi: int) -> tuple[bytes, int, int]:
    """Rasterise a single 1-indexed page. Returns (png_bytes, width, height)."""
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        page = doc[page_number - 1]
        # pypdfium2 works in scale factors; PDF user space is 72 dpi.
        bitmap = page.render(scale=dpi / 72.0)
        image = bitmap.to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue(), image.width, image.height
    finally:
        doc.close()


class PageEscalator:
    """Escalates flagged pages, paced on tokens and capped per document."""

    def __init__(
        self,
        client: LLMClient,
        settings: Settings | None = None,
        limiter: TokenBudgetLimiter | None = None,
    ) -> None:
        self.client = client
        self.settings = settings or get_settings()
        self.limiter = limiter or TokenBudgetLimiter(DEFAULT_TPM_BUDGET, name="vlm")

    async def escalate_page(
        self, pdf_bytes: bytes, page_number: int
    ) -> str | None:
        """Markdown for one page, or None if the model could not read it."""
        try:
            png, width, height = render_page_png(
                pdf_bytes, page_number, self.settings.vlm_render_dpi
            )
        except Exception as exc:  # noqa: BLE001 — a page that will not render is
            logger.warning("could not render page %d: %s", page_number, exc)
            return None  # not fatal; Tier 1's text for that page stands

        cost = estimate_image_tokens(width, height)
        await self.limiter.acquire(cost)

        messages = [
            Message(role="system", content=_PROMPT),
            Message(
                role="user",
                content=[
                    TextPart("Convert this page to markdown."),
                    ImagePart(mime="image/png", data_b64=base64.b64encode(png).decode()),
                ],
            ),
        ]

        try:
            markdown = await self.client.complete(
                messages,
                model=self.settings.llm_model_vlm,
                temperature=0.0,
                max_tokens=4096,
                timeout=60.0,
            )
        except LLMError as exc:
            logger.warning("VLM escalation failed for page %d: %s", page_number, exc)
            return None

        return markdown.strip() or None


async def escalate_document(
    pdf_bytes: bytes,
    assessments: list[PageAssessment],
    *,
    client: LLMClient,
    settings: Settings | None = None,
    limiter: TokenBudgetLimiter | None = None,
) -> tuple[dict[int, str], list[Degradation], int]:
    """Escalate every flagged page up to the cap.

    Returns ``(markdown_by_page, degradations, attempted)``. Escalation never
    fails ingest: a page the VLM cannot read keeps whatever Tier 1 extracted,
    and every reason for not escalating is recorded rather than swallowed.
    """
    cfg = settings or get_settings()
    escalator = PageEscalator(client, cfg, limiter)

    flagged = [a for a in assessments if a.is_complex]
    degradations: list[Degradation] = []

    if len(flagged) > cfg.max_escalated_pages:
        skipped = flagged[cfg.max_escalated_pages :]
        flagged = flagged[: cfg.max_escalated_pages]
        # I1: the cap is visible. The user learns which pages got Tier-1 only.
        degradations.append(
            Degradation(
                stage=DegradationStage.PARSE,
                reason=DegradationReason.CAP_REACHED,
                fallback="local text extraction only for the remaining pages",
                detail=(
                    f"{len(skipped)} page(s) exceeded the per-document escalation cap "
                    f"of {cfg.max_escalated_pages}: "
                    f"{', '.join(str(a.page) for a in skipped[:10])}"
                    f"{' …' if len(skipped) > 10 else ''}"
                ),
            )
        )

    recovered: dict[int, str] = {}
    for assessment in flagged:
        markdown = await escalator.escalate_page(pdf_bytes, assessment.page)
        if markdown:
            recovered[assessment.page] = markdown
        else:
            degradations.append(
                Degradation(
                    stage=DegradationStage.PARSE,
                    reason=DegradationReason.UNAVAILABLE,
                    fallback="local text extraction for this page",
                    detail=f"VLM could not parse page {assessment.page}",
                )
            )

    return recovered, degradations, len(flagged)


def blocks_from_escalation(
    page_number: int, markdown: str, section: str | None = None
) -> list[Block]:
    """Turn recovered markdown into blocks, splitting tables out as atomic.

    ``is_derived`` stays **False**: this is the page's own content transcribed
    from pixels, not a model's description of it. The derived flag is reserved
    for synthesised content that does not exist in the document — conflating the
    two would put an "AI-described" badge on a faithfully transcribed table and
    teach users to distrust the marker.
    """
    blocks: list[Block] = []
    buffer: list[str] = []
    in_table = False

    def flush(block_type: BlockType) -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if body:
            blocks.append(
                Block(
                    text=body,
                    block_type=block_type,
                    section=section,
                    page=page_number,
                    is_derived=False,
                )
            )
        buffer = []

    for line in markdown.splitlines():
        is_row = line.strip().startswith("|")
        if is_row and not in_table:
            flush(BlockType.PROSE)
            in_table = True
        elif not is_row and in_table:
            flush(BlockType.TABLE)
            in_table = False
        if line.strip() or in_table:
            buffer.append(line)

    flush(BlockType.TABLE if in_table else BlockType.PROSE)
    return blocks
