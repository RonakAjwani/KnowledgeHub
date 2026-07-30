# Document Parsing And Complex PDFs

Research on handling research papers and financial documents - tables, charts, diagrams, formulae. Added 2026-07-27 after the pipeline contract was drafted; it **amends** [[Retrieval Pipeline Contract]] §3.

## Honest answer: the drafted system would not handle these

Three gaps, all fixable, all small:

1. **No parser specified.** The contract said "Parse -> `Block[]`" and implicitly assumed text. A naive `pypdf` extraction flattens a table into a stream of numbers with no row/column association - worse than useless, because it *looks* like successful extraction.
2. **No `chunk_type`.** A table would go through the prose splitter and be shredded mid-row, orphaning cells from their headers.
3. **No provision for derived content.** Charts and diagrams contain no extractable text, so they would simply be invisible to retrieval.

## Unlimited-OCR (Baidu, June 2026) - how it works

The model Ronak asked about. Open source, **3B params**, top score on OmniDocBench v1.6 at **93.92**.

Two things make it notable:

**1 · End-to-end, single forward pass.** Traditional parsing is multi-stage - layout detection -> region classification -> per-region OCR / table recognition / formula recognition -> reading-order reconstruction. Errors compound across stages. Unlimited-OCR emits text, tables, formulas *and* reading order in one pass, image -> markdown. Baidu's related **Qianfan-OCR** takes the same direct image-to-markdown approach.

**2 · R-SWA (Reference Sliding Window Attention) - the actual innovation.** Earlier end-to-end parsers hit a ceiling on *output* length, not input. Parsing a 50-page PDF to markdown means generating tens of thousands of output tokens, and KV cache grows with every one - so you OOM, or you parse page-by-page and lose cross-page reading order. R-SWA keeps the **KV cache flat** regardless of output length, so the model can emit page after page continuously. That is what "unlimited" refers to.

## ...and why it can't be used here

**3B params against a 512 MB CPU-only host.** Int4 is ~1.5-2 GB; fp16 ~6 GB. Even if it fit, 0.1 vCPU would take minutes per page. The published serving path is a vLLM recipe - i.e. GPU.

The same rules out every other frontier parser: **MinerU 2.5, DeepSeek-OCR 2, dots.ocr, Marker, Granite-Docling** all want a modern GPU. Docling specifically "runs a single large vision-language model that reads each page as an image and generates the table structure one token at a time" - unusable on this CPU budget.

**Right technology, wrong deployment envelope.** Worth naming in the README as considered-and-rejected-with-reason; that is a stronger line than silently using a weak parser.

## Decision: tiered parsing with conditional VLM escalation

Same philosophy as conditional reranking - do the cheap thing by default, escalate only what needs it.

| Tier | What | When |
|---|---|---|
| **1 · Local text** | `pypdf` / `pdfplumber` | Always. Handles prose-only pages perfectly at near-zero cost. |
| **2 · VLM escalation** | Render page -> image -> **Gemini via the existing LLM adapter** -> markdown | Only for pages flagged complex |
| **3 · Cloud parser** | LlamaParse, scoped to flagged pages via `target_pages` | Optional upgrade for the deployed path - see below |

**Tier 2 is the key move.** Gemini Flash is already in the stack, already multimodal, already on a free tier - so VLM-grade table and formula extraction costs **no new dependency, no GPU, and no RAM**. It runs at ingest, which is already async, and it's a one-time per-document cost rather than per-query. It also inherits provider-swappability from the adapter.

**Complexity detection is a cheap local heuristic, not a model.** `pdfplumber` exposes `page.find_tables()` and image/figure objects - enough to decide whether a page needs escalation. In a typical research paper most pages are prose and only a handful escalate.

### What actually bounds Tier 2: tokens per minute

Verified 2026-07-27 ([[Confirmed Infrastructure Constraints]]). The limits that *sound* binding aren't: 3,600 images per request and a 100 MB payload are orders of magnitude beyond anything here. **The binding constraint is TPM**, because there is no separate image allowance - page images draw on the same token budget as text and drain it far faster. One tiled page can cost several hundred to a couple of thousand tokens before the prompt is read, against a ~250K-1M TPM ceiling.

Three design consequences:

