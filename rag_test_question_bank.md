# RAG Evaluation Question Bank

**Corpus (6 documents):**

| Tag | File | What it actually is |
|---|---|---|
| `[360ONE]` | `360_ONE_MF_July_2026_Regular_b1a10fc55a.pdf` | 360 ONE Mutual Fund monthly factsheet, July 2026 — macro tables, 12 fund fact-pages, glossary, PRC matrix |
| `[CMVF]` | `2607_24354v1.pdf` | arXiv paper "Are Prompt Optimizers Blind? Cross-Modal Visual Feedback for APO" (cs.AI, 27 Jul 2026) |
| `[MathModDB]` | `2607_24512v1.pdf` | arXiv paper "Making Mathematical Knowledge Explainable... Through LLM Integration" (cs.AI, 27 Jul 2026) |
| `[LegalKG]` | `2607_24551v1.pdf` | SEMANTiCS 2026 paper "LLM-Assisted Ontology Engineering and Construction of a French Legal Knowledge Graph" |
| `[langchain.md]` | `langchain.md` | ⚠️ **Filename is misleading.** Content is Mintlify's `docs.json` navigation documentation (groups, tabs, anchors, dropdowns, products, versions, SDK refs). It contains **zero** content about the LangChain framework. |
| `[TSLA10Q]` | `tsla-20260630.pdf` | Tesla, Inc. Form 10-Q for quarter ended June 30, 2026 |

**Legend for "Expected Answer / Behavior":** ✅ = answerable, verified below. 🚫 = correct behavior is to decline / say "not in the provided documents" — treat any confident, specific answer here as a hallucination failure.

---

## A. Single-Document Table / Fact Lookup

| # | Question | Source | Expected Answer |
|---|---|---|---|
| A1 | What was India's 10-Year G-Sec yield in June 2026? | `[360ONE]` | ✅ 6.7% |
| A2 | What was the Manufacturing PMI in February 2026? | `[360ONE]` | ✅ 56.9 |
| A3 | What is the Net AUM of the 360 ONE Focused Fund as of June 30, 2026? | `[360ONE]` | ✅ ₹6,634.45 crore |
| A4 | On Qwen3.5-4B, what mean accuracy (%) did CMVF achieve across the 8 main benchmarks? | `[CMVF]` | ✅ 75.0 |
| A5 | In the CM-Vis ablation row of Table 5, what is the RealWorldQA accuracy for LLaVA-1.6-7B? | `[CMVF]` | ✅ 73.5 |
| A6 | How many curated mathematical models does MathModDB contain as of July 2026? | `[MathModDB]` | ✅ 229 models (2,153 entries, 26,041 statements total) |
| A7 | What R@10 score did the schema retriever achieve for the "Domain & provenance" topical group? | `[MathModDB]` | ✅ 0.95 |
| A8 | How many maintenance-specific object properties are in the OpenAI-generated ontology variant? | `[LegalKG]` | ✅ 75 |
| A9 | What is Tesla's total revenue for the three months ended June 30, 2026? | `[TSLA10Q]` | ✅ $28,236 million |
| A10 | How many units of Bitcoin does Tesla hold, and at what acquisition cost? | `[TSLA10Q]` | ✅ 11,509 units, $386 million acquisition cost |
| A11 | According to `langchain.md`, what value does the `directory` property default to when not set? | `[langchain.md]` | ✅ `"none"` |

---

## B. Numerical Reasoning (requires computing, not just quoting one cell)

