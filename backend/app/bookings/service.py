"""Property Booking Business Service Layer."""

import logging
from math import ceil
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bookings.repository import PropertyBookingRepository
from app.bookings.schemas import (
    PropertyBookingCreate,
    PropertyBookingFilter,
    PropertyBookingResponse,
    PropertyBookingUpdate,
)
from app.core.db_errors import raise_clean_error_for_write
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.shared.schemas import PaginatedResponse, PaginationParams

logger = logging.getLogger(__name__)


class PropertyBookingService:
    """Service handling business rules for property token/reservation bookings."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = PropertyBookingRepository(session)

    async def create_booking(
        self, tenant_id: UUID, created_by: UUID | None, data: PropertyBookingCreate
    ) -> PropertyBookingResponse:
        """Create a token/reservation booking after validating referenced entities."""
        await self._validate_property(tenant_id, data.property_id)
        await self._validate_customer(tenant_id, data.customer_id)
        if data.lead_id is not None:
            await self._validate_lead(tenant_id, data.lead_id)

        try:
            row = await self.repository.create(tenant_id, created_by, data)
        except DBAPIError as e:
            # Defensive: covers the narrow TOCTOU window between the pre-checks
            # above and the INSERT (e.g. the property/customer/lead was removed
            # in between), plus any other constraint this table enforces.
            logger.warning(f"Property booking create DB error: {e!s}")
            raise_clean_error_for_write(e, resource="booking")
        return PropertyBookingResponse.model_validate(row)

    async def get_booking(
        self, tenant_id: UUID | None, booking_id: UUID
    ) -> PropertyBookingResponse:
        """Fetch a property booking by ID."""
        row = await self.repository.get_by_id(tenant_id, booking_id)
        if not row:
            raise NotFoundError(
                message=f"Property booking with ID '{booking_id}' was not found.",
                code="PROPERTY_BOOKING_NOT_FOUND",
            )
        return PropertyBookingResponse.model_validate(row)

    async def update_booking(
        self, tenant_id: UUID | None, booking_id: UUID, data: PropertyBookingUpdate
    ) -> PropertyBookingResponse:
        """Update a booking. Only cancellation of an 'active' booking is allowed here --
        'converted' is set automatically by the sale-creation transaction."""
        existing = await self.repository.get_by_id(tenant_id, booking_id)
        if not existing:
            raise NotFoundError(
                message=f"Property booking with ID '{booking_id}' was not found.",
                code="PROPERTY_BOOKING_NOT_FOUND",
            )

        if data.booking_status is not None:
            if data.booking_status != "cancelled":
                raise ValidationError(
                    message=(
                        "Only 'cancelled' may be set via this endpoint. 'converted' is set "
                        "automatically when a sale referencing this booking is created."
                    ),
                    code="INVALID_BOOKING_STATUS_TRANSITION",
                )
            if existing["booking_status"] != "active":
                raise ConflictError(
                    message=(
                        f"Booking '{booking_id}' is '{existing['booking_status']}' and "
                        "cannot be cancelled."
                    ),
                    code="BOOKING_NOT_ACTIVE",
                )

        notes_provided = "notes" in data.model_fields_set
        try:
            updated_row = await self.repository.update_notes_and_status(
                tenant_id, booking_id, data.booking_status, data.notes, notes_provided
            )
        except DBAPIError as e:
            logger.warning(f"Property booking update DB error: {e!s}")
            raise_clean_error_for_write(e, resource="booking")
        if not updated_row:
            raise NotFoundError(
                message=f"Property booking with ID '{booking_id}' was not found.",
                code="PROPERTY_BOOKING_NOT_FOUND",
            )
        return PropertyBookingResponse.model_validate(updated_row)

    async def list_bookings(
        self,
        tenant_id: UUID | None,
        filters: PropertyBookingFilter,
        pagination: PaginationParams,
    ) -> PaginatedResponse[PropertyBookingResponse]:
        """Search and list property bookings with pagination."""
        rows, total = await self.repository.search(tenant_id, filters, pagination)
        items = [PropertyBookingResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0

        return PaginatedResponse[PropertyBookingResponse](
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
            pages=pages,
        )

    async def _validate_property(self, tenant_id: UUID, property_id: UUID) -> None:
        result = await self.session.execute(
            text(
                "SELECT id FROM public.properties "
                "WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL"
            ),
            {"id": property_id, "tenant_id": tenant_id},
        )
        if not result.scalar_one_or_none():
            raise NotFoundError(
                message=f"Property with ID '{property_id}' was not found in this tenant.",
                code="PROPERTY_NOT_FOUND",
            )

    async def _validate_customer(self, tenant_id: UUID, customer_id: UUID) -> None:
        result = await self.session.execute(
            text(
                "SELECT id FROM public.customers "
                "WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL"
            ),
            {"id": customer_id, "tenant_id": tenant_id},
        )
        if not result.scalar_one_or_none():
            raise NotFoundError(
                message=f"Customer with ID '{customer_id}' was not found in this tenant.",
                code="CUSTOMER_NOT_FOUND",
            )

    async def _validate_lead(self, tenant_id: UUID, lead_id: UUID) -> None:
        result = await self.session.execute(
            text(
                "SELECT id FROM public.leads "
                "WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL"
            ),
            {"id": lead_id, "tenant_id": tenant_id},
        )
        if not result.scalar_one_or_none():
            raise NotFoundError(
                message=f"Lead with ID '{lead_id}' was not found in this tenant.",
                code="LEAD_NOT_FOUND",
            )
