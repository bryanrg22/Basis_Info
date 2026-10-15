"""W1: every workflow node must be reachable from START.

Reproduced 2026-10-15 (REVIEW_AND_PLAN.md A.3 W1, Appendix 2): the compiled graph's
only edges are START -> load_study -> {error_handler | resource_extraction} -> END,
so analyze_rooms, reviewing_rooms, process_assets and complete are orphaned.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

EXPECTED_NODES = {
    "load_study",
    "resource_extraction",
    "analyze_rooms",
    "reviewing_rooms",
    "process_assets",
    "complete",
    "error_handler",
}


def _reachable_from_start(edges: list[tuple[str, str]]) -> set[str]:
    reachable = {"__start__"}
    frontier = ["__start__"]
    while frontier:
        node = frontier.pop()
        for source, target in edges:
            if source == node and target not in reachable:
                reachable.add(target)
                frontier.append(target)
    return reachable


@pytest.mark.regression
@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "W1: analyze_rooms, reviewing_rooms, process_assets and complete have no "
        "path from START (agentic/graph/workflow.py create_workflow); pauses are END"
    ),
)
def test_all_seven_nodes_reachable_from_start(workflow_module) -> None:
    graph = workflow_module.create_workflow(checkpointer=MemorySaver()).get_graph()
    missing = EXPECTED_NODES - set(graph.nodes)
    if missing:
        # Precondition, not the bug: fail outright instead of counting as xfail.
        pytest.fail(f"graph no longer declares {sorted(missing)}; update this test")
    edges = [(e.source, e.target) for e in graph.edges]
    unreachable = EXPECTED_NODES - _reachable_from_start(edges)
    assert not unreachable, f"unreachable from START: {sorted(unreachable)}"
