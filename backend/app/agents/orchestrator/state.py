"""`LeadWorkflowState` and the closed vocabularies the graph routes on.

STATE IS PER-INVOCATION, NOT A SOURCE OF TRUTH (spec section 16)
----------------------------------------------------------------
One inbound message == one fresh graph invocation. Nothing is checkpointed in
LangGraph's own store, and there is no long-lived stateful graph process.
Continuity across turns comes from re-reading PostgreSQL (the lead, the
customer, the conversation) at the start of every run -- exactly the same
place a human agent or the voice channel would read it from. That keeps
PostgreSQL the single source of truth and makes the orchestrator trivially
horizontally scalable and crash-safe: a lost invocation loses nothing but one
reply.
"""

from enum import StrEnum
from typing import Any, TypedDict
from uuid import UUID


class Intent(StrEnum):
    """What the latest inbound message is trying to do. Classified by the AI
    node; used by the decision table only for the cases rules cannot settle."""

    GENERAL_INQUIRY = "general_inquiry"
    PROVIDE_QUALIFICATION_INFO = "provide_qualification_info"
    CALL_NOW = "call_now"
    CALL_LATER = "call_later"
    NOT_INTERESTED = "not_interested"
    PROPERTY_QUESTION = "property_question"
    VISIT_REQUEST = "visit_request"
    HUMAN_REQUEST = "human_request"
    OTHER = "other"


class NextAction(StrEnum):
    """The spec's action vocabulary. Every graph run ends in exactly one."""

    ASK_INFORMATION = "ASK_INFORMATION"
    CALL_NOW = "CALL_NOW"
    CALL_LATER = "CALL_LATER"
    SEND_PROPERTY_DETAILS = "SEND_PROPERTY_DETAILS"
    SCHEDULE_VISIT = "SCHEDULE_VISIT"
    FOLLOW_UP = "FOLLOW_UP"
    TRANSFER_HUMAN = "TRANSFER_HUMAN"
    MARK_NOT_INTERESTED = "MARK_NOT_INTERESTED"
    WAIT = "WAIT"


# The minimum a lead must have before it is worth a sales call.
QUALIFICATION_FIELDS: tuple[str, ...] = ("name", "budget", "interest", "call_availability")

# Below this, the AI's own classification is not trusted and the conversation
# goes to a human instead of being acted on (spec section 12).
MIN_ACTIONABLE_CONFIDENCE = 0.55


class LeadWorkflowState(TypedDict, total=False):
    """State threaded through the LangGraph run for one inbound message.

    `total=False` because LangGraph nodes return partial updates that are
    merged into the accumulated state; only the identity fields are populated
    by the caller.

    Adaptations vs. the spec's state model, all additive -- every field the
    spec names is present with the spec's meaning:

      visit_preference  the spec models the caller's *call* timing preference
                        but its action list also contains SCHEDULE_VISIT,
                        which needs the same free-text timing hint to be
                        actionable.
      context           scratch space for what the AI node extracted and what
                        the CRM read back, so downstream nodes do not re-query.
                        Never persisted; see the module docstring.
      result            what the executor node actually did, returned to the
                        caller for logging. Not an input to any decision.
    """

    # --- identity (supplied by the caller, never mutated by the graph) ---
    tenant_id: UUID
    contact_id: UUID
    lead_id: UUID | None
    conversation_id: UUID | None

    # --- input ---
    latest_message: str | None

    # --- understanding ---
    intent: str | None
    qualification: dict[str, Any]
    call_preference: str | None
    visit_preference: str | None
    confidence: float | None

    # --- decision + outcome ---
    next_action: str | None
    error: str | None
    context: dict[str, Any]
    result: dict[str, Any]


def new_state(
    *,
    tenant_id: UUID,
    contact_id: UUID,
    lead_id: UUID | None = None,
    conversation_id: UUID | None = None,
    latest_message: str | None = None,
) -> LeadWorkflowState:
    """Build the initial state for one inbound message. Everything the graph
    derives starts empty on purpose -- see the module docstring."""
    return LeadWorkflowState(
        tenant_id=tenant_id,
        contact_id=contact_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        latest_message=latest_message,
        intent=None,
        qualification={},
        call_preference=None,
        visit_preference=None,
        confidence=None,
        next_action=None,
        error=None,
        context={},
        result={},
    )
