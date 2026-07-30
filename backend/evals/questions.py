"""The golden set — curated down from 53 to 22 against the four-document corpus.

Two kinds of question, graded differently and deliberately kept apart:

``ANSWERABLE``
    The corpus contains the answer. Graded on whether the expected fact appears.

``UNANSWERABLE``
    The corpus does **not** contain it, and the only correct behaviour is to
    decline. A confident, specific, plausible-sounding answer here is a failure
    no matter how good it reads.

The negative controls are what make this set useful for setting the relevance
floors. Tuning a floor to maximise accuracy on answerable questions alone
optimises for a system that never refuses — which scores well right up until a
reviewer asks something the documents do not cover.

Curation
--------
Eighteen questions went with the two documents ``evals.corpus`` dropped, and a
further thirteen were cut as redundant. The set is now organised by the *angle*
each question probes rather than by an arbitrary letter, because the useful
report is "table lookup is fine, multi-part is not", not "section B scored 60%".
Ids are unchanged so results stay comparable with earlier runs.

``DROPPED`` records every cut and its reason, so the set cannot quietly stop
testing something.

Grading semantics
-----------------
``must_include`` is a **disjunction** — any one substring means the fact is
present, because the model's phrasing is its own. ``must_include_all`` is a
**conjunction**, and it is what makes "multi-part question" a real test: a
three-part question graded disjunctively passes when the model answers one third
of it, which is precisely the failure mode multi-part retrieval is supposed to
prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Expect(StrEnum):
    ANSWER = "answer"
    DECLINE = "decline"


@dataclass(frozen=True)
class Question:
    id: str
    section: str
    text: str
    expect: Expect
    # Any one of these is enough (disjunction). Numeric entries are matched on a
    # number boundary by the runner, not as raw substrings — "75" must not be
    # satisfied by "175".
    must_include: tuple[str, ...] = ()
    docs: tuple[str, ...] = ()
    note: str = ""
    # Every one of these must appear (conjunction). For multi-part questions.
    must_include_all: tuple[str, ...] = ()
    # Follow-up turns, for the multi-turn cases.
    follow_ups: tuple[str, ...] = field(default_factory=tuple)


QUESTIONS: list[Question] = [
    # ------------------------------------------------- exact-value lookup
    Question("A6", "lookup",
             "How many curated mathematical models does MathModDB contain as of July 2026?",
             Expect.ANSWER, ("229",), ("MathModDB",),
             note="plain prose figure"),
    Question("A11", "lookup",
             "According to langchain.md, what value does the directory property "
             "default to when not set?",
             Expect.ANSWER, ("none",), ("langchain",),
             note="non-PDF path; value lives in a Markdown property table"),

    # ------------------------------------------------- table-cell lookup
    Question("A1", "table",
             "What was India's 10-Year G-Sec yield in June 2026?",
             Expect.ANSWER, ("6.7",), ("360ONE",),
             note="ruled table, month-column lookup"),
    Question("A2", "table",
             "What was the Manufacturing PMI in February 2026?",
             Expect.ANSWER, ("56.9",), ("360ONE",),
             note="same macro table as E2, but this cell is filled — the pair is "
                  "what makes E2's refusal meaningful"),
    Question("A3", "table",
             "What is the Net AUM of the 360 ONE Focused Fund as of June 30, 2026?",
             Expect.ANSWER, ("6,634", "6634"), ("360ONE",),
             note="one fund's fact-page among 20 near-identical ones"),
    Question("A8", "table",
             "How many maintenance-specific object properties are in the "
             "OpenAI-generated ontology variant?",
             Expect.ANSWER, ("75",), ("LegalKG",),
             note="borderless (booktabs) table — the direct test of the fallback "
                  "detector added in 2067064"),

    # ------------------------------------------------- arithmetic over retrieved values
    Question("B6", "arith",
             "Comparing Mistral and OpenAI ontology variants in the LegalKG paper, "
             "how many more maintenance-specific signatures does OpenAI have than "
             "Mistral?",
             Expect.ANSWER, ("46",), ("LegalKG",),
             note="two cells must both be retrieved before the subtraction"),

    # ------------------------------------------------- paraphrase
    Question("K5", "paraphrase",
             "Is MathModDB built on the same underlying technology stack as Wikidata?",
             Expect.ANSWER, ("Wikibase",), ("MathModDB",),
             note="the document never says 'technology stack'; the answer is a term "
                  "the question does not contain"),
    Question("F2", "paraphrase",
             "Summarize what langchain.md is actually about.",
             Expect.ANSWER, ("navigation", "docs.json", "Mintlify"), ("langchain",),
             note="filename trap in the positive direction — must not refuse a file "
                  "it can perfectly well describe"),

    # ------------------------------------------------- multi-part (conjunctive)
    Question("C3", "multipart",
             "According to langchain.md, what artifact format values are supported "
             "for SDK reference generation?",
             Expect.ANSWER, docs=("langchain",),
             must_include_all=("typedoc", "docfx", "javadoc"),
             note="three values, all in one enum — a partial answer is a failure"),
    Question("I6", "multipart",
             "How many inconsistent relation signatures did the qualitative check "
             "find in the OpenAI-fused graph versus the Mistral-fused graph?",
             Expect.ANSWER, docs=("LegalKG",),
             must_include_all=("52", "39"),
             note="two numbers, one sentence"),
    Question("L3", "multipart",
             "Describe the three tools exposed by the MathModDB MCP server.",
             Expect.ANSWER, docs=("MathModDB",),
             must_include_all=("SPARQL", "Ontology"),
             note="explicitly three-part; the two named markers are the ones a "
                  "correct answer cannot paraphrase away"),
    Question("D6", "multipart",
             "How many curated mathematical models does MathModDB contain, and what "
             "similarity threshold did the LegalKG pipeline use for label fusion?",
             Expect.ANSWER, docs=("MathModDB", "LegalKG"),
             must_include_all=("229", "0.7"),
             note="ADDED. Two unrelated intents in one message, answers in two "
                  "different documents — the case interleave_intents exists for, "
                  "and the corpus had no genuinely multi-intent cross-document "
                  "question before this one"),

    # ------------------------------------------------- cross-document
    Question("D1", "crossdoc",
             "Which two documents in this corpus both discuss the Model Context "
             "Protocol (MCP)?",
             Expect.ANSWER, docs=("MathModDB", "langchain"),
             must_include_all=("MathModDB", "langchain"),
             note="shared acronym, unrelated systems"),
    Question("D3", "crossdoc",
             "Compare the generative AI tools disclosed by the authors of the "
             "MathModDB paper versus the LegalKG paper, and what each was used for.",
             Expect.ANSWER, docs=("MathModDB", "LegalKG"),
             must_include_all=("Claude", "GPT"),
             note="both halves must be retrieved; the two papers are topical twins, "
                  "so retrieval has to distinguish them rather than blend them"),

    # ------------------------------------------------- corpus-wide aggregation
    Question("D5", "aggregate",
             "Which fund manager appears across the most 360 ONE fund fact-pages, "
             "and in what capacity?",
             Expect.ANSWER, ("Viral Mehta", "Mehta"), ("360ONE",),
             note="kept knowing top-k retrieval may structurally fail it: answering "
                  "needs a scan of all 20 fact-pages, not the best 12 chunks. A "
                  "labelled expected-hard case, not a mystery failure"),

    # ------------------------------------------------- multi-turn
    Question("M1", "multiturn",
             "What is the 360 ONE Focused Fund's Net AUM?",
             Expect.ANSWER, ("6,634", "6634"), ("360ONE",),
             note="the follow-up's pronoun is meaningless without the antecedent",
             follow_ups=("Who manages it?",)),

    # ================================================================
    # NEGATIVE CONTROLS — the correct answer is a refusal.
    # ================================================================
    Question("E1", "decline",
             "What was the 360 ONE Focused Fund's AUM exactly one year ago, in "
             "June 2025?",
             Expect.DECLINE, docs=("360ONE",),
             note="topically adjacent: the factsheet gives only current AUM"),
    Question("E2", "decline",
             "What was India's two-wheeler sales YoY growth in June 2026?",
             Expect.DECLINE, docs=("360ONE",),
             note="hardest control — the row exists and the system reads that table "
                  "correctly (A2), but this cell is blank"),
    Question("E6", "decline",
             "What F1 score did the LLM-based relation extraction achieve in the "
             "LegalKG paper?",
             Expect.DECLINE, docs=("LegalKG",),
             note="the paper reports R_JSON / R_class / R_sig, never F1"),
    Question("F1", "decline",
             "According to the corpus, what are LangChain's core abstractions such "
             "as Chains, Agents, Tools and Memory?",
             Expect.DECLINE, docs=("langchain",),
             note="filename trap: langchain.md is Mintlify navigation documentation"),
    Question("J5", "decline",
             "What two dates bound the export-control suspension and restoration of "
             "Anthropic's Fable and Mythos models?",
             Expect.DECLINE,
             note="zero-relevance control — nothing in the corpus touches this"),
]


# Every question cut, and what went with it. A smaller set that silently stops
# testing something is worse than a large one.
DROPPED: dict[str, str] = {
    # --- went with the two dropped documents (evals.corpus.EXCLUDED)
    "A4, A5, I1, E3, E4, K1, L2": "CMVF questions — document dropped.",
    "A9, A10, B4, B5, B8, I3, I4, J4, K3, E8, E9": "TSLA 10-Q questions — document dropped.",
    # --- cut as redundant, documents retained
    "A7": "MathModDB R@10 table lookup. Table-cell lookup is covered by A2, A3 "
          "and A8, and A8 is the more informative one (it is the borderless case).",
    "I5": "LegalKG 0.7 similarity threshold. Absorbed into D6, which asks for the "
          "same fact as one half of a two-intent question.",
    "I2": 'Vedanta line-item count. must_include was ("three", "3") and "3" '
          "matches almost any answer containing a digit — the question could not "
          "fail, so it measured nothing.",
    "B1": "Net FPI flow change. The question supplies no operands and the expected "
          "substrings were the two endpoints rather than the delta, so a verbatim "
          "table read passed without doing the arithmetic. B6 tests the same "
          "capability honestly.",
    "B7": "RDF triple reduction percentage. The question states both operands "
          "inline, so it tests the model's arithmetic and not retrieval at all.",
    "C2, C5": "Mintlify JSON-example lookups. Structured-syntax retrieval from "
              "langchain.md is covered by A11 and C3.",
    "K6": 'Does langchain.md describe RAG configuration? must_include was ("no", '
          '"navigation") and "no" is a substring of "not", "none" and "cannot", so '
          "the grader passed on nearly any refusal-shaped text. F1 tests the same "
          "trap as a proper negative control.",
    "J2": "INR/USD month-end rate. Same table and same shape as A1/A2.",
    "E5": "SPARQL query text. Superseded by E6, which is a sharper negative: the "
          "LegalKG paper reports adjacent metrics, so it tempts a plausible guess.",
    "E7, F3, F5": "Three further filename-trap refusals. F1 is the same test; four "
                  "copies of one control inflate the refusal score.",
    "E10": "Firm-wide 360 ONE AUM. Same not-stated-anywhere shape as E1.",
}


BY_ID = {q.id: q for q in QUESTIONS}
ANSWERABLE = [q for q in QUESTIONS if q.expect is Expect.ANSWER]
UNANSWERABLE = [q for q in QUESTIONS if q.expect is Expect.DECLINE]