| # | Question | Source | Expected Answer |
|---|---|---|---|
| B1 | By how much did India's Net FPI equity flows change between February 2026 (+$2.5bn) and March 2026? | `[360ONE]` | ✅ Swung from +$2.5bn to –$12.7bn, a change of –$15.2bn |
| B2 | For each of the 4 target VLMs in Table 1, which non-visual-feedback baseline scored highest, and by how much did CMVF beat it? | `[CMVF]` | ✅ Qwen3.5-4B: DSPy 72.0 → CMVF 75.0 (+3.0); Qwen2.5-VL-7B: TG 58.6 → CMVF 60.1 (+1.6); LLaVA-1.6-7B: ProTeGi 55.6 → CMVF 58.7 (+3.1); Phi-3.5-vision: DSPy 53.8 → CMVF 55.9 (+2.1) |
| B3 | How much longer did CMVF's optimization take vs. TextGrad on Qwen3.5-4B, and what accuracy gain did that buy? | `[CMVF]` | ✅ 2.6h vs 2.1h (~24% more wall-clock time) for a +4.7pp mean-accuracy gain (75.0 vs 70.3) |
| B4 | Does 12 tranches × 35,311,992 shares equal the total shares reported for Tesla's 2025 CEO Performance Award? | `[TSLA10Q]` | ✅ Yes — 12 × 35,311,992 = 423,743,904, matching the disclosed total exactly |
| B5 | What was the dollar change in Tesla's total revenue from Q2 2025 to Q2 2026, and what percentage increase does that represent? | `[TSLA10Q]` | ✅ +$5,740 million, a 26% increase ($22,496M → $28,236M) |
| B6 | Comparing Mistral and OpenAI ontology variants in the LegalKG paper, how many more maintenance-specific signatures does OpenAI have than Mistral? | `[LegalKG]` | ✅ 105 − 59 = 46 more signatures |
| B7 | Fusion reduced the Mistral knowledge graph's RDF triple count from 2,119,485 to 1,131,066. What percentage reduction is that? | `[LegalKG]` | ✅ ≈ 46.6% reduction |
| B8 | What was Tesla's effective tax rate change (percentage points) from Q2 2025 to Q2 2026? | `[TSLA10Q]` | ✅ Decreased from 23% to 15%, an 8-percentage-point drop |

---

## C. Codeblock / Structured-Syntax Questions

| # | Question | Source | Expected Answer |
|---|---|---|---|
| C1 | Walk through Algorithm 1 ("CMVF Prompt Optimization") — what happens in the inner loop over wrong examples `W_t`? | `[CMVF]` | ✅ For each wrong example, the optimizer VLM `M_O` generates a visual description `v_i` from image+question only (no prediction/label), which then feeds Stage-2 aggregation into `g_vis_t` |
| C2 | In the Mintlify `docs.json` example under "Products containing tabs," what icon is assigned to the "Platform" product? | `[langchain.md]` | ✅ `"server"` |
| C3 | According to `langchain.md`, what artifact `format` values are supported for SDK reference generation? | `[langchain.md]` | ✅ `typedoc`, `docfx`, `javadoc`, `sphinx`, `phpdoc` |
| C4 | What does the `w(e_ij) = ω_r · f_sim(e_ij) · f_freq(r) · f_hub(c_i, c_j)` formula compute in the MathModDB paper, and what do the three multiplicative factors penalize? | `[MathModDB]` | ✅ Query-conditioned edge cost in the schema graph; factors penalize semantic irrelevance, over-represented properties, and paths through high-degree hub nodes, respectively |
| C5 | In the JSON example under "Root page," what is the value of the `root` field inside the "API pages" group? | `[langchain.md]` | ✅ `"api-overview"` |
| C6 | What does Equation 5 in the CMVF paper, `E[ΔL_CM − ΔL_text] ≳ α_V I_V − δ_T`, predict about when cross-modal feedback helps most? | `[CMVF]` | ✅ It should help most when α_V (fraction of visually-grounded errors) is large — e.g., medical VQA, OCR — and least when α_V → 0 |

---

## D. Cross-Document / Multi-Hop Questions

