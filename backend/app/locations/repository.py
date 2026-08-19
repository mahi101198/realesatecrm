"""Location Repository for PostgreSQL database operations."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.locations.schemas import LocationCreate, LocationFilter, LocationUpdate
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


class LocationRepository:
    """Repository handling database access for Location entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, tenant_id: UUID, data: LocationCreate) -> dict[str, Any]:
        """Insert a new location record. Raises IntegrityError on duplicate name+city."""
        query = text(
            """
            INSERT INTO public.locations (
                tenant_id, name, city, state, country, pincode, is_active
            ) VALUES (
                :tenant_id, :name, :city, :state, :country, :pincode, :is_active
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "name": data.name,
            "city": data.city,
            "state": data.state,
            "country": data.country,
            "pincode": data.pincode,
            "is_active": data.is_active,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def get_by_id(self, tenant_id: UUID | None, location_id: UUID) -> dict[str, Any] | None:
        """Fetch location by ID."""
        if tenant_id is not None:
            query = text(
                """
                SELECT * FROM public.locations
                WHERE id = :location_id AND tenant_id = :tenant_id
                """
            )
            params = {"location_id": location_id, "tenant_id": tenant_id}
        else:
            query = text("SELECT * FROM public.locations WHERE id = :location_id")
            params = {"location_id": location_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_by_normalized_name_city(
        self, tenant_id: UUID, name: str, city: str
    ) -> dict[str, Any] | None:
        """Fetch a location by case/whitespace-insensitive (name, city), same normalization
        as the DB's uq_locations_tenant_name_city index."""
        query = text(
            """
            SELECT * FROM public.locations
            WHERE tenant_id = :tenant_id
              AND lower(trim(name)) = lower(trim(:name))
              AND lower(trim(city)) = lower(trim(:city))
            """
        )
        result = await self.session.execute(
            query, {"tenant_id": tenant_id, "name": name, "city": city}
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update(
        self, tenant_id: UUID | None, location_id: UUID, data: LocationUpdate
    ) -> dict[str, Any] | None:
        """Update location details, including deactivation via is_active."""
        data_dict = data.model_dump(exclude_unset=True)
        if not data_dict:
            return await self.get_by_id(tenant_id, location_id)

        set_clauses: list[str] = []
        params: dict[str, Any] = {"location_id": location_id}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id

        for key, value in data_dict.items():
            param_key = f"val_{key}"
            set_clauses.append(f"{key} = :{param_key}")
            params[param_key] = value

        set_str = ", ".join(set_clauses)
        where_clause = "WHERE id = :location_id"
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"

        query = text(
            f"""
            UPDATE public.locations
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
        filters: LocationFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search and list locations with filtering and pagination."""
        where_conditions: list[str] = ["1=1"]
        params: dict[str, Any] = {
            "limit": pagination.page_size,
            "offset": pagination.offset,
        }

        if tenant_id is not None:
            where_conditions.append("tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id

        if filters.is_active is not None:
            where_conditions.append("is_active = :filter_is_active")
            params["filter_is_active"] = filters.is_active

        if filters.city:
            where_conditions.append("city ILIKE :filter_city")
            params["filter_city"] = f"%{filters.city.strip()}%"

        where_str = " AND ".join(where_conditions)

        count_query = text(f"SELECT COUNT(*) FROM public.locations WHERE {where_str}")  # noqa: S608
        count_result = await self.session.execute(count_query, params)
        total_count = count_result.scalar_one()

        select_query = text(
            f"""
            SELECT * FROM public.locations
            WHERE {where_str}
            ORDER BY city ASC, name ASC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608
        )
        select_result = await self.session.execute(select_query, params)
        rows = select_result.mappings().all()

        return [dict(r) for r in rows], total_count
