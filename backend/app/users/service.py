"""User security service — validate identity and build trusted context fields."""

import logging
from dataclasses import dataclass
from math import ceil
from uuid import UUID

from app.core.constants import ACTIVE_USER_STATUS, ROLE_PRECEDENCE
from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.request_context import RequestContext, SecurityScope
from app.shared.schemas import PaginatedResponse, PaginationParams
from app.users.repository import SecurityIdentity, UserAdminRepository, UserRepository
from app.users.schemas import RoleAssignRequest, UserFilter, UserResponse, UserRoleResponse

logger = logging.getLogger(__name__)

# Roles that grant meaningful platform/tenant-wide authority. Assigning these
# requires elevated caller privilege -- mirrors public.trg_fn_prevent_role_escalation
# (migration 019) EXACTLY, because that trigger explicitly bypasses service_role
# (`if public.is_service_role() then return; end if`), and this backend always
# connects as service_role. That means the DB trigger provides ZERO protection
# for any write this API makes -- this app-layer check is not defense-in-depth,
# it is the ONLY enforcement of role-escalation rules that actually runs for
# requests that go through this service.
_SUPER_ADMIN_ROLE_NAME = "super_admin"
_ADMIN_ROLE_NAME = "admin"


@dataclass(frozen=True)
class ValidatedSecurityState:
    """Validated security state ready to become RequestContext."""

    user_id: UUID
    auth_user_id: UUID
    tenant_id: UUID | None
    email: str
    name: str
    role: str
    permissions: frozenset[str]
    is_super_admin: bool
    scope: SecurityScope


def select_primary_role(role_names: tuple[str, ...]) -> str:
    """Pick highest-precedence role name from active roles.

    Permissions are always the union; this value is only for RequestContext.role.
    """
    if not role_names:
        raise UnauthorizedError(
            message="Authentication is required to access this resource.",
            code="NO_ACTIVE_ROLE",
        )

    precedence_index = {name: index for index, name in enumerate(ROLE_PRECEDENCE)}
    return min(
        role_names,
        key=lambda name: precedence_index.get(name, len(ROLE_PRECEDENCE)),
    )


class UserService:
    """Application-user validation for authentication."""

    def __init__(self, repository: UserRepository) -> None:
        self._repository = repository

    async def resolve_security_state(self, auth_user_id: UUID) -> ValidatedSecurityState:
        """Load and validate application user for a verified Supabase auth subject.

        Unknown, inactive, deleted, or tenant-disabled users are denied with 401.
        """
        identity = await self._repository.get_security_identity_by_auth_user_id(auth_user_id)
        if identity is None:
            raise UnauthorizedError(
                message="Authentication is required to access this resource.",
                code="USER_NOT_FOUND",
            )

        return self.validate_identity(identity)

    def validate_identity(self, identity: SecurityIdentity) -> ValidatedSecurityState:
        """Validate a loaded identity without additional DB access (testable)."""
        if identity.deleted_at is not None:
            raise UnauthorizedError(
                message="Authentication is required to access this resource.",
                code="USER_INACTIVE",
            )

        if identity.status != ACTIVE_USER_STATUS:
            raise UnauthorizedError(
                message="Authentication is required to access this resource.",
                code="USER_INACTIVE",
            )

        if not identity.role_names:
            raise UnauthorizedError(
                message="Authentication is required to access this resource.",
                code="NO_ACTIVE_ROLE",
            )

        is_super_admin = identity.is_super_admin
        primary_role = select_primary_role(identity.role_names)

        # Enforce platform invariant: super_admin has no tenant affiliation.
        if is_super_admin:
            if identity.tenant_id is not None:
                raise UnauthorizedError(
                    message="Authentication is required to access this resource.",
                    code="INVALID_USER_STATE",
                )
            scope = SecurityScope.GLOBAL
            tenant_id: UUID | None = None
        else:
            if identity.tenant_id is None:
                raise UnauthorizedError(
                    message="Authentication is required to access this resource.",
                    code="INVALID_USER_STATE",
                )
            if identity.tenant_is_active is not True or identity.tenant_deleted_at is not None:
                raise UnauthorizedError(
                    message="Authentication is required to access this resource.",
                    code="TENANT_INACTIVE",
                )
            scope = SecurityScope.TENANT
            tenant_id = identity.tenant_id

        return ValidatedSecurityState(
            user_id=identity.user_id,
            auth_user_id=identity.auth_user_id,
            tenant_id=tenant_id,
            email=identity.email,
            name=identity.name,
            role=primary_role,
            permissions=identity.permission_codes,
            is_super_admin=is_super_admin,
            scope=scope,
        )


