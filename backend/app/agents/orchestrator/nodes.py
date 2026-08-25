"""The graph's nodes: one AI node, one CRM node, one decision table, and the
executors that carry the decision out.

DECISION PRINCIPLE (spec): rules first, deterministic service, AI reasoning
only when required.

  understand_intent      AI. The ONLY node that talks to a model, and it does
                         so exactly once, with a JSON-schema-constrained
                         structured output -- never free-text parsing.
  check_qualification    Pure rules over CRM state + what the AI extracted.
                         Answers "what do we still not know about this lead".
  determine_next_action  A pure function of the state: a decision table that
                         consults the AI's classified intent only for the
                         cases the rules leave open. No I/O, no session, no
                         model. This is the piece the routing tests pin.
  execute_*              Thin adapters onto existing services and tools.

Every executor is individually exception-guarded. A failure records itself in
`state["error"]` and the run ends cleanly; it never propagates to the webhook
handler and never leaves CRM state half-written (each service call owns its
own transaction semantics inside the caller's session).
"""

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.handoff_service import SalesHandoffService
from app.agent.tools.call_tools import make_instant_call, schedule_call
from app.agents import llm
from app.agents.orchestrator import crm
from app.agents.orchestrator.state import (
    MIN_ACTIONABLE_CONFIDENCE,
    QUALIFICATION_FIELDS,
    Intent,
    LeadWorkflowState,
    NextAction,
)
from app.agents.whatsapp_agent import WhatsAppAgent
from app.appointments.schemas import AppointmentCreate
from app.appointments.service import AppointmentService
from app.events.model import EventType
from app.events.publisher import publish_event

logger = logging.getLogger(__name__)

# When the buyer says "call me later" without a parseable time, this is how
# far out the call job is queued. Short enough to stay warm, long enough not
# to feel like we ignored "later".
DEFAULT_CALLBACK_DELAY = timedelta(hours=2)
# How far out a generic FOLLOW_UP task is scheduled.
DEFAULT_FOLLOW_UP_DELAY = timedelta(hours=24)
HANDOFF_PRIORITY = 3

# Phrases that mean "a human needs to see this" regardless of what the model
# thought the intent was (spec section 12). Matched case-insensitively on word
# boundaries so "legalese" does not trip "legal".
HANDOFF_PATTERNS = (
    r"human",
    r"real person",
    r"agent",
    r"manager",
    r"supervisor",
    r"complain(t|ts|ing)?",
    r"refund",
    r"lawyer",
    r"legal",
    r"lawsuit",
    r"court",
    r"rera",
    r"fraud",
    r"cheat(ed|ing)?",
    r"negotiat(e|ion|ing)",
    r"discount",
    r"cancel my booking",
)
_HANDOFF_RE = re.compile(r"\b(" + "|".join(HANDOFF_PATTERNS) + r")\b", re.IGNORECASE)

# JSON Schema for the single structured AI call. `additionalProperties: false`
# and an exhaustive `required` list are mandatory for structured outputs;
# nullable fields use anyOf rather than a type array for the same reason.
_NULLABLE_STRING = {"anyOf": [{"type": "string"}, {"type": "null"}]}
_NULLABLE_NUMBER = {"anyOf": [{"type": "number"}, {"type": "null"}]}

INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "intent",
        "confidence",
        "summary",
        "requires_human",
        "human_reason",
        "name",
        "budget_min",
        "budget_max",
        "interest",
        "preferred_location",
        "call_availability",
        "call_available_at",
        "visit_preference",
    ],
    "properties": {
        "intent": {"type": "string", "enum": [i.value for i in Intent]},
        "confidence": {"type": "number"},
        "summary": {"type": "string"},
        "requires_human": {"type": "boolean"},
        "human_reason": _NULLABLE_STRING,
        "name": _NULLABLE_STRING,
        "budget_min": _NULLABLE_NUMBER,
        "budget_max": _NULLABLE_NUMBER,
        "interest": _NULLABLE_STRING,
        "preferred_location": _NULLABLE_STRING,
        "call_availability": _NULLABLE_STRING,
        "call_available_at": _NULLABLE_STRING,
        "visit_preference": _NULLABLE_STRING,
    },
}

