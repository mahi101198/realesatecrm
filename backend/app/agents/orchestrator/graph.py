"""The LangGraph `StateGraph` and the one public entry point.

SHAPE
    START -> understand_intent -> check_qualification -> determine_next_action
          -> (conditional) -> one execute_* node -> END

WHY THE GRAPH IS BUILT PER INVOCATION
    Nodes need the caller's `AsyncSession` -- the very same session the
    webhook handler is using, so AI-driven writes land in that request's
    transaction and roll back with it. Rather than smuggling a live DB
    connection through LangGraph's serializable state (or through
    `configurable`, whose contents outlive the call), the session is closed
    over with `functools.partial` at build time. Graph construction is pure
    Python and costs microseconds; a shared connection would cost correctness.

NO CHECKPOINTER, ON PURPOSE (spec section 16, "Data Ownership")
    No `MemorySaver`, no thread ids, no persisted graph state. One inbound
    message == one cold graph run that re-reads the lead, the contact and the
    conversation from PostgreSQL. PostgreSQL stays the source of truth,
    multiple app instances need no shared agent memory, and a crashed run
    loses one reply rather than a conversation.
"""

import functools
import logging
from typing import Any

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.orchestrator import nodes
from app.agents.orchestrator.state import LeadWorkflowState, NextAction

logger = logging.getLogger(__name__)


def build_lead_workflow(session: AsyncSession) -> Any:
    """Compile the lead workflow graph bound to `session`."""
    graph = StateGraph(LeadWorkflowState)

    graph.add_node(
        "understand_intent", functools.partial(nodes.understand_intent, session=session)
    )
    graph.add_node(
        "check_qualification", functools.partial(nodes.check_qualification, session=session)
    )
    graph.add_node("determine_next_action", nodes.determine_next_action)
    for node_name, handler in nodes.EXECUTORS.items():
        graph.add_node(node_name, functools.partial(handler, session=session))

    graph.add_edge(START, "understand_intent")
    graph.add_edge("understand_intent", "check_qualification")
    graph.add_edge("check_qualification", "determine_next_action")
    graph.add_conditional_edges(
        "determine_next_action",
        nodes.route_next_action,
        {name: name for name in nodes.EXECUTORS},
    )
    for node_name in nodes.EXECUTORS:
        graph.add_edge(node_name, END)

    return graph.compile()


async def run_lead_workflow(
    session: AsyncSession, state: LeadWorkflowState
) -> LeadWorkflowState:
    """Run one full turn. Never raises.

    Returns the final state. `state["error"]` is set when anything went wrong;
    `state["result"]` describes what was actually done. Callers are expected
    to log, not to branch -- there is nothing useful a webhook handler can do
    about a failed AI turn beyond returning 200 to Meta.
    """
    try:
        final = await build_lead_workflow(session).ainvoke(state)
    except Exception as exc:  # noqa: BLE001 -- last line of defence
        logger.exception(f"Lead workflow crashed: {exc!s}")
        return {
            **state,
            "next_action": NextAction.TRANSFER_HUMAN.value,
            "error": f"workflow_crashed: {exc!s}",
        }
    return final  # type: ignore[no-any-return]
