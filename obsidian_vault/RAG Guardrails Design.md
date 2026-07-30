# RAG Guardrails Design

Four **distinct** layers, usually conflated. Each catches a different failure. Layered guardrails are reported to cut hallucination rates 71-89% vs unguarded.

## Layer 1 - Input: topical / scope gate (pre-retrieval)

Reject out-of-scope queries before spending retrieval + generation. Threshold on off-topic probability; deployed values typically sit **0.4-0.6**. Low threshold catches more off-topic but over-refuses.

Already exists as the `route` node in [[Agentic RAG Decision]] - no new component.

⚠️ **Over-refusal is itself a documented failure mode** - "direct or indirect refusal on benign-intent queries." A gate tuned too tight bounces legitimate questions, which is worse UX than tolerating a slightly off-topic one. Tune loose, let Layer 2 catch the rest.

## Layer 2 - Retrieval: the relevance floor (post-retrieval, pre-generation)

If the best reranked chunk scores below threshold, **refuse before generating**. Highest-value guardrail in the stack: the model never sees weak context it might pad out into a confident-sounding answer.

One mechanism, three jobs: guardrail + CRAG grader + abstention trigger. Reuse [[NotebookRAG Reference Project]]'s `retrieval_confidence` (`0.6*max + 0.4*mean`).

Evidence this works: NotebookRAG's hybrid config abstained **7/7** on unanswerable traps where dense-only and sparse-only abstained 0/7.

## Layer 3 - Prompt: retrieved content is DATA, not instructions

⚠️ **This is the gap in NotebookRAG and the most important thing to add in KnowledgeHub.**

`generation/prompts.py:build_context()` interpolates raw chunk text directly into the user message - no delimiting, no escaping, no "this is data" framing. That was *fine* for NotebookRAG, whose corpus was Ronak's own Obsidian notes. **KnowledgeHub lets users upload arbitrary PDFs, which makes the corpus an untrusted input channel.**

### The attack

Indirect prompt injection: a document containing `Ignore previous instructions and...` hidden as white-on-white text, an HTML comment, or a data attribute - **invisible to a human reader, extracted verbatim by the chunking pipeline**, then inserted into the prompt.

Structurally hard to defend because the model treats retrieved text as instructions, it's invisible to defenses that inspect only the user turn, and retrieved content is implicitly trusted because it arrived via the system's own retrieval path.

2026 numbers: indirect injection ≈ **55% of prompt-injection attacks**; **62% of successful enterprise exploits** use indirect pathways.

### Mitigations (layered - none is complete alone)

1. Wrap retrieved content in clearly-marked delimited sections, with explicit framing that content inside is data and never instructions
2. **Escape the delimiter sequence inside chunk text** so content can't break out of its own wrapper
3. **Sanitise at ingest**: zero-width characters, white-on-white / zero-opacity text, HTML comments, PDF annotation and metadata layers
4. Optional: classifier pass over chunks at ingest to flag injection attempts
5. **Bound the blast radius** - assume injection eventually succeeds. This app naturally has a small one (no side-effecting tools, no outbound calls). That is an argument to *keep* it small: don't give the agent write tools.

### Why this matters for the assessment

The brief measures *problem-solving approach and ability to build complex applications*. Almost nobody building a "chat with your documents" demo notices that the documents are attacker-controlled. This is the strongest available differentiator and it costs very little to implement.

## Layer 4 - Output: verification (post-generation)

Carry over NotebookRAG's claim-level verification, with three fixes - see [[NotebookRAG Reference Project]] for detail:

1. Source text is truncated to 400 chars before the judge sees it -> false "unsupported" when evidence sits past the cutoff
2. Coverage denominator counts **all** claims including uncited discourse/filler ("Here's a summary:")
3. Both the verifier and the completeness judge run **in the request path** -> latency tax on every answer

## Layer 5 - The UX *is* a guardrail

Ronak's point, and it's the sharpest one: **clickable citations that scroll-and-highlight the exact source span let the user verify grounding in one click.**

Human-in-the-loop verification at **zero latency and zero LLM calls**, and it degrades gracefully - even if every automated check fails, the user can still see the receipt.

Hard dependency: chunks must persist `char_start` / `char_end` into the original file. NotebookRAG's `Chunk` schema has `section`, `page`, `chunk_index`, `char_count` - **but no offsets**. Must be designed at ingest; cannot be retrofitted.

[[KnowledgeHub Index]] · [[Agentic RAG Decision]] · [[KnowledgeHub Stack Decisions]]