INTENT_SYSTEM_PROMPT = """\
You classify inbound WhatsApp messages for a real-estate sales CRM and extract \
any lead-qualification facts the message contains.

intent -- pick exactly one:
  general_inquiry             greeting, small talk, or a broad "tell me more"
  provide_qualification_info  they are answering a question about themselves \
(name, budget, what they want, when to call)
  call_now                    they want to be called immediately
  call_later                  they want a call at some other stated time
  not_interested              they are declining or asking to stop
  property_question           a concrete question about inventory, price, \
availability, amenities or location
  visit_request               they want to visit a site or a show flat
  human_request               they explicitly ask for a person, or the message \
is a complaint, a negotiation, or a legal/regulatory query
  other                       none of the above

confidence -- 0.0 to 1.0, how sure you are of `intent`. Be honest: a short or \
ambiguous message deserves a low number.

requires_human -- true if a salesperson should take over now: complaints, \
price negotiation, legal or RERA questions, anger, or an explicit ask for a \
person. Put the reason in human_reason.

summary -- one sentence, third person, describing what this buyer wants and \
where the conversation stands. This is handed to a salesperson on transfer.

Extraction fields: fill only what the message actually states. Use null for \
anything not stated -- never guess, never carry over an assumption.
  budget_min / budget_max  absolute numbers in the message's own currency \
units (write 8000000 for "80 lakh", not 80).
  interest                 what they are looking for, in their words \
("3BHK near Whitefield").
  call_available_at        an ISO-8601 datetime ONLY if the message states a \
specific time you can resolve; otherwise null (keep the loose phrasing in \
call_availability).
"""


# ---------------------------------------------------------------------------
# Node 1 -- AI: classify + extract
# ---------------------------------------------------------------------------