class UserAdminService:
    """Service for the staff user/role admin surface. Owns the
    role-escalation-prevention logic -- see module docstring comment above
    for why this must be enforced here in Python, not merely relied upon at
    the DB trigger level."""

    def __init__(self, repository: UserAdminRepository) -> None:
        self.repository = repository

    async def list_users(
        self, tenant_id: UUID, filters: UserFilter, pagination: PaginationParams
    ) -> PaginatedResponse[UserResponse]:
        """List staff users in the caller's tenant."""
        rows, total = await self.repository.search(
            tenant_id, filters.status, filters.query, pagination.page_size, pagination.offset
        )
        items = [UserResponse.model_validate(r) for r in rows]
        pages = ceil(total / pagination.page_size) if pagination.page_size > 0 else 0
        return PaginatedResponse[UserResponse](
            items=items, page=pagination.page, page_size=pagination.page_size,
            total=total, pages=pages,
        )

    async def get_user(self, tenant_id: UUID, user_id: UUID) -> UserResponse:
        """Fetch a single staff user, scoped to tenant."""
        row = await self.repository.get_user_detail(tenant_id, user_id)
        if not row:
            raise NotFoundError(
                message=f"User with ID '{user_id}' was not found.", code="USER_NOT_FOUND"
            )
        return UserResponse.model_validate(row)

    async def list_roles(self, tenant_id: UUID, user_id: UUID) -> list[UserRoleResponse]:
        """List a user's active role grants."""
        await self.get_user(tenant_id, user_id)
        rows = await self.repository.list_user_roles(tenant_id, user_id)
        return [UserRoleResponse.model_validate(r) for r in rows]

    async def assign_role(
        self, context: RequestContext, tenant_id: UUID, user_id: UUID, data: RoleAssignRequest
    ) -> UserRoleResponse:
        """Assign a role to a user, enforcing role-escalation rules.

        Mirrors public.trg_fn_prevent_role_escalation's three rules exactly:
          1. A caller cannot assign/modify their own roles (self-escalation),
             unless the caller is a platform super_admin.
          2. Only a super_admin caller may grant the super_admin role, to anyone.
          3. Only a super_admin, or a caller holding user.update (already
             required to reach this method via the endpoint's permission
             gate, but checked explicitly here too for parity with the
             trigger and as a safety net if that gate is ever loosened),
             may grant the admin role.
        Additionally (not from the trigger, but required by tenant isolation):
          4. The target role must be a system role or belong to the caller's
             own tenant -- never another tenant's custom role.
        """
        await self.get_user(tenant_id, user_id)  # 404s if unknown/cross-tenant

        if user_id == context.user_id and not context.is_super_admin:
            raise ForbiddenError(
                message="You cannot assign or modify your own roles.",
                code="SELF_ROLE_ESCALATION_BLOCKED",
            )

        role = await self.repository.get_role_by_id(data.role_id)
        if not role or not role["is_active"]:
            raise NotFoundError(
                message=f"Role with ID '{data.role_id}' was not found or is inactive.",
                code="ROLE_NOT_FOUND",
            )

        if role["tenant_id"] is not None and role["tenant_id"] != tenant_id:
            raise NotFoundError(
                message=f"Role with ID '{data.role_id}' was not found.",
                code="ROLE_NOT_FOUND",
            )

        if role["name"] == _SUPER_ADMIN_ROLE_NAME and not context.is_super_admin:
            raise ForbiddenError(
                message="Only a platform super_admin can grant the super_admin role.",
                code="SUPER_ADMIN_ROLE_ESCALATION_BLOCKED",
            )

        if role["name"] == _ADMIN_ROLE_NAME and not (
            context.is_super_admin or "user.update" in context.permissions
        ):
            raise ForbiddenError(
                message="Insufficient permissions to grant the admin role.",
                code="ADMIN_ROLE_ESCALATION_BLOCKED",
            )

        row = await self.repository.assign_role(
            tenant_id, user_id, data.role_id, context.user_id, data.expires_at
        )
        return UserRoleResponse.model_validate({**row, "role_name": role["name"]})

    async def remove_role(
        self, context: RequestContext, tenant_id: UUID, user_id: UUID, role_id: UUID
    ) -> None:
        """Remove a role grant. Self-removal is blocked the same as
        self-assignment -- a user must never be able to modify their own
        authority, including by removing a restriction-carrying role."""
        await self.get_user(tenant_id, user_id)

        if user_id == context.user_id and not context.is_super_admin:
            raise ForbiddenError(
                message="You cannot remove your own roles.",
                code="SELF_ROLE_ESCALATION_BLOCKED",
            )

        success = await self.repository.remove_role(tenant_id, user_id, role_id)
        if not success:
            raise NotFoundError(
                message=f"Active role grant '{role_id}' was not found for this user.",
                code="ROLE_GRANT_NOT_FOUND",
            )
