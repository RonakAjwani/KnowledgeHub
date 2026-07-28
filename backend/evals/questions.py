"""The golden set, transcribed from ``rag_test_question_bank.md``.

Two kinds of question, graded differently and deliberately kept apart:

``ANSWERABLE``
    The corpus contains the answer. Graded on whether the expected fact appears
    and whether it is cited.

``UNANSWERABLE``
    The corpus does **not** contain it, and the only correct behaviour is to
    decline. A confident, specific, plausible-sounding answer here is a failure
    no matter how good it reads.

The negative controls are what make this set useful for setting the relevance
floors. Tuning a floor to maximise accuracy on answerable questions alone
optimises for a system that never refuses — which scores well right up until a
reviewer asks something the documents do not cover.
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
    # Substrings that must appear in a correct answer. Any one is enough — the
    # model's phrasing is its own, and requiring an exact sentence would measure
    # wording rather than retrieval.
    must_include: tuple[str, ...] = ()
    docs: tuple[str, ...] = ()
    note: str = ""
    # Follow-up turns, for the multi-turn cases.
    follow_ups: tuple[str, ...] = field(default_factory=tuple)


QUESTIONS: list[Question] = [
    # ---------------------------------------------- A · single-doc lookup
    Question("A1", "A", "What was India's 10-Year G-Sec yield in June 2026?",
             Expect.ANSWER, ("6.7",), ("360ONE",)),
    Question("A2", "A", "What was the Manufacturing PMI in February 2026?",
             Expect.ANSWER, ("56.9",), ("360ONE",)),
    Question("A3", "A", "What is the Net AUM of the 360 ONE Focused Fund as of June 30, 2026?",
             Expect.ANSWER, ("6,634", "6634"), ("360ONE",)),
    Question("A4", "A", "On Qwen3.5-4B, what mean accuracy (%) did CMVF achieve across the 8 main benchmarks?",
             Expect.ANSWER, ("75.0",), ("CMVF",)),
    Question("A5", "A", "In the CM-Vis ablation row of Table 5, what is the RealWorldQA accuracy for LLaVA-1.6-7B?",
             Expect.ANSWER, ("73.5",), ("CMVF",)),
    Question("A6", "A", "How many curated mathematical models does MathModDB contain as of July 2026?",
             Expect.ANSWER, ("229",), ("MathModDB",)),
    Question("A7", "A", 'What R@10 score did the schema retriever achieve for the "Domain & provenance" topical group?',
             Expect.ANSWER, ("0.95",), ("MathModDB",)),
    Question("A8", "A", "How many maintenance-specific object properties are in the OpenAI-generated ontology variant?",
             Expect.ANSWER, ("75",), ("LegalKG",)),
    Question("A9", "A", "What is Tesla's total revenue for the three months ended June 30, 2026?",
             Expect.ANSWER, ("28,236", "28236"), ("TSLA10Q",)),
    Question("A10", "A", "How many units of Bitcoin does Tesla hold, and at what acquisition cost?",
             Expect.ANSWER, ("11,509", "11509"), ("TSLA10Q",)),
    Question("A11", "A", "According to langchain.md, what value does the directory property default to when not set?",
             Expect.ANSWER, ("none",), ("langchain",)),

    # ---------------------------------------------- B · numerical reasoning
    Question("B1", "B", "By how much did India's Net FPI equity flows change between February 2026 and March 2026?",
             Expect.ANSWER, ("15.2", "12.7"), ("360ONE",)),
    Question("B4", "B", "Does 12 tranches of 35,311,992 shares equal the total shares reported for Tesla's 2025 CEO Performance Award?",
             Expect.ANSWER, ("423,743,904", "423743904", "yes"), ("TSLA10Q",)),
    Question("B5", "B", "What was the dollar change in Tesla's total revenue from Q2 2025 to Q2 2026, and what percentage increase does that represent?",
             Expect.ANSWER, ("5,740", "5740", "26"), ("TSLA10Q",)),
    Question("B6", "B", "Comparing Mistral and OpenAI ontology variants in the LegalKG paper, how many more maintenance-specific signatures does OpenAI have than Mistral?",
             Expect.ANSWER, ("46",), ("LegalKG",)),
    Question("B7", "B", "Fusion reduced the Mistral knowledge graph's RDF triple count from 2,119,485 to 1,131,066. What percentage reduction is that?",
             Expect.ANSWER, ("46",), ("LegalKG",)),
    Question("B8", "B", "What was Tesla's effective tax rate change in percentage points from Q2 2025 to Q2 2026?",
             Expect.ANSWER, ("23", "15"), ("TSLA10Q",)),

    # ---------------------------------------------- C · structured syntax
    Question("C2", "C", 'In the Mintlify docs.json example under "Products containing tabs", what icon is assigned to the "Platform" product?',
             Expect.ANSWER, ("server",), ("langchain",)),
    Question("C3", "C", "According to langchain.md, what artifact format values are supported for SDK reference generation?",
             Expect.ANSWER, ("typedoc", "docfx", "javadoc"), ("langchain",)),
    Question("C5", "C", 'In the JSON example under "Root page", what is the value of the root field inside the "API pages" group?',
             Expect.ANSWER, ("api-overview",), ("langchain",)),

    # ---------------------------------------------- D · cross-document
    Question("D1", "D", "Which two documents in this corpus both discuss the Model Context Protocol (MCP)?",
             Expect.ANSWER, ("MathModDB", "langchain"), ("MathModDB", "langchain"),
             note="cross-document; shared acronym, unrelated systems"),
    Question("D3", "D", "Compare the generative AI tools disclosed by the authors of the MathModDB paper versus the LegalKG paper, and what each was used for.",
             Expect.ANSWER, ("Claude", "GPT"), ("MathModDB", "LegalKG"),
             note="cross-document synthesis"),
    Question("D5", "D", "Which fund manager appears across the most 360 ONE fund fact-pages, and in what capacity?",
             Expect.ANSWER, ("Viral Mehta", "Mehta"), ("360ONE",),
             note="intra-document multi-hop"),

    # ---------------------------------------------- I · needle in a haystack
    Question("I1", "I", "What exact p-value does the CMVF paper report for the McNemar test on Qwen2.5-VL/TextVQA?",
             Expect.ANSWER, ("0.015",), ("CMVF",)),
    Question("I2", "I", 'How many distinct "Vedanta" group entities appear as separate line items in the 360 ONE Flexicap Fund portfolio?',
             Expect.ANSWER, ("three", "3"), ("360ONE",)),
    Question("I3", "I", "What discount was applied to Tesla's SpaceX equity investment for lack of marketability?",
             Expect.ANSWER, ("238",), ("TSLA10Q",)),
    Question("I4", "I", "What offset amount per share applies when Tesla's CEO 2025 Performance Award shares vest?",
             Expect.ANSWER, ("334.09",), ("TSLA10Q",)),
    Question("I5", "I", "What similarity threshold was used for both entity-label and property-label fusion in the LegalKG pipeline?",
             Expect.ANSWER, ("0.7",), ("LegalKG",)),
    Question("I6", "I", "How many inconsistent relation signatures did the qualitative check find in the OpenAI-fused graph versus the Mistral-fused graph?",
             Expect.ANSWER, ("52", "39"), ("LegalKG",)),

    # ---------------------------------------------- J · date precision
    Question("J2", "J", "What was the INR/USD exchange rate at month-end for February 2026 specifically?",
             Expect.ANSWER, ("91",), ("360ONE",)),
    Question("J4", "J", "When was the 2025 CEO Interim Award forfeited, and under what named event?",
             Expect.ANSWER, ("Tornetta",), ("TSLA10Q",)),

    # ---------------------------------------------- K · yes/no verification
    Question("K1", "K", "Does the CMVF paper evaluate GPT-4 as one of its target VLMs?",
             Expect.ANSWER, ("no", "not"), ("CMVF",)),
    Question("K3", "K", "Did Tesla report a net loss for the quarter ended June 30, 2026?",
             Expect.ANSWER, ("no", "net income", "1,128"), ("TSLA10Q",)),
    Question("K5", "K", "Is MathModDB built on the same underlying technology stack as Wikidata?",
             Expect.ANSWER, ("Wikibase", "yes"), ("MathModDB",)),
    Question("K6", "K", "Does langchain.md describe how to configure retrieval-augmented generation pipelines?",
             Expect.ANSWER, ("no", "navigation"), ("langchain",)),

    # ---------------------------------------------- L · multi-fact synthesis
    Question("L2", "L", "Explain CMVF's two-stage visual diagnostic channel — what happens in Stage 1 versus Stage 2?",
             Expect.ANSWER, ("Stage 1", "description"), ("CMVF",)),
    Question("L3", "L", "Describe the three tools exposed by the MathModDB MCP server.",
             Expect.ANSWER, ("SPARQL", "Ontology"), ("MathModDB",)),

    # ================================================================
    # NEGATIVE CONTROLS — the correct answer is a refusal.
    # ================================================================
    Question("E1", "E", "What was the 360 ONE Focused Fund's AUM exactly one year ago, in June 2025?",
             Expect.DECLINE, note="factsheet gives only current AUM"),
    Question("E2", "E", "What was India's two-wheeler sales YoY growth in June 2026?",
             Expect.DECLINE, note="that cell is blank in the macro table"),
    Question("E3", "E", "What GPU hardware was used to run the CMVF experiments?",
             Expect.DECLINE, note="never specified in the paper"),
    Question("E4", "E", "Under what license will CMVF's code and optimized prompts be released?",
             Expect.DECLINE, note="paper names no license"),
    Question("E5", "E", "What is the exact SPARQL query text used to retrieve the Stokes Darcy model's coupling conditions?",
             Expect.DECLINE, note="linked externally, not reproduced inline"),
    Question("E6", "E", "What F1 score did the LLM-based relation extraction achieve in the LegalKG paper?",
             Expect.DECLINE, note="paper reports R_JSON/R_class/R_sig, not F1"),
    Question("E7", "E", "How do you install the LangChain Python package, and how do you initialize a RetrievalQA chain?",
             Expect.DECLINE, note="filename trap — the file is Mintlify navigation docs"),
    Question("E8", "E", "What was Tesla's closing stock price on June 30, 2026?",
             Expect.DECLINE, note="not disclosed in a 10-Q"),
    Question("E9", "E", "How many Cybertrucks did Tesla deliver in Q2 2026?",
             Expect.DECLINE, note="deliveries reported only in aggregate"),
    Question("E10", "E", "What is 360 ONE Asset Management's total AUM across its entire fund lineup?",
             Expect.DECLINE, note="no firm-wide total is stated"),

    Question("F1", "F", "According to the corpus, what are LangChain's core abstractions such as Chains, Agents, Tools and Memory?",
             Expect.DECLINE, note="filename trap"),
    Question("F3", "F", "Does the corpus contain any information about LangChain's LCEL (LangChain Expression Language)?",
             Expect.DECLINE, note="filename trap"),
    Question("F5", "F", "Which vector store integrations does LangChain support, based on the uploaded documentation?",
             Expect.DECLINE, note="filename trap"),

    Question("J5", "J", "What two dates bound the export-control suspension and restoration of Anthropic's Fable and Mythos models?",
             Expect.DECLINE, note="zero-relevance control — nothing in the corpus touches this"),
]

# The filename trap deserves its own positive case: the system should be able to
# say what langchain.md actually is, not merely refuse everything mentioning it.
QUESTIONS.append(
    Question("F2", "F", "Summarize what langchain.md is actually about.",
             Expect.ANSWER, ("navigation", "docs.json", "Mintlify"), ("langchain",),
             note="must not be fooled by the filename in either direction")
)

# Multi-turn: the follow-up is meaningless without the antecedent.
QUESTIONS.append(
    Question("M1", "M", "What is the 360 ONE Focused Fund's Net AUM?",
             Expect.ANSWER, ("6,634", "6634"), ("360ONE",),
             note="multi-turn: follow-up uses a pronoun",
             follow_ups=("Who manages it?",))
)


BY_ID = {q.id: q for q in QUESTIONS}
ANSWERABLE = [q for q in QUESTIONS if q.expect is Expect.ANSWER]
UNANSWERABLE = [q for q in QUESTIONS if q.expect is Expect.DECLINE]
