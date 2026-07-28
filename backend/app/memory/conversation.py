"""Conversation memory: recent turns verbatim, a rolling summary, an entity ledger.

Three stores exist in this system and they are kept apart deliberately:

* the **document corpus** (Qdrant, per user) — retrieval;
* **conversation state** (this module, Postgres, per conversation) — history,
  rewriting, summaries;
* **user preferences** (Postgres, per user) — tone, defaults, pinned sources.

Merging any two is how a stored preference ends up in a retrieval query and
silently distorts every search a user makes. Preferences are a generation-time
concern and never appear here.

**Updated after each turn, not during.** Summarising mid-turn would put an LLM
call on the critical path of an answer that is already streaming, and would fold
the current turn into the summary that the current turn is being answered from.

The entity ledger does double duty: it is conversational memory *and* the
substitution source for coreference resolution in ``rewrite``. That is why it
stores the literal earlier wording — resolving "the valve" to a paraphrase would
resolve the reference and destroy the exact-match retrieval that finds it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import models as db
from app.graph.state import Turn
from app.llm.client import LLMClient, LLMError, Message

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM = """\
You maintain a running summary of a conversation between a user and a
document-question-answering assistant.

Return JSON only: {"summary": "<text>"}

Fold the new turns into the existing summary. Keep it under 200 words. Preserve
specific identifiers, file names, error codes and technical terms exactly as they
appeared — they are how later questions get resolved. Drop pleasantries and
anything superseded by a later turn.
"""

_ENTITY_SYSTEM = """\
You extract entities from conversation turns so later references can be resolved.

Return JSON only: {"entities": {"<short label>": "<exact wording as it appeared>"}}

Include named things a follow-up might refer to indirectly: documents, systems,
identifiers, error codes, people, metrics, tables and figures.

Record the wording EXACTLY as it appeared. Do not normalise, expand, correct or
paraphrase — the value is used for literal substitution, and a tidied-up version
would break the keyword search that finds it.

Return at most 12 entities. Omit anything generic.
"""

# Identifier-shaped tokens: a cheap local pass so the ledger is never empty even
# when the extraction call fails, and so exact strings survive regardless.
_IDENTIFIER_RE = re.compile(
    r"\b(?:[A-Z]{2,}[-_]?\d+[A-Za-z0-9-]*|[A-Za-z]+\d{3,}|\d+\.\d+\.\d+)\b"
)


@dataclass
class ConversationMemory:
    recent_turns: list[Turn]
    rolling_summary: str | None
    entity_ledger: dict[str, str]


async def load_memory(
    session: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    settings: Settings | None = None,
) -> ConversationMemory:
    """Load the last N turns verbatim plus the summary of everything older."""
    cfg = settings or get_settings()

    result = await session.execute(
        select(db.Message)
        .where(
            db.Message.conversation_id == conversation_id,
            db.Message.user_id == user_id,
        )
        .order_by(db.Message.created_at.desc())
        .limit(cfg.recent_turns_n * 2)  # a "turn" is a user/assistant pair
    )
    messages = list(reversed(result.scalars().all()))
    recent: list[Turn] = [
        Turn(role=m.role, content=m.content) for m in messages if m.content
    ]

    state = await session.get(db.ConversationState, conversation_id)
    return ConversationMemory(
        recent_turns=recent,
        rolling_summary=state.rolling_summary if state else None,
        entity_ledger=dict(state.entity_ledger) if state else {},
    )


def extract_identifiers(text: str) -> dict[str, str]:
    """Local, free, and exact — no model involved.

    Identifiers are precisely what must survive a rewrite verbatim, and they are
    also the easiest thing to recognise without one. Running this unconditionally
    means the ledger degrades to "identifiers only" rather than to empty when the
    extraction call fails.
    """
    return {token.lower(): token for token in _IDENTIFIER_RE.findall(text)}


async def update_memory(
    session: AsyncSession,
    *,
    conversation_id: str,
    user_id: str,
    turns: list[Turn],
    existing_summary: str | None,
    existing_ledger: dict[str, str],
    llm: LLMClient,
    settings: Settings | None = None,
) -> ConversationMemory:
    """Fold the completed turn into the summary and ledger.

    Called **after** the answer has been persisted. Both LLM calls fail soft: a
    conversation whose summary did not update is mildly worse at follow-ups, and
    a conversation that lost its turn because a summariser timed out is broken.
    """
    cfg = settings or get_settings()
    transcript = "\n".join(f"{t['role']}: {t['content']}" for t in turns)

    ledger = dict(existing_ledger)
    for turn in turns:
        ledger.update(extract_identifiers(turn["content"]))

    try:
        result = await llm.complete_json(
            [
                Message(role="system", content=_ENTITY_SYSTEM),
                Message(role="user", content=transcript),
            ],
            model=cfg.llm_model_rewrite,
            max_tokens=512,
            timeout=cfg.timeout_llm_rewrite_s,
        )
        extracted = result.get("entities")
        if isinstance(extracted, dict):
            ledger.update(
                {str(k): str(v) for k, v in extracted.items() if k and v}
            )
    except LLMError as exc:
        logger.warning("entity extraction failed, keeping local identifiers: %s", exc)

    # Bound the ledger: it is injected into every rewrite prompt, so unbounded
    # growth is unbounded prompt cost on every subsequent turn.
    if len(ledger) > 40:
        ledger = dict(list(ledger.items())[-40:])

    summary = existing_summary
    try:
        result = await llm.complete_json(
            [
                Message(role="system", content=_SUMMARY_SYSTEM),
                Message(
                    role="user",
                    content=(
                        f"Existing summary:\n{existing_summary or '(none)'}\n\n"
                        f"New turns:\n{transcript}"
                    ),
                ),
            ],
            model=cfg.llm_model_rewrite,
            max_tokens=512,
            timeout=cfg.timeout_llm_rewrite_s,
        )
        candidate = result.get("summary")
        if isinstance(candidate, str) and candidate.strip():
            summary = candidate.strip()
    except LLMError as exc:
        logger.warning("summary update failed, keeping previous: %s", exc)

    state = await session.get(db.ConversationState, conversation_id)
    if state is None:
        state = db.ConversationState(
            conversation_id=conversation_id, user_id=user_id
        )
        session.add(state)
    state.rolling_summary = summary
    state.entity_ledger = ledger

    return ConversationMemory(
        recent_turns=turns, rolling_summary=summary, entity_ledger=ledger
    )
