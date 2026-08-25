"""Property Repository for PostgreSQL database queries."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.properties.schemas import (
    PropertyCreate,
    PropertyReserveRequest,
    PropertySearchFilter,
    PropertyUpdate,
)
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


class PropertyRepository:
    """Repository handling database access for Property inventory entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, tenant_id: UUID, created_by: UUID | None, data: PropertyCreate
    ) -> dict[str, Any]:
        """Insert a new property record. Raises IntegrityError on duplicate property_code."""
        query = text(
            """
            INSERT INTO public.properties (
                tenant_id, project_id, property_type_id, property_code, unit_number, block,
                floor_number, plot_area, built_up_area, carpet_area, super_built_up_area,
                area_unit, bedrooms, bathrooms, balconies, parking_covered, parking_open,
                facing, is_corner, is_park_facing, is_road_facing,
                base_price, offer_price, price_per_unit, currency,
                status, is_public, is_featured, custom_attributes, construction_status,
                created_by
            ) VALUES (
                :tenant_id, :project_id, :property_type_id, :property_code, :unit_number, :block,
                :floor_number, :plot_area, :built_up_area, :carpet_area, :super_built_up_area,
                CAST(:area_unit AS public.area_unit), :bedrooms, :bathrooms, :balconies,
                :parking_covered, :parking_open,
                CAST(:facing AS public.property_facing),
                :is_corner, :is_park_facing, :is_road_facing,
                :base_price, :offer_price, :price_per_unit, :currency,
                CAST(:status AS public.property_status),
                :is_public, :is_featured, :custom_attributes,
                CAST(:construction_status AS public.construction_status),
                :created_by
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "project_id": data.project_id,
            "property_type_id": data.property_type_id,
            "property_code": data.property_code,
            "unit_number": data.unit_number,
            "block": data.block,
            "floor_number": data.floor_number,
            "plot_area": data.plot_area,
            "built_up_area": data.built_up_area,
            "carpet_area": data.carpet_area,
            "super_built_up_area": data.super_built_up_area,
            "area_unit": data.area_unit,
            "bedrooms": data.bedrooms,
            "bathrooms": data.bathrooms,
            "balconies": data.balconies,
            "parking_covered": data.parking_covered,
            "parking_open": data.parking_open,
            "facing": data.facing,
            "is_corner": data.is_corner,
            "is_park_facing": data.is_park_facing,
            "is_road_facing": data.is_road_facing,
            "base_price": data.base_price,
            "offer_price": data.offer_price,
            "price_per_unit": data.price_per_unit,
            "currency": data.currency,
            "status": data.status,
            "is_public": data.is_public,
            "is_featured": data.is_featured,
            "custom_attributes": data.custom_attributes or {},
            "construction_status": data.construction_status,
            "created_by": created_by,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def update(
        self, tenant_id: UUID | None, property_id: UUID, data: PropertyUpdate
    ) -> dict[str, Any] | None:
        """Update existing property fields. Never accepts a `status` transition here --
        status changes only happen via the dedicated /reserve endpoint or a sale closing,
        to preserve the explicit property-status state machine."""
        data_dict = data.model_dump(exclude_unset=True)
        if not data_dict:
            return await self.get_by_id(tenant_id, property_id)

        set_clauses: list[str] = []
        params: dict[str, Any] = {"property_id": property_id}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id

        enum_casts = {
            "area_unit": "public.area_unit",
            "facing": "public.property_facing",
            "construction_status": "public.construction_status",
        }

        for key, value in data_dict.items():
            param_key = f"val_{key}"
            if key in enum_casts:
                set_clauses.append(f"{key} = :{param_key}::{enum_casts[key]}")
            else:
                set_clauses.append(f"{key} = :{param_key}")
            params[param_key] = value

        set_str = ", ".join(set_clauses)
        where_clause = "WHERE id = :property_id AND deleted_at IS NULL"
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"

        query = text(
            f"""
            UPDATE public.properties
            SET {set_str}, updated_at = NOW()
            {where_clause}
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_by_id(self, tenant_id: UUID | None, property_id: UUID) -> dict[str, Any] | None:
        """Fetch property by ID."""
        if tenant_id is not None:
            query = text(
                """
                SELECT * FROM public.properties
                WHERE id = :property_id AND tenant_id = :tenant_id AND deleted_at IS NULL
                """
            )
            params = {"property_id": property_id, "tenant_id": tenant_id}
        else:
            query = text(
                """
                SELECT * FROM public.properties
                WHERE id = :property_id AND deleted_at IS NULL
                """
            )
            params = {"property_id": property_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def search(
        self,
        tenant_id: UUID | None,
        filters: PropertySearchFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search and filter property inventory in PostgreSQL using indexes."""
        where_conditions = ["deleted_at IS NULL"]
        params: dict[str, Any] = {
            "limit": pagination.page_size,
            "offset": pagination.offset,
        }

        if tenant_id is not None:
            where_conditions.append("tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id

        if filters.project_id:
            where_conditions.append("project_id = :filter_project_id")
            params["filter_project_id"] = filters.project_id

        if filters.property_type_id:
            where_conditions.append("property_type_id = :filter_type_id")
            params["filter_type_id"] = filters.property_type_id

        if filters.status:
            where_conditions.append("status = CAST(:filter_status AS public.property_status)")
            params["filter_status"] = filters.status

        if filters.min_budget is not None:
            where_conditions.append("COALESCE(offer_price, base_price) >= :min_budget")
            params["min_budget"] = filters.min_budget

        if filters.max_budget is not None:
            where_conditions.append("COALESCE(offer_price, base_price) <= :max_budget")
            params["max_budget"] = filters.max_budget

        if filters.min_area is not None:
            where_conditions.append("COALESCE(plot_area, built_up_area, carpet_area) >= :min_area")
            params["min_area"] = filters.min_area

        if filters.max_area is not None:
            where_conditions.append("COALESCE(plot_area, built_up_area, carpet_area) <= :max_area")
            params["max_area"] = filters.max_area

        if filters.bedrooms is not None:
            where_conditions.append("bedrooms = :filter_bedrooms")
            params["filter_bedrooms"] = filters.bedrooms

        if filters.facing:
            where_conditions.append("facing = CAST(:filter_facing AS public.property_facing)")
            params["filter_facing"] = filters.facing

        if filters.is_corner is not None:
            where_conditions.append("is_corner = :filter_is_corner")
            params["filter_is_corner"] = filters.is_corner

        if filters.query:
            where_conditions.append(
                "(property_code ILIKE :search_q OR "
                "unit_number ILIKE :search_q OR "
                "block ILIKE :search_q)"
            )
            params["search_q"] = f"%{filters.query.strip()}%"

        where_str = " AND ".join(where_conditions)

        count_query = text(f"SELECT COUNT(*) FROM public.properties WHERE {where_str}")  # noqa: S608
        count_result = await self.session.execute(count_query, params)
        total_count = count_result.scalar_one()

        select_query = text(
            f"""
            SELECT * FROM public.properties
            WHERE {where_str}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608
        )
        select_result = await self.session.execute(select_query, params)
        rows = select_result.mappings().all()

        return [dict(r) for r in rows], total_count

    async def reserve_property(
        self,
        tenant_id: UUID,
        property_id: UUID,
        data: PropertyReserveRequest,
        actor_user_id: UUID | None,
    ) -> dict[str, Any]:
        """Atomically reserve or hold a property using procedure reserve_property."""
        query = text(
            """
            SELECT public.reserve_property(
                :p_tenant_id,
                :p_property_id,
                :p_lead_id,
                :p_call_id,
                CAST(:p_new_status AS public.property_status),
                :p_reason,
                :p_actor_user_id
            )
            """
        )
        await self.session.execute(
            query,
            {
                "p_tenant_id": tenant_id,
                "p_property_id": property_id,
                "p_lead_id": data.lead_id,
                "p_call_id": data.call_id,
                "p_new_status": data.new_status,
                "p_reason": data.reason,
                "p_actor_user_id": actor_user_id,
            },
        )

        updated = await self.get_by_id(tenant_id, property_id)
        if not updated:
            raise RuntimeError("Property reservation failed: updated property row missing.")
        return updated

    # -- Detail view support: a small number of targeted queries, no N+1 loops --

    async def get_project_context(
        self, tenant_id: UUID | None, project_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch project (+ structured location, if set) context for the detail view."""
        where = "WHERE p.id = :project_id AND p.deleted_at IS NULL"
        params: dict[str, Any] = {"project_id": project_id}
        if tenant_id is not None:
            where += " AND p.tenant_id = :tenant_id"
            params["tenant_id"] = tenant_id

        query = text(
            f"""
            SELECT
                p.id, p.name, p.slug, p.city, p.state,
                l.id AS location_id, l.name AS location_name, l.city AS location_city
            FROM public.projects p
            LEFT JOIN public.locations l ON l.id = p.location_id
            {where}
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_construction_milestones(
        self, tenant_id: UUID, property_id: UUID
    ) -> list[dict[str, Any]]:
        """Fetch all construction milestones for a property."""
        result = await self.session.execute(
            text(
                """
                SELECT id, milestone, status, target_date, actual_completion_date
                FROM public.property_construction_milestones
                WHERE property_id = :property_id AND tenant_id = :tenant_id
                ORDER BY created_at ASC
                """
            ),
            {"property_id": property_id, "tenant_id": tenant_id},
        )
        return [dict(r) for r in result.mappings().all()]

    async def get_construction_milestone_by_stage(
        self, tenant_id: UUID, property_id: UUID, milestone: str
    ) -> dict[str, Any] | None:
        """Fetch a single milestone row by its natural key (property_id, milestone)."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.property_construction_milestones
                WHERE property_id = :property_id AND tenant_id = :tenant_id
                  AND milestone = CAST(:milestone AS public.construction_milestone)
                """
            ),
            {"property_id": property_id, "tenant_id": tenant_id, "milestone": milestone},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def create_construction_milestone(
        self,
        tenant_id: UUID,
        property_id: UUID,
        milestone: str,
        target_date: Any,
        notes: str | None,
    ) -> dict[str, Any]:
        """Insert a new construction milestone row. Raises IntegrityError on
        duplicate (property_id, milestone)."""
        result = await self.session.execute(
            text(
                """
                INSERT INTO public.property_construction_milestones (
                    tenant_id, property_id, milestone, status, target_date, notes
                ) VALUES (
                    :tenant_id, :property_id, CAST(:milestone AS public.construction_milestone),
                    'pending'::public.construction_milestone_status, :target_date, :notes
                )
                RETURNING *
                """
            ),
            {
                "tenant_id": tenant_id,
                "property_id": property_id,
                "milestone": milestone,
                "target_date": target_date,
                "notes": notes,
            },
        )
        return dict(result.mappings().one())

    async def update_construction_milestone(
        self,
        tenant_id: UUID,
        property_id: UUID,
        milestone: str,
        data_dict: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update an existing milestone row by its natural key. Never creates one."""
        if not data_dict:
            return await self.get_construction_milestone_by_stage(tenant_id, property_id, milestone)

        set_clauses: list[str] = []
        params: dict[str, Any] = {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "milestone": milestone,
        }
        for key, value in data_dict.items():
            param_key = f"val_{key}"
            if key == "status":
                set_clauses.append(f"{key} = :{param_key}::public.construction_milestone_status")
            else:
                set_clauses.append(f"{key} = :{param_key}")
            params[param_key] = value

        set_str = ", ".join(set_clauses)
        query = text(
            f"""
            UPDATE public.property_construction_milestones
            SET {set_str}, updated_at = NOW()
            WHERE property_id = :property_id AND tenant_id = :tenant_id
              AND milestone = CAST(:milestone AS public.construction_milestone)
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_ownership_history_with_co_owners(
        self, tenant_id: UUID, property_id: UUID
    ) -> list[dict[str, Any]]:
        """Fetch the full ownership chain for a property with each period's
        co-owners attached, using exactly two queries total (not one per row)."""
        ownership_result = await self.session.execute(
            text(
                """
                SELECT id, customer_id, purchase_purpose, previous_ownership_id,
                       ownership_start_date, ownership_end_date, ownership_status
                FROM public.property_ownerships
                WHERE property_id = :property_id AND tenant_id = :tenant_id
                ORDER BY ownership_start_date ASC, created_at ASC
                """
            ),
            {"property_id": property_id, "tenant_id": tenant_id},
        )
        periods = [dict(r) for r in ownership_result.mappings().all()]
        if not periods:
            return []

        ownership_ids = [p["id"] for p in periods]
        co_owner_result = await self.session.execute(
            text(
                """
                SELECT ownership_id, customer_id, role, share_percentage
                FROM public.property_ownership_co_owners
                WHERE ownership_id = ANY(:ownership_ids) AND tenant_id = :tenant_id
                ORDER BY created_at ASC
                """
            ),
            {"ownership_ids": ownership_ids, "tenant_id": tenant_id},
        )
        co_owners_by_ownership: dict[Any, list[dict[str, Any]]] = {}
        for row in co_owner_result.mappings().all():
            co_owners_by_ownership.setdefault(row["ownership_id"], []).append(dict(row))

        for period in periods:
            period["co_owners"] = co_owners_by_ownership.get(period["id"], [])
        return periods

    async def get_open_resale_listing(
        self, tenant_id: UUID, ownership_id: UUID
    ) -> dict[str, Any] | None:
        """Fetch the open resale listing (if any) for a given ownership record."""
        result = await self.session.execute(
            text(
                """
                SELECT id, listing_status, asking_price, listed_at
                FROM public.property_resale_listings
                WHERE ownership_id = :ownership_id AND tenant_id = :tenant_id
                  AND listing_status = 'open'::public.resale_listing_status
                """
            ),
            {"ownership_id": ownership_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_current_prices(self, tenant_id: UUID, property_id: UUID) -> list[dict[str, Any]]:
        """Fetch all currently-effective price rows for a property."""
        result = await self.session.execute(
            text(
                """
                SELECT price_type, amount, currency, effective_from
                FROM public.property_prices
                WHERE property_id = :property_id AND tenant_id = :tenant_id
                  AND is_current = true
                ORDER BY price_type ASC
                """
            ),
            {"property_id": property_id, "tenant_id": tenant_id},
        )
        return [dict(r) for r in result.mappings().all()]
