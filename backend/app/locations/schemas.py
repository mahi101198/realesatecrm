"""Location Domain Pydantic Schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LocationCreate(BaseModel):
    """Request payload to create a tenant location (city/area)."""

    name: str = Field(..., min_length=1, max_length=255, description='e.g. "Gurgaon", "Whitefield"')
    city: str = Field(..., min_length=1, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str = Field(default="India", max_length=100)
    pincode: str | None = Field(default=None, max_length=20)
    is_active: bool = True


class LocationUpdate(BaseModel):
    """Request payload to update a location, including deactivation via is_active."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None


class LocationResponse(BaseModel):
    """Location API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    name: str
    city: str
    state: str | None = None
    country: str
    pincode: str | None = None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class LocationFilter(BaseModel):
    """Filter parameters for listing locations."""

    is_active: bool | None = None
    city: str | None = None
