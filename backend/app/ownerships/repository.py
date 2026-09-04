"""Property Ownership, Co-Owner & Resale Listing Repository for PostgreSQL access."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ownerships.schemas import (
    PropertyOwnershipCoOwnerCreate,
    PropertyOwnershipFilter,
    PropertyResaleListingCreate,
    PropertyResaleListingFilter,
)
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


class PropertyOwnershipRepository:
    """Repository handling database access for property_ownerships and
    property_ownership_co_owners."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_current_for_property(
        self, tenant_id: UUID | None, property_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch the current (ownership_end_date IS NULL) owner-of-record for a property with joined details."""
        where = "WHERE po.property_id = :property_id AND po.ownership_end_date IS NULL"
        params: dict[str, Any] = {"property_id": property_id}
        if tenant_id is not None:
            where += " AND po.tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        query = text(
            f"""
            SELECT
                po.*,
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
                ps.sale_amount    AS sale_amount,
                ps.sale_date      AS sale_date,
                u.name            AS verified_by_name
            FROM public.property_ownerships po
            LEFT JOIN public.customers c ON c.id = po.customer_id
            LEFT JOIN public.properties p ON p.id = po.property_id
            LEFT JOIN public.projects pr ON pr.id = p.project_id
            LEFT JOIN public.property_sales ps ON ps.id = po.sale_id
            LEFT JOIN public.users u ON u.id = po.verified_by
            {where}
            """
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_history_for_property(
        self, tenant_id: UUID | None, property_id: UUID
    ) -> list[dict[str, Any]]:
        """Fetch the full ownership chain for a property, oldest first."""
        where = "WHERE po.property_id = :property_id"
        params: dict[str, Any] = {"property_id": property_id}
        if tenant_id is not None:
            where += " AND po.tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        query = text(
            f"""
            SELECT
                po.*,
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
                ps.sale_amount    AS sale_amount,
                ps.sale_date      AS sale_date,
                u.name            AS verified_by_name
            FROM public.property_ownerships po
            LEFT JOIN public.customers c ON c.id = po.customer_id
            LEFT JOIN public.properties p ON p.id = po.property_id
            LEFT JOIN public.projects pr ON pr.id = p.project_id
            LEFT JOIN public.property_sales ps ON ps.id = po.sale_id
            LEFT JOIN public.users u ON u.id = po.verified_by
            {where}
            ORDER BY po.ownership_start_date ASC, po.created_at ASC
            """
        )
        result = await self.session.execute(query, params)
        return [dict(r) for r in result.mappings().all()]

    async def get_by_id(self, tenant_id: UUID | None, ownership_id: UUID) -> dict[str, Any] | None:
        """Fetch an ownership row by ID with joined details."""
        where = "WHERE po.id = :id"
        params: dict[str, Any] = {"id": ownership_id}
        if tenant_id is not None:
            where += " AND po.tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        query = text(
            f"""
            SELECT
                po.*,
                c.full_name       AS customer_name,
                c.phone           AS customer_phone,
                c.email           AS customer_email,
                c.city            AS customer_city,
                c.address_line1   AS customer_address,
                p.property_code   AS property_code,
                p.unit_number     AS unit_number,
                p.block           AS property_block,
                p.floor_number    AS property_floor_number,
                p.bedrooms        AS property_bedrooms,
                p.bathrooms       AS property_bathrooms,
                p.balconies       AS property_balconies,
                p.carpet_area     AS property_carpet_area,
                p.built_up_area   AS property_built_up_area,
                p.super_built_up_area AS property_super_built_up_area,
                p.facing          AS property_facing,
                p.is_corner       AS property_is_corner,
                pr.name           AS project_name,
                pr.locality       AS project_locality,
                pr.city           AS project_city,
                pr.state          AS project_state,
                pr.developer_name AS project_developer_name,
                pr.rera_number    AS project_rera_number,
                ps.sale_amount    AS sale_amount,
                ps.sale_date      AS sale_date,
                ps.discount_amount AS sale_discount_amount,
                ps.tax_amount     AS sale_tax_amount,
                ps.sale_status    AS sale_status,
                u.name            AS verified_by_name,
                prl.id            AS resale_listing_id,
                prl.asking_price  AS resale_asking_price,
                prl.listing_status AS resale_listing_status,
                prl.notes         AS resale_notes,
                prl.listed_at     AS resale_listed_at
            FROM public.property_ownerships po
            LEFT JOIN public.customers c ON c.id = po.customer_id
            LEFT JOIN public.properties p ON p.id = po.property_id
            LEFT JOIN public.projects pr ON pr.id = p.project_id
            LEFT JOIN public.property_sales ps ON ps.id = po.sale_id
            LEFT JOIN public.users u ON u.id = po.verified_by
            LEFT JOIN public.property_resale_listings prl ON prl.ownership_id = po.id AND prl.listing_status = 'open'
            {where}
            """
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def search(
        self,
        tenant_id: UUID | None,
        filters: PropertyOwnershipFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search and list ownership records with filtering and pagination."""
        where_conditions: list[str] = ["1=1"]
        params: dict[str, Any] = {
            "limit": pagination.page_size,
            "offset": pagination.offset,
        }

        if tenant_id is not None:
            where_conditions.append("po.tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id

        if filters.property_id:
            where_conditions.append("po.property_id = :filter_property_id")
            params["filter_property_id"] = filters.property_id

        if filters.customer_id:
            where_conditions.append("po.customer_id = :filter_customer_id")
            params["filter_customer_id"] = filters.customer_id

        if filters.ownership_status:
            where_conditions.append("po.ownership_status = CAST(:filter_status AS public.ownership_status)")
            params["filter_status"] = filters.ownership_status

        where_str = " AND ".join(where_conditions)

        count_query = text(f"SELECT COUNT(*) FROM public.property_ownerships po WHERE {where_str}")
        count_result = await self.session.execute(count_query, params)
        total_count = count_result.scalar_one()

        select_query = text(
            f"""
            SELECT
                po.*,
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
                ps.sale_amount    AS sale_amount,
                ps.sale_date      AS sale_date,
                u.name            AS verified_by_name
            FROM public.property_ownerships po
            LEFT JOIN public.customers c ON c.id = po.customer_id
            LEFT JOIN public.properties p ON p.id = po.property_id
            LEFT JOIN public.projects pr ON pr.id = p.project_id
            LEFT JOIN public.property_sales ps ON ps.id = po.sale_id
            LEFT JOIN public.users u ON u.id = po.verified_by
            WHERE {where_str}
            ORDER BY po.ownership_start_date DESC, po.created_at DESC
            LIMIT :limit OFFSET :offset
            """
        )
        select_result = await self.session.execute(select_query, params)
        rows = select_result.mappings().all()
        return [dict(r) for r in rows], total_count

    async def get_by_customer(
        self, tenant_id: UUID, customer_id: UUID
    ) -> list[dict[str, Any]]:
        """Return the full ownership history for a customer (both current and past).

        Uses a single LEFT JOIN across property_ownerships → properties →
        projects → property_sales so the caller receives everything
        needed for the customer 360° view without any extra round-trips.

        Ordered newest-first (ownership_start_date DESC, created_at DESC).
        """
        query = text(
            """
            SELECT
                po.id,
                po.property_id,
                po.sale_id,
                po.purchase_purpose,
                po.previous_ownership_id,
                po.ownership_start_date,
                po.ownership_end_date,
                po.ownership_status,
                po.verified_by,
                po.created_at,

                -- property fields
                p.property_code,
                p.unit_number,
                pr.name           AS project_name,

                -- sale fields (nullable: manual/legacy ownerships may have no sale)
                ps.sale_date,
                ps.sale_amount,
                ps.sale_status

            FROM   public.property_ownerships po
            JOIN   public.properties           p  ON p.id  = po.property_id
            LEFT JOIN public.projects          pr ON pr.id = p.project_id
            LEFT JOIN public.property_sales    ps ON ps.id = po.sale_id

            WHERE  po.tenant_id   = :tenant_id
              AND  po.customer_id = :customer_id

            ORDER BY po.ownership_start_date DESC, po.created_at DESC
            """  # noqa: S608
        )
        result = await self.session.execute(
            query, {"tenant_id": tenant_id, "customer_id": customer_id}
        )
        return [dict(r) for r in result.mappings().all()]


    async def update(
        self,
        tenant_id: UUID | None,
        ownership_id: UUID,
        verified_by: UUID | None,
        verified_by_provided: bool,
        ownership_status: str | None,
    ) -> dict[str, Any] | None:
        """Update verified_by and/or ownership_status on an ownership record."""
        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": ownership_id}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id

        if verified_by_provided:
            set_clauses.append("verified_by = :verified_by")
            params["verified_by"] = verified_by
        if ownership_status is not None:
            set_clauses.append(
                "ownership_status = CAST(:ownership_status AS public.ownership_status)"
            )
            params["ownership_status"] = ownership_status

        if not set_clauses:
            return await self.get_by_id(tenant_id, ownership_id)

        set_str = ", ".join(set_clauses)
        where_clause = "WHERE id = :id"
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"

        query = text(
            f"""
            UPDATE public.property_ownerships
            SET {set_str}, updated_at = NOW()
            {where_clause}
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def add_co_owner(
        self, tenant_id: UUID, ownership_id: UUID, data: PropertyOwnershipCoOwnerCreate
    ) -> dict[str, Any]:
        """Insert a co-owner row. May raise DBAPIError (errcode P0004) if the
        DB's trg_validate_co_owner_share trigger detects the share sum > 100."""
        query = text(
            """
            INSERT INTO public.property_ownership_co_owners (
                tenant_id, ownership_id, customer_id, role, share_percentage
            ) VALUES (
                :tenant_id, :ownership_id, :customer_id, :role, :share_percentage
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "ownership_id": ownership_id,
            "customer_id": data.customer_id,
            "role": data.role,
            "share_percentage": data.share_percentage,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def list_co_owners(self, tenant_id: UUID, ownership_id: UUID) -> list[dict[str, Any]]:
        """List all co-owners for an ownership record with joined customer details."""
        result = await self.session.execute(
            text(
                """
                SELECT
                    pco.*,
                    c.full_name AS customer_name,
                    c.phone     AS customer_phone,
                    c.email     AS customer_email
                FROM public.property_ownership_co_owners pco
                LEFT JOIN public.customers c ON c.id = pco.customer_id
                WHERE pco.ownership_id = :ownership_id AND pco.tenant_id = :tenant_id
                ORDER BY pco.created_at ASC
                """
            ),
            {"ownership_id": ownership_id, "tenant_id": tenant_id},
        )
        return [dict(r) for r in result.mappings().all()]

    async def get_co_owner(
        self, tenant_id: UUID, ownership_id: UUID, co_owner_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch a single co-owner row, scoped to its parent ownership record."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.property_ownership_co_owners
                WHERE id = :id AND ownership_id = :ownership_id AND tenant_id = :tenant_id
                """
            ),
            {"id": co_owner_id, "ownership_id": ownership_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def delete_co_owner(self, tenant_id: UUID, ownership_id: UUID, co_owner_id: UUID) -> bool:
        """Remove a co-owner from an ownership record."""
        result = await self.session.execute(
            text(
                """
                DELETE FROM public.property_ownership_co_owners
                WHERE id = :id AND ownership_id = :ownership_id AND tenant_id = :tenant_id
                """
            ),
            {"id": co_owner_id, "ownership_id": ownership_id, "tenant_id": tenant_id},
        )
        rowcount = getattr(result, "rowcount", 0)
        return bool(rowcount > 0)


class PropertyResaleListingRepository:
    """Repository handling database access for property_resale_listings."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, tenant_id: UUID, created_by: UUID | None, data: PropertyResaleListingCreate
    ) -> dict[str, Any]:
        """Insert a new resale listing. Raises IntegrityError if an open listing
        already exists for this ownership_id (see
        uq_property_resale_listings_one_open_per_ownership)."""
        query = text(
            """
            INSERT INTO public.property_resale_listings (
                tenant_id, ownership_id, asking_price, listing_status, notes, created_by
            ) VALUES (
                :tenant_id, :ownership_id, :asking_price, 'open'::public.resale_listing_status,
                :notes, :created_by
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "ownership_id": data.ownership_id,
            "asking_price": data.asking_price,
            "notes": data.notes,
            "created_by": created_by,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def get_by_id(self, tenant_id: UUID | None, listing_id: UUID) -> dict[str, Any] | None:
        """Fetch a resale listing by ID with joined details."""
        where = "WHERE prl.id = :id"
        params: dict[str, Any] = {"id": listing_id}
        if tenant_id is not None:
            where += " AND prl.tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        query = text(
            f"""
            SELECT
                prl.*,
                c.full_name       AS owner_name,
                c.phone           AS owner_phone,
                c.email           AS owner_email,
                p.property_code   AS property_code,
                p.unit_number     AS unit_number,
                p.bedrooms        AS property_bedrooms,
                p.built_up_area   AS property_built_up_area,
                pr.name           AS project_name,
                pr.locality       AS project_locality,
                pr.city           AS project_city
            FROM public.property_resale_listings prl
            JOIN public.property_ownerships po ON po.id = prl.ownership_id
            LEFT JOIN public.customers c ON c.id = po.customer_id
            LEFT JOIN public.properties p ON p.id = po.property_id
            LEFT JOIN public.projects pr ON pr.id = p.project_id
            {where}
            """
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update(
        self, tenant_id: UUID | None, listing_id: UUID, data_dict: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a resale listing's status/asking_price/notes."""
        if not data_dict:
            return await self.get_by_id(tenant_id, listing_id)

        set_clauses: list[str] = []
        params: dict[str, Any] = {"id": listing_id}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id

        for key, value in data_dict.items():
            param_key = f"val_{key}"
            if key == "listing_status":
                set_clauses.append(f"{key} = CAST(:{param_key} AS public.resale_listing_status)")
            else:
                set_clauses.append(f"{key} = :{param_key}")
            params[param_key] = value

        set_str = ", ".join(set_clauses)
        where_clause = "WHERE id = :id"
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"

        query = text(
            f"""
            UPDATE public.property_resale_listings
            SET {set_str}, updated_at = NOW()
            {where_clause}
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def search(
        self, tenant_id: UUID, filters: PropertyResaleListingFilter
    ) -> list[dict[str, Any]]:
        """List resale listings with joined owner, property, and project info."""
        where_conditions = ["prl.tenant_id = :tenant_id"]
        params: dict[str, Any] = {"tenant_id": tenant_id}

        if filters.ownership_id:
            where_conditions.append("prl.ownership_id = :filter_ownership_id")
            params["filter_ownership_id"] = filters.ownership_id

        if filters.listing_status:
            where_conditions.append(
                "prl.listing_status = CAST(:filter_status AS public.resale_listing_status)"
            )
            params["filter_status"] = filters.listing_status

        where_str = " AND ".join(where_conditions)
        query = text(
            f"""
            SELECT
                prl.*,
                c.full_name       AS owner_name,
                c.phone           AS owner_phone,
                c.email           AS owner_email,
                p.property_code   AS property_code,
                p.unit_number     AS unit_number,
                p.bedrooms        AS property_bedrooms,
                p.built_up_area   AS property_built_up_area,
                pr.name           AS project_name,
                pr.locality       AS project_locality,
                pr.city           AS project_city
            FROM public.property_resale_listings prl
            JOIN public.property_ownerships po ON po.id = prl.ownership_id
            LEFT JOIN public.customers c ON c.id = po.customer_id
            LEFT JOIN public.properties p ON p.id = po.property_id
            LEFT JOIN public.projects pr ON pr.id = p.project_id
            WHERE {where_str}
            ORDER BY prl.listed_at DESC
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        return [dict(r) for r in result.mappings().all()]
