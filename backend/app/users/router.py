"""Staff User & Role Admin REST API Router.

Minimum viable admin surface: list/get staff users, assign/remove roles.
Role assignment enforces strict escalation prevention -- see
UserAdminService.assign_role's docstring for why this is the ONLY real
enforcement (the DB trigger that implements the same rules explicitly
bypasses service_role, which is how this backend always connects).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.shared.schemas import PaginatedResponse, PaginationParams
from app.users.repository import UserAdminRepository
from app.users.schemas import (
    RoleAssignRequest,
    RoleResponse,
    UserFilter,
    UserResponse,
    UserRoleResponse,
)
from app.users.service import UserAdminService

router = APIRouter(prefix="/users", tags=["User & Role Admin"])


def _service(session: AsyncSession) -> UserAdminService:
    return UserAdminService(UserAdminRepository(session))


@router.get(
    "/roles",
    response_model=list[RoleResponse],
    status_code=status.HTTP_200_OK,
    summary="List Assignable Roles",
    description=(
        "List every role assignable within the caller's tenant: system roles "
        "plus this tenant's own custom roles. Registered before /{user_id} so "
        "'roles' is never mistaken for a user_id."
    ),
)
async def list_assignable_roles(
    context: RequestContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[RoleResponse]:
    """List assignable roles endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    return await _service(session).list_assignable_roles(tenant_id)


@router.get(
    "",
    response_model=PaginatedResponse[UserResponse],
    status_code=status.HTTP_200_OK,
    summary="List Staff Users",
    description="List staff user accounts within the caller's tenant.",
)
async def list_users(
    user_status: Annotated[str | None, Query(alias="status")] = None,
    query: Annotated[str | None, Query(description="Search name or email")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    context: RequestContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> PaginatedResponse[UserResponse]:
    """List users endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to list users.")
    filters = UserFilter(status=user_status, query=query)
    pagination = PaginationParams(page=page, page_size=page_size)
    return await _service(session).list_users(tenant_id, filters, pagination)


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Staff User",
    description="Fetch a single staff user by ID.",
)
async def get_user(
    user_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> UserResponse:
    """Get user endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    user = await _service(session).get_user(tenant_id, user_id)
    ensure_tenant_resource_access(context, user.tenant_id)
    return user


@router.get(
    "/{user_id}/roles",
    response_model=list[UserRoleResponse],
    status_code=status.HTTP_200_OK,
    summary="List User Roles",
    description="List a staff user's active role grants.",
)
async def list_user_roles(
    user_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.USER_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[UserRoleResponse]:
    """List user roles endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    return await _service(session).list_roles(tenant_id, user_id)


@router.post(
    "/{user_id}/roles",
    response_model=UserRoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign Role",
    description=(
        "Grant a role to a staff user. A caller can never grant super_admin unless "
        "they are themselves a super_admin, can never grant admin without "
        "super_admin/user.update authority, and can never assign or modify their "
        "own roles."
    ),
)
async def assign_role(
    user_id: UUID,
    data: RoleAssignRequest,
    context: RequestContext = Depends(require_permission(Permission.USER_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> UserRoleResponse:
    """Assign role endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    service = _service(session)
    user = await service.get_user(tenant_id, user_id)
    ensure_tenant_resource_access(context, user.tenant_id)
    return await service.assign_role(context, tenant_id, user_id, data)


@router.delete(
    "/{user_id}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove Role",
    description="Remove an active role grant from a staff user. Self-removal is blocked.",
)
async def remove_role(
    user_id: UUID,
    role_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.USER_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Remove role endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    service = _service(session)
    user = await service.get_user(tenant_id, user_id)
    ensure_tenant_resource_access(context, user.tenant_id)
    await service.remove_role(context, tenant_id, user_id, role_id)