| # | Question | Source(s) | Expected Answer |
|---|---|---|---|
| D1 | Which two documents in this corpus both discuss the Model Context Protocol (MCP)? | `[MathModDB]` + `[langchain.md]` | ✅ The MathModDB paper (builds an MCP server) and `langchain.md` (footer links to an "Admin Model Context Protocol (MCP) server" doc page) — unrelated to each other, just a shared acronym |
| D2 | Which two papers in this corpus were both submitted to arXiv's cs.AI category on 27 July 2026? | `[CMVF]` + `[MathModDB]` | ✅ 2607.24354v1 (CMVF) and 2607.24512v1 (MathModDB); the LegalKG paper is a SEMANTiCS 2026 workshop paper with no arXiv stamp shown |
| D3 | Compare the generative AI tools disclosed by the authors of the MathModDB paper vs. the LegalKG paper, and what each was used for. | `[MathModDB]` + `[LegalKG]` | ✅ MathModDB: Claude Opus 4.7 & Sonnet 4.6 (language polishing, structure feedback, creating Figures 1–2) + Grammarly (proofreading). LegalKG: GPT-5.3 and Codex-5.5 (grammar/spelling, paraphrasing, formatting) |
| D4 | The term "Path" appears in both the CMVF paper and `langchain.md` — what does it refer to in each? | `[CMVF]` + `[langchain.md]` | ✅ CMVF: PathVQA, a pathology medical-VQA benchmark dataset. `langchain.md`: file paths referencing MDX page files (e.g., `"settings"`, `"pages"`) inside the `navigation.pages` array |
| D5 | Which fund manager appears across the most 360 ONE fund fact-pages, and in what capacity? | `[360ONE]` (intra-doc multi-hop) | ✅ Viral Mehta — listed as co-fund manager (w.e.f. June 30, 2026) on the Focused Fund, Flexicap Fund, Balanced Hybrid Fund, and Multi Asset Allocation Fund, and as fund manager on the ELSS Tax Saver Nifty 50 Index Fund and MSCI India ETF |
| D6 | Does any document in the corpus mention an actual dollar figure tied to India's Bloomberg Global Aggregate Bond Index inclusion, and does any other document discuss bond index inclusion at all? | `[360ONE]` only | ✅ Only `[360ONE]` — projected to attract ~$25 billion over the coming years; no other document in the corpus discusses bond index inclusion |

---

## E. Answer NOT in the Corpus — Hallucination Traps

*A well-behaved system should say it cannot find this information, not fabricate a plausible-sounding number.*

| # | Question | Nominal Source | Expected Behavior |
|---|---|---|---|
| E1 | What was the 360 ONE Focused Fund's AUM exactly one year ago, in June 2025? | `[360ONE]` | 🚫 Not provided — factsheet only gives current AUM and monthly-average AUM as of June 30, 2026 |
| E2 | What was India's two-wheeler sales YoY growth in June 2026? | `[360ONE]` | 🚫 That cell is blank in the macro table (data reported May-26 onward: 14.9, 28.4, 19.3, 35.2, 26.2); June-26 is not reported for this metric |
| E3 | What GPU hardware was used to run the CMVF experiments? | `[CMVF]` | 🚫 Not specified anywhere in the paper |
| E4 | Under what license will CMVF's code and optimized prompts be released? | `[CMVF]` | 🚫 The paper only says "Our code and optimized prompts will be released" — no license is named |
| E5 | What is the exact SPARQL query text used to retrieve the Stokes Darcy model's coupling conditions? | `[MathModDB]` | 🚫 The paper states a sample query is available at a linked repository URL but does not reproduce the query text inline |
| E6 | What F1 score did the LLM-based relation extraction achieve in the LegalKG paper? | `[LegalKG]` | 🚫 Not reported — the paper reports R_JSON, R_class, R_sig, R_prop, not F1/precision/recall in that form |
| E7 | How do you install the LangChain Python package, and how do you initialize a `RetrievalQA` chain? | `[langchain.md]` (misleading filename) | 🚫 This document never discusses the LangChain framework at all — it's Mintlify navigation config docs |
| E8 | What was Tesla's closing stock price on June 30, 2026? | `[TSLA10Q]` | 🚫 Stock price is not disclosed in a 10-Q's financial statements/MD&A |
| E9 | How many Cybertrucks did Tesla deliver in Q2 2026? | `[TSLA10Q]` | 🚫 Deliveries are reported only in aggregate ("~838 thousand consumer vehicles" through Q2), not broken out by model |
| E10 | What is 360 ONE Asset Management's total AUM across its entire fund lineup (not just the funds shown)? | `[360ONE]` | 🚫 Only individual scheme AUMs are given for the funds covered in this factsheet; no firm-wide total is stated, and the factsheet doesn't claim to cover every scheme |

