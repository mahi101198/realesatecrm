"""Role & Permission Management Business Service Layer.

Security model (there is no DB trigger guarding roles/role_permissions
mutation the way trg_fn_prevent_role_escalation guards user_roles, so every
guarantee below is enforced here):

  1. Every role created through this API is tenant-owned and non-system --
     there is no path to creating or mutating a platform system role
     (tenant_id IS NULL, is_system_role = true). Those stay migration-only,
     so "Sales Agent" means the same thing in every tenant.
  2. update_role / replace_permissions only ever match a role WHERE
     tenant_id = caller's tenant AND is_system_role = false, enforced in the
     repository's SQL WHERE clause itself, not just in this service.
  3. A caller can never grant a role permissions they do not themselves
     hold, unless they are a platform super_admin. Without this, a user who
     merely has role.update could create/edit a role into holding
     permissions (e.g. audit.read, platform.*) beyond their own authority.
"""

import logging
from uuid import UUID

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db_errors import raise_clean_error_for_write
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.request_context import RequestContext
from app.db.transaction import atomic
from app.roles.repository import RoleRepository
from app.roles.schemas import (
    PermissionCatalogItem,
    RoleCreate,
    RolePermissionsReplace,
    RoleResponse,
    RoleUpdate,
)

logger = logging.getLogger(__name__)


class RoleService:
    """Service orchestrating tenant-custom role CRUD and permission grants."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = RoleRepository(session)

    async def list_roles(self, tenant_id: UUID) -> list[RoleResponse]:
        """List every role visible to this tenant (system + own custom roles)."""
        rows = await self.repository.list_roles(tenant_id)
        return [RoleResponse.model_validate(r) for r in rows]

    async def get_role(self, tenant_id: UUID, role_id: UUID) -> RoleResponse:
        """Fetch a single role visible to this tenant."""
        row = await self.repository.get_by_id(tenant_id, role_id)
        if not row:
            raise NotFoundError(
                message=f"Role with ID '{role_id}' was not found.",
                code="ROLE_NOT_FOUND",
            )
        return RoleResponse.model_validate(row)

    async def create_role(self, tenant_id: UUID, data: RoleCreate) -> RoleResponse:
        """Create a new tenant-custom role."""
        try:
            row = await self.repository.create(tenant_id, data)
        except DBAPIError as e:
            logger.warning(f"Role create DB error: {e!s}")
            raise_clean_error_for_write(e, resource="role")
        return RoleResponse.model_validate(row)

    async def update_role(
        self, tenant_id: UUID, role_id: UUID, data: RoleUpdate
    ) -> RoleResponse:
        """Update a tenant-custom role's descriptive fields.

        System roles and other tenants' roles never match the repository's
        guarded UPDATE, so this raises NotFound for both -- indistinguishable
        from the caller's point of view, same reasoning as
        ensure_tenant_resource_access elsewhere in this app.
        """
        existing = await self.get_role(tenant_id, role_id)  # 404s if not visible at all
        if existing.is_system_role:
            raise ForbiddenError(
                message="System roles are platform-defined and cannot be modified.",
                code="SYSTEM_ROLE_IMMUTABLE",
            )

        update_dict = data.model_dump(exclude_unset=True)
        try:
            row = await self.repository.update(tenant_id, role_id, update_dict)
        except DBAPIError as e:
            logger.warning(f"Role update DB error: {e!s}")
            raise_clean_error_for_write(e, resource="role")
        if not row:
            raise NotFoundError(
                message=f"Role with ID '{role_id}' was not found.",
                code="ROLE_NOT_FOUND",
            )
        return RoleResponse.model_validate(row)

    async def deactivate_role(self, tenant_id: UUID, role_id: UUID) -> RoleResponse:
        """Deactivate (soft-delete) a tenant-custom role.

        Blocked while any user still actively holds it -- reassign those
        users first, mirroring how bookings/sales block destructive actions
        on records something else still depends on.
        """
        existing = await self.get_role(tenant_id, role_id)
        if existing.is_system_role:
            raise ForbiddenError(
                message="System roles are platform-defined and cannot be deleted.",
                code="SYSTEM_ROLE_IMMUTABLE",
            )
        if existing.assignee_count > 0:
            raise ConflictError(
                message=(
                    f"'{existing.display_name}' is still assigned to "
                    f"{existing.assignee_count} user(s). Reassign them before deleting this role."
                ),
                code="ROLE_HAS_ACTIVE_ASSIGNEES",
            )

        row = await self.repository.update(tenant_id, role_id, {"is_active": False})
        if not row:
            raise NotFoundError(
                message=f"Role with ID '{role_id}' was not found.",
                code="ROLE_NOT_FOUND",
            )
        return RoleResponse.model_validate(row)

    async def replace_permissions(
        self,
        context: RequestContext,
        role_id: UUID,
        data: RolePermissionsReplace,
    ) -> RoleResponse:
        """Replace a tenant-custom role's full permission set."""
        tenant_id = context.tenant_id
        if tenant_id is None:
            raise ValidationError(
                message="Tenant scope is required.", code="MISSING_TENANT_SCOPE"
            )

        existing = await self.get_role(tenant_id, role_id)
        if existing.is_system_role:
            raise ForbiddenError(
                message="System role permissions are platform-defined and cannot be edited here.",
                code="SYSTEM_ROLE_IMMUTABLE",
            )

        requested = list(dict.fromkeys(data.permission_codes))  # de-dupe, preserve order

        resolved = await self.repository.get_permission_ids(requested)
        unknown = [code for code in requested if code not in resolved]
        if unknown:
            raise ValidationError(
                message=f"Unknown permission code(s): {', '.join(unknown)}.",
                code="UNKNOWN_PERMISSION_CODE",
            )

        if not context.is_super_admin:
            not_held = [code for code in requested if code not in context.permissions]
            if not_held:
                raise ForbiddenError(
                    message=(
                        "You cannot grant a role permissions you do not hold yourself: "
                        f"{', '.join(not_held)}."
                    ),
                    code="PERMISSION_ESCALATION_BLOCKED",
                )

        async with atomic(self.session):
            ok = await self.repository.replace_permissions(
                tenant_id, role_id, list(resolved.values())
            )
        if not ok:
            # Role existed a moment ago (get_role above) but the guarded
            # replace didn't match -- treat as not-found rather than leak why.
            raise NotFoundError(
                message=f"Role with ID '{role_id}' was not found.",
                code="ROLE_NOT_FOUND",
            )
        return await self.get_role(tenant_id, role_id)

    async def list_permission_catalog(self) -> list[PermissionCatalogItem]:
        """List every permission code available to assign to a role."""
        rows = await self.repository.list_permissions()
        return [PermissionCatalogItem.model_validate(r) for r in rows]
