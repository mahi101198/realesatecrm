"""Project Repository for database operations."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.projects.schemas import ProjectCreate, ProjectFilter, ProjectUpdate
from app.shared.schemas import PaginationParams

logger = logging.getLogger(__name__)


class ProjectRepository:
    """Repository handling database queries for Projects."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self, tenant_id: UUID, created_by: UUID | None, slug: str, data: ProjectCreate
    ) -> dict[str, Any]:
        """Insert a new project record. Raises IntegrityError on duplicate slug."""
        query = text(
            """
            INSERT INTO public.projects (
                tenant_id, project_type_id, location_id, name, slug, description,
                developer_name, rera_number, rera_state, rera_url,
                address_line1, address_line2, locality, city, district, state,
                country, pincode, latitude, longitude,
                launch_date, possession_date, completion_date,
                status, is_featured, is_public,
                price_min, price_max, currency,
                total_units, available_units, project_area, project_area_unit,
                metadata, created_by
            ) VALUES (
                :tenant_id, :project_type_id, :location_id, :name, :slug, :description,
                :developer_name, :rera_number, :rera_state, :rera_url,
                :address_line1, :address_line2, :locality, :city, :district, :state,
                :country, :pincode, :latitude, :longitude,
                :launch_date, :possession_date, :completion_date,
                CAST(:status AS public.project_status), :is_featured, :is_public,
                :price_min, :price_max, :currency,
                :total_units, :available_units, :project_area,
                CAST(:project_area_unit AS public.area_unit),
                :metadata, :created_by
            )
            RETURNING *
            """
        )
        params = {
            "tenant_id": tenant_id,
            "project_type_id": data.project_type_id,
            "location_id": data.location_id,
            "name": data.name,
            "slug": slug,
            "description": data.description,
            "developer_name": data.developer_name,
            "rera_number": data.rera_number,
            "rera_state": data.rera_state,
            "rera_url": data.rera_url,
            "address_line1": data.address_line1,
            "address_line2": data.address_line2,
            "locality": data.locality,
            "city": data.city,
            "district": data.district,
            "state": data.state,
            "country": data.country,
            "pincode": data.pincode,
            "latitude": data.latitude,
            "longitude": data.longitude,
            "launch_date": data.launch_date,
            "possession_date": data.possession_date,
            "completion_date": data.completion_date,
            "status": data.status,
            "is_featured": data.is_featured,
            "is_public": data.is_public,
            "price_min": data.price_min,
            "price_max": data.price_max,
            "currency": data.currency,
            "total_units": data.total_units,
            "available_units": data.available_units,
            "project_area": data.project_area,
            "project_area_unit": data.project_area_unit,
            "metadata": data.metadata or {},
            "created_by": created_by,
        }
        result = await self.session.execute(query, params)
        row = result.mappings().one()
        return dict(row)

    async def update(
        self, tenant_id: UUID | None, project_id: UUID, data: ProjectUpdate
    ) -> dict[str, Any] | None:
        """Update existing project fields."""
        data_dict = data.model_dump(exclude_unset=True)
        if not data_dict:
            return await self.get_by_id(tenant_id, project_id)

        set_clauses: list[str] = []
        params: dict[str, Any] = {"project_id": project_id}
        if tenant_id is not None:
            params["tenant_id"] = tenant_id

        enum_casts = {
            "status": "public.project_status",
            "project_area_unit": "public.area_unit",
        }

        for key, value in data_dict.items():
            param_key = f"val_{key}"
            if key in enum_casts:
                set_clauses.append(f"{key} = :{param_key}::{enum_casts[key]}")
            else:
                set_clauses.append(f"{key} = :{param_key}")
            params[param_key] = value

        set_str = ", ".join(set_clauses)
        where_clause = "WHERE id = :project_id AND deleted_at IS NULL"
        if tenant_id is not None:
            where_clause += " AND tenant_id = :tenant_id"

        query = text(
            f"""
            UPDATE public.projects
            SET {set_str}, updated_at = NOW()
            {where_clause}
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def get_by_id(self, tenant_id: UUID | None, project_id: UUID) -> dict[str, Any] | None:
        """Fetch project by ID."""
        if tenant_id is not None:
            query = text(
                """
                SELECT * FROM public.projects
                WHERE id = :project_id AND tenant_id = :tenant_id AND deleted_at IS NULL
                """
            )
            params = {"project_id": project_id, "tenant_id": tenant_id}
        else:
            query = text(
                """
                SELECT * FROM public.projects
                WHERE id = :project_id AND deleted_at IS NULL
                """
            )
            params = {"project_id": project_id}

        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def search(
        self,
        tenant_id: UUID | None,
        filters: ProjectFilter,
        pagination: PaginationParams,
    ) -> tuple[list[dict[str, Any]], int]:
        """Search and list projects with pagination."""
        where_conditions = ["deleted_at IS NULL"]
        params: dict[str, Any] = {
            "limit": pagination.page_size,
            "offset": pagination.offset,
        }

        if tenant_id is not None:
            where_conditions.append("tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id

        if filters.city:
            where_conditions.append("city ILIKE :filter_city")
            params["filter_city"] = f"%{filters.city.strip()}%"

        if filters.status:
            where_conditions.append("status = CAST(:filter_status AS public.project_status)")
            params["filter_status"] = filters.status

        if filters.project_type_id:
            where_conditions.append("project_type_id = :filter_project_type_id")
            params["filter_project_type_id"] = filters.project_type_id

        if filters.is_featured is not None:
            where_conditions.append("is_featured = :filter_is_featured")
            params["filter_is_featured"] = filters.is_featured

        if filters.query:
            where_conditions.append(
                "(name ILIKE :search_q OR "
                "developer_name ILIKE :search_q OR "
                "locality ILIKE :search_q)"
            )
            params["search_q"] = f"%{filters.query.strip()}%"

        where_str = " AND ".join(where_conditions)

        count_query = text(f"SELECT COUNT(*) FROM public.projects WHERE {where_str}")  # noqa: S608
        count_result = await self.session.execute(count_query, params)
        total_count = count_result.scalar_one()

        select_query = text(
            f"""
            SELECT * FROM public.projects
            WHERE {where_str}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
            """  # noqa: S608
        )
        select_result = await self.session.execute(select_query, params)
        rows = select_result.mappings().all()

        return [dict(r) for r in rows], total_count