**1 · Pace the escalation queue on tokens, not requests.** [[NotebookRAG Reference Project]]'s `_pace()` limiter spaces calls by RPM. For this path that measures the wrong quantity entirely - ten prose pages and ten dense scanned tables are ten requests either way and wildly different token loads. The VLM path needs a token-budget limiter; the RPM limiter stays where it belongs, on Cohere's 10 rpm.

**2 · Render DPI is a real tuning constant, not an implementation detail.** It is the direct lever on token cost: ≤ 384 px in both dimensions is a flat 258 tokens, above that the image tiles and cost climbs. Too low and the table is unreadable, which defeats the entire escalation; too high and one document exhausts the minute's budget. Cannot be chosen without sample pages - it belongs with the other corpus-dependent constants in [[KnowledgeHub Index]].

**3 · Cap escalated pages per document, and surface the cap.** A 200-page scanned PDF where every page escalates would exhaust TPM and stall ingest. The cap is not optional - but per invariant I1 it must be **visible**: hitting it emits a degradation and shows in the document's extraction-quality signal, so the user learns that pages 40+ were parsed by Tier 1 only. Silently truncating escalation would produce exactly the "fails convincingly" outcome this note argues against.

Async ingest is the saving grace: escalation is queued and spread over time rather than fired in a burst, so TPM pressure becomes a throughput question rather than a failure.

### Tier 3 economics - `target_pages` is what makes it viable

Verified 2026-07-27. LlamaParse bills **credits per page**, and the rate depends on tier: Fast 1 · Cost-effective 3 · Agentic 10 · Agentic Plus 45, +3/page for layout extraction, at $1.25 per 1,000 credits.

**The 1-credit tier is a trap.** Fast emits spatial text only, no markdown - it recovers no table structure, which is the entire reason Tier 3 exists. Cost-effective at 3 credits/page is the realistic floor.

**`target_pages` is the finding that matters.** It bills only the pages requested, which composes exactly with the architecture already chosen here: the local heuristic has *already* identified which pages are complex, so Tier 3 never parses a whole document. Six complex pages out of fifty costs 18 credits at Cost-effective or 60 at Agentic - against 150 or 500 for the full file. The tiered design was chosen for RAM and latency reasons; it turns out to cut the cloud-parser bill by the same ratio.

Parsed files also cache for 48 h, so re-ingesting the same document during development is free.

⚠️ The 10,000 free credits/month figure remains unconfirmed - the pricing page documents paid rates only. It barely matters at these volumes: 10,000 credits is $12.50.

## Chunk contract amendments

```
Chunk (additions)
  chunk_type   prose | table | figure | formula
  is_derived   bool     content synthesised, not extracted from the document
```

**Tables are atomic.** A table chunk bypasses the prose splitter entirely. If it exceeds the chunk ceiling, split **by row with the header row repeated in every split** - never mid-row.

**Tables need a lead line before embedding** - raw markdown retrieves badly as prose. But that lead line should be **the author's own words, not a model's.** See § Cross-reference resolution below; synthesis is the fallback, not the default.

**BM25 is the hero for tables**, and this validates the hybrid decision. Tables are dense with exact identifiers, labels and numbers - precisely where embeddings fail and the sparse branch wins. Worth demonstrating explicitly: a table lookup is the cleanest possible illustration of why hybrid beats dense-only.

## Cross-reference resolution - prefer the author's explanation over a model's

**Ronak's call, and it supersedes the "synthesise a description" default.** Research papers and financial reports almost always explain their tables and figures in the surrounding prose. Fetch *that* rather than having a model invent one.

This is an established document-AI technique. A cross-reference has **two sides**: the **target** (where the object lives - the caption) and the **source** (body-text mentions pointing at it). Resolving them links a figure to every place the document discusses it. Supporting data point: hierarchy-aware chunking with contextualisation raised equivalence scores from **69.2% -> 84.0%** on SEC documents - the exact document class in question.

### Why it beats synthesis - four reasons, the last one decisive

1. **It's real document text**, so it has real char offsets. Citations resolve natively and the source-pane highlight works with no special case.
2. **It's more accurate.** An author explaining their own table beats a VLM inferring from pixels.
3. **It's free.** No LLM call, no Gemini quota, no latency.
4. **⚠️ A VLM reading values off a chart is a hallucination dressed as extraction.** If a user asks "what was Q3 revenue" and the number comes from a model squinting at a bar chart's axis, that is a *fabricated figure with a citation attached* - the single worst failure mode for a system whose entire pitch is verifiable grounding. Retrieving the author's sentence "Q3 revenue reached $8M (Figure 3)" is real evidence. This alone justifies the approach.

