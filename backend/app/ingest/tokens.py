"""Token counting, denominated in the embedding model's own tokenizer.

Chunk sizes are expressed in tokens because that is the unit the retriever
actually cares about — ``bge-small-en-v1.5`` truncates at 512 tokens, and a chunk
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


def _heuristic_token_count(text: str) -> int:
    """Deterministic stand-in used in tests and if the model cannot load.

    Counts word-ish runs and punctuation, then applies a modest inflation factor
    for WordPiece splitting of longer words. Close enough to keep chunk sizes
    sane; never used to make a claim about exact model behaviour.
    """
    if not text:
        return 0
    pieces = _WORDish.findall(text)
    long_words = sum(1 for p in pieces if len(p) > 6)
    return len(pieces) + long_words // 2


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


def count_tokens(text: str, *, exact: bool = True) -> int:
    """Tokens in ``text``. Falls back to the heuristic when the model is absent."""
    if not text:
        return 0
    if exact:
        model = get_embedding_model()
        if model is not None:
            try:
                return int(model.token_count(text))
            except Exception as exc:  # noqa: BLE001
                logger.warning("token_count failed, using heuristic: %s", exc)
    return _heuristic_token_count(text)


def reset_for_tests() -> None:
    global _model, _load_failed
    _model = None
    _load_failed = False
