"""AI Agent Write Tools for Real-Estate Sales CRM."""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.repository import AgentRepository
from app.appointments.schemas import AppointmentCancelRequest, AppointmentCreate, AppointmentUpdate
from app.appointments.service import AppointmentService
from app.core.exceptions import AppError
from app.core.permissions import (
    Permission,
    check_permission,
    ensure_tenant_resource_access,
    resolve_tenant_scope,
)
from app.core.request_context import RequestContext
from app.customers.schemas import CustomerUpdate
from app.customers.service import CustomerService
from app.leads.schemas import LeadNoteCreate, LeadPropertyInterestCreate, LeadUpdate
from app.leads.service import LeadService

logger = logging.getLogger(__name__)

_VALID_INTEREST_LEVELS = {"very_high", "high", "medium", "low", "very_low"}


def _success_response(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}


def _error_response(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "error_code": code, "message": message}


async def update_lead_requirements_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
    property_type: str | None = None,
    preferred_location: str | None = None,
    budget_min: Decimal | float | None = None,
    budget_max: Decimal | float | None = None,
    area_min: Decimal | float | None = None,
    area_max: Decimal | float | None = None,
    purpose: str | None = None,
    purchase_timeline: str | None = None,
    financing_requirement: str | None = None,
    source_call_attempt_id: UUID | None = None,
) -> dict[str, Any]:
    """AI Tool: Update structured lead requirements with automatic requirement history audit logging."""
    try:
        check_permission(context, Permission.LEAD_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        repo = AgentRepository(session)
        updates = {
            "budget_min": Decimal(str(budget_min)) if budget_min is not None else None,
            "budget_max": Decimal(str(budget_max)) if budget_max is not None else None,
            "area_min": Decimal(str(area_min)) if area_min is not None else None,
            "area_max": Decimal(str(area_max)) if area_max is not None else None,
            "purpose": purpose,
            "timeline": purchase_timeline,
            "finance_requirement": financing_requirement,
            "preferred_city": preferred_location,
        }
        filtered_updates = {k: v for k, v in updates.items() if v is not None}
        await repo.update_lead_requirements_with_history(
            tenant_id, lead_id, filtered_updates, source_call_attempt_id
        )
        return _success_response(
            {
                "operation": "update_lead_requirements",
                "lead_id": str(lead_id),
                "updated_fields": list(filtered_updates.keys()),
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in update_lead_requirements_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to update lead requirements.")


async def update_customer_details_tool(
    context: RequestContext,
    session: AsyncSession,
    customer_id: UUID,
    full_name: str | None = None,
    email: str | None = None,
    alternate_phone: str | None = None,
    city: str | None = None,
    preferred_language: str | None = None,
    do_not_call: bool | None = None,
) -> dict[str, Any]:
    """AI Tool: Update customer profile details and contact preferences."""
    try:
        check_permission(context, Permission.CUSTOMER_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        service = CustomerService(session)
        cust = await service.get_customer(tenant_id, customer_id)
        ensure_tenant_resource_access(context, cust.tenant_id)

        update_payload = CustomerUpdate(
            full_name=full_name,
            email=EmailStr(email) if email else None,
            alternate_phone=alternate_phone,
            city=city,
            preferred_language=preferred_language,
            do_not_call=do_not_call,
        )
        res = await service.update_customer(tenant_id, customer_id, update_payload)
        return _success_response(res.model_dump(mode="json"))
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in update_customer_details_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to update customer details.")


async def add_lead_note_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
    note_text: str,
) -> dict[str, Any]:
    """AI Tool: Add a note to a lead."""
    try:
        check_permission(context, Permission.LEAD_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        service = LeadService(session)
        lead = await service.get_lead(tenant_id, lead_id)
        ensure_tenant_resource_access(context, lead.tenant_id)

        note_payload = LeadNoteCreate(note_text=note_text, is_pinned=False)
        res = await service.add_note(
            tenant_id or lead.tenant_id, lead_id, context.user_id, note_payload
        )
        return _success_response(res.model_dump(mode="json"))
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in add_lead_note_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to add lead note.")


async def record_call_observation_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
    customer_id: UUID,
    observation_type: str,
    observation_value: str,
    confidence: float = 1.0,
    source_call_attempt_id: UUID | None = None,
) -> dict[str, Any]:
    """AI Tool: Record sales intelligence (objection, competitor, timeline, decision maker)."""
    try:
        check_permission(context, Permission.LEAD_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        repo = AgentRepository(session)
        row = await repo.record_observation(
            tenant_id,
            lead_id,
            customer_id,
            observation_type,
            observation_value,
            confidence,
            source_call_attempt_id,
        )
        return _success_response(
            {
                "observation_id": str(row["id"]),
                "observation_type": row["observation_type"],
                "observation_value": row["observation_value"],
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in record_call_observation_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to record call observation.")


async def create_follow_up_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
    customer_id: UUID,
    scheduled_at: datetime,
    follow_up_type: str = "ai_call",
    reason: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """AI Tool: Create a follow-up task with DNC check and deduplication."""
    try:
        check_permission(context, Permission.LEAD_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        repo = AgentRepository(session)

        # Check DNC
        is_dnc = await repo.check_do_not_call(tenant_id, customer_id)
        if is_dnc:
            return _error_response(
                "DO_NOT_CALL_ACTIVE", "Cannot schedule follow-up: Customer is marked as Do-Not-Call."
            )

        if isinstance(scheduled_at, str):
            scheduled_at = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))

        res = await repo.create_lead_follow_up(
            tenant_id, lead_id, customer_id, scheduled_at, follow_up_type, reason, notes
        )

        return _success_response(
            {
                "follow_up_id": str(res["id"]),
                "scheduled_at": res["scheduled_at"].isoformat(),
                "status": res["status"],
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in create_follow_up_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to create follow-up.")


async def reschedule_follow_up_tool(
    context: RequestContext,
    session: AsyncSession,
    follow_up_id: UUID,
    scheduled_at: datetime,
    reason: str | None = None,
) -> dict[str, Any]:
    """AI Tool: Reschedule an existing open follow-up."""
    try:
        check_permission(context, Permission.LEAD_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        repo = AgentRepository(session)
        res = await repo.reschedule_follow_up(tenant_id, follow_up_id, scheduled_at, reason)
        return _success_response(
            {
                "follow_up_id": str(res["id"]),
                "scheduled_at": res["scheduled_at"].isoformat(),
                "status": res["status"],
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in reschedule_follow_up_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to reschedule follow-up.")


async def cancel_follow_up_tool(
    context: RequestContext,
    session: AsyncSession,
    follow_up_id: UUID,
    cancel_reason: str | None = None,
) -> dict[str, Any]:
    """AI Tool: Cancel a scheduled follow-up."""
    try:
        check_permission(context, Permission.LEAD_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        repo = AgentRepository(session)
        res = await repo.cancel_follow_up(tenant_id, follow_up_id, cancel_reason)
        return _success_response(
            {
                "follow_up_id": str(res["id"]),
                "status": res["status"],
                "cancel_reason": res["cancel_reason"],
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in cancel_follow_up_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to cancel follow-up.")


async def schedule_site_visit_tool(
    context: RequestContext,
    session: AsyncSession,
    customer_id: UUID,
    scheduled_at: datetime,
    lead_id: UUID | None = None,
    project_id: UUID | None = None,
    property_id: UUID | None = None,
    sales_agent_id: UUID | None = None,
    duration_minutes: int = 60,
    notes: str | None = None,
) -> dict[str, Any]:
    """AI Tool: Schedule a site visit appointment with double-booking prevention."""
    try:
        check_permission(context, Permission.APPOINTMENT_CREATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        service = AppointmentService(session)
        create_payload = AppointmentCreate(
            customer_id=customer_id,
            lead_id=lead_id,
            project_id=project_id,
            property_id=property_id,
            sales_agent_id=sales_agent_id,
            scheduled_at=scheduled_at,
            duration_minutes=duration_minutes,
            source="ai_agent",
            notes=notes,
        )
        res = await service.schedule_site_visit(tenant_id, context.user_id, create_payload)
        return _success_response(res.model_dump(mode="json"))
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in schedule_site_visit_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to schedule site visit.")


async def reschedule_site_visit_tool(
    context: RequestContext,
    session: AsyncSession,
    appointment_id: UUID,
    new_scheduled_at: datetime,
    reason: str | None = None,
) -> dict[str, Any]:
    """AI Tool: Reschedule an existing site visit appointment."""
    try:
        check_permission(context, Permission.APPOINTMENT_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        service = AppointmentService(session)
        update_payload = AppointmentUpdate(scheduled_at=new_scheduled_at, notes=reason)
        res = await service.reschedule_appointment(tenant_id, appointment_id, update_payload)
        return _success_response(res.model_dump(mode="json"))
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in reschedule_site_visit_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to reschedule site visit.")


async def cancel_site_visit_tool(
    context: RequestContext,
    session: AsyncSession,
    appointment_id: UUID,
    cancellation_reason: str | None = None,
) -> dict[str, Any]:
    """AI Tool: Cancel a site visit appointment."""
    try:
        check_permission(context, Permission.APPOINTMENT_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        service = AppointmentService(session)
        cancel_payload = AppointmentCancelRequest(reason=cancellation_reason)
        res = await service.cancel_appointment(
            tenant_id, appointment_id, context.user_id, cancel_payload
        )
        return _success_response(res.model_dump(mode="json"))
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in cancel_site_visit_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to cancel site visit.")


async def request_sales_agent_transfer_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
    customer_id: UUID,
    reason: str | None = None,
    priority: int = 5,
) -> dict[str, Any]:
    """AI Tool: Initiate structured human sales agent transfer/handoff."""
    try:
        check_permission(context, Permission.LEAD_ASSIGN)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        repo = AgentRepository(session)
        res = await repo.create_sales_handoff(
            tenant_id, lead_id, customer_id, reason, priority, "Initiated by AI agent"
        )
        return _success_response(
            {
                "handoff_id": str(res["id"]),
                "status": res["status"],
                "requested_at": res["requested_at"].isoformat(),
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in request_sales_agent_transfer_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to transfer lead to sales agent.")


async def record_lead_property_interest_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
    project_id: UUID,
    property_id: UUID | None = None,
    interest_level: str = "medium",
    is_primary: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    """AI Tool: Record that a lead is interested in a project/property discussed during a call.

    Reuses LeadService.add_property_interest, the same validation the staff-facing
    POST /leads/{id}/property-interests endpoint uses: the lead must belong to this
    tenant, the project must belong to this tenant, and if a specific property unit
    is given it must also belong to this tenant.
    """
    try:
        check_permission(context, Permission.LEAD_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        if interest_level not in _VALID_INTEREST_LEVELS:
            return _error_response(
                "INVALID_INTEREST_LEVEL",
                f"interest_level must be one of {sorted(_VALID_INTEREST_LEVELS)}.",
            )

        service = LeadService(session)
        lead = await service.get_lead(tenant_id, lead_id)
        ensure_tenant_resource_access(context, lead.tenant_id)

        payload = LeadPropertyInterestCreate(
            project_id=project_id,
            property_id=property_id,
            interest_level=interest_level,
            is_primary=is_primary,
            notes=notes,
        )
        res = await service.add_property_interest(tenant_id, lead_id, payload)
        return _success_response(res.model_dump(mode="json"))
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in record_lead_property_interest_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to record property interest.")


async def update_lead_score_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
    lead_score: int,
) -> dict[str, Any]:
    """AI Tool: Update a lead's lead_score (0-100).

    Idempotent: setting the same score twice produces the same resulting row, no
    duplicate side effects. Bounds are enforced both here (client-side, mirroring
    the DB's chk_leads_score CHECK constraint) and by the LeadUpdate schema.
    """
    try:
        check_permission(context, Permission.LEAD_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        if not 0 <= lead_score <= 100:
            return _error_response(
                "INVALID_LEAD_SCORE", "lead_score must be between 0 and 100 inclusive."
            )

        service = LeadService(session)
        lead = await service.get_lead(tenant_id, lead_id)
        ensure_tenant_resource_access(context, lead.tenant_id)

        res = await service.update_lead(tenant_id, lead_id, LeadUpdate(lead_score=lead_score))
        return _success_response(
            {"lead_id": str(res.id), "lead_score": res.lead_score}
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in update_lead_score_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to update lead score.")


async def create_sales_callback_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
    customer_id: UUID,
    scheduled_at: datetime,
    reason: str | None = None,
) -> dict[str, Any]:
    """AI Tool: Schedule a human sales representative callback specifically."""
    try:
        check_permission(context, Permission.LEAD_UPDATE)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        repo = AgentRepository(session)

        is_dnc = await repo.check_do_not_call(tenant_id, customer_id)
        if is_dnc:
            return _error_response(
                "DO_NOT_CALL_ACTIVE", "Cannot schedule sales callback: Customer is marked as Do-Not-Call."
            )

        res = await repo.create_lead_follow_up(
            tenant_id, lead_id, customer_id, scheduled_at, "human_callback", reason, "Human sales callback requested"
        )
        return _success_response(
            {
                "follow_up_id": str(res["id"]),
                "scheduled_at": res["scheduled_at"].isoformat(),
                "type": "human_callback",
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in create_sales_callback_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to create sales callback.")
