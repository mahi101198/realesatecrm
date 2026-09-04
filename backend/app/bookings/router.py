"""Property Booking REST API Router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.bookings.schemas import (
    PropertyBookingCreate,
    PropertyBookingFilter,
    PropertyBookingResponse,
    PropertyBookingUpdate,
)
from app.bookings.service import PropertyBookingService
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(prefix="/property-bookings", tags=["Property Bookings"])


@router.post(
    "",
    response_model=PropertyBookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Property Booking",
    description="Record a token/reservation booking on a property unit.",
)
async def create_booking(
    data: PropertyBookingCreate,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_BOOKING_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyBookingResponse:
    """Create property booking endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to create a property booking.")
    service = PropertyBookingService(session)
    return await service.create_booking(tenant_id, context.user_id, data)


@router.get(
    "/{booking_id}",
    response_model=PropertyBookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Property Booking Details",
    description="Fetch details of a single property booking by ID.",
)
async def get_booking(
    booking_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_BOOKING_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyBookingResponse:
    """Get property booking details endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyBookingService(session)
    booking = await service.get_booking(tenant_id, booking_id)
    ensure_tenant_resource_access(context, booking.tenant_id)
    return booking


@router.get(
    "",
    response_model=PaginatedResponse[PropertyBookingResponse],
    status_code=status.HTTP_200_OK,
    summary="Search / List Property Bookings",
    description="List and filter property bookings with pagination.",
)
async def list_bookings(
    property_id: Annotated[UUID | None, Query(description="Filter by property ID")] = None,
    customer_id: Annotated[UUID | None, Query(description="Filter by customer ID")] = None,
    lead_id: Annotated[UUID | None, Query(description="Filter by originating lead ID")] = None,
    booking_status: Annotated[
        str | None, Query(alias="status", description="Filter by booking status")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_BOOKING_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[PropertyBookingResponse]:
    """List property bookings endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = PropertyBookingFilter(
        property_id=property_id,
        customer_id=customer_id,
        lead_id=lead_id,
        booking_status=booking_status,
    )
    pagination = PaginationParams(page=page, page_size=page_size)
    service = PropertyBookingService(session)
    return await service.list_bookings(tenant_id, filters, pagination)


@router.patch(
    "/{booking_id}",
    response_model=PropertyBookingResponse,
    status_code=status.HTTP_200_OK,
    summary="Update / Cancel Property Booking",
    description=(
        "Update booking notes, or cancel an active booking. Bookings become "
        "'converted' automatically when a sale referencing them is created."
    ),
)
async def update_booking(
    booking_id: UUID,
    data: PropertyBookingUpdate,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_BOOKING_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyBookingResponse:
    """Update property booking endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyBookingService(session)
    booking = await service.get_booking(tenant_id, booking_id)
    ensure_tenant_resource_access(context, booking.tenant_id)
    return await service.update_booking(tenant_id, booking_id, data)
