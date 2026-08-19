"""Tenant Repository for PostgreSQL database operations."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tenants.schemas import TenantUpdate

logger = logging.getLogger(__name__)


class TenantRepository:
    """Repository handling database access for tenant company-profile data."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, tenant_id: UUID) -> dict[str, Any] | None:
        """Fetch a tenant by ID."""
        result = await self.session.execute(
            text("SELECT * FROM public.tenants WHERE id = :id AND deleted_at IS NULL"),
            {"id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def update(self, tenant_id: UUID, data: TenantUpdate) -> dict[str, Any] | None:
        """Update tenant company-profile fields. `data` is TenantUpdate, whose
        field set is the actual security boundary here -- see that schema's
        docstring. This method blindly writes whatever fields are set on it,
        by design, trusting the schema to never carry a plan/limit field."""
        data_dict = data.model_dump(exclude_unset=True)
        if not data_dict:
            return await self.get_by_id(tenant_id)

        set_clauses = [f"{key} = :val_{key}" for key in data_dict]
        params: dict[str, Any] = {f"val_{key}": value for key, value in data_dict.items()}
        params["id"] = tenant_id
        set_str = ", ".join(set_clauses)

        query = text(
            f"""
            UPDATE public.tenants
            SET {set_str}, updated_at = NOW()
            WHERE id = :id AND deleted_at IS NULL
            RETURNING *
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        row = result.mappings().one_or_none()
        return dict(row) if row else None
