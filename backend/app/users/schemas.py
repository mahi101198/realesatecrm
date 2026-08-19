"""Safe user-facing schemas. Security-sensitive fields are never mass-assignable."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MeResponse(BaseModel):
    """Authenticated caller identity derived from JWT + database state."""

    model_config = ConfigDict(from_attributes=False)

    id: UUID
    email: str
    name: str
    role: str
    tenant_id: UUID | None
    permissions: list[str] = Field(default_factory=list)
    is_super_admin: bool


class UserResponse(BaseModel):
    """Staff user account representation for the admin API. Never exposes
    auth_user_id (internal Supabase Auth linkage)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None
    name: str
    email: str
    phone: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime


class UserFilter(BaseModel):
    """Filter parameters for listing staff users."""

    status: str | None = None
    query: str | None = Field(default=None, description="Search name or email")


class UserRoleResponse(BaseModel):
    """A single role grant on a user."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    role_id: UUID
    role_name: str
    granted_by: UUID | None = None
    granted_at: datetime
    expires_at: datetime | None = None
    is_active: bool


class RoleAssignRequest(BaseModel):
    """Request payload to assign a role to a user."""

    role_id: UUID = Field(..., description="Target role ID")
    expires_at: datetime | None = Field(
        default=None, description="Optional time-limited grant expiry"
    )
