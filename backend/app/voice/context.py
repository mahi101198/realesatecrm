"""The context contract handed to the Voice Agent (spec section 4).

WHAT THE SPEC REQUIRES
    The Voice Service receives `tenant_id, contact_id, lead_id, customer_name,
    interest, budget, conversation_summary, reason_for_call` -- so the agent
    opens the call already knowing what WhatsApp already established and never
    makes the customer repeat it.

WHERE EACH FIELD COMES FROM (nothing is re-derived here)
    * The correlation from Superfone's `request_uuid`/`call_uuid` back to CRM
      rows is `resolve_call_by_provider_id` below -- the only query in this
      package. It exists because a media WebSocket arrives carrying a provider
      call id and nothing else; there is no existing service that maps one to a
      (tenant, call_job, call_attempt) triple. It is a lookup BY provider id, so
      tenant_id is its RESULT, not its input -- exactly like
      `SuperfoneWebhookService._find_and_normalize_sfvopi_call`. Every read that
      follows is then tenant-scoped with the tenant it returned.
    * customer_name / interest / budget / everything qualification-related comes
      from `AgentRepository.get_pre_call_context` -- the SAME deterministic
      snapshot `AgentGateway.prepare_call` already builds and stores in
      `agent_call_contexts`. We prefer that stored snapshot when it exists so
      the agent talks about the lead as it was when the call was queued.
    * conversation_summary comes from `public.conversation_summaries` (migration
      006) for this lead's most recent summarised call, falling back to the
      rolling summary on `public.agent_sessions`. Both already exist; this
      package creates no new table.
    * reason_for_call comes off the `call_jobs` row the scheduler created
      (`reason` / `job_type`), i.e. why the orchestrator asked for this call.
"""

import logging
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.repository import AgentRepository

logger = logging.getLogger(__name__)


class CallCorrelation(BaseModel):
    """Everything the media stream needs to know which call it is carrying."""

    tenant_id: UUID
    call_id: UUID
    call_job_id: UUID
    call_attempt_id: UUID
    contact_id: UUID
    lead_id: UUID | None = None
    provider_call_id: str


class VoiceCallContext(BaseModel):
    """The spec-section-4 briefing passed into the Voice Agent.

    Deliberately flat and JSON-serialisable: it is also written verbatim into
    the LiveKit room metadata so anyone debugging a room can see what the agent
    was told, and it must survive that round trip.
    """

    tenant_id: UUID
    contact_id: UUID
    lead_id: UUID | None = None
    call_id: UUID
    call_job_id: UUID
    call_attempt_id: UUID

    customer_name: str | None = None
    preferred_language: str = "hi"
    interest: str | None = None
    budget: str | None = None
    conversation_summary: str | None = None
    reason_for_call: str | None = None

    # Extra deterministic colour the agent may use but the spec does not name.
    lead_snapshot: dict[str, Any] = Field(default_factory=dict)

    def as_room_metadata(self) -> dict[str, str]:
        """Compact, non-PII-heavy metadata for the LiveKit room.

        Phone numbers, email and the full snapshot are deliberately excluded:
        room metadata is visible to every participant token and to LiveKit's
        dashboard, so it carries correlation ids only.
        """
        return {
            "tenant_id": str(self.tenant_id),
            "call_job_id": str(self.call_job_id),
            "call_attempt_id": str(self.call_attempt_id),
            "lead_id": str(self.lead_id) if self.lead_id else "",
        }


# `call_jobs` records WHY a call was queued only as its `job_type` enum -- there
# is no free-text reason column, and inventing one would mean a migration for
# something the enum already expresses. These are the spoken renderings.
REASON_BY_JOB_TYPE: dict[str, str] = {
    "initial_lead_call": "First call to a new enquiry: qualify and understand requirements.",
    "follow_up_call": "Follow-up on an existing enquiry that is already partly qualified.",
    "callback": "The customer asked to be called back at this time.",
    "human_callback": "A human callback was arranged; confirm and prepare the handover.",
    "whatsapp_requested_call": "The customer asked over WhatsApp to be called.",
}


def reason_for_job_type(job_type: str | None) -> str | None:
    """Render a call_jobs.job_type as the agent-facing reason for calling."""
    if not job_type:
        return None
    return REASON_BY_JOB_TYPE.get(job_type, f"Scheduled call of type '{job_type}'.")


_RESOLVE_CALL_SQL = text(
    """
    SELECT
        c.tenant_id     AS tenant_id,
        c.id            AS call_id,
        c.customer_id   AS contact_id,
        c.lead_id       AS lead_id,
        cj.id           AS call_job_id,
        ca.id           AS call_attempt_id,
        cj.job_type     AS job_type
    FROM public.calls c
    JOIN public.call_jobs cj
      ON cj.call_id = c.id AND cj.tenant_id = c.tenant_id
    JOIN public.call_attempts ca
      ON ca.call_job_id = cj.id AND ca.tenant_id = c.tenant_id
    WHERE c.provider_call_id = :provider_call_id
    ORDER BY ca.attempt_number DESC
    LIMIT 1
    """
)


