"""Schema guarantees that invariants depend on.

These assert against table metadata rather than a live database, so they run in
CI without a Postgres service. What they guard is structural: a column that
becomes NOT NULL, or a table that loses ``user_id``, breaks an invariant in a way
that no functional test would necessarily catch.
"""

from __future__ import annotations

import pytest

from app.db.models import (
    Base,
    Chunk,
    Conversation,
    ConversationState,
    Document,
    Message,
    MessageCitation,
    UserPreference,
    Workspace,
)
from app.models.schemas import chunk_id

# Tables that hold user data. user_preferences and conversation_state key on the
# user and conversation respectively; every other table carries user_id outright.
USER_SCOPED_TABLES = {
    "documents",
    "chunks",
    "conversations",
    "messages",
    "message_citations",
    "conversation_state",
}


# ------------------------------------------------------------------ invariant I3


def test_every_user_facing_table_carries_user_id() -> None:
    """I3: there is no unscoped read path.

    A table without user_id is a table you can query without a tenant filter,
    which makes the invariant a matter of remembering rather than of structure.
    """
    for name in USER_SCOPED_TABLES:
        table = Base.metadata.tables[name]
        assert "user_id" in table.c, f"{name} has no user_id column"
        assert not table.c.user_id.nullable, f"{name}.user_id must be NOT NULL"


def test_user_id_is_indexed_everywhere_it_appears() -> None:
    """Scoping every query on an unindexed column would make I3 expensive enough
    to be tempting to skip."""
    for name in USER_SCOPED_TABLES:
        table = Base.metadata.tables[name]
        indexed = table.c.user_id.index or any(
            "user_id" in [c.name for c in idx.columns] for idx in table.indexes
        )
        assert indexed, f"{name}.user_id is not indexed"


def test_user_preferences_is_keyed_by_user() -> None:
    assert UserPreference.__table__.c.user_id.primary_key


# ------------------------------------------------------------------ invariant I2


def test_verified_is_nullable() -> None:
    """I2: unknown is not zero.

    NULL means "not yet checked, or the judge failed". A NOT NULL DEFAULT false
    would make a dead verifier indistinguishable from "citation unsupported",
    which is precisely the bug the invariant exists to prevent.
    """
    col = MessageCitation.__table__.c.verified
    assert col.nullable is True
    assert col.default is None or col.default.arg is None
    assert col.server_default is None


def test_scores_are_nullable() -> None:
    """rerank_score is None when rerank was skipped or failed — never 0.0, which
    would read as "the reranker judged this irrelevant"."""
    assert MessageCitation.__table__.c.rerank_score.nullable is True
    assert MessageCitation.__table__.c.fused_score.nullable is True


# --------------------------------------------------------------- offset chain


@pytest.mark.parametrize(
    "column",
    ["char_start", "char_end", "parent_char_start", "parent_char_end"],
)
def test_chunk_offsets_are_required(column: str) -> None:
    """I5: every chunk knows where it lives in normalized_text.

    A nullable offset is a chunk that cannot be highlighted, and the failure would
    surface as a silently missing citation link rather than an error.
    """
    assert not Chunk.__table__.c[column].nullable


def test_document_holds_the_offset_referent() -> None:
    assert not Document.__table__.c.normalized_text.nullable


def test_related_spans_and_derived_flag_exist() -> None:
    """Cross-reference resolution and derived-content marking both need these,
    and neither can be retrofitted once documents are ingested."""
    assert "related_spans" in Chunk.__table__.c
    assert not Chunk.__table__.c.is_derived.nullable


# ---------------------------------------------------------------- idempotency


def test_duplicate_upload_is_prevented_per_user_and_workspace_not_globally() -> None:
    """Re-uploading returns the existing document; two users uploading the same
    public PDF are still two documents — and so are the same user's two
    unrelated workspaces uploading it, since a workspace's whole point is an
    independent document set."""
    constraint = next(
        c
        for c in Document.__table__.constraints
        if c.name == "uq_documents_user_ws_sha"
    )
    assert {c.name for c in constraint.columns} == {
        "user_id",
        "workspace_id",
        "content_sha256",
    }


def test_chunk_ids_are_deterministic_and_position_sensitive() -> None:
    a = chunk_id("doc-1", 0, "identical text")
    b = chunk_id("doc-1", 0, "identical text")
    c = chunk_id("doc-1", 1, "identical text")

    assert a == b, "re-ingest must produce the same id, so upsert is idempotent"
    assert a != c, "identical text at a different index is a distinct chunk"
    assert len(a) == 24


def test_chunk_index_is_unique_within_a_document() -> None:
    constraint = next(
        c for c in Chunk.__table__.constraints if c.name == "uq_chunks_doc_index"
    )
    assert {c.name for c in constraint.columns} == {"doc_id", "chunk_index"}


# ------------------------------------------------------------------ deletion


def test_deletion_cascades_from_document_to_chunks() -> None:
    """Deletion must actually delete. The Qdrant side is handled in the ingest
    pipeline; this is the Postgres half."""
    fk = next(iter(Chunk.__table__.c.doc_id.foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_deletion_cascades_from_message_to_citations() -> None:
    fk = next(iter(MessageCitation.__table__.c.message_id.foreign_keys))
    assert fk.ondelete == "CASCADE"


# ------------------------------------------------------------- three stores


def test_conversation_state_is_separate_from_user_preferences() -> None:
    """Three stores, kept apart on purpose.

    Merging conversation state with user preferences is how a stored tone or
    default scoping ends up in a retrieval query, silently distorting every
    search the user makes.
    """
    assert ConversationState.__tablename__ != UserPreference.__tablename__
    assert "entity_ledger" in ConversationState.__table__.c
    assert "preferences" in UserPreference.__table__.c
    # Preferences carry no conversation or retrieval linkage at all.
    assert set(UserPreference.__table__.c.keys()) == {
        "user_id",
        "preferences",
        "updated_at",
    }


def test_messages_persist_degradations() -> None:
    """I1: a reloaded conversation must still show that an answer was degraded."""
    assert "degradations" in Message.__table__.c
    assert not Message.__table__.c.degradations.nullable


# --------------------------------------------------------------- workspaces


def test_workspace_id_is_nullable_on_documents_and_conversations() -> None:
    """Nullable, not required: a document or conversation from before workspaces
    existed, or from a caller that never sets one, is still valid — just
    ungrouped. I3 is still enforced by user_id regardless of workspace_id."""
    assert Document.__table__.c.workspace_id.nullable
    assert Conversation.__table__.c.workspace_id.nullable


def test_workspace_foreign_keys_cascade_on_delete() -> None:
    """Deleting a workspace must not strand its documents or conversations —
    they either go with it or the delete has to fail; leaving them behind with
    a dangling workspace_id is the one outcome nothing should produce."""
    for table, column in ((Document, "workspace_id"), (Conversation, "workspace_id")):
        fk = next(iter(table.__table__.c[column].foreign_keys))
        assert fk.ondelete == "CASCADE"


def test_workspace_scoped_dedup_lets_the_same_file_live_in_two_workspaces() -> None:
    """The same PDF can legitimately belong to two unrelated workspaces — a
    style guide referenced from two different projects should be two rows, not
    one document silently reparented between them."""
    constraint = next(
        c
        for c in Document.__table__.constraints
        if c.name == "uq_documents_user_ws_sha"
    )
    assert {c.name for c in constraint.columns} == {
        "user_id",
        "workspace_id",
        "content_sha256",
    }


def test_workspace_scoped_by_user_id() -> None:
    assert "user_id" in Workspace.__table__.c
    assert not Workspace.__table__.c.user_id.nullable
