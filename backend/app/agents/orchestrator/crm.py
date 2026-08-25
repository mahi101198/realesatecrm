"""CRM gateway for the orchestrator: every DB touch, funnelled and named.

The orchestrator nodes never see SQL, a session query or a table name. They
call the functions below, each of which delegates to something that already
exists and is already tenant-scoped:

    read   lead snapshot        -> app.agent.tools.read_tools.get_lead_context_tool
                                   (the registered READ tool, invoked with the
                                   synthetic read-only context)
    read   contact              -> app.customers.service.CustomerService
    write  name / availability  -> app.customers.service.CustomerService
    write  budget / location    -> AgentRepository.update_lead_requirements_with_history
                                   (the same audited path update_lead_requirements_tool uses)
    write  interest             -> AgentRepository.record_observation
    write  lead status          -> app.leads.service.LeadService
    write  follow-up            -> AgentRepository.create_lead_follow_up

WHY THE WRITES BYPASS THE `*_tool` REGISTRY WRAPPERS
----------------------------------------------------
Those wrappers resolve their tenant from an authenticated `RequestContext` and
stamp `context.user_id` into audit columns. There is no authenticated user
behind an inbound webhook, and inventing one would either fail a foreign key
or attribute AI activity to a real person. So writes go one level deeper, to
the same service/repository functions the wrappers call, passing the verified
tenant id explicitly -- exactly the pattern
`app/webhooks/whatsapp_dashboard/service.py` already uses for call placement.
Reads keep using the registered tool because it needs no actor at all.
"""

import logging
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.repository import AgentRepository
from app.agent.tools.read_tools import get_lead_context_tool
from app.agents.security import build_agent_read_context
from app.customers.schemas import CustomerUpdate
from app.customers.service import CustomerService
from app.leads.schemas import LeadUpdate
from app.leads.service import LeadService

logger = logging.getLogger(__name__)

# lead_observations.observation_type used for AI-extracted requirements.
REQUIREMENT_OBSERVATION_TYPE = "requirement"


async def load_lead_snapshot(
    session: AsyncSession, tenant_id: UUID, lead_id: UUID
) -> dict[str, Any]:
    """Server-derived lead context (customer summary, requirements, interests,
    observations). Returns `{}` when the lead cannot be read -- a missing
    snapshot must degrade the conversation, not crash it."""
    context = build_agent_read_context(tenant_id)
    result = await get_lead_context_tool(context, session, lead_id=lead_id)
    if not result.get("success"):
        logger.warning(
            f"AI orchestrator could not load lead context for lead={lead_id}: "
            f"{result.get('error_code')}"
        )
        return {}
    data = result.get("data")
    return data if isinstance(data, dict) else {}


async def load_contact(
    session: AsyncSession, tenant_id: UUID, contact_id: UUID
) -> dict[str, Any]:
    """The contact row as a plain dict. `preferred_contact_time` (this
    schema's home for "when may we call you") lives here and nowhere in the
    lead snapshot, which is the only reason this second read exists."""
    try:
        customer = await CustomerService(session).get_customer(tenant_id, contact_id)
    except Exception as exc:  # noqa: BLE001 -- degrade, never crash the webhook
        logger.warning(f"AI orchestrator could not load contact {contact_id}: {exc!s}")
        return {}
    return customer.model_dump(mode="json")


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return parsed if parsed >= 0 else None


async def persist_extracted_qualification(
    session: AsyncSession,
    tenant_id: UUID,
    contact_id: UUID,
    lead_id: UUID | None,
    extracted: dict[str, Any],
) -> list[str]:
    """Write back whatever the AI extracted. Returns the field names actually
    persisted.

    Every branch is independently guarded: one failing write (say a bad phone
    format on the customer update) must not discard the others, and none of
    them may propagate out of the graph.
    """
    persisted: list[str] = []

    customer_updates: dict[str, Any] = {}
    if extracted.get("name"):
        customer_updates["full_name"] = str(extracted["name"])[:255]
    if extracted.get("call_availability"):
        customer_updates["preferred_contact_time"] = str(extracted["call_availability"])[:100]
    if customer_updates:
        try:
            await CustomerService(session).update_customer(
                tenant_id, contact_id, CustomerUpdate(**customer_updates)
            )
            persisted.extend(customer_updates.keys())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"AI orchestrator failed to update contact {contact_id}: {exc!s}")

    if lead_id is None:
        return persisted

    repo = AgentRepository(session)

    requirement_updates: dict[str, Any] = {
        "budget_min": _to_decimal(extracted.get("budget_min")),
        "budget_max": _to_decimal(extracted.get("budget_max")),
        "preferred_city": (
            str(extracted["preferred_location"])[:100]
            if extracted.get("preferred_location")
            else None
        ),
    }
    requirement_updates = {k: v for k, v in requirement_updates.items() if v is not None}
    if requirement_updates:
        try:
            await repo.update_lead_requirements_with_history(
                tenant_id, lead_id, requirement_updates
            )
            persisted.extend(requirement_updates.keys())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"AI orchestrator failed to update requirements on lead {lead_id}: {exc!s}"
            )

    if extracted.get("interest"):
        try:
            await repo.record_observation(
                tenant_id,
                lead_id,
                contact_id,
                REQUIREMENT_OBSERVATION_TYPE,
                str(extracted["interest"])[:1000],
                float(extracted.get("confidence") or 1.0),
            )
            persisted.append("interest")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"AI orchestrator failed to record interest on lead {lead_id}: {exc!s}"
            )

    return persisted


async def mark_lead_not_interested(
    session: AsyncSession, tenant_id: UUID, lead_id: UUID, reason: str | None = None
) -> dict[str, Any]:
    """Close the lead as lost. `LeadService.update_lead` owns the status
    transition side effects (lost_at stamping), so this stays a one-liner."""
    updated = await LeadService(session).update_lead(
        tenant_id,
        lead_id,
        LeadUpdate(status="lost", lost_reason=reason or "Contact declined over WhatsApp."),
    )
    return {"lead_id": str(updated.id), "status": updated.status}


async def create_follow_up(
    session: AsyncSession,
    tenant_id: UUID,
    lead_id: UUID,
    contact_id: UUID,
    scheduled_at: Any,
    reason: str | None = None,
) -> dict[str, Any]:
    """Queue a follow-up task. Same repository method `create_follow_up_tool`
    uses, which already deduplicates open follow-ups for the lead."""
    row = await AgentRepository(session).create_lead_follow_up(
        tenant_id, lead_id, contact_id, scheduled_at, "whatsapp_follow_up", reason, None
    )
    return {"follow_up_id": str(row["id"]), "status": row.get("status")}
