"""SQLAlchemy tables — contract §2 (entities) and §7 (persistence).

Two things here are load-bearing beyond ordinary schema design.

**``user_id`` on every table, indexed, and required by every repository signature.**
Invariant I3 says there is no unscoped read path. A schema where tenant scoping is
optional makes I3 a matter of remembering; a schema where it is on every row and in
every function signature makes forgetting it a type error at the call site.

**``message_citations`` is not a UI join.** It is one row per citation carrying
rank, fused score, rerank score and verification verdict — which makes it the
evaluation dataset and the retrieval trace at the same time. Storing citations as
a JSON blob on the message would have been less code and would have cost the
ablation table, the debugging surface, and any query of the form "which chunks do
we cite most and are they the ones we rank highest?".
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Workspace(Base):
    """A named group of documents that many conversations share.

    The point is upload-once: a workspace's documents stay uploaded across
    every chat opened inside it, rather than a user re-attaching files per
    conversation. Modelled as its own table rather than a tag on ``Document``
    because a conversation also belongs to one — both need the grouping, and a
    workspace can be renamed or deleted independently of either.
    """

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    documents: Mapped[list[Document]] = relationship(back_populates="workspace")
    conversations: Mapped[list[Conversation]] = relationship(back_populates="workspace")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Nullable, not required: a document uploaded before workspaces existed, or
    # through a path that never sets one, is still a valid document — just not
    # grouped under anything. I3 is still enforced by user_id regardless.
    workspace_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # Contract §2 names this blob_ref. The resolution was Postgres bytea rather
    # than a path, because Render's filesystem is ephemeral and a stored path
    # would resolve to a missing file within the hour — so the "reference" is the
    # bytes. Download-only: no citation, highlight or verification path reads it.
    blob_ref: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    # THE offset referent. Every char_start/char_end in the system indexes into
    # this string, and the source pane renders it verbatim.
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False, default="")

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # What G3 removed, and what the parser recovered. Both are surfaced in the
    # document manager — a parser that fails loudly is more useful than one that
    # fails convincingly.
    sanitization_report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    extraction: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    workspace: Mapped[Workspace | None] = relationship(back_populates="documents")

    __table_args__ = (
        # Idempotency, scoped per user *and* workspace: re-uploading a file
        # returns the existing document rather than duplicating it. Scoped to the
        # workspace, not just the user, because the same PDF can legitimately
        # belong to two unrelated workspaces — a style guide referenced from two
        # different projects should be two rows, not one document silently
        # reparented. Postgres treats each NULL workspace_id as distinct, so
        # documents predating workspaces (workspace_id IS NULL) never collide
        # with each other here — acceptable, since every upload through the
        # current API always supplies one.
        UniqueConstraint(
            "user_id", "workspace_id", "content_sha256", name="uq_documents_user_ws_sha"
        ),
        Index("ix_documents_user_status", "user_id", "status"),
    )


class Chunk(Base):
    """Mirror of the Qdrant point, kept in Postgres for citation resolution.

    Qdrant is written first and Postgres second: an orphaned vector is
    recoverable by re-running ingest, whereas an orphaned citation row points at
    a vector that does not exist and breaks a user-visible link.
    """

    __tablename__ = "chunks"

    # sha256(doc_id | chunk_index | text)[:24] — deterministic, so re-ingest is an
    # idempotent upsert rather than a duplicate-vector generator.
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    doc_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # CHILD — the retrieval unit. Embedded, indexed, BM25'd.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)

    # PARENT — the generation unit. What the LLM actually receives.
    parent_text: Mapped[str] = mapped_column(Text, nullable=False)
    parent_char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_char_end: Mapped[int] = mapped_column(Integer, nullable=False)

    section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    chunk_type: Mapped[str] = mapped_column(String(16), nullable=False, default="prose")
    is_derived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # [[start, end], ...] — where the document itself discusses this object.
    related_spans: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_chunks_user_doc", "user_id", "doc_id"),
        UniqueConstraint("doc_id", "chunk_index", name="uq_chunks_doc_index"),
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workspace_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    workspace: Mapped[Workspace | None] = relationship(back_populates="conversations")


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # Every fallback that engaged on this turn (I1). Persisted rather than only
    # streamed, so a reloaded conversation still shows that an answer was degraded.
    degradations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Per-node timings — the latency budget, measured rather than assumed.
    latency_ms: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Which formulation was used, whether rerank ran, what the grade decided.
    # Kept alongside the turn so the retrieval trace survives the request.
    pipeline: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
    citations: Mapped[list[MessageCitation]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_messages_conv_created", "conversation_id", "created_at"),)


class MessageCitation(Base):
    """One row per citation. The evaluation dataset and the debugging surface."""

    __tablename__ = "message_citations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    chunk_id: Mapped[str] = mapped_column(String(24), nullable=False)
    doc_id: Mapped[str] = mapped_column(String(36), nullable=False)

    marker: Mapped[int] = mapped_column(Integer, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    fused_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rerank_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Nullable on purpose and forever: NULL means "not yet checked, or the judge
    # failed". A dead verifier must never read as "citation unsupported" (I2).
    # A NOT NULL DEFAULT false here would encode the exact bug the invariant bans.
    verified: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)

    message: Mapped[Message] = relationship(back_populates="citations")

    __table_args__ = (Index("ix_citations_message", "message_id", "marker"),)


class ConversationState(Base):
    """Rolling summary and entity ledger — updated after each turn, never during.

    Deliberately separate from both the document corpus and user preferences.
    Merging any two of the three is how preferences leak into retrieval queries.
    """

    __tablename__ = "conversation_state"

    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    rolling_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Doubles as the substitution source for coreference resolution in `rewrite`.
    entity_ledger: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class UserPreference(Base):
    """Per-user settings that survive sessions.

    Never read on a retrieval path. Tone and default scoping are generation-time
    concerns; letting them reach the query embedding would silently distort every
    search a user makes.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )
