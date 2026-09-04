"""Property Sale & Sale Payment Repository for PostgreSQL database operations."""

import logging
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.sales.schemas import (
    PropertySaleCreate,
    PropertySaleFilter,
    PropertySalePaymentCreate,
    PropertySalePaymentFilter,
)
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


class PropertySaleRepository:
    """Repository handling database access for property_sales and the
    ownership-transfer transaction it triggers."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- Row locking (used only inside PropertySaleService.create_sale's atomic block) --

    async def lock_property_for_update(
        self, tenant_id: UUID, property_id: UUID
    ) -> dict[str, Any] | None:
        """Lock the property row FOR UPDATE, same pattern as reserve_property()."""
        result = await self.session.execute(
            text(
                """
                SELECT id, status FROM public.properties
                WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
                FOR UPDATE
                """
            ),
            {"id": property_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def lock_booking_for_update(
        self, tenant_id: UUID, booking_id: UUID
    ) -> dict[str, Any] | None:
        """Lock the referenced booking row FOR UPDATE to prevent a double-convert race."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.property_bookings
                WHERE id = :id AND tenant_id = :tenant_id
                FOR UPDATE
                """
            ),
            {"id": booking_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_active_ownership_for_update(
        self, tenant_id: UUID, property_id: UUID
    ) -> dict[str, Any] | None:
        """Lock the current (ownership_end_date IS NULL) ownership row, if any."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.property_ownerships
                WHERE property_id = :property_id AND tenant_id = :tenant_id
                  AND ownership_end_date IS NULL
                FOR UPDATE
                """
            ),
            {"property_id": property_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    # -- Mutations performed inside the same transaction --

    async def insert_sale(
        self, tenant_id: UUID, created_by: UUID | None, data: PropertySaleCreate
    ) -> dict[str, Any]:
        """Insert the property_sales row."""
        query = text(
            """
            INSERT INTO public.property_sales (
                tenant_id, booking_id, property_id, customer_id,
                sale_date, sale_amount, discount_amount, tax_amount,
                sale_status, created_by
            ) VALUES (
                :tenant_id, :booking_id, :property_id, :customer_id,
                COALESCE(:sale_date, CURRENT_DATE), :sale_amount, :discount_amount, :tax_amount,
                'active'::public.sale_status, :created_by
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "booking_id": data.booking_id,
            "property_id": data.property_id,
            "customer_id": data.customer_id,
            "sale_date": data.sale_date,
            "sale_amount": data.sale_amount,
            "discount_amount": data.discount_amount,
            "tax_amount": data.tax_amount,
            "created_by": created_by,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def mark_booking_converted(self, tenant_id: UUID, booking_id: UUID) -> None:
        """Flip a booking to 'converted' once the sale referencing it is recorded."""
        await self.session.execute(
            text(
                """
                UPDATE public.property_bookings
                SET booking_status = 'converted'::public.booking_status, updated_at = NOW()
                WHERE id = :id AND tenant_id = :tenant_id
                """
            ),
            {"id": booking_id, "tenant_id": tenant_id},
        )

    async def mark_lead_converted(self, tenant_id: UUID, lead_id: UUID) -> None:
        """Mark the originating lead as converted -- closes enquiry-to-sale loop."""
        await self.session.execute(
            text(
                """
                UPDATE public.leads
                SET status = 'converted'::public.lead_status,
                    converted_at = NOW(), updated_at = NOW()
                WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
                  AND status <> 'converted'::public.lead_status
                """
            ),
            {"id": lead_id, "tenant_id": tenant_id},
        )

    async def close_ownership(self, ownership_id: UUID, end_date: date) -> None:
        """Close out the previous owner's ownership period."""
        await self.session.execute(
            text(
                """
                UPDATE public.property_ownerships
                SET ownership_end_date = :end_date, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": ownership_id, "end_date": end_date},
        )

    async def insert_ownership(
        self,
        tenant_id: UUID,
        property_id: UUID,
        customer_id: UUID,
        sale_id: UUID,
        purchase_purpose: str | None,
        previous_ownership_id: UUID | None,
        start_date: date,
        created_by: UUID | None,
    ) -> dict[str, Any]:
        """Insert the new ownership row for the buyer."""
        query = text(
            """
            INSERT INTO public.property_ownerships (
                tenant_id, property_id, customer_id, sale_id, purchase_purpose,
                previous_ownership_id, ownership_start_date, ownership_status, created_by
            ) VALUES (
                :tenant_id, :property_id, :customer_id, :sale_id,
                CAST(:purchase_purpose AS public.purpose),
                :previous_ownership_id, :start_date, 'active'::public.ownership_status, :created_by
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "property_id": property_id,
            "customer_id": customer_id,
            "sale_id": sale_id,
            "purchase_purpose": purchase_purpose,
            "previous_ownership_id": previous_ownership_id,
            "start_date": start_date,
            "created_by": created_by,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def close_open_resale_listing_for_ownership(
        self, tenant_id: UUID, ownership_id: UUID
    ) -> None:
        """Auto-convert any open resale listing tied to the ownership row that just closed."""
        await self.session.execute(
            text(
                """
                UPDATE public.property_resale_listings
                SET listing_status = 'converted'::public.resale_listing_status, updated_at = NOW()
                WHERE ownership_id = :ownership_id AND tenant_id = :tenant_id
                  AND listing_status = 'open'::public.resale_listing_status
                """
            ),
            {"ownership_id": ownership_id, "tenant_id": tenant_id},
        )

    async def update_property_status(
        self, tenant_id: UUID, property_id: UUID, new_status: str
    ) -> None:
        """Flip the property's own status column (e.g. to 'sold')."""
        await self.session.execute(
            text(
                """
                UPDATE public.properties
                SET status = CAST(:status AS public.property_status), updated_at = NOW()
                WHERE id = :id AND tenant_id = :tenant_id
                """
            ),
            {"id": property_id, "tenant_id": tenant_id, "status": new_status},
        )

    # -- Plain reads / non-transactional writes --

    async def get_by_id(self, tenant_id: UUID | None, sale_id: UUID) -> dict[str, Any] | None:
        """Fetch a property sale by ID with human-readable joined details."""
        where_clause = "WHERE ps.id = :id"
        params: dict[str, Any] = {"id": sale_id}
        if tenant_id is not None:
            where_clause += " AND ps.tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        query = text(
            f"""
            SELECT
                ps.*,
                c.full_name       AS customer_name,
                c.phone           AS customer_phone,
                c.email           AS customer_email,
                c.city            AS customer_city,
                p.property_code   AS property_code,
                p.unit_number     AS unit_number,
                p.bedrooms        AS property_bedrooms,
                p.built_up_area   AS property_built_up_area,
                pr.name           AS project_name,
                pr.locality       AS project_locality,
                pr.city           AS project_city,
                bal.amount_received      AS amount_received,
                bal.outstanding_balance  AS outstanding_balance,
                u.name            AS created_by_name
            FROM public.property_sales ps
            LEFT JOIN public.customers c    ON c.id = ps.customer_id
            LEFT JOIN public.properties p   ON p.id = ps.property_id
            LEFT JOIN public.projects pr    ON pr.id = p.project_id
            LEFT JOIN public.v_property_sale_balances bal ON bal.sale_id = ps.id
            LEFT JOIN public.users u        ON u.id = ps.created_by
            {where_clause}
            """
        )

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update_status(
        self, tenant_id: UUID | None, sale_id: UUID, sale_status: str
    ) -> dict[str, Any] | None:
        """Update a sale's status (cancel/reverse)."""
        where_clause = "WHERE id = :id"
        params: dict[str, Any] = {"id": sale_id, "sale_status": sale_status}
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        query = text(
            f"""
            UPDATE public.property_sales
            SET sale_status = CAST(:sale_status AS public.sale_status), updated_at = NOW()
            {where_clause}
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def search(
        self,
        tenant_id: UUID | None,
        filters: PropertySaleFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search and list property sales with filtering and pagination."""
        where_conditions: list[str] = ["1=1"]
        params: dict[str, Any] = {
            "limit": pagination.page_size,
            "offset": pagination.offset,
        }

        if tenant_id is not None:
            where_conditions.append("ps.tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id

        if filters.property_id:
            where_conditions.append("ps.property_id = :filter_property_id")
            params["filter_property_id"] = filters.property_id

        if filters.customer_id:
            where_conditions.append("ps.customer_id = :filter_customer_id")
            params["filter_customer_id"] = filters.customer_id

        if filters.sale_status:
            where_conditions.append("ps.sale_status = CAST(:filter_status AS public.sale_status)")
            params["filter_status"] = filters.sale_status

        where_str = " AND ".join(where_conditions)

        count_query = text(  # noqa: S608
            f"SELECT COUNT(*) FROM public.property_sales ps WHERE {where_str}"
        )
        count_result = await self.session.execute(count_query, params)
        total_count = count_result.scalar_one()

        select_query = text(
            f"""
            SELECT
                ps.*,
                c.full_name       AS customer_name,
                c.phone           AS customer_phone,
                c.email           AS customer_email,
                c.city            AS customer_city,
                p.property_code   AS property_code,
                p.unit_number     AS unit_number,
                p.bedrooms        AS property_bedrooms,
                p.built_up_area   AS property_built_up_area,
                pr.name           AS project_name,
                pr.locality       AS project_locality,
                pr.city           AS project_city,
                bal.amount_received      AS amount_received,
                bal.outstanding_balance  AS outstanding_balance,
                u.name            AS created_by_name
            FROM public.property_sales ps
            LEFT JOIN public.customers c    ON c.id = ps.customer_id
            LEFT JOIN public.properties p   ON p.id = ps.property_id
            LEFT JOIN public.projects pr    ON pr.id = p.project_id
            LEFT JOIN public.v_property_sale_balances bal ON bal.sale_id = ps.id
            LEFT JOIN public.users u        ON u.id = ps.created_by
            WHERE {where_str}
            ORDER BY ps.sale_date DESC, ps.created_at DESC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608
        )
        select_result = await self.session.execute(select_query, params)
        rows = select_result.mappings().all()

        return [dict(r) for r in rows], total_count

    async def get_balance(self, tenant_id: UUID, sale_id: UUID) -> dict[str, Any] | None:
        """Fetch the outstanding-balance rollup from v_property_sale_balances."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.v_property_sale_balances
                WHERE sale_id = :sale_id AND tenant_id = :tenant_id
                """
            ),
            {"sale_id": sale_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None


class PropertySalePaymentRepository:
    """Repository handling database access for property_sale_payments."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        tenant_id: UUID,
        sale_id: UUID,
        created_by: UUID | None,
        data: PropertySalePaymentCreate,
    ) -> dict[str, Any]:
        """Insert a new payment record against a sale."""
        query = text(
            """
            INSERT INTO public.property_sale_payments (
                tenant_id, sale_id, payment_date, amount, payment_mode,
                payment_status, reference_number, notes, created_by
            ) VALUES (
                :tenant_id, :sale_id, COALESCE(:payment_date, CURRENT_DATE), :amount,
                CAST(:payment_mode AS public.payment_mode),
                CAST(:payment_status AS public.payment_status),
                :reference_number, :notes, :created_by
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "sale_id": sale_id,
            "payment_date": data.payment_date,
            "amount": data.amount,
            "payment_mode": data.payment_mode,
            "payment_status": data.payment_status,
            "reference_number": data.reference_number,
            "notes": data.notes,
            "created_by": created_by,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def get_by_id(
        self, tenant_id: UUID | None, payment_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch a payment by ID."""
        if tenant_id is not None:
            query = text(
                "SELECT * FROM public.property_sale_payments "
                "WHERE id = :id AND tenant_id = :tenant_id"
            )
            params = {"id": payment_id, "tenant_id": tenant_id}
        else:
            query = text("SELECT * FROM public.property_sale_payments WHERE id = :id")
            params = {"id": payment_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update(
        self,
        tenant_id: UUID | None,
        payment_id: UUID,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update payment status / reference / notes."""
        if not data:
            return await self.get_by_id(tenant_id, payment_id)

        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": payment_id}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id

        for key, value in data.items():
            param_key = f"val_{key}"
            if key == "payment_status":
                set_clauses.append(f"{key} = CAST(:{param_key} AS public.payment_status)")
            else:
                set_clauses.append(f"{key} = :{param_key}")
            params[param_key] = value

        set_str = ", ".join(set_clauses)
        where_clause = "WHERE id = :id"
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"

        query = text(
            f"""
            UPDATE public.property_sale_payments
            SET {set_str}, updated_at = NOW()
            {where_clause}
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def list_for_sale(
        self,
        tenant_id: UUID,
        sale_id: UUID,
        filters: PropertySalePaymentFilter,
    ) -> list[dict[str, Any]]:
        """List all payments for a sale, most recent first."""
        where_conditions = ["sale_id = :sale_id", "tenant_id = :tenant_id"]
        params: dict[str, Any] = {"sale_id": sale_id, "tenant_id": tenant_id}

        if filters.payment_status:
            where_conditions.append(
                "payment_status = CAST(:filter_status AS public.payment_status)"
            )
            params["filter_status"] = filters.payment_status

        where_str = " AND ".join(where_conditions)
        query = text(
            f"""
            SELECT * FROM public.property_sale_payments
            WHERE {where_str}
            ORDER BY payment_date DESC, created_at DESC
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        return [dict(r) for r in result.mappings().all()]
