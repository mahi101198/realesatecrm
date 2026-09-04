"""Follow-up REST API Router."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.followups.schemas import (
    FollowUpCompleteRequest,
    FollowUpCreate,
    FollowUpFilter,
    FollowUpResponse,
    FollowUpUpdate,
)
from app.followups.service import FollowUpService
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(prefix="/followups", tags=["Follow-ups"])


@router.post(
    "",
    response_model=FollowUpResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Follow-up Task",
    description="Create a new follow-up task for a lead.",
)
async def create_followup(
    data: FollowUpCreate,
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> FollowUpResponse:
    """Create follow-up endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to create a follow-up.")
    service = FollowUpService(session)
    return await service.create_followup(tenant_id, context.user_id, data)


@router.get(
    "/{followup_id}",
    response_model=FollowUpResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Follow-up Details",
    description="Fetch details of a single follow-up task by ID.",
)
async def get_followup(
    followup_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> FollowUpResponse:
    """Get follow-up details endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = FollowUpService(session)
    fu = await service.get_followup(tenant_id, followup_id)
    ensure_tenant_resource_access(context, fu.tenant_id)
    return fu


@router.get(
    "",
    response_model=PaginatedResponse[FollowUpResponse],
    status_code=status.HTTP_200_OK,
    summary="Search / List Follow-ups",
    description="List and filter follow-up tasks with pagination.",
)
async def list_followups(
    lead_id: Annotated[UUID | None, Query(description="Filter by lead ID")] = None,
    customer_id: Annotated[UUID | None, Query(description="Filter by customer ID")] = None,
    assigned_sales_agent_id: Annotated[
        UUID | None, Query(description="Filter by assigned sales agent ID")
    ] = None,
    fu_status: Annotated[str | None, Query(alias="status", description="Filter by status")] = None,
    follow_up_type: Annotated[str | None, Query(description="Filter by follow-up type")] = None,
    scheduled_before: Annotated[
        datetime | None, Query(description="Scheduled on or before timestamp")
    ] = None,
    scheduled_after: Annotated[
        datetime | None, Query(description="Scheduled on or after timestamp")
    ] = None,
    query: Annotated[
        str | None, Query(description="Search customer name, phone, lead, or reason")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.LEAD_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[FollowUpResponse]:
    """List follow-ups endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = FollowUpFilter(
        lead_id=lead_id,
        customer_id=customer_id,
        assigned_sales_agent_id=assigned_sales_agent_id,
        status=fu_status,
        follow_up_type=follow_up_type,
        scheduled_before=scheduled_before,
        scheduled_after=scheduled_after,
        query=query,
    )
    pagination = PaginationParams(page=page, page_size=page_size)
    service = FollowUpService(session)
    return await service.list_followups(tenant_id, filters, pagination)


@router.patch(
    "/{followup_id}",
    response_model=FollowUpResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Follow-up",
    description="Update follow-up task details.",
)
async def update_followup(
    followup_id: UUID,
    data: FollowUpUpdate,
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> FollowUpResponse:
    """Update follow-up endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = FollowUpService(session)
    fu = await service.get_followup(tenant_id, followup_id)
    ensure_tenant_resource_access(context, fu.tenant_id)
    return await service.update_followup(tenant_id, followup_id, data)


@router.post(
    "/{followup_id}/complete",
    response_model=FollowUpResponse,
    status_code=status.HTTP_200_OK,
    summary="Complete Follow-up Task",
    description="Mark a follow-up task as completed.",
)
async def complete_followup(
    followup_id: UUID,
    data: FollowUpCompleteRequest,
    context: RequestContext = Depends(require_permission(Permission.LEAD_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> FollowUpResponse:
    """Complete follow-up endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = FollowUpService(session)
    fu = await service.get_followup(tenant_id, followup_id)
    ensure_tenant_resource_access(context, fu.tenant_id)
    return await service.complete_followup(tenant_id, followup_id, context.user_id, data)
