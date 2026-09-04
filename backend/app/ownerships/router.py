"""Property Ownership, Co-Owner & Resale Listing REST API Router."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.ownerships.schemas import (
    PropertyOwnershipCoOwnerCreate,
    PropertyOwnershipCoOwnerResponse,
    PropertyOwnershipFilter,
    PropertyOwnershipResponse,
    PropertyOwnershipUpdate,
    PropertyResaleListingCreate,
    PropertyResaleListingFilter,
    PropertyResaleListingResponse,
    PropertyResaleListingUpdate,
)
from app.ownerships.service import PropertyOwnershipService, PropertyResaleListingService
from app.shared.schemas import PaginatedResponse, PaginationParams

router = APIRouter(tags=["Property Ownership"])


# ---------------------------------------------------------------------------
# PROPERTY OWNERSHIPS
# ---------------------------------------------------------------------------


@router.get(
    "/property-ownerships",
    response_model=PaginatedResponse[PropertyOwnershipResponse],
    status_code=status.HTTP_200_OK,
    summary="List Ownership Records",
    description="Fetch paginated property ownership records with joined customer and property info.",
)
async def list_ownerships(
    property_id: Annotated[UUID | None, Query(description="Filter by property ID")] = None,
    customer_id: Annotated[UUID | None, Query(description="Filter by customer ID")] = None,
    ownership_status: Annotated[str | None, Query(alias="status", description="Filter by status (active/historical/reversed)")] = None,
    pagination: PaginationParams = Depends(),
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_OWNERSHIP_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[PropertyOwnershipResponse]:
    """List ownership records endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyOwnershipService(session)
    filters = PropertyOwnershipFilter(
        property_id=property_id,
        customer_id=customer_id,
        ownership_status=ownership_status,
    )
    return await service.list_ownerships(tenant_id, filters, pagination)


