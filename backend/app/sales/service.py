"""Property Sale & Sale Payment Business Service Layer.

PropertySaleService.create_sale is the single most concurrency-sensitive
operation in this domain: creating a property_sales row is the moment
ownership transfers. The whole thing runs inside one DB transaction using
SELECT ... FOR UPDATE row locking on the property (and, if referenced, the
booking) -- the same idiom the existing reserve_property() SQL function uses
-- so two concurrent attempts to sell the same property can never both
succeed, and a resale can never race against another resale of the same unit.
"""

import logging
from math import ceil
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_errors import raise_clean_error_for_write
from app.core.exceptions import ConflictError, NotFoundError, ValidationError
from app.db.transaction import atomic
from app.sales.repository import PropertySalePaymentRepository, PropertySaleRepository
from app.sales.schemas import (
    PropertySaleBalanceResponse,
    PropertySaleCreate,
    PropertySaleFilter,
    PropertySalePaymentCreate,
    PropertySalePaymentFilter,
    PropertySalePaymentResponse,
    PropertySalePaymentUpdate,
    PropertySaleResponse,
    PropertySaleUpdate,
)
from app.shared.schemas import PaginatedResponse, PaginationParams

logger = logging.getLogger(__name__)

_SELLABLE_PROPERTY_STATUSES = ("reserved", "hold")
_VALID_SALE_STATUS_TRANSITIONS = ("cancelled", "reversed")


