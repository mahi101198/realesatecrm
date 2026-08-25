"""Lead workflow orchestrator -- the LangGraph state machine that decides
what to do about an inbound message.

Public surface is deliberately two names:

    new_state(...)                build the per-invocation state
    run_lead_workflow(session, s) run one turn; never raises

Everything else (nodes, the CRM gateway, the decision table) is internal but
importable for tests, which pin the deterministic pieces directly.
"""

from app.agents.orchestrator.graph import build_lead_workflow, run_lead_workflow
from app.agents.orchestrator.state import (
    MIN_ACTIONABLE_CONFIDENCE,
    QUALIFICATION_FIELDS,
    Intent,
    LeadWorkflowState,
    NextAction,
    new_state,
)

__all__ = [
    "MIN_ACTIONABLE_CONFIDENCE",
    "QUALIFICATION_FIELDS",
    "Intent",
    "LeadWorkflowState",
    "NextAction",
    "build_lead_workflow",
    "new_state",
    "run_lead_workflow",
]
