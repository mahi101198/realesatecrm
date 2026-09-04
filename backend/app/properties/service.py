"""Property Service Layer."""

import logging
from math import ceil
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_errors import raise_clean_error_for_write
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.properties.repository import PropertyRepository
from app.properties.schemas import (
    KNOWN_CONSTRUCTION_MILESTONES,
    ConstructionMilestoneCreate,
    ConstructionMilestoneResponse,
    ConstructionMilestoneUpdate,
    PropertyCreate,
    PropertyDetailCoOwner,
    PropertyDetailLocationContext,
    PropertyDetailMilestone,
    PropertyDetailOwnershipPeriod,
    PropertyDetailPrice,
    PropertyDetailProjectContext,
    PropertyDetailResaleListing,
    PropertyDetailResponse,
    PropertyReserveRequest,
    PropertyResponse,
    PropertySearchFilter,
    PropertyUpdate,
)
from app.shared.schemas import PaginatedResponse, PaginationParams

logger = logging.getLogger(__name__)


class PropertyService:
    """Service for property inventory search, detail lookup, and concurrency-safe reservation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = PropertyRepository(session)

    async def create_property(
        self, tenant_id: UUID, created_by: UUID | None, data: PropertyCreate
    ) -> PropertyResponse:
        """Create a property unit after validating its parent project and type."""
        proj_check = await self.session.execute(
            text(
                "SELECT id FROM public.projects "
                "WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL"
            ),
            {"id": data.project_id, "tenant_id": tenant_id},
        )
        if not proj_check.scalar_one_or_none():
            raise NotFoundError(
                message=f"Project with ID '{data.project_id}' was not found in this tenant.",
                code="PROJECT_NOT_FOUND",
            )

        type_check = await self.session.execute(
            text("SELECT id FROM public.property_types WHERE id = :id AND is_active = true"),
            {"id": data.property_type_id},
        )
        if not type_check.scalar_one_or_none():
            raise ValidationError(
                message=f"Property type '{data.property_type_id}' was not found or is inactive.",
                code="PROPERTY_TYPE_NOT_FOUND",
            )

        try:
            row = await self.repository.create(tenant_id, created_by, data)
        except DBAPIError as e:
            logger.warning(f"Property create DB error: {e!s}")
            orig_msg = str(getattr(e, "orig", e)).lower()
            if "uq_properties_project_code" in orig_msg or (
                "duplicate key" in orig_msg and "property_code" in orig_msg
            ):
                raise ConflictError(
                    message=(
                        f"A property with code '{data.property_code}' already exists "
                        "in this project."
                    ),
                    code="DUPLICATE_PROPERTY_CODE",
                ) from e
            raise_clean_error_for_write(e, resource="property")
        return PropertyResponse.model_validate(row)

    async def update_property(
        self, tenant_id: UUID | None, property_id: UUID, data: PropertyUpdate
    ) -> PropertyResponse:
        """Update property details. Status transitions are deliberately excluded --
        use reserve_property() or the sale-creation flow instead."""
        existing = await self.repository.get_by_id(tenant_id, property_id)
        if not existing:
            raise NotFoundError(
                message=f"Property with ID '{property_id}' was not found.",
                code="PROPERTY_NOT_FOUND",
            )

        if data.property_type_id is not None:
            type_check = await self.session.execute(
                text("SELECT id FROM public.property_types WHERE id = :id AND is_active = true"),
                {"id": data.property_type_id},
            )
            if not type_check.scalar_one_or_none():
                raise ValidationError(
                    message=(
                        f"Property type '{data.property_type_id}' was not found or is inactive."
                    ),
                    code="PROPERTY_TYPE_NOT_FOUND",
                )

        try:
            updated_row = await self.repository.update(tenant_id, property_id, data)
        except DBAPIError as e:
            logger.warning(f"Property update DB error: {e!s}")
            raise_clean_error_for_write(e, resource="property")
        if not updated_row:
            raise NotFoundError(
                message=f"Property with ID '{property_id}' was not found.",
                code="PROPERTY_NOT_FOUND",
            )
        return PropertyResponse.model_validate(updated_row)

    async def create_construction_milestone(
        self, tenant_id: UUID, property_id: UUID, data: ConstructionMilestoneCreate
    ) -> ConstructionMilestoneResponse:
        """Register a construction milestone for a property. Each milestone
        is created individually (not all 6 auto-created up front) -- staff
        deliberately register only the stages they intend to track; a plot
        that never gets built on, for instance, may never need any."""
        if data.milestone not in KNOWN_CONSTRUCTION_MILESTONES:
            raise ValidationError(
                message=f"'{data.milestone}' is not a recognized construction milestone.",
                code="INVALID_MILESTONE",
            )

        existing_property = await self.repository.get_by_id(tenant_id, property_id)
        if not existing_property:
            raise NotFoundError(
                message=f"Property with ID '{property_id}' was not found.",
                code="PROPERTY_NOT_FOUND",
            )

        try:
            row = await self.repository.create_construction_milestone(
                tenant_id, property_id, data.milestone, data.target_date, data.notes
            )
        except DBAPIError as e:
            logger.warning(f"Construction milestone create DB error: {e!s}")
            orig_msg = str(getattr(e, "orig", e)).lower()
            if "uq_property_construction_milestones_property_stage" in orig_msg or (
                "duplicate key" in orig_msg
            ):
                raise ConflictError(
                    message=(
                        f"Milestone '{data.milestone}' has already been registered "
                        "for this property."
                    ),
                    code="DUPLICATE_MILESTONE",
                ) from e
            raise_clean_error_for_write(e, resource="construction milestone")
        return ConstructionMilestoneResponse.model_validate(row)

    async def update_construction_milestone(
        self,
        tenant_id: UUID,
        property_id: UUID,
        milestone: str,
        data: ConstructionMilestoneUpdate,
    ) -> ConstructionMilestoneResponse:
        """Progress an existing milestone. Never creates one -- PATCH on a
        milestone that hasn't been registered yet 404s, it does not silently
        create it (see create_construction_milestone's docstring)."""
        if milestone not in KNOWN_CONSTRUCTION_MILESTONES:
            raise ValidationError(
                message=f"'{milestone}' is not a recognized construction milestone.",
                code="INVALID_MILESTONE",
            )

        existing = await self.repository.get_construction_milestone_by_stage(
            tenant_id, property_id, milestone
        )
        if not existing:
            raise NotFoundError(
                message=(
                    f"Milestone '{milestone}' has not been registered for property "
                    f"'{property_id}' yet."
                ),
                code="MILESTONE_NOT_FOUND",
            )

        data_dict = data.model_dump(exclude_unset=True)

        # Invariant: actual_completion_date only makes sense once the stage is
        # actually complete -- check against whichever status will be in effect
        # after this update (the new status if being changed, else the existing one).
        if data_dict.get("actual_completion_date") is not None:
            effective_status = data_dict.get("status", existing["status"])
            if effective_status != "completed":
                raise ValidationError(
                    message=(
                        "actual_completion_date can only be set when the milestone's "
                        "status is (or is being set to) 'completed'."
                    ),
                    code="ACTUAL_COMPLETION_DATE_REQUIRES_COMPLETED_STATUS",
                )

        try:
            updated_row = await self.repository.update_construction_milestone(
                tenant_id, property_id, milestone, data_dict
            )
        except DBAPIError as e:
            logger.warning(f"Construction milestone update DB error: {e!s}")
            raise_clean_error_for_write(e, resource="construction milestone")
        if not updated_row:
            raise NotFoundError(
                message=(
                    f"Milestone '{milestone}' has not been registered for property "
                    f"'{property_id}' yet."
                ),
                code="MILESTONE_NOT_FOUND",
            )
        return ConstructionMilestoneResponse.model_validate(updated_row)

    async def get_property(self, tenant_id: UUID | None, property_id: UUID) -> PropertyResponse:
        """Fetch property inventory record by ID."""
        row = await self.repository.get_by_id(tenant_id, property_id)
        if not row:
            raise NotFoundError(
                message=f"Property with ID '{property_id}' was not found.",
                code="PROPERTY_NOT_FOUND",
            )
        return PropertyResponse.model_validate(row)

    async def get_property_detail(
        self, tenant_id: UUID | None, property_id: UUID
    ) -> PropertyDetailResponse:
        """Aggregated detail view: base fields, project/location context,
        construction milestones, full ownership chain (with co-owners per
        period), any open resale listing, and current prices.

        Uses a small, fixed number of targeted queries (never one query per
        ownership row) -- see PropertyRepository's detail-view helper methods.
        """
        row = await self.repository.get_by_id(tenant_id, property_id)
        if not row:
            raise NotFoundError(
                message=f"Property with ID '{property_id}' was not found.",
                code="PROPERTY_NOT_FOUND",
            )
        prop = PropertyResponse.model_validate(row)
        effective_tenant: UUID = tenant_id or row["tenant_id"]

        project_row = await self.repository.get_project_context(effective_tenant, row["project_id"])
        project_context = None
        if project_row:
            location_context = None
            if project_row.get("location_id"):
                location_context = PropertyDetailLocationContext(
                    id=project_row["location_id"],
                    name=project_row["location_name"],
                    city=project_row["location_city"],
                )
            project_context = PropertyDetailProjectContext(
                id=project_row["id"],
                name=project_row["name"],
                slug=project_row["slug"],
                city=project_row["city"],
                state=project_row["state"],
                location=location_context,
            )

        milestone_rows = await self.repository.get_construction_milestones(
            effective_tenant, property_id
        )
        milestones = [PropertyDetailMilestone.model_validate(m) for m in milestone_rows]

        ownership_rows = await self.repository.get_ownership_history_with_co_owners(
            effective_tenant, property_id
        )
        ownership_history: list[PropertyDetailOwnershipPeriod] = []
        current_owner: PropertyDetailOwnershipPeriod | None = None
        for o in ownership_rows:
            period = PropertyDetailOwnershipPeriod(
                id=o["id"],
                customer_id=o["customer_id"],
                customer_name=o.get("customer_name"),
                customer_phone=o.get("customer_phone"),
                customer_email=o.get("customer_email"),
                customer_city=o.get("customer_city"),
                sale_amount=o.get("sale_amount"),
                sale_date=o.get("sale_date"),
                purchase_purpose=o["purchase_purpose"],
                previous_ownership_id=o["previous_ownership_id"],
                ownership_start_date=o["ownership_start_date"],
                ownership_end_date=o["ownership_end_date"],
                ownership_status=o["ownership_status"],
                co_owners=[PropertyDetailCoOwner.model_validate(c) for c in o["co_owners"]],
            )
            ownership_history.append(period)
            if period.ownership_end_date is None:
                current_owner = period

        open_resale_listing = None
        if current_owner is not None:
            listing_row = await self.repository.get_open_resale_listing(
                effective_tenant, current_owner.id
            )
            if listing_row:
                open_resale_listing = PropertyDetailResaleListing.model_validate(listing_row)

        price_rows = await self.repository.get_current_prices(effective_tenant, property_id)
        current_prices = [PropertyDetailPrice.model_validate(p) for p in price_rows]

        return PropertyDetailResponse(
            property=prop,
            project=project_context,
            construction_milestones=milestones,
            current_owner=current_owner,
            ownership_history=ownership_history,
            open_resale_listing=open_resale_listing,
            current_prices=current_prices,
        )

    async def search_properties(
        self,
        tenant_id: UUID | None,
        filters: PropertySearchFilter,
        pagination: PaginationParams,
    ) -> PaginatedResponse[PropertyResponse]:
        """Search available/all properties with database-side filtering."""
        rows, total = await self.repository.search(tenant_id, filters, pagination)
        items = [PropertyResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0

        return PaginatedResponse[PropertyResponse](
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
            pages=pages,
        )

    async def reserve_property(
        self,
        tenant_id: UUID,
        property_id: UUID,
        data: PropertyReserveRequest,
        actor_user_id: UUID | None,
    ) -> PropertyResponse:
        """Reserve or hold a property unit using atomic database row locking."""
        if data.new_status not in ("hold", "reserved"):
            raise ValidationError(
                message="Invalid reservation status. Must be 'hold' or 'reserved'.",
                code="INVALID_RESERVATION_STATUS",
            )

        existing = await self.repository.get_by_id(tenant_id, property_id)
        if not existing:
            raise NotFoundError(
                message=f"Property with ID '{property_id}' was not found.",
                code="PROPERTY_NOT_FOUND",
            )

        if existing["status"] != "available":
            raise ConflictError(
                message=f"Property '{property_id}' is not available.",
                code="PROPERTY_NOT_AVAILABLE",
            )

        try:
            row = await self.repository.reserve_property(
                tenant_id, property_id, data, actor_user_id
            )
            return PropertyResponse.model_validate(row)
        except DBAPIError as e:
            logger.warning(f"Property reservation database exception: {e}")
            raise ConflictError(
                message="Property is no longer available or is being reserved by another request.",
                code="PROPERTY_NOT_AVAILABLE",
            ) from e
