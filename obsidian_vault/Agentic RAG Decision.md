# Agentic RAG Decision

**Decision: bounded control loop via LangGraph, not an open-ended agent.**

## The question

Does the KnowledgeHub brief want agentic RAG or a fixed retrieve-then-generate pipeline?

## Evidence against going fully agentic

- The brief never mentions agents, tools, multi-step reasoning, or multi-hop questions. It asks for answers from retrieved context, conversation memory, and citations.
- The bonus list is streaming / auth / hybrid search + re-ranking / CI. Agentic is not on it.
- Field consensus: build the pipeline first, add agent orchestration only with evidence that single-pass retrieval is failing on real queries.
- Costs: higher tail latency, higher token spend, more failure modes, higher output variance without strong eval gates.
- **The stated UX bar is NotebookLM — i.e. fast.** Unbounded grading/retry loops are the direct enemy of that. Each loop is another serial LLM round trip.

## Evidence against a purely fixed pipeline

A hardcoded `retrieve → generate` reads as unsophisticated for a mid/senior assessment, and genuinely fails on turns that need no retrieval or where the first retrieval is bad.

## Resolution — bounded CRAG

A LangGraph state graph with conditional edges and a **hard cap of one corrective retry**:

```
route ──┬─► answer_from_history      (no retrieval needed)
        ├─► clarify                  (underspecified)
        └─► retrieve → grade ──┬─► generate        (confidence OK)
                               ├─► rewrite → retrieve → generate   (ONE retry only)
                               └─► abstain         (retry also failed)
```

Real conditional branching and self-correction, bounded worst case (2 retrievals max, never N).

## Why this is a small delta, not a rewrite

[[NotebookRAG Reference Project]] already has the hard parts under different names:
- **Confidence scoring → the grader node**
- **Structured abstention → the terminal abstain node**
- **RRF + rerank → the retrieve node**

Wiring these into LangGraph makes the control flow explicit, adds the corrective-retry edge, and makes it drawable in the README.

## The latency trick

Most LangGraph CRAG tutorials make **one LLM grading call per retrieved document** — 5 documents = 5 extra serial LLM calls. Don't.

**Derive the grade from the reranker scores already computed.** The cross-encoder produces a calibrated relevance score as a side effect of reranking. Threshold on that. Zero extra LLM calls in the grading node.

## The actual reason to use LangGraph

Not autonomy. Two concrete things:

1. **Checkpointing / state persistence** — needed anyway for multi-turn conversation state.
2. **Node-level event streaming** — drives the NotebookLM-style progressive UI ("searching 12 sources…" → "found 5" → answer streams). The graph structure buys the observability that makes it *feel* fast.

## Latency budget (bounded loop)

| Stage | Cost |
|---|---|
| route (run parallel with retrieval) | ~0 added |
| dense + sparse + server-side fusion | ~50ms |
| cross-encoder rerank (doubles as grade) | ~200ms |
| generation TTFT on Groq/Cerebras | ~500ms |
| **worst case + one retry** | **+~800ms** |

[[KnowledgeHub Index]] · [[Multi Turn Memory Architecture]] · [[KnowledgeHub Stack Decisions]]
