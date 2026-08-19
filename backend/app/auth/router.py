"""Authentication router — minimal Phase 2 surface."""

from fastapi import APIRouter, Depends, status

from app.auth.dependencies import get_current_user
from app.auth.schemas import MeResponse
from app.users.service import ValidatedSecurityState

router = APIRouter(tags=["Authentication"])


@router.get(
    "/me",
    response_model=MeResponse,
    status_code=status.HTTP_200_OK,
    summary="Current authenticated identity",
    description=(
        "Returns the authenticated application user derived from the verified "
        "Supabase JWT and current database RBAC state. Does not accept user_id, "
        "tenant_id, or role as request parameters."
    ),
)
async def get_me(
    user: ValidatedSecurityState = Depends(get_current_user),
) -> MeResponse:
    """Return safe identity fields for the authenticated caller."""
    return MeResponse(
        id=user.user_id,
        email=user.email,
        name=user.name,
        role=user.role,
        tenant_id=user.tenant_id,
        permissions=sorted(user.permissions),
        is_super_admin=user.is_super_admin,
    )
