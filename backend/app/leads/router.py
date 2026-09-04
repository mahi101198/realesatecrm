"""Lead Domain REST API Router."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.leads.schemas import (
    LeadAssign,
    LeadCreate,
    LeadFilter,
    LeadNoteCreate,
    LeadNoteResponse,
    LeadPropertyInterestCreate,
    LeadPropertyInterestResponse,
    LeadResponse,
    LeadUpdate,
    SalesAgentResponse,
)
from app.leads.service import LeadService
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(prefix="/leads", tags=["Leads"])


@router.post(
    "",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Lead",
    description="Create a new lead opportunity for a customer within tenant context.",
)
async def create_lead(
    data: LeadCreate,
    context: RequestContext = Depends(require_permission(Permission.LEAD_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> LeadResponse:
    """Create lead endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to create a lead.")
    service = LeadService(session)
    return await service.create_lead(tenant_id, context.user_id, data)


@router.get(
    "/sales-agents",
    response_model=list[SalesAgentResponse],
    status_code=status.HTTP_200_OK,
    summary="List Assignable Sales Agents",
    description=(
        "List active sales agents assignable to leads in the caller's tenant. "
        "Registered before /{lead_id} so 'sales-agents' is never mistaken for a "
        "lead_id. The `id` returned here is a sales_agents.id -- NOT a staff "
        "user's id -- and is exactly what POST /leads/{lead_id}/assign expects."
    ),
)
async def list_lead_sales_agents(
    context: RequestContext = Depends(require_permission(Permission.LEAD_ASSIGN)),
    session: AsyncSession = Depends(get_db_session),
) -> list[SalesAgentResponse]:
    """List assignable sales agents endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    return await LeadService(session).list_sales_agents(tenant_id)


@router.get(
    "/{lead_id}",
    response_model=LeadResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Lead Details",
    description="Fetch details of a single lead by ID.",
)
async def get_lead(
    lead_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> LeadResponse:
    """Get lead details endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = LeadService(session)
    lead = await service.get_lead(tenant_id, lead_id)
    ensure_tenant_resource_access(context, lead.tenant_id)
    return lead


@router.get(
    "",
    response_model=PaginatedResponse[LeadResponse],
    status_code=status.HTTP_200_OK,
    summary="Search / List Leads",
    description="Search and filter leads with database-side pagination.",
)
async def list_leads(
    customer_id: Annotated[UUID | None, Query(description="Filter by customer ID")] = None,
    lead_status: Annotated[
        str | None, Query(alias="status", description="Filter by status")
    ] = None,
    sales_stage: Annotated[str | None, Query(description="Filter by sales stage")] = None,
    assigned_sales_agent_id: Annotated[
        UUID | None, Query(description="Filter by assigned agent")
    ] = None,
    campaign_id: Annotated[UUID | None, Query(description="Filter by campaign")] = None,
    property_type_id: Annotated[UUID | None, Query(description="Filter by property type")] = None,
    min_budget: Annotated[Decimal | None, Query(ge=0, description="Minimum budget filter")] = None,
    max_budget: Annotated[Decimal | None, Query(ge=0, description="Maximum budget filter")] = None,
    query: Annotated[str | None, Query(description="Search lead_number or location")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[LeadResponse]:
    """List leads endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = LeadFilter(
        customer_id=customer_id,
        status=lead_status,
        sales_stage=sales_stage,
        assigned_sales_agent_id=assigned_sales_agent_id,
        campaign_id=campaign_id,
        property_type_id=property_type_id,
        min_budget=min_budget,
        max_budget=max_budget,
        query=query,
    )
    pagination = PaginationParams(page=page, page_size=page_size)
    service = LeadService(session)
    return await service.list_leads(tenant_id, filters, pagination)


@router.patch(
    "/{lead_id}",
    response_model=LeadResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Lead",
    description="Update an existing lead record.",
)
async def update_lead(
    lead_id: UUID,
    data: LeadUpdate,
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> LeadResponse:
    """Update lead endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = LeadService(session)
    lead = await service.get_lead(tenant_id, lead_id)
    ensure_tenant_resource_access(context, lead.tenant_id)
    return await service.update_lead(tenant_id, lead_id, data)


@router.post(
    "/{lead_id}/assign",
    response_model=LeadResponse,
    status_code=status.HTTP_200_OK,
    summary="Assign Lead",
    description="Assign a sales agent to a lead.",
)
async def assign_lead(
    lead_id: UUID,
    assignment: LeadAssign,
    context: RequestContext = Depends(require_permission(Permission.LEAD_ASSIGN)),
    session: AsyncSession = Depends(get_db_session),
) -> LeadResponse:
    """Assign lead endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to assign a lead.")
    service = LeadService(session)
    lead = await service.get_lead(tenant_id, lead_id)
    ensure_tenant_resource_access(context, lead.tenant_id)
    return await service.assign_lead(tenant_id, lead_id, assignment, context.user_id)


@router.post(
    "/{lead_id}/property-interests",
    response_model=LeadPropertyInterestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Property Interest",
    description="Record a project or property interest for a lead.",
)
async def add_property_interest(
    lead_id: UUID,
    data: LeadPropertyInterestCreate,
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> LeadPropertyInterestResponse:
    """Add property interest endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    service = LeadService(session)
    lead = await service.get_lead(tenant_id, lead_id)
    ensure_tenant_resource_access(context, lead.tenant_id)
    return await service.add_property_interest(tenant_id, lead_id, data)


@router.get(
    "/{lead_id}/property-interests",
    response_model=list[LeadPropertyInterestResponse],
    status_code=status.HTTP_200_OK,
    summary="List Property Interests",
    description="List all property/project interests recorded for a lead.",
)
async def list_property_interests(
    lead_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[LeadPropertyInterestResponse]:
    """List property interests endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    service = LeadService(session)
    lead = await service.get_lead(tenant_id, lead_id)
    ensure_tenant_resource_access(context, lead.tenant_id)
    return await service.list_property_interests(tenant_id, lead_id)


@router.delete(
    "/{lead_id}/property-interests/{interest_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove Property Interest",
    description="Remove a property interest from a lead.",
)
async def remove_property_interest(
    lead_id: UUID,
    interest_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove property interest endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    service = LeadService(session)
    lead = await service.get_lead(tenant_id, lead_id)
    ensure_tenant_resource_access(context, lead.tenant_id)
    await service.remove_property_interest(tenant_id, lead_id, interest_id)


@router.post(
    "/{lead_id}/notes",
    response_model=LeadNoteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Lead Note",
    description="Add a note to a lead.",
)
async def add_lead_note(
    lead_id: UUID,
    data: LeadNoteCreate,
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> LeadNoteResponse:
    """Add lead note endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    service = LeadService(session)
    lead = await service.get_lead(tenant_id, lead_id)
    ensure_tenant_resource_access(context, lead.tenant_id)
    return await service.add_note(tenant_id, lead_id, context.user_id, data)


@router.get(
    "/{lead_id}/notes",
    response_model=list[LeadNoteResponse],
    status_code=status.HTTP_200_OK,
    summary="List Lead Notes",
    description="List all notes associated with a lead.",
)
async def list_lead_notes(
    lead_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[LeadNoteResponse]:
    """List lead notes endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    service = LeadService(session)
    lead = await service.get_lead(tenant_id, lead_id)
    ensure_tenant_resource_access(context, lead.tenant_id)
    return await service.list_notes(tenant_id, lead_id)
