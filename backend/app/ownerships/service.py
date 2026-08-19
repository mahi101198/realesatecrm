"""Property Ownership, Co-Owner & Resale Listing Business Service Layer."""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_errors import raise_clean_error_for_write
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.ownerships.repository import PropertyOwnershipRepository, PropertyResaleListingRepository
from app.ownerships.schemas import (
    PropertyOwnershipCoOwnerCreate,
    PropertyOwnershipCoOwnerResponse,
    PropertyOwnershipResponse,
    PropertyOwnershipUpdate,
    PropertyResaleListingCreate,
    PropertyResaleListingFilter,
    PropertyResaleListingResponse,
    PropertyResaleListingUpdate,
)

logger = logging.getLogger(__name__)

_CO_OWNER_SHARE_EXCEEDED_ERRCODE = "P0004"


class PropertyOwnershipService:
    """Service for the read/manage side of the ownership ledger. Ownership rows
    themselves are only ever CREATED by PropertySaleService.create_sale -- this
    service never changes who owns a property, only verifies/reverses records
    and manages co-owners."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = PropertyOwnershipRepository(session)

    async def get_current(
        self, tenant_id: UUID | None, property_id: UUID
    ) -> PropertyOwnershipResponse:
        """Fetch the current owner-of-record for a property."""
        row = await self.repository.get_current_for_property(tenant_id, property_id)
        if not row:
            raise NotFoundError(
                message=f"Property '{property_id}' has no current recorded owner.",
                code="NO_CURRENT_OWNER",
            )
        return PropertyOwnershipResponse.model_validate(row)

    async def get_history(
        self, tenant_id: UUID | None, property_id: UUID
    ) -> list[PropertyOwnershipResponse]:
        """Fetch the full ownership chain for a property, oldest first."""
        rows = await self.repository.get_history_for_property(tenant_id, property_id)
        return [PropertyOwnershipResponse.model_validate(r) for r in rows]

    async def get_ownership(
        self, tenant_id: UUID | None, ownership_id: UUID
    ) -> PropertyOwnershipResponse:
        """Fetch a single ownership record by ID."""
        row = await self.repository.get_by_id(tenant_id, ownership_id)
        if not row:
            raise NotFoundError(
                message=f"Ownership record with ID '{ownership_id}' was not found.",
                code="OWNERSHIP_NOT_FOUND",
            )
        return PropertyOwnershipResponse.model_validate(row)

    async def update_ownership(
        self, tenant_id: UUID | None, ownership_id: UUID, data: PropertyOwnershipUpdate
    ) -> PropertyOwnershipResponse:
        """Verify and/or reverse an ownership record. Never changes property_id/customer_id."""
        existing = await self.repository.get_by_id(tenant_id, ownership_id)
        if not existing:
            raise NotFoundError(
                message=f"Ownership record with ID '{ownership_id}' was not found.",
                code="OWNERSHIP_NOT_FOUND",
            )

        if data.ownership_status is not None:
            if data.ownership_status != "reversed":
                raise ValidationError(
                    message="Only 'reversed' may be set via this endpoint.",
                    code="INVALID_OWNERSHIP_STATUS_TRANSITION",
                )
            if existing["ownership_status"] != "active":
                raise ConflictError(
                    message=f"Ownership record '{ownership_id}' is already reversed.",
                    code="OWNERSHIP_ALREADY_REVERSED",
                )

        verified_by_provided = "verified_by" in data.model_fields_set
        try:
            updated_row = await self.repository.update(
                tenant_id,
                ownership_id,
                data.verified_by,
                verified_by_provided,
                data.ownership_status,
            )
        except DBAPIError as e:
            # Most plausibly a malformed/non-existent verified_by user UUID
            # (FK violation) -- the shared helper maps that to a clean,
            # specific ValidationError instead of a raw 500.
            logger.warning(f"Ownership update DB error: {e!s}")
            raise_clean_error_for_write(e, resource="ownership record")
        if not updated_row:
            raise NotFoundError(
                message=f"Ownership record with ID '{ownership_id}' was not found.",
                code="OWNERSHIP_NOT_FOUND",
            )
        return PropertyOwnershipResponse.model_validate(updated_row)

    async def add_co_owner(
        self, tenant_id: UUID, ownership_id: UUID, data: PropertyOwnershipCoOwnerCreate
    ) -> PropertyOwnershipCoOwnerResponse:
        """Add a co-owner, surfacing the DB's share-sum-exceeds-100 guard as a
        clean ConflictError rather than a raw DB exception."""
        ownership = await self.repository.get_by_id(tenant_id, ownership_id)
        if not ownership:
            raise NotFoundError(
                message=f"Ownership record with ID '{ownership_id}' was not found.",
                code="OWNERSHIP_NOT_FOUND",
            )

        await self._validate_customer(tenant_id, data.customer_id)

        try:
            row = await self.repository.add_co_owner(tenant_id, ownership_id, data)
        except DBAPIError as e:
            orig = getattr(e, "orig", None)
            orig_msg = str(orig if orig is not None else e).lower()
            sqlstate = getattr(orig, "sqlstate", None)
            logger.warning(f"Add co-owner database exception (sqlstate={sqlstate}): {orig_msg}")
            if sqlstate == _CO_OWNER_SHARE_EXCEEDED_ERRCODE or "exceeds 100" in orig_msg:
                raise ConflictError(
                    message=(
                        "Total co-owner share_percentage for this ownership "
                        "record would exceed 100."
                    ),
                    code="CO_OWNER_SHARE_EXCEEDS_100",
                ) from e
            if "uq_co_owners_ownership_customer" in orig_msg or (
                "duplicate key" in orig_msg and "customer" in orig_msg
            ):
                raise ConflictError(
                    message="This customer is already a co-owner of this ownership record.",
                    code="DUPLICATE_CO_OWNER",
                ) from e
            raise_clean_error_for_write(e, resource="co-owner")
        return PropertyOwnershipCoOwnerResponse.model_validate(row)

    async def list_co_owners(
        self, tenant_id: UUID, ownership_id: UUID
    ) -> list[PropertyOwnershipCoOwnerResponse]:
        """List co-owners for an ownership record."""
        ownership = await self.repository.get_by_id(tenant_id, ownership_id)
        if not ownership:
            raise NotFoundError(
                message=f"Ownership record with ID '{ownership_id}' was not found.",
                code="OWNERSHIP_NOT_FOUND",
            )
        rows = await self.repository.list_co_owners(tenant_id, ownership_id)
        return [PropertyOwnershipCoOwnerResponse.model_validate(r) for r in rows]

    async def remove_co_owner(self, tenant_id: UUID, ownership_id: UUID, co_owner_id: UUID) -> None:
        """Remove a co-owner from an ownership record."""
        success = await self.repository.delete_co_owner(tenant_id, ownership_id, co_owner_id)
        if not success:
            raise NotFoundError(
                message=f"Co-owner '{co_owner_id}' was not found for ownership record "
                f"'{ownership_id}'.",
                code="CO_OWNER_NOT_FOUND",
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


class PropertyResaleListingService:
    """Service handling business rules for the pre-transaction 'owner wants to
    resell' intent. listing_status = 'converted' is set automatically by
    PropertySaleService.create_sale, never through this service."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = PropertyResaleListingRepository(session)
        self.ownership_repository = PropertyOwnershipRepository(session)

    async def create_listing(
        self, tenant_id: UUID, created_by: UUID | None, data: PropertyResaleListingCreate
    ) -> PropertyResaleListingResponse:
        """Create a resale listing after validating ownership_id is the property's
        currently-active owner-of-record."""
        ownership = await self.ownership_repository.get_by_id(tenant_id, data.ownership_id)
        if not ownership:
            raise NotFoundError(
                message=f"Ownership record with ID '{data.ownership_id}' was not found.",
                code="OWNERSHIP_NOT_FOUND",
            )
        if ownership["ownership_end_date"] is not None:
            raise ValidationError(
                message=(
                    f"Ownership record '{data.ownership_id}' is not the current owner of "
                    "this property (it has already ended) -- a resale listing can only be "
                    "created against the active ownership record."
                ),
                code="OWNERSHIP_NOT_CURRENT",
            )

        try:
            row = await self.repository.create(tenant_id, created_by, data)
        except DBAPIError as e:
            orig_msg = str(getattr(e, "orig", e)).lower()
            logger.warning(f"Resale listing create DB error: {orig_msg}")
            if "uq_property_resale_listings_one_open_per_ownership" in orig_msg or (
                "duplicate key" in orig_msg
            ):
                raise ConflictError(
                    message=(
                        f"Ownership record '{data.ownership_id}' already has "
                        "an open resale listing."
                    ),
                    code="DUPLICATE_OPEN_RESALE_LISTING",
                ) from e
            raise_clean_error_for_write(e, resource="resale listing")
        return PropertyResaleListingResponse.model_validate(row)

    async def get_listing(
        self, tenant_id: UUID | None, listing_id: UUID
    ) -> PropertyResaleListingResponse:
        """Fetch a resale listing by ID."""
        row = await self.repository.get_by_id(tenant_id, listing_id)
        if not row:
            raise NotFoundError(
                message=f"Resale listing with ID '{listing_id}' was not found.",
                code="RESALE_LISTING_NOT_FOUND",
            )
        return PropertyResaleListingResponse.model_validate(row)

    async def update_listing(
        self, tenant_id: UUID | None, listing_id: UUID, data: PropertyResaleListingUpdate
    ) -> PropertyResaleListingResponse:
        """Withdraw an open listing, or update its asking price/notes.
        listing_status='converted' is rejected here -- it is set automatically."""
        existing = await self.repository.get_by_id(tenant_id, listing_id)
        if not existing:
            raise NotFoundError(
                message=f"Resale listing with ID '{listing_id}' was not found.",
                code="RESALE_LISTING_NOT_FOUND",
            )

        if data.listing_status is not None:
            if data.listing_status != "withdrawn":
                raise ValidationError(
                    message="Only 'withdrawn' may be set via this endpoint.",
                    code="INVALID_LISTING_STATUS_TRANSITION",
                )
            if existing["listing_status"] != "open":
                raise ConflictError(
                    message=f"Listing '{listing_id}' is '{existing['listing_status']}', not open.",
                    code="LISTING_NOT_OPEN",
                )

        update_dict = data.model_dump(exclude_unset=True)
        try:
            updated_row = await self.repository.update(tenant_id, listing_id, update_dict)
        except DBAPIError as e:
            logger.warning(f"Resale listing update DB error: {e!s}")
            raise_clean_error_for_write(e, resource="resale listing")
        if not updated_row:
            raise NotFoundError(
                message=f"Resale listing with ID '{listing_id}' was not found.",
                code="RESALE_LISTING_NOT_FOUND",
            )
        return PropertyResaleListingResponse.model_validate(updated_row)

    async def list_listings(
        self, tenant_id: UUID, filters: PropertyResaleListingFilter
    ) -> list[PropertyResaleListingResponse]:
        """List resale listings with filtering."""
        rows = await self.repository.search(tenant_id, filters)
        return [PropertyResaleListingResponse.model_validate(r) for r in rows]
