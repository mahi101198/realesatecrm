"""Tenant Admin REST API Router.

GET requires only authentication (no specific permission) -- matches the
existing RLS read policy on `tenants`, which lets any authenticated member
of a tenant read their own tenant row with no permission gate. PATCH
requires user.update, matching migration 019's own RLS check
(`has_permission('user.update')`) on the tenant UPDATE policy exactly.

Tenant CREATE is intentionally not exposed here -- see app/tenants/__init__.py.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_request_context_dep, require_permission
from app.core.permissions import Permission, ensure_tenant_resource_access
from app.core.request_context import RequestContext
from app.db.session import get_db_session
from app.tenants.schemas import TenantResponse, TenantUpdate
from app.tenants.service import TenantService

router = APIRouter(prefix="/tenants", tags=["Tenant Admin"])


@router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    status_code=status.HTTP_200_OK,
    summary="Get Tenant",
    description=(
        "Fetch a tenant's company profile. Any authenticated member of that tenant may read it."
    ),
)
async def get_tenant(
    tenant_id: UUID,
    context: RequestContext = Depends(get_request_context_dep),
    session: AsyncSession = Depends(get_db_session),
) -> TenantResponse:
    """Get tenant endpoint."""
    service = TenantService(session)
    tenant = await service.get_tenant(tenant_id)
    ensure_tenant_resource_access(context, tenant.id)
    return tenant


@router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    status_code=status.HTTP_200_OK,
    summary="Update Tenant Company Profile",
    description=(
        "Update tenant company-profile fields (name, contact, address, branding). "
        "Subscription/plan settings and slug can never be changed through this endpoint."
    ),
)
async def update_tenant(
    tenant_id: UUID,
    data: TenantUpdate,
    context: RequestContext = Depends(require_permission(Permission.USER_UPDATE)),
    session: AsyncSession = Depends(get_db_session),
) -> TenantResponse:
    """Update tenant endpoint."""
    # For a tenant-scoped caller, ensure_tenant_resource_access requires
    # tenant_id == context.tenant_id (else 404) -- a tenant admin can only
    # ever update their OWN tenant's profile. Super admin bypasses this.
    ensure_tenant_resource_access(context, tenant_id)
    service = TenantService(session)
    return await service.update_tenant(tenant_id, data)
