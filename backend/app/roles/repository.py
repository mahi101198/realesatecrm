"""Role & Permission Management Repository for PostgreSQL database operations."""

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Shared SELECT shape for a role row + its joined permission codes and active
# assignee count. Used by both list_roles and get_by_id so the two never
# drift out of sync.
_ROLE_SELECT_FIELDS = """
    r.*,
    COALESCE(
        (SELECT array_agg(p.code ORDER BY p.code)
         FROM public.role_permissions rp
         JOIN public.permissions p ON p.id = rp.permission_id
         WHERE rp.role_id = r.id),
        ARRAY[]::text[]
    ) AS permission_codes,
    (SELECT count(*) FROM public.user_roles ur
     WHERE ur.role_id = r.id AND ur.is_active = true) AS assignee_count
"""


class RoleRepository:
    """Repository handling database access for roles/permissions/role_permissions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_roles(self, tenant_id: UUID) -> list[dict[str, Any]]:
        """List every role visible to a tenant: system roles + this tenant's
        own custom roles."""
        query = text(
            f"""
            SELECT {_ROLE_SELECT_FIELDS}
            FROM public.roles r
            WHERE r.tenant_id IS NULL OR r.tenant_id = :tenant_id
            ORDER BY r.is_system_role DESC, r.name
            """  # noqa: S608
        )
        result = await self.session.execute(query, {"tenant_id": tenant_id})
        return [dict(r) for r in result.mappings().all()]

    async def get_by_id(self, tenant_id: UUID, role_id: UUID) -> dict[str, Any] | None:
        """Fetch a single role visible to this tenant (system or own-tenant)."""
        query = text(
            f"""
            SELECT {_ROLE_SELECT_FIELDS}
            FROM public.roles r
            WHERE r.id = :role_id AND (r.tenant_id IS NULL OR r.tenant_id = :tenant_id)
            """  # noqa: S608
        )
        result = await self.session.execute(query, {"role_id": role_id, "tenant_id": tenant_id})
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def create(self, tenant_id: UUID, data: Any) -> dict[str, Any]:
        """Insert a new tenant-custom role (is_system_role always false)."""
        query = text(
            """
            INSERT INTO public.roles (tenant_id, name, display_name, description, is_system_role)
            VALUES (:tenant_id, :name, :display_name, :description, false)
            RETURNING *, ARRAY[]::text[] AS permission_codes, 0 AS assignee_count
            """
        )
        result = await self.session.execute(
            query,
            {
                "tenant_id": tenant_id,
                "name": data.name,
                "display_name": data.display_name,
                "description": data.description,
            },
        )
        row = result.mappings().one()
        return dict(row)

    async def update(
        self, tenant_id: UUID, role_id: UUID, data: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Update a tenant-owned, non-system role's descriptive fields.

        The `is_system_role = false` guard is enforced right here in the
        WHERE clause, not only in the service layer -- a system role can
        never be updated through this query no matter what called it.
        """
        if not data:
            return await self.get_by_id(tenant_id, role_id)

        set_clauses = [f"{key} = :val_{key}" for key in data]
        params: dict[str, Any] = {f"val_{key}": value for key, value in data.items()}
        params["role_id"] = role_id
        params["tenant_id"] = tenant_id

        query = text(
            f"""
            UPDATE public.roles
            SET {", ".join(set_clauses)}, updated_at = NOW()
            WHERE id = :role_id AND tenant_id = :tenant_id AND is_system_role = false
            RETURNING id
            """  # noqa: S608
        )
        result = await self.session.execute(query, params)
        updated = result.mappings().one_or_none()
        if not updated:
            return None
        return await self.get_by_id(tenant_id, role_id)

    async def get_permission_ids(self, codes: list[str]) -> dict[str, UUID]:
        """Resolve permission codes to IDs. Returns only the codes that exist."""
        if not codes:
            return {}
        query = text("SELECT id, code FROM public.permissions WHERE code = ANY(:codes)")
        result = await self.session.execute(query, {"codes": codes})
        return {row["code"]: row["id"] for row in result.mappings().all()}

    async def replace_permissions(
        self, tenant_id: UUID, role_id: UUID, permission_ids: list[UUID]
    ) -> bool:
        """Replace a tenant-owned, non-system role's full permission set.

        Returns False (no-op) if the role doesn't exist, isn't owned by this
        tenant, or is a system role -- checked via the same guarded UPDATE
        idiom as `update()`, so this can never touch a system role's grants.
        """
        guard = await self.session.execute(
            text(
                """
                SELECT id FROM public.roles
                WHERE id = :role_id AND tenant_id = :tenant_id AND is_system_role = false
                """
            ),
            {"role_id": role_id, "tenant_id": tenant_id},
        )
        if guard.mappings().one_or_none() is None:
            return False

        await self.session.execute(
            text("DELETE FROM public.role_permissions WHERE role_id = :role_id"),
            {"role_id": role_id},
        )
        if permission_ids:
            await self.session.execute(
                text(
                    """
                    INSERT INTO public.role_permissions (role_id, permission_id)
                    SELECT :role_id, unnest(CAST(:permission_ids AS uuid[]))
                    """
                ),
                {"role_id": role_id, "permission_ids": permission_ids},
            )
        return True

    async def list_permissions(self) -> list[dict[str, Any]]:
        """List the full permission catalog, grouped implicitly by resource/action order."""
        result = await self.session.execute(
            text("SELECT * FROM public.permissions ORDER BY resource, action")
        )
        return [dict(r) for r in result.mappings().all()]
