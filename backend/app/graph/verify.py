"""G4 — claim-level citation verification, off the request path.

Three fixes carried from the reference project, each of which was a measured
failure there rather than a theoretical one:

1. **Judge against the union of a claim's cited chunks.** A sentence citing
   ``[1][2]`` draws on both. Judging each marker separately against the whole
   sentence marks *both* unsupported, because neither source alone entails it.
2. **Send the full parent text, not a 400-character prefix.** Evidence past the
   cutoff produced false "unsupported" verdicts and was a likely contributor to
   that project's 0.736 citation accuracy.
3. **Count only factual claims in the coverage denominator.** "Here's a summary:"
   is discourse, carries no citation, and dragged coverage down as if it were an
   uncited fact.

And the invariant that governs the whole module: **a failed judge yields ``None``,
never ``False`` (I2).** A dead verifier must not be indistinguishable from
"citations unsupported" — the second is a finding, the first is an absence of one.

This runs **after** the answer has streamed. Putting it on the request path was
the reference project's latency tax on every answer, and the UX bar here is
NotebookLM — verdicts patch citation chips in afterwards, or never arrive at all.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field

from app.config import Settings, get_settings
from app.graph import prompts
from app.llm.client import LLMClient, LLMError, Message

logger = logging.getLogger(__name__)

# Models emit CJK lenticular 【1】, fullwidth ［1］, and tortoise 〔1〕 brackets,
# not just ASCII. Missing this alone scored correctly-cited answers as 0.0
# citation accuracy on the reference project's first run — the answers were
# right, the extractor simply could not see the markers.
CITATION_MARKER_RE = re.compile(r"[\[［【〔❨❪⟦]\s*(\d+)\s*[\]］】〕❩❫⟧]")

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# A "claim" is a sentence making a factual assertion. Discourse scaffolding is
# not a claim, carries no citation, and must not count against coverage.
_DISCOURSE_PATTERNS = (
    r"^(here'?s|here is|below is|the following|to summari[sz]e|in summary)\b",
    r"^(sure|certainly|of course|happy to)\b",
    r"^(let me|i'?ll|i will|i can)\b",
    r"^(based on|according to) (the|your) (documents?|sources?)[:,]?$",
)
_DISCOURSE_RE = re.compile("|".join(_DISCOURSE_PATTERNS), re.IGNORECASE)


def normalise_markers(text: str) -> str:
    """Rewrite every bracket variant to ASCII ``[n]``."""
    return CITATION_MARKER_RE.sub(lambda m: f"[{m.group(1)}]", text)


def is_factual_claim(sentence: str) -> bool:
    """Whether a sentence belongs in the coverage denominator."""
    stripped = sentence.strip()
    if len(stripped) < 12:
        return False
    if _DISCOURSE_RE.match(stripped):
        return False
    # A sentence that is nothing but a marker is a fragment, not a claim.
    return bool(CITATION_MARKER_RE.sub("", stripped).strip())


@dataclass
class Claim:
    text: str
    markers: list[int] = field(default_factory=list)


def split_claims(answer: str) -> list[Claim]:
    """Split an answer into claims, keeping each one's markers.

    **Line-aware**, not a pure sentence split: bullet items routinely carry no
    terminal punctuation, so splitting on sentence boundaries alone merges a
    whole list into one "sentence" and mis-pairs every claim with the wrong
    marker. That mispairing dragged the reference project's citation accuracy to
    0.29 on lookup questions.
    """
    normalised = normalise_markers(answer)
    claims: list[Claim] = []

    for line in normalised.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip list bullets so the sentence reads naturally to the judge.
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", line)
        for sentence in _SENTENCE_SPLIT_RE.split(line):
            sentence = sentence.strip()
            if not sentence or not is_factual_claim(sentence):
                continue
            markers = [int(m) for m in CITATION_MARKER_RE.findall(sentence)]
            claims.append(Claim(text=sentence, markers=markers))

    return claims


@dataclass
class VerificationResult:
    # marker -> verdict. None means "not checked, or the judge failed" (I2).
    verdicts: dict[int, bool | None]
    coverage: float | None
    claims_checked: int = 0

    @property
    def any_unsupported(self) -> bool:
        return any(v is False for v in self.verdicts.values())


class Verifier:
    def __init__(self, llm: LLMClient, settings: Settings | None = None) -> None:
        self.llm = llm
        self.settings = settings or get_settings()

    async def verify(
        self, answer: str, sources_by_marker: dict[int, str]
    ) -> VerificationResult:
        """Check every cited claim. Never raises — an absent verdict is ``None``."""
        claims = split_claims(answer)
        cited = [c for c in claims if c.markers]

        if not claims:
            return VerificationResult(verdicts={}, coverage=None)

        # Coverage counts factual claims only — discourse was filtered above.
        coverage = len(cited) / len(claims) if claims else 0.0

        results = await asyncio.gather(
            *(self._judge(claim, sources_by_marker) for claim in cited),
            return_exceptions=True,
        )

        verdicts: dict[int, bool | None] = {m: None for m in sources_by_marker}
        for claim, verdict in zip(cited, results, strict=True):
            value = None if isinstance(verdict, BaseException) else verdict
            for marker in claim.markers:
                if value is False:
                    # One unsupported use is enough to flag the marker; a later
                    # supported use must not overwrite it back to True.
                    verdicts[marker] = False
                elif value is True and verdicts.get(marker) is not False:
                    verdicts[marker] = True

        return VerificationResult(
            verdicts=verdicts, coverage=coverage, claims_checked=len(cited)
        )

    async def _judge(self, claim: Claim, sources: dict[int, str]) -> bool | None:
        """One claim against the union of its cited sources."""
        texts = [sources[m] for m in claim.markers if m in sources]
        if not texts:
            # Cites a marker that does not exist. Not a judgement about support —
            # the model invented a reference — so it stays unknown here and is
            # visible as a dangling citation elsewhere.
            return None

        try:
            result = await self.llm.complete_json(
                [
                    Message(role="system", content=prompts.VERIFY_SYSTEM),
                    Message(
                        role="user", content=prompts.build_verify_message(claim.text, texts)
                    ),
                ],
                model=self.settings.llm_model_verify,
                max_tokens=200,
                timeout=self.settings.timeout_llm_verify_s,
            )
        except LLMError as exc:
            logger.warning("verification judge failed: %s", exc)
            return None  # I2 — unknown, not unsupported

        supported = result.get("supported")
        return bool(supported) if isinstance(supported, bool) else None
