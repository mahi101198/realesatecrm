"""Role & Permission Management Domain Pydantic Schemas."""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class RoleCreate(BaseModel):
    """Request payload to create a new tenant-custom role.

    Always created with is_system_role=false, scoped to the caller's own
    tenant -- there is no way to create a system (platform-wide) role via
    this API.
    """

    name: str = Field(
        ..., min_length=2, max_length=50, description="Machine name, e.g. 'junior_agent'"
    )
    display_name: str = Field(..., min_length=2, max_length=100)
    description: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _NAME_PATTERN.match(v):
            raise ValueError(
                "name must start with a lowercase letter and contain only "
                "lowercase letters, digits, and underscores."
            )
        return v


class RoleUpdate(BaseModel):
    """Request payload to update a tenant-custom role's descriptive fields.

    name is immutable once created; is_system_role can never be set via API.
    """

    display_name: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = None
    is_active: bool | None = None


class RolePermissionsReplace(BaseModel):
    """Request payload to replace a role's full set of permission codes."""

    permission_codes: list[str] = Field(
        default_factory=list,
        description="Full replacement set of permission codes for this role.",
    )


class RoleResponse(BaseModel):
    """Role API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID | None = None
    name: str
    display_name: str
    description: str | None = None
    is_system_role: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    # ── Joined display fields ────────────────────────────────────────────
    permission_codes: list[str] = Field(default_factory=list)
    assignee_count: int = 0


class PermissionCatalogItem(BaseModel):
    """A single permission code available for assignment to a role."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None = None
    resource: str
    action: str
