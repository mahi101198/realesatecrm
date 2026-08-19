"""Property Ownership, Co-Owner & Resale Listing Repository for PostgreSQL access."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ownerships.schemas import (
    PropertyOwnershipCoOwnerCreate,
    PropertyResaleListingCreate,
    PropertyResaleListingFilter,
)

logger = logging.getLogger(__name__)


class PropertyOwnershipRepository:
    """Repository handling database access for property_ownerships and
    property_ownership_co_owners."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_current_for_property(
        self, tenant_id: UUID | None, property_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch the current (ownership_end_date IS NULL) owner-of-record for a property."""
        where = "WHERE property_id = :property_id AND ownership_end_date IS NULL"
        params: dict[str, Any] = {"property_id": property_id}
        if tenant_id is not None:
            where += " AND tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        result = await self.session.execute(
            text(f"SELECT * FROM public.property_ownerships {where}"),  # noqa: S608
            params,
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_history_for_property(
        self, tenant_id: UUID | None, property_id: UUID
    ) -> list[dict[str, Any]]:
        """Fetch the full ownership chain for a property, oldest first."""
        where = "WHERE property_id = :property_id"
        params: dict[str, Any] = {"property_id": property_id}
        if tenant_id is not None:
            where += " AND tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        result = await self.session.execute(
            text(
                f"SELECT * FROM public.property_ownerships {where} "  # noqa: S608
                "ORDER BY ownership_start_date ASC, created_at ASC"
            ),
            params,
        )
        return [dict(r) for r in result.mappings().all()]

    async def get_by_id(self, tenant_id: UUID | None, ownership_id: UUID) -> dict[str, Any] | None:
        """Fetch an ownership row by ID."""
        if tenant_id is not None:
            query = text(
                "SELECT * FROM public.property_ownerships WHERE id = :id AND tenant_id = :tenant_id"
            )
            params = {"id": ownership_id, "tenant_id": tenant_id}
        else:
            query = text("SELECT * FROM public.property_ownerships WHERE id = :id")
            params = {"id": ownership_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

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
            set_clauses.append("ownership_status = :ownership_status::public.ownership_status")
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
        """List all co-owners for an ownership record."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.property_ownership_co_owners
                WHERE ownership_id = :ownership_id AND tenant_id = :tenant_id
                ORDER BY created_at ASC
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
        """Fetch a resale listing by ID."""
        if tenant_id is not None:
            query = text(
                "SELECT * FROM public.property_resale_listings "
                "WHERE id = :id AND tenant_id = :tenant_id"
            )
            params = {"id": listing_id, "tenant_id": tenant_id}
        else:
            query = text("SELECT * FROM public.property_resale_listings WHERE id = :id")
            params = {"id": listing_id}

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
                set_clauses.append(f"{key} = :{param_key}::public.resale_listing_status")
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
        """List resale listings with filtering."""
        where_conditions = ["tenant_id = :tenant_id"]
        params: dict[str, Any] = {"tenant_id": tenant_id}

        if filters.ownership_id:
            where_conditions.append("ownership_id = :filter_ownership_id")
            params["filter_ownership_id"] = filters.ownership_id

        if filters.listing_status:
            where_conditions.append("listing_status = :filter_status::public.resale_listing_status")
            params["filter_status"] = filters.listing_status

        where_str = " AND ".join(where_conditions)
        query = text(
            f"""
            SELECT * FROM public.property_resale_listings
            WHERE {where_str}
            ORDER BY listed_at DESC
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        return [dict(r) for r in result.mappings().all()]