async def understand_intent(
    state: LeadWorkflowState, *, session: AsyncSession
) -> dict[str, Any]:
    """Classify the latest message and extract qualification facts.

    On any model failure this returns an `error` rather than raising. The
    decision table treats that as "route to a human", which is the correct
    conservative behaviour: we could not understand the buyer, so a person
    should read the message.
    """
    message = (state.get("latest_message") or "").strip()
    if not message:
        return {"intent": Intent.OTHER.value, "confidence": 0.0, "context": {}}

    try:
        extracted = await llm.call_structured(
            system=INTENT_SYSTEM_PROMPT,
            user=message,
            schema=INTENT_SCHEMA,
        )
    except llm.LLMError as exc:
        logger.warning(f"AI intent classification unavailable: {exc!s}")
        return {"error": f"intent_classification_failed: {exc!s}", "confidence": 0.0}

    intent = str(extracted.get("intent") or Intent.OTHER.value)
    if intent not in {i.value for i in Intent}:
        intent = Intent.OTHER.value

    try:
        confidence = float(extracted.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0

    # Persist what we just learned BEFORE deciding what to do, so the decision
    # and any downstream human sees the same CRM state.
    persisted: list[str] = []
    if state.get("lead_id") is not None or extracted.get("name"):
        persisted = await crm.persist_extracted_qualification(
            session,
            state["tenant_id"],
            state["contact_id"],
            state.get("lead_id"),
            {**extracted, "confidence": confidence},
        )

    return {
        "intent": intent,
        "confidence": max(0.0, min(1.0, confidence)),
        "call_preference": extracted.get("call_availability"),
        "visit_preference": extracted.get("visit_preference"),
        "context": {"extracted": extracted, "persisted_fields": persisted},
    }


# ---------------------------------------------------------------------------
# Node 2 -- deterministic: what do we still not know?
# ---------------------------------------------------------------------------


def _name_is_known(customer: dict[str, Any]) -> bool:
    """A contact auto-created from an inbound message gets `full_name` set to
    the WhatsApp profile name OR, failing that, to the phone number itself
    (see ContactResolver's defaults). The latter is not a name."""
    full_name = (customer.get("full_name") or "").strip()
    if not full_name:
        return False
    phone = (customer.get("phone") or "").strip()
    return full_name.lstrip("+") != phone.lstrip("+")


def _interest_is_known(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("property_interests"):
        return True
    requirement = snapshot.get("requirement") or {}
    if requirement.get("property_type") or requirement.get("preferred_location"):
        return True
    observations = (snapshot.get("sales_context") or {}).get("observations") or []
    return any(str(o).startswith(f"{crm.REQUIREMENT_OBSERVATION_TYPE}:") for o in observations)


async def check_qualification(
    state: LeadWorkflowState, *, session: AsyncSession
) -> dict[str, Any]:
    """Compute which of {name, budget, interest, call_availability} are still
    missing, reading the CRM as the source of truth and layering this turn's
    extraction on top.

    Reading the CRM first is what stops the agent re-asking for information a
    previous turn (or a phone call, or a human) already captured -- a spec
    requirement, and the single most common way a chatbot annoys a buyer.
    """
    tenant_id = state["tenant_id"]
    contact_id = state["contact_id"]
    lead_id = state.get("lead_id")

    snapshot = (
        await crm.load_lead_snapshot(session, tenant_id, lead_id) if lead_id else {}
    )
    contact = await crm.load_contact(session, tenant_id, contact_id)
    extracted = (state.get("context") or {}).get("extracted") or {}

    customer = snapshot.get("customer") or {}
    # Prefer the freshly-read contact row; fall back to the lead snapshot.
    merged_customer = {**customer, **{k: v for k, v in contact.items() if v is not None}}
    requirement = snapshot.get("requirement") or {}

    known: dict[str, Any] = {}

    if extracted.get("name"):
        known["name"] = extracted["name"]
    elif _name_is_known(merged_customer):
        known["name"] = merged_customer.get("full_name")

    if extracted.get("budget_min") or extracted.get("budget_max"):
        known["budget"] = (
            f"{extracted.get('budget_min') or '?'} - {extracted.get('budget_max') or '?'}"
        )
    elif requirement.get("budget_min") or requirement.get("budget_max"):
        known["budget"] = (
            f"{requirement.get('budget_min') or '?'} - {requirement.get('budget_max') or '?'}"
        )

    if extracted.get("interest"):
        known["interest"] = extracted["interest"]
    elif _interest_is_known(snapshot):
        known["interest"] = requirement.get("property_type") or requirement.get(
            "preferred_location"
        ) or "recorded"

    if extracted.get("call_availability"):
        known["call_availability"] = extracted["call_availability"]
    elif merged_customer.get("preferred_contact_time"):
        known["call_availability"] = merged_customer["preferred_contact_time"]

    missing = [field for field in QUALIFICATION_FIELDS if not known.get(field)]

    qualification: dict[str, Any] = {field: known.get(field) for field in QUALIFICATION_FIELDS}
    qualification["missing"] = missing
    qualification["is_qualified"] = not missing

    context = dict(state.get("context") or {})
    context["lead_snapshot"] = snapshot
    context["contact"] = merged_customer

    update: dict[str, Any] = {"qualification": qualification, "context": context}
    if not missing and lead_id is not None:
        await _publish(
            session,
            state,
            EventType.QUALIFICATION_COMPLETED,
            {"fields": {k: str(v) for k, v in known.items()}},
        )
    return update


# ---------------------------------------------------------------------------
# Node 3 -- deterministic decision table
# ---------------------------------------------------------------------------


def determine_next_action(state: LeadWorkflowState) -> dict[str, Any]:
    """Pure function. Returns the single action this run will take.

    Ordering matters and encodes the safety posture: silence and escalation
    are checked before anything that acts on the buyer's behalf.
    """
    message = (state.get("latest_message") or "").strip()
    if not message:
        return {"next_action": NextAction.WAIT.value}

    # An AI failure is not a reason to guess; it is a reason to get a person.
    if state.get("error"):
        return {"next_action": NextAction.TRANSFER_HUMAN.value}

    extracted = (state.get("context") or {}).get("extracted") or {}
    intent = state.get("intent")

    if (
        bool(extracted.get("requires_human"))
        or intent == Intent.HUMAN_REQUEST.value
        or _HANDOFF_RE.search(message) is not None
    ):
        return {"next_action": NextAction.TRANSFER_HUMAN.value}

    confidence = state.get("confidence")
    if confidence is not None and confidence < MIN_ACTIONABLE_CONFIDENCE:
        return {"next_action": NextAction.TRANSFER_HUMAN.value}

    # Explicit, unambiguous intents win over qualification bookkeeping: never
    # answer "call me now" with "what's your budget?".
    direct = {
        Intent.NOT_INTERESTED.value: NextAction.MARK_NOT_INTERESTED.value,
        Intent.CALL_NOW.value: NextAction.CALL_NOW.value,
        Intent.CALL_LATER.value: NextAction.CALL_LATER.value,
        Intent.VISIT_REQUEST.value: NextAction.SCHEDULE_VISIT.value,
        Intent.PROPERTY_QUESTION.value: NextAction.SEND_PROPERTY_DETAILS.value,
    }
    if intent in direct:
        return {"next_action": direct[intent]}

    qualification = state.get("qualification") or {}
    if qualification.get("missing"):
        return {"next_action": NextAction.ASK_INFORMATION.value}

    # Fully qualified and nothing urgent asked: hand it to the phone channel
    # if we know when to ring, otherwise keep the conversation going.
    if qualification.get("call_availability"):
        return {"next_action": NextAction.CALL_LATER.value}
    if intent == Intent.GENERAL_INQUIRY.value:
        return {"next_action": NextAction.SEND_PROPERTY_DETAILS.value}
    return {"next_action": NextAction.FOLLOW_UP.value}


def route_next_action(state: LeadWorkflowState) -> str:
    """Conditional-edge selector. Returns the executor node name."""
    return ACTION_TO_NODE.get(
        str(state.get("next_action") or NextAction.WAIT.value), "execute_wait"
    )


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------


async def _publish(
    session: AsyncSession,
    state: LeadWorkflowState,
    event_type: EventType,
    payload: dict[str, Any],
) -> None:
    """Best-effort domain event. Instrumentation must never be the reason an
    AI turn fails."""
    try:
        await publish_event(
            session,
            tenant_id=state["tenant_id"],
            event_type=event_type,
            contact_id=state.get("contact_id"),
            lead_id=state.get("lead_id"),
            conversation_id=state.get("conversation_id"),
            payload={"source": "ai_orchestrator", **payload},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"AI orchestrator failed to publish {event_type}: {exc!s}")


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO-8601 string the model produced. Anything unparseable, or
    in the past, is treated as "no time given" rather than as an error."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed if parsed > datetime.now(UTC) else None


def _agent(session: AsyncSession) -> WhatsAppAgent:
    return WhatsAppAgent(session)


async def _reply(
    session: AsyncSession, state: LeadWorkflowState, action_hint: str
) -> dict[str, Any]:
    """Compose + send an AI reply. Shared by the conversational executors."""
    qualification = state.get("qualification") or {}
    known = {
        field: qualification.get(field)
        for field in QUALIFICATION_FIELDS
        if qualification.get(field)
    }
    return await _agent(session).respond(
        tenant_id=state["tenant_id"],
        contact_id=state["contact_id"],
        lead_id=state.get("lead_id"),
        latest_message=state.get("latest_message") or "",
        known=known,
        missing=list(qualification.get("missing") or []),
        lead_snapshot=(state.get("context") or {}).get("lead_snapshot") or {},
        action_hint=action_hint,
    )


async def _send_plain(
    session: AsyncSession, state: LeadWorkflowState, text: str
) -> dict[str, Any]:
    """Send a fixed, non-AI acknowledgement. Used by executors that must still
    say something when the model layer is exactly what failed."""
    return await _agent(session).send(
        tenant_id=state["tenant_id"],
        contact_id=state["contact_id"],
        lead_id=state.get("lead_id"),
        text=text,
    )


async def execute_ask_information(
    state: LeadWorkflowState, *, session: AsyncSession
) -> dict[str, Any]:
    missing = (state.get("qualification") or {}).get("missing") or []
    hint = (
        "Answer anything they asked, then ask for exactly one missing detail: "
        f"{missing[0] if missing else 'none'}."
    )
    return await _guard(state, lambda: _reply(session, state, hint))


async def execute_send_property_details(
    state: LeadWorkflowState, *, session: AsyncSession
) -> dict[str, Any]:
    hint = (
        "Answer their property question using ONLY tool results. If something "
        "is still missing from their profile, add one short follow-up question."
    )
    return await _guard(state, lambda: _reply(session, state, hint))


async def execute_call_now(
    state: LeadWorkflowState, *, session: AsyncSession
) -> dict[str, Any]:
    """Place the call through the one function in this codebase that dials."""
    lead_id = state.get("lead_id")
    if lead_id is None:
        return await execute_ask_information(state, session=session)

    async def run() -> dict[str, Any]:
        contact = (state.get("context") or {}).get("contact") or {}
        result = await make_instant_call(
            state["tenant_id"],
            state["contact_id"],
            lead_id,
            contact.get("phone"),
            {
                "job_type": "whatsapp_requested_call",
                "reason": "Requested an immediate call over WhatsApp.",
                "source": "ai_orchestrator",
                "conversation_id": state.get("conversation_id"),
            },
            session=session,
        )
        return {"call": result}

    return await _guard(state, run)


async def execute_call_later(
    state: LeadWorkflowState, *, session: AsyncSession
) -> dict[str, Any]:
    """Queue a call job. Places no call itself -- the background scheduler
    dispatches it through `make_instant_call` when it comes due."""
    lead_id = state.get("lead_id")
    if lead_id is None:
        return await execute_ask_information(state, session=session)

    async def run() -> dict[str, Any]:
        contact = (state.get("context") or {}).get("contact") or {}
        extracted = (state.get("context") or {}).get("extracted") or {}
        scheduled_at = _parse_datetime(extracted.get("call_available_at")) or (
            datetime.now(UTC) + DEFAULT_CALLBACK_DELAY
        )
        job = await schedule_call(
            state["tenant_id"],
            state["contact_id"],
            lead_id,
            contact.get("phone"),
            scheduled_at,
            {
                "job_type": "whatsapp_requested_call",
                "reason": state.get("call_preference") or "Requested a callback over WhatsApp.",
                "source": "ai_orchestrator",
                "conversation_id": state.get("conversation_id"),
            },
            session=session,
        )
        return {"call_job_id": str(job["id"]), "scheduled_at": scheduled_at.isoformat()}

    return await _guard(state, run)


async def execute_schedule_visit(
    state: LeadWorkflowState, *, session: AsyncSession
) -> dict[str, Any]:
    """Book a site visit when a concrete slot was stated, otherwise record the
    request and ask for a slot. Never invents a time."""

    async def run() -> dict[str, Any]:
        slot = _parse_datetime(state.get("visit_preference"))
        if slot is None:
            await _publish(
                session,
                state,
                EventType.VISIT_REQUESTED,
                {"preference": state.get("visit_preference")},
            )
            reply = await _reply(
                session,
                state,
                "They want a site visit but gave no usable date/time. Confirm "
                "enthusiastically and ask which day and time suits them.",
            )
            return {"visit": "requested", **reply}

        appointment = await AppointmentService(session).schedule_site_visit(
            state["tenant_id"],
            None,  # created_by: no human actor behind a webhook
            AppointmentCreate(
                customer_id=state["contact_id"],
                lead_id=state.get("lead_id"),
                scheduled_at=slot,
                source="ai_agent",
                notes="Requested over WhatsApp.",
            ),
        )
        await _publish(
            session,
            state,
            EventType.VISIT_SCHEDULED,
            {"appointment_id": str(appointment.id), "scheduled_at": slot.isoformat()},
        )
        return {"visit": "scheduled", "appointment_id": str(appointment.id)}

    return await _guard(state, run)


async def execute_transfer_human(
    state: LeadWorkflowState, *, session: AsyncSession
) -> dict[str, Any]:
    """Open a sales handoff with an AI-written conversation summary.

    `conversation_summary` was left NULL by the deterministic foundation layer
    with a `pending_phase_2_ai_summarizer` marker. This fills it -- from the
    same structured call that classified the message, so a handoff costs no
    extra model round-trip.
    """
    lead_id = state.get("lead_id")

    async def run() -> dict[str, Any]:
        extracted = (state.get("context") or {}).get("extracted") or {}
        summary = str(extracted.get("summary") or "").strip() or None
        reason = (
            str(extracted.get("human_reason") or "").strip()
            or "AI could not confidently handle this WhatsApp message."
        )
        payload: dict[str, Any] = {"reason": reason}
        if lead_id is not None:
            handoff = await SalesHandoffService(session).request_handoff(
                state["tenant_id"],
                lead_id,
                state["contact_id"],
                reason,
                HANDOFF_PRIORITY,
                "Escalated by the WhatsApp AI orchestrator.",
                conversation_summary=summary,
            )
            payload["handoff_id"] = str(handoff["id"])

        # Deterministic acknowledgement on purpose: this branch is reached
        # when the model failed, so it must not depend on the model.
        try:
            await _send_plain(
                session,
                state,
                "Thanks for reaching out -- one of our sales colleagues will "
                "get back to you on this shortly.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"AI orchestrator could not acknowledge a handoff: {exc!s}")
        return payload

    return await _guard(state, run)


async def execute_mark_not_interested(
    state: LeadWorkflowState, *, session: AsyncSession
) -> dict[str, Any]:
    lead_id = state.get("lead_id")

    async def run() -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if lead_id is not None:
            payload = await crm.mark_lead_not_interested(
                session,
                state["tenant_id"],
                lead_id,
                "Contact said they are not interested over WhatsApp.",
            )
            await _publish(session, state, EventType.LEAD_UPDATED, {"status": "lost"})
        try:
            await _send_plain(
                session,
                state,
                "Understood -- we won't follow up further. If anything changes, "
                "just message us here anytime.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"AI orchestrator could not acknowledge an opt-out: {exc!s}")
        return payload

    return await _guard(state, run)


async def execute_follow_up(
    state: LeadWorkflowState, *, session: AsyncSession
) -> dict[str, Any]:
    lead_id = state.get("lead_id")

    async def run() -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if lead_id is not None:
            payload = await crm.create_follow_up(
                session,
                state["tenant_id"],
                lead_id,
                state["contact_id"],
                datetime.now(UTC) + DEFAULT_FOLLOW_UP_DELAY,
                "Keep the WhatsApp conversation warm.",
            )
        reply = await _reply(
            session,
            state,
            "Acknowledge their message warmly and offer a concrete next step "
            "(a call, a site visit, or matching options).",
        )
        return {**payload, **reply}

    return await _guard(state, run)


async def execute_wait(
    state: LeadWorkflowState, *, session: AsyncSession  # noqa: ARG001 -- node signature
) -> dict[str, Any]:
    """Deliberate no-op: nothing to answer, nothing to record."""
    return {"result": {"action": NextAction.WAIT.value, "noop": True}}


async def _guard(state: LeadWorkflowState, run: Any) -> dict[str, Any]:
    """Run one executor body, converting any failure into state, not an
    exception. Spec section 20: an AI failure must never corrupt CRM state or
    crash the caller."""
    action = state.get("next_action")
    try:
        payload = await run()
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"AI orchestrator action {action} failed: {exc!s}")
        return {
            "error": f"{action}_failed: {exc!s}",
            "result": {"action": action, "ok": False},
        }
    return {"result": {"action": action, "ok": True, **(payload or {})}}


ACTION_TO_NODE: dict[str, str] = {
    NextAction.ASK_INFORMATION.value: "execute_ask_information",
    NextAction.SEND_PROPERTY_DETAILS.value: "execute_send_property_details",
    NextAction.CALL_NOW.value: "execute_call_now",
    NextAction.CALL_LATER.value: "execute_call_later",
    NextAction.SCHEDULE_VISIT.value: "execute_schedule_visit",
    NextAction.TRANSFER_HUMAN.value: "execute_transfer_human",
    NextAction.MARK_NOT_INTERESTED.value: "execute_mark_not_interested",
    NextAction.FOLLOW_UP.value: "execute_follow_up",
    NextAction.WAIT.value: "execute_wait",
}

EXECUTORS: dict[str, Any] = {
    "execute_ask_information": execute_ask_information,
    "execute_send_property_details": execute_send_property_details,
    "execute_call_now": execute_call_now,
    "execute_call_later": execute_call_later,
    "execute_schedule_visit": execute_schedule_visit,
    "execute_transfer_human": execute_transfer_human,
    "execute_mark_not_interested": execute_mark_not_interested,
    "execute_follow_up": execute_follow_up,
    "execute_wait": execute_wait,
}