async def resolve_call_by_provider_id(
    session: AsyncSession, provider_call_id: str
) -> tuple[CallCorrelation, dict[str, Any]] | None:
    """Map a Superfone call id to the CRM rows behind it.

    Returns `(correlation, job_row_extras)` or None when the id belongs to no
    call we placed -- which is the expected answer for any media stream we did
    not originate, and must be treated as "close the socket", never as an error
    worth retrying.
    """
    result = await session.execute(
        _RESOLVE_CALL_SQL, {"provider_call_id": provider_call_id}
    )
    row = result.mappings().one_or_none()
    if row is None:
        return None
    correlation = CallCorrelation(
        tenant_id=row["tenant_id"],
        call_id=row["call_id"],
        call_job_id=row["call_job_id"],
        call_attempt_id=row["call_attempt_id"],
        contact_id=row["contact_id"],
        lead_id=row["lead_id"],
        provider_call_id=provider_call_id,
    )
    return correlation, {"job_type": str(row["job_type"]) if row["job_type"] else None}


_CONVERSATION_SUMMARY_SQL = text(
    """
    SELECT summary_text
    FROM public.conversation_summaries
    WHERE tenant_id = :tenant_id
      AND (CAST(:lead_id AS uuid) IS NULL OR lead_id = CAST(:lead_id AS uuid))
      AND customer_id = :contact_id
      AND summary_text IS NOT NULL
    ORDER BY generated_at DESC
    LIMIT 1
    """
)

_AGENT_SESSION_SUMMARY_SQL = text(
    """
    SELECT conversation_summary
    FROM public.agent_sessions
    WHERE tenant_id = :tenant_id
      AND customer_id = :contact_id
      AND conversation_summary IS NOT NULL
    ORDER BY started_at DESC
    LIMIT 1
    """
)


async def load_conversation_summary(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    contact_id: UUID,
    lead_id: UUID | None,
) -> str | None:
    """Most recent narrative recap of prior contact, or None.

    Reads only tables that already exist (migration 006). Returns None rather
    than raising when nothing has been summarised yet -- a first-ever call has
    no history and the prompt says so explicitly.
    """
    params = {"tenant_id": tenant_id, "contact_id": contact_id, "lead_id": lead_id}
    result = await session.execute(_CONVERSATION_SUMMARY_SQL, params)
    row = result.mappings().one_or_none()
    if row and row["summary_text"]:
        return str(row["summary_text"])

    result = await session.execute(_AGENT_SESSION_SUMMARY_SQL, params)
    row = result.mappings().one_or_none()
    if row and row["conversation_summary"]:
        return str(row["conversation_summary"])
    return None


def _format_budget(requirement: dict[str, Any]) -> str | None:
    """Render the budget range as one human phrase the agent can say aloud."""
    low = requirement.get("budget_min")
    high = requirement.get("budget_max")
    if low and high:
        return f"{low} to {high}"
    if high:
        return f"up to {high}"
    if low:
        return f"from {low}"
    return None


def _format_interest(snapshot: dict[str, Any]) -> str | None:
    """One phrase describing what the lead is after, from the stored snapshot."""
    requirement = snapshot.get("requirement") or {}
    parts: list[str] = []
    if requirement.get("bedrooms"):
        parts.append(f"{requirement['bedrooms']} BHK")
    if requirement.get("property_type"):
        parts.append(str(requirement["property_type"]))
    if requirement.get("preferred_location"):
        parts.append(f"in {requirement['preferred_location']}")

    interests = snapshot.get("property_interests") or []
    projects = [str(i.get("project_name")) for i in interests if i.get("project_name")]
    if projects:
        parts.append(f"(interested in {', '.join(projects[:3])})")
    return " ".join(parts) or None


async def build_voice_call_context(
    session: AsyncSession,
    correlation: CallCorrelation,
    *,
    reason_for_call: str | None = None,
) -> VoiceCallContext:
    """Assemble the spec-section-4 briefing for one call.

    Every read is tenant-scoped by `correlation.tenant_id`. Missing pieces come
    back as None rather than raising: an agent that knows less is still a usable
    agent, whereas a briefing that throws would drop a live call.

    PREFERS THE STORED SNAPSHOT OVER RECOMPUTING IT LIVE. `AgentGateway.
    prepare_call` already ran this exact six-query aggregation
    (`get_pre_call_context`) and persisted the result to `agent_call_contexts`
    BEFORE the phone was dialed -- there was no customer waiting yet. Running
    it again here re-pays that cost at the one moment that matters most: the
    customer has just picked up and is waiting to hear the agent's greeting.
    Falling back to the live aggregation (only) when no snapshot exists keeps
    this correct for paths that skip `prepare_call`, such as the browser test
    harness in `test_service.py`.
    """
    repository = AgentRepository(session)
    snapshot: dict[str, Any] = {}
    if correlation.lead_id is not None:
        stored = await repository.get_latest_call_context_snapshot(
            correlation.tenant_id, correlation.call_job_id
        )
        if stored is not None:
            snapshot = stored
        else:
            pre_call = await repository.get_pre_call_context(
                correlation.tenant_id, correlation.lead_id
            )
            if pre_call is not None:
                snapshot = pre_call.model_dump(mode="json")

    customer = snapshot.get("customer") or {}
    summary = await load_conversation_summary(
        session,
        tenant_id=correlation.tenant_id,
        contact_id=correlation.contact_id,
        lead_id=correlation.lead_id,
    )

    return VoiceCallContext(
        tenant_id=correlation.tenant_id,
        contact_id=correlation.contact_id,
        lead_id=correlation.lead_id,
        call_id=correlation.call_id,
        call_job_id=correlation.call_job_id,
        call_attempt_id=correlation.call_attempt_id,
        customer_name=customer.get("full_name"),
        preferred_language=customer.get("preferred_language") or "hi",
        interest=_format_interest(snapshot),
        budget=_format_budget(snapshot.get("requirement") or {}),
        conversation_summary=summary,
        reason_for_call=reason_for_call,
        lead_snapshot=snapshot,
    )
