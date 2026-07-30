"""The declared evaluation corpus — which files are in it, and why each is.

Previously the corpus was "whatever is in ``document corpus/``" and the question
bank referred to documents by free-form strings (``"360ONE"``, ``"TSLA10Q"``)
that nothing connected to a file. Two consequences, both of which cost a
session: a question could name a document that was not ingested and simply fail
as if retrieval were broken, and ``docs_cited`` in the results was an
eight-character ``doc_id`` prefix nobody could map back to a filename. Declaring
the corpus here fixes both — the runner resolves ids to keys, and asserts up
front that every question's documents are present.

**Six documents were cut to four**, deliberately. Heterogeneity that large makes
a failure impossible to attribute: when accuracy is 60%, "which stage is wrong"
has six confounded answers. The four kept cover every retrieval path the system
has, and three of them overlap topically so that "multi-document" is something
the system is actually tested on rather than a word in the README:

    MathModDB + LegalKG   both LLM-assisted ontology/knowledge-graph papers
    MathModDB + langchain both mention MCP, describing unrelated systems

``EXCLUDED`` records what was dropped and what coverage went with it, because a
smaller corpus that quietly stops testing something is worse than a large one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CorpusDoc:
    key: str
    filename: str
    why: str


# Ingested, and the only documents the question bank may reference.
CORPUS: tuple[CorpusDoc, ...] = (
    CorpusDoc(
        "MathModDB",
        "2607.24512v1.pdf",
        "16pp paper. Cross-document anchor: shares MCP with langchain.md and "
        "ontology/KG subject matter with LegalKG. Carries the multi-fact "
        "synthesis and needle cases.",
    ),
    CorpusDoc(
        "LegalKG",
        "2607.24551v1.pdf",
        "5pp paper, and the document the borderless-table detector actually "
        "recovered tables in (3 captions / 0 line-detections before commit "
        "2067064). Topical twin of MathModDB, so cross-document synthesis has "
        "two genuinely comparable sources.",
    ),
    CorpusDoc(
        "360ONE",
        "360_ONE_MF_July_2026_Regular_b1a10fc55a.pdf",
        "22pp mutual-fund factsheet: ruled tables, dense numerics, and 486 "
        "chunks — the densest haystack in the corpus, more chunks than the "
        "43-page 10-Q it replaces.",
    ),
    CorpusDoc(
        "langchain",
        "langchain.md",
        "The non-PDF path (no parser, no page numbers) and the filename trap: "
        "it is Mintlify navigation documentation, not LangChain documentation.",
    ),
)

# Dropped, with the coverage each one took with it. Stated rather than implied.
EXCLUDED: dict[str, str] = {
    "tsla-20260630.pdf": (
        "TSLA10Q, 43pp. Dropped for iteration cost. Loses: the largest page "
        "count, and 11 of the corpus's 13 escalation-flagged pages — but those "
        "pages were never escalated anyway (Groq exposes no vision model, so "
        "Tier-2 has never run on this corpus at all). Haystack difficulty is "
        "preserved by 360ONE, which has more chunks."
    ),
    "2607.24354v1.pdf": (
        "CMVF, 9pp. Dropped because it has no topical partner in the corpus — "
        "every CMVF question was single-document, so it added heterogeneity "
        "without adding a retrieval path. Loses: nothing the other three "
        "documents do not also exercise."
    ),
}

BY_KEY = {doc.key: doc for doc in CORPUS}
BY_FILENAME = {doc.filename: doc for doc in CORPUS}
FILENAMES = frozenset(doc.filename for doc in CORPUS)
