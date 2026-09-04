"""Role & Permission Management REST API Router."""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_permission
from app.core.permissions import Permission, resolve_tenant_scope
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.roles.schemas import (
    PermissionCatalogItem,
    RoleCreate,
    RolePermissionsReplace,
    RoleResponse,
    RoleUpdate,
)
from app.roles.service import RoleService

router = APIRouter(prefix="/roles", tags=["Role & Permission Management"])


def _service(session: AsyncSession) -> RoleService:
    return RoleService(session)


# Registered before /{role_id} so "permissions-catalog" is never mistaken for a role_id.
@router.get(
    "/permissions-catalog",
    response_model=list[PermissionCatalogItem],
    status_code=status.HTTP_200_OK,
    summary="List Permission Catalog",
    description="List every permission code available for assignment to a role.",
)
async def list_permission_catalog(
    context: RequestContext = Depends(require_permission(Permission.ROLE_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[PermissionCatalogItem]:
    """List permission catalog endpoint."""
    return await _service(session).list_permission_catalog()


@router.get(
    "",
    response_model=list[RoleResponse],
    status_code=status.HTTP_200_OK,
    summary="List Roles",
    description="List every role visible to this tenant: system roles plus this tenant's own custom roles.",
)
async def list_roles(
    context: RequestContext = Depends(require_permission(Permission.ROLE_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> list[RoleResponse]:
    """List roles endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to list roles.")
    return await _service(session).list_roles(tenant_id)


@router.post(
    "",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Role",
    description="Create a new tenant-custom role. Always non-system, scoped to the caller's tenant.",
)
async def create_role(
    data: RoleCreate,
    context: RequestContext = Depends(require_permission(Permission.ROLE_CREATE)),
    session: AsyncSession = Depends(get_db_session),
) -> RoleResponse:
    """Create role endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required to create a role.")
    return await _service(session).create_role(tenant_id, data)


@router.get(
    "/{role_id}",
    response_model=RoleResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Role Details",
    description="Fetch a single role's details, permission grants, and active assignee count.",
)
async def get_role(
    role_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.ROLE_READ)),
    session: AsyncSession = Depends(get_db_session),
) -> RoleResponse:
    """Get role details endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    return await _service(session).get_role(tenant_id, role_id)


@router.patch(
    "/{role_id}",
    response_model=RoleResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Role",
    description="Update a tenant-custom role's display name, description, or active status.",
)
async def update_role(
    role_id: UUID,
    data: RoleUpdate,
    context: RequestContext = Depends(require_permission(Permission.ROLE_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> RoleResponse:
    """Update role endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    return await _service(session).update_role(tenant_id, role_id, data)


@router.delete(
    "/{role_id}",
    response_model=RoleResponse,
    status_code=status.HTTP_200_OK,
    summary="Deactivate Role",
    description="Deactivate a tenant-custom role. Blocked while any user still actively holds it.",
)
async def deactivate_role(
    role_id: UUID,
    context: RequestContext = Depends(require_permission(Permission.ROLE_DELETE)),
    session: AsyncSession = Depends(get_db_session),
) -> RoleResponse:
    """Deactivate role endpoint."""
    tenant_id = resolve_tenant_scope(context)
    if tenant_id is None:
        raise ValueError("Tenant scope is required.")
    return await _service(session).deactivate_role(tenant_id, role_id)


@router.put(
    "/{role_id}/permissions",
    response_model=RoleResponse,
    status_code=status.HTTP_200_OK,
    summary="Replace Role Permissions",
    description=(
        "Replace a tenant-custom role's full permission set. A caller can never grant "
        "permissions they do not themselves hold, unless they are a platform super_admin."
    ),
)
async def replace_role_permissions(
    role_id: UUID,
    data: RolePermissionsReplace,
    context: RequestContext = Depends(require_permission(Permission.ROLE_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> RoleResponse:
    """Replace role permissions endpoint."""
    return await _service(session).replace_permissions(context, role_id, data)