---

## F. Filename/Content-Mismatch Traps (`langchain.md`)

*These specifically probe whether the retriever is fooled by the filename metadata rather than the actual chunk content.*

| # | Question | Expected Behavior |
|---|---|---|
| F1 | According to the corpus, what are LangChain's core abstractions (Chains, Agents, Tools, Memory)? | 🚫 Not present — `langchain.md` contains no LangChain-framework content |
| F2 | Summarize what `langchain.md` is actually about. | ✅ Should correctly identify it as Mintlify `docs.json` navigation documentation (groups/tabs/anchors/dropdowns/products/versions), explicitly noting the mismatch with the filename if surfaced |
| F3 | Does the corpus contain any information about LangChain's `LCEL` (LangChain Expression Language)? | 🚫 No — nothing in any document discusses LCEL |
| F4 | What does the `drilldown` interaction property control, per the file named `langchain.md`? | ✅ Whether expanding a navigation group auto-navigates to its first page (`true`), only expands/collapses without navigating (`false`), or defers to the theme default (unset) |
| F5 | Which vector store integrations does LangChain support, based on the uploaded documentation? | 🚫 Not present anywhere in the corpus |

---

## G. Terminology Disambiguation (same string, different meaning across chunks)

| # | Question | Expected Answer |
|---|---|---|
| G1 | The word "Path" shows up as both a benchmark name and a filesystem concept in this corpus — disambiguate both uses. | ✅ `[CMVF]`: PathVQA, a pathology-imaging VQA dataset. `[langchain.md]`: literal page-path strings in navigation arrays |
| G2 | "MCP" appears in two documents — are they describing the same system? | ✅ No. `[MathModDB]` describes a purpose-built server exposing SPARQL + Steiner-tree schema retrieval over MathModDB; `[langchain.md]` only links out to an unrelated "Admin MCP server" doc page with no further detail given |
| G3 | "RE" is used as an abbreviation in the CMVF paper's tables — what does it stand for, and could it be confused with anything else in the corpus? | ✅ REVOLVE (a baseline rewriter). Could be confused with "RE" as in Real Estate/REIT abbreviations used in the 360 ONE factsheet's fund portfolios (e.g., "Realty" sector, "REIT/InvIT Instruments") — different documents, unrelated meanings |
| G4 | What does "CT" refer to in this corpus? | ✅ Only appears in `[CMVF]` as SLAKE-CT, a computed-tomography medical VQA benchmark — not used elsewhere in the corpus |
| G5 | The term "model" is used throughout this corpus — give three different senses it takes on across three different documents. | ✅ e.g. `[MathModDB]`: a formalized mathematical model (e.g., Stokes-Darcy); `[CMVF]`: a vision-language model (VLM) being prompted/evaluated; `[360ONE]`: a mutual fund "scheme" (informally) — the point is the system should not conflate these senses when answering |

---

## H. Ambiguous / Underspecified Queries

*Good behavior: ask for clarification, or explicitly enumerate all matches rather than picking one arbitrarily.*

