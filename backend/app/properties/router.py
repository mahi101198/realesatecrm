"""Property REST API Router."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.properties.schemas import (
    ConstructionMilestoneCreate,
    ConstructionMilestoneResponse,
    ConstructionMilestoneUpdate,
    PropertyCreate,
    PropertyDetailResponse,
    PropertyReserveRequest,
    PropertyResponse,
    PropertySearchFilter,
    PropertyUpdate,
)
from app.properties.service import PropertyService
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(prefix="/properties", tags=["Properties"])


@router.post(
    "",
    response_model=PropertyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Property",
    description="Create a new property inventory unit within a project.",
)
async def create_property(
    data: PropertyCreate,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyResponse:
    """Create property endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to create a property.")
    service = PropertyService(session)
    return await service.create_property(tenant_id, context.user_id, data)


@router.get(
    "/{property_id}",
    response_model=PropertyResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Property Details",
    description="Fetch details of a single property inventory unit by ID.",
)
async def get_property(
    property_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyResponse:
    """Get property details endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyService(session)
    prop = await service.get_property(tenant_id, property_id)
    ensure_tenant_resource_access(context, prop.tenant_id)
    return prop


@router.get(
    "/{property_id}/detail",
    response_model=PropertyDetailResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Property Detail View",
    description=(
        "Aggregated property detail: base fields, project/location context, "
        "construction status and milestones, current owner and full ownership "
        "history (with co-owners per period), any open resale listing, and "
        "current prices. Does not change the plain GET /{property_id} contract."
    ),
)
async def get_property_detail(
    property_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyDetailResponse:
    """Get aggregated property detail view endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyService(session)
    detail = await service.get_property_detail(tenant_id, property_id)
    ensure_tenant_resource_access(context, detail.property.tenant_id)
    return detail


@router.get(
    "",
    response_model=PaginatedResponse[PropertyResponse],
    status_code=status.HTTP_200_OK,
    summary="Search / List Properties",
    description="Search property inventory using database-side structured filters.",
)
async def list_properties(
    project_id: Annotated[UUID | None, Query(description="Filter by project ID")] = None,
    property_type_id: Annotated[
        UUID | None, Query(description="Filter by property type ID")
    ] = None,
    prop_status: Annotated[
        str | None, Query(alias="status", description="Filter by availability status")
    ] = None,
    min_budget: Annotated[Decimal | None, Query(ge=0, description="Minimum price filter")] = None,
    max_budget: Annotated[Decimal | None, Query(ge=0, description="Maximum price filter")] = None,
    min_area: Annotated[
        Decimal | None, Query(ge=0, description="Minimum plot/built-up area filter")
    ] = None,
    max_area: Annotated[
        Decimal | None, Query(ge=0, description="Maximum plot/built-up area filter")
    ] = None,
    bedrooms: Annotated[int | None, Query(ge=0, description="Filter by number of bedrooms")] = None,
    facing: Annotated[str | None, Query(description="Filter by orientation/facing")] = None,
    is_corner: Annotated[bool | None, Query(description="Filter corner properties")] = None,
    query: Annotated[
        str | None, Query(description="Search property_code, unit_number, block")
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[PropertyResponse]:
    """List properties endpoint."""
    tenant_id = resolve_tenant_scope(context)
    filters = PropertySearchFilter(
        project_id=project_id,
        property_type_id=property_type_id,
        status=prop_status,
        min_budget=min_budget,
        max_budget=max_budget,
        min_area=min_area,
        max_area=max_area,
        bedrooms=bedrooms,
        facing=facing,
        is_corner=is_corner,
        query=query,
    )
    pagination = PaginationParams(page=page, page_size=page_size)
    service = PropertyService(session)
    return await service.search_properties(tenant_id, filters, pagination)


@router.patch(
    "/{property_id}",
    response_model=PropertyResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Property",
    description=(
        "Update property inventory unit details. Status transitions are not accepted "
        "here -- use POST /properties/{id}/reserve or a property sale instead."
    ),
)
async def update_property(
    property_id: UUID,
    data: PropertyUpdate,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyResponse:
    """Update property endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyService(session)
    prop = await service.get_property(tenant_id, property_id)
    ensure_tenant_resource_access(context, prop.tenant_id)
    return await service.update_property(tenant_id, property_id, data)


@router.post(
    "/{property_id}/construction-milestones",
    response_model=ConstructionMilestoneResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register Construction Milestone",
    description=(
        "Register a construction milestone (foundation/structure/"
        "brickwork_and_plastering/electrical_and_plumbing/finishing/handover) for a "
        "property. Only registers the one specified stage, not all six -- register "
        "each stage a property actually needs tracked."
    ),
)
async def create_construction_milestone(
    property_id: UUID,
    data: ConstructionMilestoneCreate,
    context: RequestContext = Depends(require_permission(Permission.CONSTRUCTION_MILESTONE_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> ConstructionMilestoneResponse:
    """Create construction milestone endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to create a construction milestone.")
    service = PropertyService(session)
    prop = await service.get_property(tenant_id, property_id)
    ensure_tenant_resource_access(context, prop.tenant_id)
    return await service.create_construction_milestone(tenant_id, property_id, data)


@router.patch(
    "/{property_id}/construction-milestones/{milestone}",
    response_model=ConstructionMilestoneResponse,
    status_code=status.HTTP_200_OK,
    summary="Progress Construction Milestone",
    description=(
        "Update an already-registered milestone's status/dates/verifier/notes. "
        "Keyed by the milestone name itself (the natural key is (property_id, "
        "milestone), not a separate row id) -- 404s if that stage hasn't been "
        "registered yet via POST, rather than silently creating it."
    ),
)
async def update_construction_milestone(
    property_id: UUID,
    milestone: str,
    data: ConstructionMilestoneUpdate,
    context: RequestContext = Depends(require_permission(Permission.CONSTRUCTION_MILESTONE_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> ConstructionMilestoneResponse:
    """Update construction milestone endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to update a construction milestone.")
    service = PropertyService(session)
    prop = await service.get_property(tenant_id, property_id)
    ensure_tenant_resource_access(context, prop.tenant_id)
    return await service.update_construction_milestone(tenant_id, property_id, milestone, data)


@router.post(
    "/{property_id}/reserve",
    response_model=PropertyResponse,
    status_code=status.HTTP_200_OK,
    summary="Reserve Property Unit",
    description="Concurrently reserve or hold a property unit using SELECT FOR UPDATE row locking.",
)
async def reserve_property(
    property_id: UUID,
    data: PropertyReserveRequest,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyResponse:
    """Reserve property endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to reserve a property.")
    service = PropertyService(session)
    prop = await service.get_property(tenant_id, property_id)
    ensure_tenant_resource_access(context, prop.tenant_id)
    return await service.reserve_property(tenant_id, property_id, data, context.user_id)
