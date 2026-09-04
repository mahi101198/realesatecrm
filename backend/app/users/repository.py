"""User security-context repository — efficient DB loads for auth."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.users.models import PermissionRecord, Role, RolePermission, Tenant, User, UserRole


@dataclass(frozen=True)
class SecurityIdentity:
    """Flat security identity loaded in one query path for RequestContext building."""

    user_id: UUID
    auth_user_id: UUID
    tenant_id: UUID | None
    email: str
    name: str
    status: str
    deleted_at: datetime | None
    tenant_is_active: bool | None
    tenant_deleted_at: datetime | None
    role_names: tuple[str, ...]
    permission_codes: frozenset[str]
    is_super_admin: bool


class UserRepository:
    """Database access for application users and RBAC resolution."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_security_identity_by_auth_user_id(
        self,
        auth_user_id: UUID,
    ) -> SecurityIdentity | None:
        """Load user + active roles + permission codes + tenant status in one path.

        Active role means: user_roles.is_active, not expired, role.is_active.
        Permissions are the union across all active roles.
        """
        stmt = (
            select(User)
            .where(User.auth_user_id == auth_user_id)
            .options(
                selectinload(User.tenant),
                selectinload(User.user_roles)
                .selectinload(UserRole.role)
                .selectinload(Role.role_permissions)
                .selectinload(RolePermission.permission),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        user = result.scalar_one_or_none()
        if user is None or user.auth_user_id is None:
            return None

        now = datetime.now(UTC)
        role_names: list[str] = []
        permission_codes: set[str] = set()
        is_super_admin = False

        for user_role in user.user_roles:
            if not user_role.is_active:
                continue
            if user_role.expires_at is not None and user_role.expires_at <= now:
                continue

            role = user_role.role
            if role is None or not role.is_active:
                continue

            role_names.append(role.name)
            if role.name == "super_admin" and role.is_system_role:
                is_super_admin = True

            for role_permission in role.role_permissions:
                permission: PermissionRecord | None = role_permission.permission
                if permission is not None:
                    permission_codes.add(permission.code)

        tenant: Tenant | None = user.tenant
        return SecurityIdentity(
            user_id=user.id,
            auth_user_id=user.auth_user_id,
            tenant_id=user.tenant_id,
            email=user.email,
            name=user.name,
            status=user.status,
            deleted_at=user.deleted_at,
            tenant_is_active=tenant.is_active if tenant is not None else None,
            tenant_deleted_at=tenant.deleted_at if tenant is not None else None,
            role_names=tuple(role_names),
            permission_codes=frozenset(permission_codes),
            is_super_admin=is_super_admin,
        )

    async def get_by_id(self, user_id: UUID, tenant_id: UUID | None = None) -> User | None:
        """Load a user by application primary key, optionally scoped to tenant_id."""
        stmt = select(User).where(User.id == user_id)
        if tenant_id is not None:
            stmt = stmt.where(User.tenant_id == tenant_id)
        stmt = stmt.limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class UserAdminRepository:
    """Database access for the staff user/role admin surface (list/get users,
    assign/remove roles). Deliberately separate from UserRepository above,
    which is the narrow, security-critical identity-resolution path for
    authentication -- this is ordinary tenant-scoped CRUD, kept in the raw
    SQL `text()` style used by every other domain repository in this
    codebase rather than extending the auth-identity ORM slice."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search(
        self,
        tenant_id: UUID,
        status_filter: str | None,
        query: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """List staff users within a tenant."""
        where_conditions = ["tenant_id = :tenant_id", "deleted_at IS NULL"]
        params: dict[str, Any] = {"tenant_id": tenant_id, "limit": limit, "offset": offset}

        if status_filter:
            where_conditions.append("status = CAST(:status AS public.user_status)")
            params["status"] = status_filter
        if query:
            where_conditions.append("(name ILIKE :search_q OR email ILIKE :search_q)")
            params["search_q"] = f"%{query.strip()}%"

        where_str = " AND ".join(where_conditions)
        count_result = await self.session.execute(
            text(f"SELECT COUNT(*) FROM public.users WHERE {where_str}"),  # noqa: S608
            params,
        )
        total = count_result.scalar_one()

        select_result = await self.session.execute(
            text(
                f"""
                SELECT * FROM public.users
                WHERE {where_str}
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
                """  # noqa: S608
            ),
            params,
        )
        return [dict(r) for r in select_result.mappings().all()], total

    async def get_user_detail(self, tenant_id: UUID, user_id: UUID) -> dict[str, Any] | None:
        """Fetch a single staff user, scoped to tenant."""
        result = await self.session.execute(
            text(
                """
                SELECT * FROM public.users
                WHERE id = :id AND tenant_id = :tenant_id AND deleted_at IS NULL
                """
            ),
            {"id": user_id, "tenant_id": tenant_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def list_assignable_roles(self, tenant_id: UUID) -> list[dict[str, Any]]:
        """List every role assignable within this tenant: system roles
        (tenant_id IS NULL) plus this tenant's own custom roles, active only."""
        result = await self.session.execute(
            text(
                """
                SELECT id, name, display_name, description, is_system_role
                FROM public.roles
                WHERE is_active = true AND (tenant_id IS NULL OR tenant_id = :tenant_id)
                ORDER BY is_system_role DESC, display_name ASC
                """
            ),
            {"tenant_id": tenant_id},
        )
        return [dict(r) for r in result.mappings().all()]

    async def get_role_by_id(self, role_id: UUID) -> dict[str, Any] | None:
        """Fetch a role's name/tenant_id/system-role flag, for escalation checks."""
        result = await self.session.execute(
            text(
                """
                SELECT id, tenant_id, name, is_system_role, is_active
                FROM public.roles WHERE id = :id
                """
            ),
            {"id": role_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row else None

    async def list_user_roles(self, tenant_id: UUID, user_id: UUID) -> list[dict[str, Any]]:
        """List active role grants for a user, scoped to tenant."""
        result = await self.session.execute(
            text(
                """
                SELECT ur.id, ur.user_id, ur.role_id, r.name AS role_name,
                       ur.granted_by, ur.granted_at, ur.expires_at, ur.is_active
                FROM public.user_roles ur
                JOIN public.roles r ON r.id = ur.role_id
                WHERE ur.user_id = :user_id AND ur.tenant_id = :tenant_id AND ur.is_active = true
                ORDER BY ur.granted_at DESC
                """
            ),
            {"user_id": user_id, "tenant_id": tenant_id},
        )
        return [dict(r) for r in result.mappings().all()]

    async def assign_role(
        self,
        tenant_id: UUID,
        user_id: UUID,
        role_id: UUID,
        granted_by: UUID | None,
        expires_at: datetime | None,
    ) -> dict[str, Any]:
        """Grant a role to a user. Reactivates + refreshes an existing
        (user_id, role_id) grant if one already exists (uq_user_roles_user_role),
        rather than erroring on a previously-removed-then-reassigned role."""
        result = await self.session.execute(
            text(
                """
                INSERT INTO public.user_roles (
                    tenant_id, user_id, role_id, granted_by, granted_at, expires_at, is_active
                ) VALUES (
                    :tenant_id, :user_id, :role_id, :granted_by, NOW(), :expires_at, true
                )
                ON CONFLICT (user_id, role_id) DO UPDATE SET
                    is_active = true,
                    granted_by = EXCLUDED.granted_by,
                    granted_at = NOW(),
                    expires_at = EXCLUDED.expires_at
                RETURNING id, user_id, role_id, granted_by, granted_at, expires_at, is_active
                """
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "role_id": role_id,
                "granted_by": granted_by,
                "expires_at": expires_at,
            },
        )
        return dict(result.mappings().one())

    async def remove_role(self, tenant_id: UUID, user_id: UUID, role_id: UUID) -> bool:
        """Soft-remove a role grant (is_active = false), consistent with this
        codebase's don't-hard-delete convention for state-bearing records."""
        result = await self.session.execute(
            text(
                """
                UPDATE public.user_roles
                SET is_active = false
                WHERE user_id = :user_id AND role_id = :role_id AND tenant_id = :tenant_id
                  AND is_active = true
                """
            ),
            {"user_id": user_id, "role_id": role_id, "tenant_id": tenant_id},
        )
        rowcount = getattr(result, "rowcount", 0)
        return bool(rowcount > 0)
