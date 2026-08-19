"""Location REST API Router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.locations.schemas import (
    LocationCreate,
    LocationFilter,
    LocationResponse,
    LocationUpdate,
)
from app.locations.service import LocationService
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(prefix="/locations", tags=["Locations"])


@router.post(
    "",
    response_model=LocationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Location",
    description="Define a new tenant location (city/area).",
)
async def create_location(
    data: LocationCreate,
    context: RequestContext = Depends(require_permission(Permission.LOCATION_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> LocationResponse:
    """Create location endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to create a location.")
    service = LocationService(session)
    return await service.create_location(tenant_id, data)


@router.get(
    "/{location_id}",
    response_model=LocationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Location Details",
    description="Fetch details of a single location by ID.",
)
async def get_location(
    location_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.LOCATION_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> LocationResponse:
    """Get location details endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = LocationService(session)
    location = await service.get_location(tenant_id, location_id)
    ensure_tenant_resource_access(context, location.tenant_id)
    return location


@router.get(
    "",
    response_model=PaginatedResponse[LocationResponse],
    status_code=status.HTTP_200_OK,
    summary="Search / List Locations",
    description="List and filter tenant locations with pagination.",
)
async def list_locations(
    is_active: Annotated[bool | None, Query(description="Filter by active status")] = None,
    city: Annotated[str | None, Query(description="Filter by city")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.LOCATION_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[LocationResponse]:
    """List locations endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = LocationFilter(is_active=is_active, city=city)
    pagination = PaginationParams(page=page, page_size=page_size)
    service = LocationService(session)
    return await service.list_locations(tenant_id, filters, pagination)


@router.patch(
    "/{location_id}",
    response_model=LocationResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Location",
    description="Update location details, including deactivation via is_active.",
)
async def update_location(
    location_id: UUID,
    data: LocationUpdate,
    context: RequestContext = Depends(require_permission(Permission.LOCATION_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> LocationResponse:
    """Update location endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = LocationService(session)
    location = await service.get_location(tenant_id, location_id)
    ensure_tenant_resource_access(context, location.tenant_id)
    return await service.update_location(tenant_id, location_id, data)
