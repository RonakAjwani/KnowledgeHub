"""Token counting, denominated in the embedding model's own tokenizer.

Chunk sizes are expressed in tokens because that is the unit the retriever
actually cares about - ``bge-small-en-v1.5`` truncates at 512 tokens, and a chunk
measured in characters sits at an unpredictable distance from that ceiling
depending on how dense the document's vocabulary is. The reference project sized
chunks in characters and the sizes drifted between documents as a result.

``fastembed.TextEmbedding`` exposes ``token_count`` publicly as of 0.8.0, so the
counter is the same tokenizer that will produce the vectors rather than an
approximation of it.

Two properties matter for how this is wired:

* **Lazy.** Instantiating the model downloads roughly 130 MB of ONNX weights.
  Chunking logic must be unit-testable without that, so the model is loaded on
  first real use and a deterministic heuristic stands in when it is unavailable.
* **Shared.** One process-wide instance. Two copies of the model would not fit
  beside each other inside 512 MB.
"""

from __future__ import annotations

import logging
import re
import threading

from app.config import settings

logger = logging.getLogger(__name__)

_model = None
_lock = threading.Lock()
_load_failed = False

_WORDish = re.compile(r"\w+|[^\w\s]")


# The fewest characters a real token can plausibly span. MEASURED on this
# tokenizer: English runs 1.9-4 chars/token and CJK about 1, so a floor of
# len/8 sits below anything genuine and is inert on ordinary text. It exists
# only to catch counts that have collapsed - see `_UNK_COLLAPSE_NOTE`.
_MIN_CHARS_PER_TOKEN = 8

_UNK_COLLAPSE_NOTE = """\
WordPiece has a `max_input_chars_per_word` of 100 and maps any longer unbroken
run to a single `[UNK]`. MEASURED: 100 "A"s tokenize to 52 tokens, 110 to
**3** (`[CLS] [UNK] [SEP]`), and 9,000 also to 3. So an unbroken run reports a
constant no matter how long it gets.

That is not a synthetic case. A base64 data URI - an image embedded in a
markdown file - measured **4,422 characters to 13 tokens**, which passes every
chunk and parent ceiling untouched, becomes one chunk whose dense vector is
literally the embedding of `[UNK]`, and lands in the parent the model receives
at roughly 85x the token budget it was admitted under. Long URLs, minified
code blocks, hex digests and any PDF whose word-boundary recovery failed all
produce the same shape.

The windowed re-count above does not catch it: that guards against the count
saturating *at* the 512 cap, and this under-reports instead.
"""


def _heuristic_token_count(text: str) -> int:
    """Deterministic stand-in used in tests and if the model cannot load.

    Counts word-ish runs and punctuation, then applies a modest inflation factor
    for WordPiece splitting of longer words. Close enough to keep chunk sizes
    sane; never used to make a claim about exact model behaviour.

    Carries the same character floor as `count_tokens`, and needs it for its own
    reason: this is what `fit_context` counts the prompt with, and it scored a
    9,000-character unbroken run at **1** token - so the budget that exists to
    keep a request under the serving model's limit was blind to precisely the
    input most able to blow it.
    """
    if not text:
        return 0
    pieces = _WORDish.findall(text)
    long_words = sum(1 for p in pieces if len(p) > 6)
    return max(len(pieces) + long_words // 2, len(text) // _MIN_CHARS_PER_TOKEN)


def get_embedding_model():
    """The process-wide fastembed instance, or None if it cannot be loaded."""
    global _model, _load_failed
    if _model is not None or _load_failed:
        return _model
    with _lock:
        if _model is None and not _load_failed:
            try:
                from fastembed import TextEmbedding

                _model = TextEmbedding(
                    model_name=settings.embed_model,
                    cache_dir=settings.fastembed_cache_dir,
                    # 0.1 vCPU: extra ONNX threads contend rather than parallelise.
                    threads=1,
                )
                logger.info("loaded embedding model %s", settings.embed_model)
            except Exception as exc:  # noqa: BLE001
                # Counting is not worth failing ingest over; sizing degrades to
                # the heuristic and the pipeline continues.
                logger.warning("could not load embedding model for tokenising: %s", exc)
                _load_failed = True
    return _model


# `bge-small-en-v1.5`'s tokenizer ships with truncation enabled at 512, which is
# correct for embedding and wrong for counting: `token_count` returns the length
# *after* truncation, so it saturates. MEASURED - a 320,000-character string and
# a 3,200-character one both report 512.
#
# That made the counter useless as a ceiling check above 512, which is exactly
# where a ceiling matters. The parent window grows while `tokens + extra <=
# parent_tokens`; with both terms clamped at 512 the sum could never reach 1200,
# so the test never failed. On a 54 KB single-section document **every** parent
# exceeded its ceiling and the largest was ~10,400 tokens, 8.7x the cap - and
# the parent is what the model receives, so that is context budget and token
# spend, not a cosmetic overrun. Short documents hide it completely: a section
# has to exceed 512 tokens before the clamp is reachable at all.
_MODEL_MAX_TOKENS = 512

# Small enough that a window cannot itself saturate in any script. Latin text
# runs ~4 chars/token, but a WordPiece vocabulary trained on English can emit
# more than one token per character for CJK and other non-Latin scripts, so this
# is sized for the pathological case rather than the common one.
_COUNT_WINDOW_CHARS = 256


def _windowed_token_count(model, text: str) -> int:
    """Sum the counts of windows small enough that none can be truncated.

    Deliberately *not* `tokenizer.no_truncation()`: that tokenizer instance is
    the one `embed()` uses, and turning truncation off there would hand the ONNX
    session sequences longer than it accepts. Windowing needs no shared mutable
    state and no private attribute.

    Splitting mid-word adds a token or two per window, so this reads slightly
    high - the right direction for a ceiling, and under half a percent on the
    documents it fires for.
    """
    total = 0
    for start in range(0, len(text), _COUNT_WINDOW_CHARS):
        total += int(model.token_count(text[start : start + _COUNT_WINDOW_CHARS]))
    return total


def count_tokens(text: str, *, exact: bool = True) -> int:
    """Tokens in ``text``. Falls back to the heuristic when the model is absent.

    Guards two opposite failures of the underlying counter, both measured:
    saturation at the model's 512-token input limit (see ``_windowed_token_count``)
    and collapse of an over-long unbroken run to a single ``[UNK]``
    (see ``_UNK_COLLAPSE_NOTE``).
    """
    if not text:
        return 0
    if exact:
        model = get_embedding_model()
        if model is not None:
            try:
                count = int(model.token_count(text))
                # Below the cap the answer is exact and cost one call. At the cap
                # it is indistinguishable from a truncated one, so re-count in
                # windows. Text that genuinely lands on 512 pays for a second
                # pass and gets the same answer, which is the cheap side of the
                # trade.
                if count >= _MODEL_MAX_TOKENS:
                    count = _windowed_token_count(model, text)
                # Then the floor, which guards the opposite failure - a count
                # that came back far too *low* because the tokenizer collapsed
                # an over-long run to `[UNK]`. Inert on ordinary text, where the
                # real count is always well above len/8.
                return max(count, len(text) // _MIN_CHARS_PER_TOKEN)
            except Exception as exc:  # noqa: BLE001
                logger.warning("token_count failed, using heuristic: %s", exc)
    return _heuristic_token_count(text)


def reset_for_tests() -> None:
    global _model, _load_failed
    _model = None
    _load_failed = False
