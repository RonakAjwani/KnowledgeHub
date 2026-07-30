"""Domain types - the Pydantic mirror of contract §2.

These are the shapes that cross stage boundaries. The SQLAlchemy tables in
``app.db.models`` persist them; these are what the pipeline actually passes around.

The one field worth pausing on is ``Chunk.char_start`` / ``char_end``. Every offset
in this system is into ``Document.normalized_text`` - never into raw file bytes,
never into a chunk's own text (invariant I5). That single referent is what makes
the citation chain work: a click on ``[2]`` resolves to a chunk, whose offsets
resolve to a span of the exact string the source pane is rendering. Introduce a
second referent anywhere and the highlight silently points at the wrong text.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class BlockType(StrEnum):
    PROSE = "prose"
    TABLE = "table"
    FIGURE = "figure"
    FORMULA = "formula"


class DocumentStatus(StrEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


class DegradationStage(StrEnum):
    ROUTE = "route"
    REWRITE = "rewrite"
    RETRIEVE = "retrieve"
    RERANK = "rerank"
    GENERATE = "generate"
    VERIFY = "verify"
    # Not in the contract's stage list, which covers the query pipeline. Ingest
    # degrades too (the escalated-page cap is the live case), and I1 applies to
    # every fallback in the system, not only to query-time ones.
    PARSE = "parse"


class DegradationReason(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PARSE_ERROR = "parse_error"
    QUOTA_EXHAUSTED = "quota_exhausted"
    UNAVAILABLE = "unavailable"
    CAP_REACHED = "cap_reached"


# --------------------------------------------------------------------- parsing


class Block(BaseModel):
    """One parsed unit of a document, before normalisation.

    Blocks are the *only* input to ``build_normalized_text``. They carry no
    offsets: offsets do not exist until the builder assigns them, because they
    cannot be known until sanitisation has run (contract §1's ordering constraint).
    """

    model_config = ConfigDict(frozen=True)

    text: str
    block_type: BlockType = BlockType.PROSE
    section: str | None = None
    page: int | None = None
    # True for content synthesised rather than extracted - a VLM's description of
    # a chart, for instance. Rendered with a visible marker so a reader can always
    # tell "this is in the document" from "this is a model's description of a
    # picture in the document".
    is_derived: bool = False


class BlockSpan(BaseModel):
    """Where a block ended up in ``normalized_text``.

    ``text[start:end]`` is exactly the block's rendered, post-sanitisation content.
    Everything downstream - the chunker, the cross-reference scanner, the citation
    resolver - consumes these rather than re-deriving positions.
    """

    model_config = ConfigDict(frozen=True)

    block_index: int
    start: int
    end: int
    section: str | None = None
    page: int | None = None
    block_type: BlockType = BlockType.PROSE
    is_derived: bool = False

    @property
    def length(self) -> int:
        return self.end - self.start

    def slice(self, text: str) -> str:
        return text[self.start : self.end]


class SanitizationReport(BaseModel):
    """What G3 removed. Persisted on the Document and surfaced in the UI.

    Non-fatal by design: suspicious content is removed and counted, never
    rejected. A document full of zero-width characters is more likely to be a
    badly-exported PDF than an attack, and refusing it would be the wrong call
    either way - the point is that the removal is visible.
    """

    removed_spans: int = 0
    kinds: dict[str, int] = Field(default_factory=dict)

    @property
    def total_removed_chars(self) -> int:
        return sum(self.kinds.values())


class NormalizedDocument(BaseModel):
    """The output of the one builder. After this, ``text`` is immutable.

    Any later transformation of ``text`` - a stray ``.strip()``, a whitespace
    collapse, a re-clean - invalidates every offset in the document and every
    citation derived from them.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    spans: tuple[BlockSpan, ...]
    report: SanitizationReport


# ---------------------------------------------------------------------- chunks


def chunk_id(doc_id: str, chunk_index: int, text: str) -> str:
    """Deterministic chunk id - ``sha256(doc_id | chunk_index | text)[:24]``.

    Determinism is what makes re-ingest an idempotent upsert rather than a
    duplicate-vector generator, which is one of the assignment's unstated
    functional requirements. Identical text at a different index is deliberately a
    *distinct* chunk: position carries meaning.
    """
    digest = hashlib.sha256(f"{doc_id}|{chunk_index}|{text}".encode())
    return digest.hexdigest()[:24]


class Chunk(BaseModel):
    """Child is the retrieval unit; parent is the generation unit.

    ``char_start``/``char_end`` drive the source-pane highlight; the parent range
    drives the scroll target. Both are offsets into ``Document.normalized_text``.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    doc_id: str
    user_id: str
    chunk_index: int

    text: str  # CHILD - embedded, indexed, BM25'd
    char_start: int
    char_end: int

    parent_text: str  # PARENT - what the LLM actually receives
    parent_char_start: int
    parent_char_end: int

    section: str | None = None
    page: int | None = None
    token_count: int = 0

    chunk_type: BlockType = BlockType.PROSE
    is_derived: bool = False
    # Where the document itself discusses this object - the body-text mentions of
    # "Table 2" found by scanning the caption's label across the document. Real
    # document text, so a table citation can highlight both the table and the
    # paragraph explaining it.
    related_spans: tuple[tuple[int, int], ...] = ()

    # The source document's filename, filled in at hydration. Without it the
    # model receives a chunk with no idea which document it came from, and
    # "which of these documents covers X" is unanswerable even when the right
    # passage is sitting in the context - observed exactly that way against a
    # corpus containing langchain.md.
    source_name: str | None = None


# ------------------------------------------------------------------- retrieval


class RetrievedChunk(BaseModel):
    chunk: Chunk
    dense_rank: int | None = None
    sparse_rank: int | None = None
    fused_score: float = 0.0
    # None when rerank was skipped or failed - never 0.0, which would be
    # indistinguishable from "the reranker scored this as irrelevant" (I2).
    rerank_score: float | None = None


class Degradation(BaseModel):
    """A fallback that engaged. Invariant I1 made concrete.

    Every fallback appends one of these to the response and emits a matching SSE
    event. A degraded path must never be indistinguishable from a healthy one.
    """

    model_config = ConfigDict(frozen=True)

    stage: DegradationStage
    reason: DegradationReason
    fallback: str
    detail: str | None = None


class Citation(BaseModel):
    marker: int
    chunk_id: str
    doc_id: str
    filename: str
    section: str | None = None
    page: int | None = None
    char_start: int
    char_end: int
    # None means "not yet checked, or the judge failed" - never False. A dead
    # verifier must not read as "citations unsupported" (I2).
    verified: bool | None = None