@router.get(
    "/property-ownerships/{property_id}/current",
    response_model=PropertyOwnershipResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Current Owner",
    description="Fetch the current (active) owner-of-record for a property.",
)
async def get_current_ownership(
    property_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_OWNERSHIP_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyOwnershipResponse:
    """Get current owner endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyOwnershipService(session)
    return await service.get_current(tenant_id, property_id)


@router.get(
    "/property-ownerships/{property_id}/history",
    response_model=list[PropertyOwnershipResponse],
    status_code=status.HTTP_200_OK,
    summary="Get Ownership History",
    description="Fetch the full ownership chain for a property, ordered by start date.",
)
async def get_ownership_history(
    property_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_OWNERSHIP_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[PropertyOwnershipResponse]:
    """Get ownership history endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyOwnershipService(session)
    return await service.get_history(tenant_id, property_id)


@router.get(
    "/property-ownerships/{ownership_id}",
    response_model=PropertyOwnershipResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Ownership Record",
    description="Fetch a single ownership record by ID.",
)
async def get_ownership(
    ownership_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_OWNERSHIP_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyOwnershipResponse:
    """Get ownership record endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyOwnershipService(session)
    ownership = await service.get_ownership(tenant_id, ownership_id)
    ensure_tenant_resource_access(context, ownership.tenant_id)
    return ownership


@router.patch(
    "/property-ownerships/{ownership_id}",
    response_model=PropertyOwnershipResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify / Reverse Ownership Record",
    description=(
        "Set verified_by and/or reverse an ownership record. Never changes who "
        "the owner is -- that only happens via a new property sale."
    ),
)
async def update_ownership(
    ownership_id: UUID,
    data: PropertyOwnershipUpdate,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_OWNERSHIP_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyOwnershipResponse:
    """Update ownership record endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyOwnershipService(session)
    ownership = await service.get_ownership(tenant_id, ownership_id)
    ensure_tenant_resource_access(context, ownership.tenant_id)
    return await service.update_ownership(tenant_id, ownership_id, data)


# ---------------------------------------------------------------------------
# CO-OWNERS
# ---------------------------------------------------------------------------


@router.post(
    "/property-ownerships/{ownership_id}/co-owners",
    response_model=PropertyOwnershipCoOwnerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Co-Owner",
    description="Add an additional owner to an ownership record (optional joint ownership).",
)
async def add_co_owner(
    ownership_id: UUID,
    data: PropertyOwnershipCoOwnerCreate,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_OWNERSHIP_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyOwnershipCoOwnerResponse:
    """Add co-owner endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    ownership_service = PropertyOwnershipService(session)
    ownership = await ownership_service.get_ownership(tenant_id, ownership_id)
    ensure_tenant_resource_access(context, ownership.tenant_id)
    return await ownership_service.add_co_owner(tenant_id, ownership_id, data)


@router.get(
    "/property-ownerships/{ownership_id}/co-owners",
    response_model=list[PropertyOwnershipCoOwnerResponse],
    status_code=status.HTTP_200_OK,
    summary="List Co-Owners",
    description="List all co-owners for an ownership record.",
)
async def list_co_owners(
    ownership_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_OWNERSHIP_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[PropertyOwnershipCoOwnerResponse]:
    """List co-owners endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    ownership_service = PropertyOwnershipService(session)
    ownership = await ownership_service.get_ownership(tenant_id, ownership_id)
    ensure_tenant_resource_access(context, ownership.tenant_id)
    return await ownership_service.list_co_owners(tenant_id, ownership_id)


@router.delete(
    "/property-ownerships/{ownership_id}/co-owners/{co_owner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove Co-Owner",
    description="Remove a co-owner from an ownership record.",
)
async def remove_co_owner(
    ownership_id: UUID,
    co_owner_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.PROPERTY_OWNERSHIP_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove co-owner endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    ownership_service = PropertyOwnershipService(session)
    ownership = await ownership_service.get_ownership(tenant_id, ownership_id)
    ensure_tenant_resource_access(context, ownership.tenant_id)
    await ownership_service.remove_co_owner(tenant_id, ownership_id, co_owner_id)


# ---------------------------------------------------------------------------
# RESALE LISTINGS
# ---------------------------------------------------------------------------


@router.post(
    "/property-resale-listings",
    response_model=PropertyResaleListingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Resale Listing",
    description="Record that the current owner wants to resell their property.",
)
async def create_resale_listing(
    data: PropertyResaleListingCreate,
    context: RequestContext = Depends(require_permission(Permission.RESALE_LISTING_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyResaleListingResponse:
    """Create resale listing endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    service = PropertyResaleListingService(session)
    return await service.create_listing(tenant_id, context.user_id, data)


@router.get(
    "/property-resale-listings/{listing_id}",
    response_model=PropertyResaleListingResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Resale Listing",
    description="Fetch details of a single resale listing by ID.",
)
async def get_resale_listing(
    listing_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.RESALE_LISTING_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyResaleListingResponse:
    """Get resale listing endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyResaleListingService(session)
    listing = await service.get_listing(tenant_id, listing_id)
    ensure_tenant_resource_access(context, listing.tenant_id)
    return listing


@router.get(
    "/property-resale-listings",
    response_model=list[PropertyResaleListingResponse],
    status_code=status.HTTP_200_OK,
    summary="List Resale Listings",
    description="List and filter resale listings.",
)
async def list_resale_listings(
    ownership_id: Annotated[UUID | None, Query(description="Filter by ownership record ID")] = None,
    listing_status: Annotated[
        str | None, Query(alias="status", description="Filter by listing status")
    ] = None,
    context: RequestContext = Depends(require_permission(Permission.RESALE_LISTING_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[PropertyResaleListingResponse]:
    """List resale listings endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    filters = PropertyResaleListingFilter(ownership_id=ownership_id, listing_status=listing_status)
    service = PropertyResaleListingService(session)
    return await service.list_listings(tenant_id, filters)


@router.patch(
    "/property-resale-listings/{listing_id}",
    response_model=PropertyResaleListingResponse,
    status_code=status.HTTP_200_OK,
    summary="Withdraw / Update Resale Listing",
    description=(
        "Withdraw an open listing, or update its asking price/notes. "
        "'converted' is set automatically when the resale closes."
    ),
)
async def update_resale_listing(
    listing_id: UUID,
    data: PropertyResaleListingUpdate,
    context: RequestContext = Depends(require_permission(Permission.RESALE_LISTING_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> PropertyResaleListingResponse:
    """Update resale listing endpoint."""
    tenant_id = resolve_tenant_scope(context)
    service = PropertyResaleListingService(session)
    listing = await service.get_listing(tenant_id, listing_id)
    ensure_tenant_resource_access(context, listing.tenant_id)
    return await service.update_listing(tenant_id, listing_id, data)