class PropertySaleService:
    """Service orchestrating the sale-creation transaction (ownership transfer) and
    the read/cancel side of property_sales."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = PropertySaleRepository(session)

    async def create_sale(
        self, tenant_id: UUID, created_by: UUID | None, data: PropertySaleCreate
    ) -> PropertySaleResponse:
        """Atomically record a sale and transfer ownership.

        Steps, all inside one transaction:
          1. Lock the property row FOR UPDATE; require status IN ('reserved', 'hold') --
             selling a property that was never reserved is a business-rule violation.
          2. If booking_id is given, lock and validate that booking (active, same
             property), then insert the sale, mark the booking 'converted', and mark
             its originating lead 'converted' (if any).
          3. Look up the property's current active ownership row (FOR UPDATE). If one
             exists, this is a resale: close it out and chain the new ownership row's
             previous_ownership_id to it, auto-converting any open resale listing tied
             to it. If none exists, this is a primary sale (previous_ownership_id NULL).
          4. Flip properties.status to 'sold'.
        """
        await self._validate_customer(tenant_id, data.customer_id)

        try:
            async with atomic(self.session):
                prop = await self.repository.lock_property_for_update(
                    tenant_id, data.property_id
                )
                if not prop:
                    raise NotFoundError(
                        message=f"Property with ID '{data.property_id}' was not found.",
                        code="PROPERTY_NOT_FOUND",
                    )
                if prop["status"] not in _SELLABLE_PROPERTY_STATUSES:
                    raise ConflictError(
                        message=(
                            f"Property '{data.property_id}' must be reserved or on hold "
                            f"before it can be sold. Current status: '{prop['status']}'."
                        ),
                        code="PROPERTY_NOT_RESERVED",
                    )

                booking = None
                if data.booking_id is not None:
                    booking = await self.repository.lock_booking_for_update(
                        tenant_id, data.booking_id
                    )
                    if not booking:
                        raise NotFoundError(
                            message=f"Booking with ID '{data.booking_id}' was not found.",
                            code="PROPERTY_BOOKING_NOT_FOUND",
                        )
                    if booking["property_id"] != data.property_id:
                        raise ValidationError(
                            message="Booking does not belong to the property being sold.",
                            code="BOOKING_PROPERTY_MISMATCH",
                        )
                    if booking["booking_status"] != "active":
                        raise ConflictError(
                            message=(
                                f"Booking '{data.booking_id}' is "
                                f"'{booking['booking_status']}' and cannot be converted."
                            ),
                            code="BOOKING_NOT_ACTIVE",
                        )

                sale_row = await self.repository.insert_sale(tenant_id, created_by, data)

                if booking is not None:
                    await self.repository.mark_booking_converted(tenant_id, booking["id"])
                    if booking.get("lead_id"):
                        await self.repository.mark_lead_converted(tenant_id, booking["lead_id"])

                active_ownership = await self.repository.get_active_ownership_for_update(
                    tenant_id, data.property_id
                )
                previous_ownership_id = None
                if active_ownership:
                    await self.repository.close_ownership(
                        active_ownership["id"], sale_row["sale_date"]
                    )
                    previous_ownership_id = active_ownership["id"]
                    await self.repository.close_open_resale_listing_for_ownership(
                        tenant_id, active_ownership["id"]
                    )

                await self.repository.insert_ownership(
                    tenant_id=tenant_id,
                    property_id=data.property_id,
                    customer_id=data.customer_id,
                    sale_id=sale_row["id"],
                    purchase_purpose=data.purchase_purpose,
                    previous_ownership_id=previous_ownership_id,
                    start_date=sale_row["sale_date"],
                    created_by=created_by,
                )

                await self.repository.update_property_status(
                    tenant_id, data.property_id, "sold"
                )
        except DBAPIError as e:
            logger.warning(f"Property sale creation database exception: {e}")
            orig_msg = str(getattr(e, "orig", e)).lower()
            # A malformed input value (e.g. an invalid purchase_purpose) is a
            # data problem, not a concurrency problem -- retrying would fail
            # identically, so it gets its own specific, non-misleading message
            # rather than falling into the generic "please retry" framing below.
            if "invalid input value for enum" in orig_msg or "invalid input syntax" in orig_msg:
                raise ValidationError(
                    message="One or more fields (e.g. purchase_purpose) contain an invalid value.",
                    code="INVALID_FIELD_VALUE",
                ) from e
            if "violates check constraint" in orig_msg:
                raise ValidationError(
                    message="One or more fields are outside the allowed range for this sale.",
                    code="CHECK_CONSTRAINT_VIOLATION",
                ) from e
            raise ConflictError(
                message="Could not complete the sale due to a concurrent update. Please retry.",
                code="SALE_CREATION_CONFLICT",
            ) from e

        return PropertySaleResponse.model_validate(sale_row)

    async def get_sale(self, tenant_id: UUID | None, sale_id: UUID) -> PropertySaleResponse:
        """Fetch a property sale by ID."""
        row = await self.repository.get_by_id(tenant_id, sale_id)
        if not row:
            raise NotFoundError(
                message=f"Property sale with ID '{sale_id}' was not found.",
                code="PROPERTY_SALE_NOT_FOUND",
            )
        return PropertySaleResponse.model_validate(row)

    async def update_sale(
        self, tenant_id: UUID | None, sale_id: UUID, data: PropertySaleUpdate
    ) -> PropertySaleResponse:
        """Cancel or reverse a sale. Does NOT automatically reopen/undo the ownership
        transfer it produced -- that is a deliberate, separate action via the
        property_ownerships PATCH endpoint, since another resale may already have
        happened since."""
        if data.sale_status not in _VALID_SALE_STATUS_TRANSITIONS:
            raise ValidationError(
                message="sale_status must be 'cancelled' or 'reversed'.",
                code="INVALID_SALE_STATUS_TRANSITION",
            )

        existing = await self.repository.get_by_id(tenant_id, sale_id)
        if not existing:
            raise NotFoundError(
                message=f"Property sale with ID '{sale_id}' was not found.",
                code="PROPERTY_SALE_NOT_FOUND",
            )
        if existing["sale_status"] != "active":
            raise ConflictError(
                message=f"Sale '{sale_id}' is already '{existing['sale_status']}'.",
                code="SALE_NOT_ACTIVE",
            )

        updated_row = await self.repository.update_status(tenant_id, sale_id, data.sale_status)
        if not updated_row:
            raise NotFoundError(
                message=f"Property sale with ID '{sale_id}' was not found.",
                code="PROPERTY_SALE_NOT_FOUND",
            )
        return PropertySaleResponse.model_validate(updated_row)

    async def list_sales(
        self,
        tenant_id: UUID | None,
        filters: PropertySaleFilter,
        pagination: PaginationParams,
    ) -> PaginatedResponse[PropertySaleResponse]:
        """Search and list property sales with pagination."""
        rows, total = await self.repository.search(tenant_id, filters, pagination)
        items = [PropertySaleResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0

        return PaginatedResponse[PropertySaleResponse](
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=total,
            pages=pages,
        )

    async def get_balance(self, tenant_id: UUID, sale_id: UUID) -> PropertySaleBalanceResponse:
        """Fetch the outstanding-balance rollup for a sale."""
        # Ensure the sale exists (and belongs to this tenant) even if it has zero
        # payments yet, in which case the view would otherwise return nothing.
        await self.get_sale(tenant_id, sale_id)
        row = await self.repository.get_balance(tenant_id, sale_id)
        if not row:
            raise NotFoundError(
                message=f"Property sale with ID '{sale_id}' was not found.",
                code="PROPERTY_SALE_NOT_FOUND",
            )
        return PropertySaleBalanceResponse.model_validate(row)

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


class PropertySalePaymentService:
    """Service handling business rules for payments/installments against a sale."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = PropertySalePaymentRepository(session)
        self.sale_repository = PropertySaleRepository(session)

    async def create_payment(
        self,
        tenant_id: UUID,
        sale_id: UUID,
        created_by: UUID | None,
        data: PropertySalePaymentCreate,
    ) -> PropertySalePaymentResponse:
        """Record a payment against a sale."""
        sale = await self.sale_repository.get_by_id(tenant_id, sale_id)
        if not sale:
            raise NotFoundError(
                message=f"Property sale with ID '{sale_id}' was not found.",
                code="PROPERTY_SALE_NOT_FOUND",
            )

        try:
            row = await self.repository.create(tenant_id, sale_id, created_by, data)
        except DBAPIError as e:
            logger.warning(f"Sale payment create DB error: {e!s}")
            raise_clean_error_for_write(e, resource="payment")
        return PropertySalePaymentResponse.model_validate(row)

    async def get_payment(
        self, tenant_id: UUID | None, payment_id: UUID
    ) -> PropertySalePaymentResponse:
        """Fetch a payment by ID."""
        row = await self.repository.get_by_id(tenant_id, payment_id)
        if not row:
            raise NotFoundError(
                message=f"Payment with ID '{payment_id}' was not found.",
                code="PAYMENT_NOT_FOUND",
            )
        return PropertySalePaymentResponse.model_validate(row)

    async def update_payment(
        self,
        tenant_id: UUID | None,
        payment_id: UUID,
        data: PropertySalePaymentUpdate,
    ) -> PropertySalePaymentResponse:
        """Correct a payment's status (e.g. mark bounced/refunded) or reference."""
        existing = await self.repository.get_by_id(tenant_id, payment_id)
        if not existing:
            raise NotFoundError(
                message=f"Payment with ID '{payment_id}' was not found.",
                code="PAYMENT_NOT_FOUND",
            )

        update_dict = data.model_dump(exclude_unset=True)
        try:
            updated_row = await self.repository.update(tenant_id, payment_id, update_dict)
        except DBAPIError as e:
            logger.warning(f"Sale payment update DB error: {e!s}")
            raise_clean_error_for_write(e, resource="payment")
        if not updated_row:
            raise NotFoundError(
                message=f"Payment with ID '{payment_id}' was not found.",
                code="PAYMENT_NOT_FOUND",
            )
        return PropertySalePaymentResponse.model_validate(updated_row)

    async def list_payments(
        self, tenant_id: UUID, sale_id: UUID, filters: PropertySalePaymentFilter
    ) -> list[PropertySalePaymentResponse]:
        """List all payments recorded against a sale."""
        sale = await self.sale_repository.get_by_id(tenant_id, sale_id)
        if not sale:
            raise NotFoundError(
                message=f"Property sale with ID '{sale_id}' was not found.",
                code="PROPERTY_SALE_NOT_FOUND",
            )
        rows = await self.repository.list_for_sale(tenant_id, sale_id, filters)
        return [PropertySalePaymentResponse.model_validate(r) for r in rows]
