"""Appointment REST API Router."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.appointments.schemas import (
    AppointmentCancelRequest,
    AppointmentCreate,
    AppointmentFilter,
    AppointmentResponse,
    AppointmentUpdate,
)
from app.appointments.service import AppointmentService
from app.auth.dependencies import require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(prefix="/appointments", tags=["Appointments"])


@router.post(
    "",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Schedule Site Visit / Appointment",
    description="Book a site visit appointment with double-booking prevention.",
)
async def schedule_site_visit(
    data: AppointmentCreate,
    context: RequestContext = Depends(require_permission(Permission.APPOINTMENT_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> AppointmentResponse:
    """Schedule appointment endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to schedule an appointment.")
    service = AppointmentService(session)
    return await service.schedule_site_visit(tenant_id, context.user_id, data)


@router.get(
    "/{appointment_id}",
    response_model=AppointmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Appointment Details",
    description="Fetch details of a single appointment by ID.",
)
async def get_appointment(
    appointment_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.APPOINTMENT_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> AppointmentResponse:
    """Get appointment details endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = AppointmentService(session)
    appt = await service.get_appointment(tenant_id, appointment_id)
    ensure_tenant_resource_access(context, appt.tenant_id)
    return appt


@router.get(
    "",
    response_model=PaginatedResponse[AppointmentResponse],
    status_code=status.HTTP_200_OK,
    summary="Search / List Appointments",
    description="List and filter appointments with pagination.",
)
async def list_appointments(
    customer_id: Annotated[UUID | None, Query(description="Filter by customer ID")] = None,
    lead_id: Annotated[UUID | None, Query(description="Filter by lead ID")] = None,
    sales_agent_id: Annotated[
        UUID | None, Query(description="Filter by assigned sales agent ID")
    ] = None,
    project_id: Annotated[UUID | None, Query(description="Filter by project ID")] = None,
    appt_status: Annotated[
        str | None, Query(alias="status", description="Filter by status")
    ] = None,
    start_date: Annotated[
        datetime | None, Query(description="Filter scheduled_at on or after timestamp")
    ] = None,
    end_date: Annotated[
        datetime | None, Query(description="Filter scheduled_at on or before timestamp")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.APPOINTMENT_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[AppointmentResponse]:
    """List appointments endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = AppointmentFilter(
        customer_id=customer_id,
        lead_id=lead_id,
        sales_agent_id=sales_agent_id,
        project_id=project_id,
        status=appt_status,
        start_date=start_date,
        end_date=end_date,
    )
    pagination = PaginationParams(page=page, page_size=page_size)
    service = AppointmentService(session)
    return await service.list_appointments(tenant_id, filters, pagination)


@router.patch(
    "/{appointment_id}",
    response_model=AppointmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Appointment",
    description="Update appointment details.",
)
async def update_appointment(
    appointment_id: UUID,
    data: AppointmentUpdate,
    context: RequestContext = Depends(require_permission(Permission.APPOINTMENT_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> AppointmentResponse:
    """Update appointment endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = AppointmentService(session)
    appt = await service.get_appointment(tenant_id, appointment_id)
    ensure_tenant_resource_access(context, appt.tenant_id)
    return await service.update_appointment(tenant_id, appointment_id, data)


@router.post(
    "/{appointment_id}/cancel",
    response_model=AppointmentResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel Appointment",
    description="Cancel an appointment.",
)
async def cancel_appointment(
    appointment_id: UUID,
    data: AppointmentCancelRequest,
    context: RequestContext = Depends(require_permission(Permission.APPOINTMENT_CANCEL)),
    session: AsyncSession = Depends(get_db_session),
) -> AppointmentResponse:
    """Cancel appointment endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = AppointmentService(session)
    appt = await service.get_appointment(tenant_id, appointment_id)
    ensure_tenant_resource_access(context, appt.tenant_id)
    return await service.cancel_appointment(tenant_id, appointment_id, context.user_id, data)