| # | Question | Why it's ambiguous | Reasonable Answer |
|---|---|---|---|
| H1 | What is Mehta's role at 360 ONE? | Only "Viral Mehta" is named; role varies by fund (fund manager vs. co-fund manager) | Should surface Viral Mehta across all funds and note the role differs by scheme |
| H2 | What is CMVF's accuracy on RAD? | RAD (VQA-RAD) accuracy differs per target VLM | Should give all four: Qwen3.5-4B 63.4, Qwen2.5-VL-7B 51.0, LLaVA-1.6-7B 48.2, Phi-3.5-vision 46.5 |
| H3 | What's the expense ratio of the 360 ONE debt fund? | Multiple debt funds exist (Dynamic Bond, Liquid, Overnight) | Should list all three separately rather than guessing which one was meant |
| H4 | What percentage does Onesource Specialty Pharma Limited hold in "the" 360 ONE fund? | It appears in at least 4 different fund portfolios at different weights | Should list per-fund: Focused Fund 1.93%, Flexicap 1.73%, Balanced Hybrid 0.50%, Multi Asset Allocation 0.45% |
| H5 | What tranche of Tesla's CEO award vests first? | "First" could mean lowest market-cap milestone or earliest chronological vesting, which depend on when milestones are achieved (not fixed dates) | Should explain vesting depends on achievement timing, not a fixed schedule, and describe the tranche structure rather than naming one number confidently |

---

## I. Needle-in-a-Haystack / Fine-Print Detail

| # | Question | Source | Expected Answer |
|---|---|---|---|
| I1 | What exact p-value does the CMVF paper report for the McNemar test on Qwen2.5-VL/TextVQA? | `[CMVF]` | ✅ p = 0.015 |
| I2 | How many distinct "Vedanta" group entities appear as separate line items in the 360 ONE Flexicap Fund's portfolio, and what are their individual weights? | `[360ONE]` | ✅ Three: Vedanta Aluminium Metal Limited (1.11%), Vedanta Iron And Steel Limited (0.09%), Vedanta Oil and Gas Ltd (0.08%) |
| I3 | What was the discount applied to Tesla's SpaceX equity investment for lack of marketability, and when do the underlying restrictions expire? | `[TSLA10Q]` | ✅ $238 million discount; restrictions expire September 2026 (separate IPO-related sales restrictions expire December 2026) |
| I4 | What offset amount per share applies when Tesla's CEO's 2025 Performance Award shares vest? | `[TSLA10Q]` | ✅ $334.09 per share |
| I5 | What similarity threshold (θ) was used for both entity-label and property-label fusion in the LegalKG pipeline? | `[LegalKG]` | ✅ 0.7 for both (θ_E = 0.7, θ_P = 0.7) |
| I6 | How many inconsistent relation signatures did the qualitative check find in the OpenAI-fused graph vs. the Mistral-fused graph? | `[LegalKG]` | ✅ OpenAI: 52; Mistral: 39 |

---

## J. Date / Version Precision Questions

*Tests whether the system tracks which of multiple as-of dates within one document a figure belongs to.*

| # | Question | Source | Expected Answer |
|---|---|---|---|
| J1 | Tesla's 10-Q reports share counts as of two different dates — what are those dates and figures? | `[TSLA10Q]` | ✅ 3,949,547,394 shares outstanding as of July 16, 2026 (cover page); 3,949 million shares issued and outstanding as of June 30, 2026 (balance sheet) |
| J2 | What was the INR/USD exchange rate at month-end for February 2026 specifically (not January, not March)? | `[360ONE]` | ✅ 91.0 |
| J3 | As of what specific date does the 360 ONE factsheet report each fund's portfolio holdings? | `[360ONE]` | ✅ June 30, 2026 (portfolios), while the factsheet itself is titled "July 2026" |
| J4 | When was the 2025 CEO Interim Award forfeited, and under what named event? | `[TSLA10Q]` | ✅ April 21, 2026, upon the Board's determination that a "Tornetta Decision Event" had occurred (96 million shares forfeited) |
| J5 | What two dates bound the export-control suspension and restoration of Anthropic's Fable/Mythos models mentioned anywhere in this corpus? | N/A | 🚫 Not in this corpus at all — that's outside knowledge, not something in any of the six uploaded documents (good negative control for date-precision questions) |

