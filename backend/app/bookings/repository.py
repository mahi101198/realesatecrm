"""Property Booking Repository for PostgreSQL database operations."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.bookings.schemas import PropertyBookingCreate, PropertyBookingFilter
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


class PropertyBookingRepository:
    """Repository handling database access for property_bookings."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, tenant_id: UUID, created_by: UUID | None, data: PropertyBookingCreate
    ) -> dict[str, Any]:
        """Insert a new property booking record."""
        query = text(
            """
            INSERT INTO public.property_bookings (
                tenant_id, property_id, customer_id, lead_id,
                booking_date, booking_amount, booking_status, notes, created_by
            ) VALUES (
                :tenant_id, :property_id, :customer_id, :lead_id,
                COALESCE(:booking_date, CURRENT_DATE), :booking_amount,
                'active'::public.booking_status, :notes, :created_by
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "property_id": data.property_id,
            "customer_id": data.customer_id,
            "lead_id": data.lead_id,
            "booking_date": data.booking_date,
            "booking_amount": data.booking_amount,
            "notes": data.notes,
            "created_by": created_by,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def get_by_id(self, tenant_id: UUID | None, booking_id: UUID) -> dict[str, Any] | None:
        """Fetch a property booking by ID."""
        if tenant_id is not None:
            query = text(
                "SELECT * FROM public.property_bookings WHERE id = :id AND tenant_id = :tenant_id"
            )
            params = {"id": booking_id, "tenant_id": tenant_id}
        else:
            query = text("SELECT * FROM public.property_bookings WHERE id = :id")
            params = {"id": booking_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update_notes_and_status(
        self,
        tenant_id: UUID | None,
        booking_id: UUID,
        booking_status: str | None,
        notes: str | None,
        notes_provided: bool,
    ) -> dict[str, Any] | None:
        """Update booking notes and/or booking_status."""
        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": booking_id}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id

        if booking_status is not None:
            set_clauses.append("booking_status = :booking_status::public.booking_status")
            params["booking_status"] = booking_status
        if notes_provided:
            set_clauses.append("notes = :notes")
            params["notes"] = notes

        if not set_clauses:
            return await self.get_by_id(tenant_id, booking_id)

        set_str = ", ".join(set_clauses)
        where_clause = "WHERE id = :id"
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"

        query = text(
            f"""
            UPDATE public.property_bookings
            SET {set_str}, updated_at = NOW()
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
        filters: PropertyBookingFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search and list property bookings with filtering and pagination."""
        where_conditions: list[str] = ["1=1"]
        params: dict[str, Any] = {
            "limit": pagination.page_size,
            "offset": pagination.offset,
        }

        if tenant_id is not None:
            where_conditions.append("tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id

        if filters.property_id:
            where_conditions.append("property_id = :filter_property_id")
            params["filter_property_id"] = filters.property_id

        if filters.customer_id:
            where_conditions.append("customer_id = :filter_customer_id")
            params["filter_customer_id"] = filters.customer_id

        if filters.booking_status:
            where_conditions.append("booking_status = :filter_status::public.booking_status")
            params["filter_status"] = filters.booking_status

        where_str = " AND ".join(where_conditions)

        count_query = text(
            f"SELECT COUNT(*) FROM public.property_bookings WHERE {where_str}"  # noqa: S608
        )
        count_result = await self.session.execute(count_query, params)
        total_count = count_result.scalar_one()

        select_query = text(
            f"""
            SELECT * FROM public.property_bookings
            WHERE {where_str}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608
        )
        select_result = await self.session.execute(select_query, params)
        rows = select_result.mappings().all()

        return [dict(r) for r in rows], total_count
