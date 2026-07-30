"""Graph wiring - bounded CRAG, one corrective retry, no autonomy.

LangGraph is here for two things it does well: **checkpointing** (needed for
multi-turn state anyway) and **node-level event streaming** (which drives the
progressive "searching 12 sources -> found 5" UI). It is emphatically *not* here
for autonomy. The control flow is fixed, the branches are conditional edges, and
the loop is bounded by a counter:

    route ─┬─ history ──────────────────────────► END
           ├─ refuse ───────────────────────────► END
           └─ rewrite -> retrieve -> rerank -> grade ─┬─ pass ──► generate ► END
                          ▲                        ├─ retry ─► retry_node ─┘
                          └────────────────────────┘         (attempt 0 -> 1)
                                                   └─ abstain ► END

Worst case is two retrievals, never N. Most agentic-RAG tutorials make one LLM
grading call *per retrieved document* - five documents, five extra serial calls -
and this graph makes zero, because the reranker's score already is the grade.

The checkpointer is in-memory and per-turn. Durable conversation state lives in
Postgres under the schema §7 already defines; a second LangGraph-managed store
would duplicate it and double the round trips on a 0.1 vCPU box.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.graph import nodes
from app.graph.nodes import Deps
from app.graph.state import Grade, QueryState, Route

logger = logging.getLogger(__name__)


def _bind(
    fn: Callable[[QueryState, Deps], Awaitable[dict[str, Any]]], deps: Deps
) -> Callable[[QueryState], Awaitable[dict[str, Any]]]:
    """Inject dependencies without making them global."""

    async def _node(state: QueryState) -> dict[str, Any]:
        return await fn(state, deps)

    _node.__name__ = fn.__name__
    return _node


def _route_branch(state: QueryState) -> str:
    route = state.get("route", Route.RETRIEVE)
    if route == Route.HISTORY:
        return "history"
    if route == Route.REFUSE:
        return "refuse"
    return "rewrite"


def _grade_branch(state: QueryState) -> str:
    """Where G2's verdict sends the turn.

    ``retry`` is only reachable at ``attempt == 0``; ``grade_node`` returns
    ``abstain`` rather than ``retry`` once the counter is spent, so the cap holds
    even if this function were called again.
    """
    grade = state.get("grade", Grade.ABSTAIN)
    if grade == Grade.PASS:
        return "generate"
    if grade == Grade.RETRY:
        return "retry"
    return "abstain"


def build_graph(deps: Deps | None = None, checkpointer: Any = None):
    """Compile the query graph.

    Node names are the SSE contract's node names. The client keys its progress UI
    on ``(node, attempt)``, so renaming one here breaks the frontend silently.
    """
    deps = deps or Deps.default()
    graph = StateGraph(QueryState)

    node_functions = {
        "route": nodes.route_node,
        "rewrite": nodes.rewrite_node,
        "retrieve": nodes.retrieve_node,
        "rerank": nodes.rerank_node,
        "grade": nodes.grade_node,
        "generate": nodes.generate_node,
        "retry": nodes.retry_node,
        "history": nodes.history_node,
        "refuse": nodes.refuse_node,
        "abstain": nodes.abstain_node,
    }
    for name, fn in node_functions.items():
        # LangGraph's add_node is overloaded across ten node protocols, and a
        # plain `async (state) -> dict` does not structurally match any of them
        # under mypy even though it is exactly what the runtime accepts. One
        # documented ignore here rather than ten scattered ones.
        graph.add_node(name, _bind(fn, deps))  # type: ignore[call-overload]

    graph.add_edge(START, "route")
    graph.add_conditional_edges(
        "route",
        _route_branch,
        {"rewrite": "rewrite", "history": "history", "refuse": "refuse"},
    )

    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("retrieve", "rerank")
    graph.add_edge("rerank", "grade")

    graph.add_conditional_edges(
        "grade",
        _grade_branch,
        {"generate": "generate", "retry": "retry", "abstain": "abstain"},
    )
    # The one cycle in the graph, and it can traverse exactly once: `retry`
    # increments the counter, and `grade` returns ABSTAIN rather than RETRY when
    # the counter is already 1 (I6).
    graph.add_edge("retry", "retrieve")

    for terminal in ("generate", "history", "refuse", "abstain"):
        graph.add_edge(terminal, END)

    return graph.compile(checkpointer=checkpointer)
