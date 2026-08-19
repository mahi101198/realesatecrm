"""Location Business Service Layer."""

import logging
from math import ceil
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.locations.repository import LocationRepository
from app.locations.schemas import (
    LocationCreate,
    LocationFilter,
    LocationResponse,
    LocationUpdate,
)
from app.shared.schemas import PaginatedResponse, PaginationParams

logger = logging.getLogger(__name__)


class LocationService:
    """Service handling business rules for tenant Locations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = LocationRepository(session)

    async def create_location(self, tenant_id: UUID, data: LocationCreate) -> LocationResponse:
        """Create a location after a case/whitespace-insensitive duplicate check."""
        existing = await self.repository.get_by_normalized_name_city(
            tenant_id, data.name, data.city
        )
        if existing:
            raise ConflictError(
                message=(
                    f"A location named '{data.name}' in '{data.city}' already exists "
                    "for this tenant."
                ),
                code="DUPLICATE_LOCATION",
            )

        try:
            row = await self.repository.create(tenant_id, data)
        except IntegrityError as e:
            # Defensive backstop for the race between the pre-check above and the
            # INSERT: the DB's uq_locations_tenant_name_city index is the real guard.
            logger.warning(f"Location create integrity conflict: {e!s}")
            raise ConflictError(
                message=(
                    f"A location named '{data.name}' in '{data.city}' already exists "
                    "for this tenant."
                ),
                code="DUPLICATE_LOCATION",
            ) from e
        return LocationResponse.model_validate(row)

    async def get_location(self, tenant_id: UUID | None, location_id: UUID) -> LocationResponse:
        """Fetch a location by ID."""
        row = await self.repository.get_by_id(tenant_id, location_id)
        if not row:
            raise NotFoundError(
                message=f"Location with ID '{location_id}' was not found.",
                code="LOCATION_NOT_FOUND",
            )
        return LocationResponse.model_validate(row)

    async def update_location(
        self, tenant_id: UUID | None, location_id: UUID, data: LocationUpdate
    ) -> LocationResponse:
        """Update location details, including deactivation via is_active."""
        existing = await self.repository.get_by_id(tenant_id, location_id)
        if not existing:
            raise NotFoundError(
                message=f"Location with ID '{location_id}' was not found.",
                code="LOCATION_NOT_FOUND",
            )

        try:
            updated_row = await self.repository.update(tenant_id, location_id, data)
        except IntegrityError as e:
            logger.warning(f"Location update integrity conflict: {e!s}")
            raise ConflictError(
                message="A location with this name and city already exists for this tenant.",
                code="DUPLICATE_LOCATION",
            ) from e

        if not updated_row:
            raise NotFoundError(
                message=f"Location with ID '{location_id}' was not found.",
                code="LOCATION_NOT_FOUND",
            )
        return LocationResponse.model_validate(updated_row)

    async def list_locations(
        self,
        tenant_id: UUID | None,
        filters: LocationFilter,
        pagination: PaginationParams,
    ) -> PaginatedResponse[LocationResponse]:
        """Search and list locations with pagination."""
        rows, total = await self.repository.search(tenant_id, filters, pagination)
        items = [LocationResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0

        return PaginatedResponse[LocationResponse](
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
            pages=pages,
        )