---

## K. Yes/No & Verification Questions

| # | Question | Source | Expected Answer |
|---|---|---|---|
| K1 | Does the CMVF paper evaluate GPT-4 as one of its target VLMs? | `[CMVF]` | ✅ No — targets are Qwen3.5-4B, Qwen2.5-VL-7B, LLaVA-1.6-Mistral-7B, and Phi-3.5-vision |
| K2 | Is the 360 ONE Overnight Fund rated by CRISIL? | `[360ONE]` | ✅ No — it carries an A1+ mfs rating from ICRA, not CRISIL |
| K3 | Did Tesla report a net loss for the quarter ended June 30, 2026? | `[TSLA10Q]` | ✅ No — net income was $1,128 million ($1,114 million attributable to common stockholders) |
| K4 | Does the LegalKG paper use any formal SHACL validation in the pipeline described? | `[LegalKG]` | ✅ No — SHACL is mentioned only as future work, not implemented in the described pipeline |
| K5 | Is MathModDB built on the same underlying technology stack as Wikidata? | `[MathModDB]` | ✅ Yes — it's built on Wikibase, the same open-source infrastructure underlying Wikidata |
| K6 | Does `langchain.md` describe how to configure retrieval-augmented generation pipelines? | `[langchain.md]` | ✅ No — it exclusively covers documentation-site navigation configuration (`docs.json`) |

---

## L. Multi-Fact Synthesis / Summarization

*Requires pulling together several facts from one document into a coherent answer, not single-cell lookup.*

| # | Question | Source |
|---|---|---|
| L1 | Summarize the main drivers behind the change in Tesla's SG&A expense in Q2 2026 vs. Q2 2025, including dollar figures for each driver. | `[TSLA10Q]` — expect stock-based comp (+$283M, mostly CEO award), employee/labor costs (+$134M), litigation-related operating expenses (+$109M), facilities (+$68M) |
| L2 | Explain CMVF's two-stage visual diagnostic channel — what happens in Stage 1 vs. Stage 2, and why is the prediction withheld in Stage 1? | `[CMVF]` — Stage 1: question-aware visual description without label access (prevents answer-conditioned rationalization); Stage 2: aggregates descriptions + error triples into reusable blind-spot patterns |
| L3 | Describe the three tools exposed by the MathModDB MCP server and the recommended agent workflow order. | `[MathModDB]` — Explore_Ontology (schema-first, must be called before SPARQL), SPARQL_Query (single query, 100-result cap), Batch_SPARQL_Query (named dictionary of queries) |
| L4 | Summarize the two-stage LegalKG pipeline from raw legal text to the final RDF knowledge graph. | `[LegalKG]` — ontology engineering (open extraction on stratified sample → embedding fusion → property induction) then KG construction (signature-guided closed extraction over full corpus → fusion → RML-based RDF lifting) |
| L5 | Summarize 360 ONE's stated market outlook for FY2026-27, including their sector positioning bias. | `[360ONE]` — earnings-driven rather than valuation-rerating market, reviving FPI inflows on rupee stability/US trade deal/West Asia resolution, growth engine shifting to private consumption, preference for domestically-focused over globally-dependent sectors |

---

### Notes for grading
- **Sections A–D, I–L** expect grounded, specific answers — score on factual accuracy and correct source attribution.
- **Section E** and **F1/F3/F5** are pass/fail on refusal-or-hedge behavior — any confident, specific fabricated answer is a failure regardless of how plausible it sounds.
- **Sections G–H** are partial-credit: reward systems that disambiguate/enumerate rather than silently picking one interpretation.
- **J5** is a deliberate zero-relevance control question — a good system should recognize the corpus has no bearing on it at all, not stretch a tangential document to answer it.

Happy to also export this as CSV/JSON (better for scripted grading loops) if that's more useful than markdown.