Ronak's own framing adds a fifth: if a table was split, a model summarising it is describing a **partial** table while sounding authoritative about the whole.

### The critical distinction

This replaces VLM **summarisation**, not VLM **extraction**. Two different jobs:

| Job | Who does it |
|---|---|
| Pixels -> structured markdown table | **VLM** (tier 2) - still required for scanned/complex tables |
| Making that table findable by retrieval | **The author's narrative** - no model |

### Implementation: a regex, not a model

Captions carry labels - `Table 2`, `Tab. 2`, `TABLE II`, `Figure 3`, `Fig. 3`. Extract the label from the caption (target), then scan the document for other mentions of that label (sources) and collect the containing sentences.

Deterministic, local, near-zero cost. Solving this with a regex where the obvious move is an LLM is exactly the kind of judgment the assessment is looking for.

### Escalation ladder

1. **Caption** - always attach, structured and reliable
2. **Referencing narrative** - gathered via cross-reference resolution
3. **Table content itself** - markdown, for exact cell lookups (BM25's job)
4. **VLM synthesis** - **fallback only**, when 1 and 2 are absent or too thin. Marked `is_derived`.

### Where it doesn't work - be honest

Bare appendix tables, raw financial statements, and data dumps often have no surrounding narrative. Terse captions (`Table 2: Results`) carry little retrieval signal. Narrative also states *conclusions*, not *contents* - "Table 3 shows our method outperforms baselines" will not answer "what was the F1 score for BERT on SQuAD?" That still needs the cell values, which is why rung 3 exists.

### Schema impact

The gathered narrative goes into the chunk's embedded text (contextual-retrieval pattern - the chunk's identity remains the table's own span). Additionally:

```
Chunk (addition)
  related_spans  list[(start, end)]   where the document itself discusses this object
```

**UX payoff:** clicking a table citation highlights the table *and* scrolls to the paragraph explaining it. Two pieces of evidence, both real, both verifiable - a genuinely NotebookLM-class touch that costs one schema field.

### Consequence: the derived-content problem shrinks

Most figures and tables now carry **real** citations. `is_derived` becomes the exception rather than the norm, which makes the special-casing below a narrow edge path instead of a main one.

## Derived content and the offset contract - the sharp problem

A VLM description of a chart (*"Figure 3 shows revenue rising from $2M to $8M"*) **does not exist in the source document.** So `char_start`/`char_end` into `normalized_text` - the entire G5 source-pane highlight mechanism - has nothing to point at.

**Resolution: insert derived blocks into `normalized_text` at the figure's position, explicitly marked as derived.**

Offsets stay valid, one citation mechanism is preserved, and the source pane (which renders `normalized_text` anyway, per [[Retrieval Pipeline Contract]] §1) shows the description inline where the figure sits.

Two properties this must have:

- **Visually distinguished in the UI.** A citation resolving to derived content gets an "AI-described figure" badge. The user must always be able to tell *"this sentence is in the document"* from *"this sentence is a model's description of a picture in the document."* Blurring that would undermine the whole verification story.
- **Never silently mixed.** Derived spans are marked in `normalized_text` itself, not just in chunk metadata.

**This retroactively validates the §1 decision.** Had the source pane rendered the original PDF instead of normalized text, derived content would have had nowhere to live.

## Formulae - set expectations honestly

Formula *retrieval* is weak in any text-based RAG. LaTeX embeds poorly and BM25 tokenises symbols badly. Mitigation is to keep surrounding prose in the parent window, so a formula is found via its explanation rather than its symbols. Do not over-engineer this; note the limitation and move on.

## Graceful degradation

"Gracefully" is the real requirement, and it means **visibly**, per invariant I1:

- Persist an extraction-quality signal per document (pages escalated, tables recovered, extraction confidence)
- Surface it in the document manager, so the user knows a scanned or malformed PDF parsed imperfectly
- If a table could not be recovered structurally, **say so** rather than indexing a flattened row of numbers as if it were prose

A parser that fails loudly is more useful than one that fails convincingly.

[[KnowledgeHub Index]] · [[Retrieval Pipeline Contract]] · [[RAG Guardrails Design]] · [[Confirmed Infrastructure Constraints]]
