"""Project Domain Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectResponse(BaseModel):
    """Project representation for API response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    project_type_id: UUID
    location_id: UUID | None = None
    name: str
    slug: str
    description: str | None = None
    developer_name: str | None = None
    rera_number: str | None = None
    address_line1: str | None = None
    locality: str | None = None
    city: str
    state: str
    country: str
    pincode: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    launch_date: date | None = None
    possession_date: date | None = None
    completion_date: date | None = None
    status: str
    is_featured: bool
    is_public: bool
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    currency: str
    total_units: int | None = None
    available_units: int | None = None
    project_area: Decimal | None = None
    project_area_unit: str | None = None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ProjectFilter(BaseModel):
    """Filter parameters for listing projects."""

    city: str | None = None
    status: str | None = None
    project_type_id: UUID | None = None
    is_featured: bool | None = None
    query: str | None = Field(default=None, description="Search by name, developer, locality")


class ProjectCreate(BaseModel):
    """Request payload to create a new project."""

    project_type_id: UUID = Field(..., description="Project type ID")
    location_id: UUID | None = Field(default=None, description="Structured location ID")
    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(
        default=None,
        max_length=255,
        description="URL-safe identifier, unique per tenant. Auto-derived from name if omitted.",
    )
    description: str | None = None
    developer_name: str | None = Field(default=None, max_length=255)
    rera_number: str | None = Field(default=None, max_length=100)
    rera_state: str | None = Field(default=None, max_length=100)
    rera_url: str | None = None
    address_line1: str | None = Field(default=None, max_length=500)
    address_line2: str | None = Field(default=None, max_length=500)
    locality: str | None = Field(default=None, max_length=100)
    city: str = Field(..., min_length=1, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    state: str = Field(..., min_length=1, max_length=100)
    country: str = Field(default="India", max_length=100)
    pincode: str | None = Field(default=None, max_length=20)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    launch_date: date | None = None
    possession_date: date | None = None
    completion_date: date | None = None
    status: str = Field(default="pre_launch", description="project_status enum value")
    is_featured: bool = False
    is_public: bool = False
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="INR", max_length=10)
    total_units: int | None = Field(default=None, ge=0)
    available_units: int | None = Field(default=None, ge=0)
    project_area: Decimal | None = Field(default=None, gt=0)
    project_area_unit: str | None = Field(default="acre")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    """Request payload to update an existing project."""

    project_type_id: UUID | None = None
    location_id: UUID | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    developer_name: str | None = Field(default=None, max_length=255)
    rera_number: str | None = Field(default=None, max_length=100)
    rera_state: str | None = Field(default=None, max_length=100)
    rera_url: str | None = None
    address_line1: str | None = Field(default=None, max_length=500)
    address_line2: str | None = Field(default=None, max_length=500)
    locality: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    district: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, min_length=1, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, max_length=20)
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    launch_date: date | None = None
    possession_date: date | None = None
    completion_date: date | None = None
    status: str | None = None
    is_featured: bool | None = None
    is_public: bool | None = None
    price_min: Decimal | None = Field(default=None, ge=0)
    price_max: Decimal | None = Field(default=None, ge=0)
    total_units: int | None = Field(default=None, ge=0)
    available_units: int | None = Field(default=None, ge=0)
    project_area: Decimal | None = Field(default=None, gt=0)
    project_area_unit: str | None = None
    metadata: dict[str, Any] | None = None
