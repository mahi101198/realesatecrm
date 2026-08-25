"""AI Agent Read Tools for Real-Estate Sales CRM."""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.orchestrator import CallContextService
from app.agent.repository import AgentRepository
from app.appointments.service import AppointmentService
from app.core.exceptions import AppError
from app.core.permissions import (
    Permission,
    check_permission,
    ensure_tenant_resource_access,
    resolve_tenant_scope,
)
from app.core.request_context import RequestContext
from app.projects.service import ProjectService
from app.properties.schemas import PropertySearchFilter
from app.properties.service import PropertyService
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


def _success_response(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}


def _error_response(code: str, message: str) -> dict[str, Any]:
    return {"success": False, "error_code": code, "message": message}


async def get_lead_context_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
) -> dict[str, Any]:
    """AI Tool: Get compact server-derived relationship context for a lead."""
    try:
        check_permission(context, Permission.LEAD_READ)
        tenant_id = resolve_tenant_scope(context)
        service = CallContextService(session)
        result = await service.build_pre_call_context(tenant_id, lead_id)
        ensure_tenant_resource_access(context, result.tenant_id)
        return _success_response(result.model_dump(mode="json"))
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in get_lead_context_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to retrieve lead context.")


async def get_recent_call_summary_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
) -> dict[str, Any]:
    """AI Tool: Get structured summary of the previous call with the customer."""
    try:
        check_permission(context, Permission.LEAD_READ)
        tenant_id = resolve_tenant_scope(context)

        sql = text(
            """
            SELECT c.id, c.status, c.outcome, c.call_summary, c.ended_at, c.duration_seconds
            FROM public.calls c
            WHERE c.lead_id = :lead_id
              AND (CAST(:tenant_id AS uuid) IS NULL OR c.tenant_id = CAST(:tenant_id AS uuid))
            ORDER BY c.created_at DESC
            LIMIT 1
            """
        )
        res = await session.execute(sql, {"lead_id": lead_id, "tenant_id": tenant_id})
        row = res.mappings().one_or_none()
        if not row:
            return _success_response(
                {"has_previous_call": False, "summary": "No previous call found."}
            )

        # Fetch observations from last call
        obs_sql = text(
            """
            SELECT observation_type, observation_value
            FROM public.lead_observations
            WHERE lead_id = :lead_id
            ORDER BY created_at DESC
            LIMIT 5
            """
        )
        obs_res = await session.execute(obs_sql, {"lead_id": lead_id})
        observations = [
            f"{r['observation_type']}: {r['observation_value']}" for r in obs_res.mappings().all()
        ]

        return _success_response(
            {
                "has_previous_call": True,
                "call_id": str(row["id"]),
                "outcome": row["outcome"],
                "call_summary": row["call_summary"] or "Call connected previously.",
                "duration_seconds": row["duration_seconds"],
                "ended_at": row["ended_at"].isoformat() if row["ended_at"] else None,
                "observations": observations,
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in get_recent_call_summary_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to retrieve recent call summary.")


async def get_open_follow_ups_tool(
    context: RequestContext,
    session: AsyncSession,
    lead_id: UUID,
) -> dict[str, Any]:
    """AI Tool: Check open follow-ups to prevent duplicate scheduling."""
    try:
        check_permission(context, Permission.LEAD_READ)
        tenant_id = resolve_tenant_scope(context)
        if tenant_id is None:
            return _error_response("MISSING_TENANT_SCOPE", "Tenant scope required.")

        repo = AgentRepository(session)
        open_followups = await repo.get_open_follow_ups(tenant_id, lead_id)
        formatted = [
            {
                "follow_up_id": str(f["id"]),
                "follow_up_type": f["follow_up_type"],
                "scheduled_at": f["scheduled_at"].isoformat(),
                "reason": f["reason"],
                "status": f["status"],
            }
            for f in open_followups
        ]
        return _success_response({"open_follow_ups": formatted, "count": len(formatted)})
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in get_open_follow_ups_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to retrieve open follow-ups.")


async def search_properties_tool(
    context: RequestContext,
    session: AsyncSession,
    budget_min: Decimal | float | None = None,
    budget_max: Decimal | float | None = None,
    bedrooms: int | None = None,
    project_id: UUID | None = None,
    location: str | None = None,  # noqa: ARG001 -- accepted for API-caller
    # compatibility; PropertySearchFilter has no location/city/locality field
    # to bind it to (see migration 023: no locations table yet, city/locality
    # stay free text on projects), and the LLM-facing schema in
    # agents/whatsapp_agent/prompts.py never sends this argument.
    limit: int = 5,
) -> dict[str, Any]:
    """AI Tool: Search available properties matching customer requirements."""
    try:
        check_permission(context, Permission.PROPERTY_READ)
        tenant_id = resolve_tenant_scope(context)
        service = PropertyService(session)
        filters = PropertySearchFilter(
            status="available",
            project_id=project_id,
            min_budget=Decimal(str(budget_min)) if budget_min is not None else None,
            max_budget=Decimal(str(budget_max)) if budget_max is not None else None,
            bedrooms=bedrooms,
        )
        pagination = PaginationParams(page=1, page_size=min(limit, 10))
        paginated = await service.search_properties(tenant_id, filters, pagination)

        concise_items = []
        for prop in paginated.items:
            price_val = prop.offer_price or prop.base_price
            area_val = prop.carpet_area or prop.built_up_area or prop.plot_area
            concise_items.append(
                {
                    "property_id": str(prop.id),
                    "project_id": str(prop.project_id),
                    "property_code": prop.property_code,
                    "unit_number": prop.unit_number,
                    "bedrooms": prop.bedrooms,
                    "facing": prop.facing,
                    "price": float(price_val) if price_val is not None else None,
                    "area": float(area_val) if area_val is not None else None,
                    "area_unit": prop.area_unit,
                    "status": prop.status,
                }
            )

        return _success_response({"properties": concise_items, "total_found": paginated.total})
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in search_properties_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to search available properties.")


async def get_property_details_tool(
    context: RequestContext,
    session: AsyncSession,
    property_id: UUID,
) -> dict[str, Any]:
    """AI Tool: Get authoritative property details."""
    try:
        check_permission(context, Permission.PROPERTY_READ)
        tenant_id = resolve_tenant_scope(context)
        service = PropertyService(session)
        prop = await service.get_property(tenant_id, property_id)
        ensure_tenant_resource_access(context, prop.tenant_id)
        return _success_response(prop.model_dump(mode="json"))
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in get_property_details_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to get property details.")


async def get_project_details_tool(
    context: RequestContext,
    session: AsyncSession,
    project_id: UUID,
) -> dict[str, Any]:
    """AI Tool: Get project details (amenities, developer, location)."""
    try:
        check_permission(context, Permission.PROJECT_READ)
        tenant_id = resolve_tenant_scope(context)
        service = ProjectService(session)
        proj = await service.get_project(tenant_id, project_id)
        ensure_tenant_resource_access(context, proj.tenant_id)
        return _success_response(proj.model_dump(mode="json"))
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in get_project_details_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to get project details.")


async def get_property_availability_tool(
    context: RequestContext,
    session: AsyncSession,
    property_id: UUID,
) -> dict[str, Any]:
    """AI Tool: Check live availability status of a specific property unit."""
    try:
        check_permission(context, Permission.PROPERTY_READ)
        tenant_id = resolve_tenant_scope(context)
        service = PropertyService(session)
        prop = await service.get_property(tenant_id, property_id)
        is_available = prop.status == "available"
        return _success_response(
            {
                "property_id": str(property_id),
                "is_available": is_available,
                "status": prop.status,
                "unit_number": prop.unit_number,
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in get_property_availability_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to check property availability.")


async def check_site_visit_availability_tool(
    context: RequestContext,
    session: AsyncSession,
    sales_agent_id: UUID,
    scheduled_at: datetime,
    duration_minutes: int = 60,
) -> dict[str, Any]:
    """AI Tool: Check if sales agent has availability for a site visit slot."""
    try:
        check_permission(context, Permission.APPOINTMENT_READ)
        tenant_id = resolve_tenant_scope(context)

        service = AppointmentService(session)
        is_available = await service.check_availability(
            tenant_id, sales_agent_id, scheduled_at, duration_minutes
        )
        return _success_response(
            {
                "is_available": is_available,
                "sales_agent_id": str(sales_agent_id),
                "scheduled_at": scheduled_at.isoformat(),
                "duration_minutes": duration_minutes,
            }
        )
    except AppError as e:
        return _error_response(e.code, e.message)
    except Exception as e:
        logger.error(f"Error in check_site_visit_availability_tool: {e!s}")
        return _error_response("INTERNAL_ERROR", "Failed to check site visit availability.")
