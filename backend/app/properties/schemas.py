"""Property Domain Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PropertyResponse(BaseModel):
    """Property inventory unit representation for API response."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    project_id: UUID
    property_type_id: UUID
    property_code: str
    unit_number: str | None = None
    block: str | None = None
    floor_number: int | None = None
    plot_area: Decimal | None = None
    built_up_area: Decimal | None = None
    carpet_area: Decimal | None = None
    super_built_up_area: Decimal | None = None
    area_unit: str
    bedrooms: int | None = None
    bathrooms: int | None = None
    balconies: int | None = None
    parking_covered: int
    parking_open: int
    facing: str | None = None
    is_corner: bool
    is_park_facing: bool
    is_road_facing: bool
    base_price: Decimal | None = None
    offer_price: Decimal | None = None
    price_per_unit: Decimal | None = None
    currency: str
    status: str
    is_public: bool
    is_featured: bool
    custom_attributes: dict[str, Any]
    construction_status: str | None = None
    created_at: datetime
    updated_at: datetime


class PropertyCreate(BaseModel):
    """Request payload to create a new property inventory unit."""

    project_id: UUID = Field(..., description="Parent project ID")
    property_type_id: UUID = Field(..., description="Property type ID")
    property_code: str = Field(..., min_length=1, max_length=100, description='e.g. "P-101"')
    unit_number: str | None = Field(default=None, max_length=50)
    block: str | None = Field(default=None, max_length=50)
    floor_number: int | None = None
    plot_area: Decimal | None = Field(default=None, gt=0)
    built_up_area: Decimal | None = Field(default=None, gt=0)
    carpet_area: Decimal | None = Field(default=None, gt=0)
    super_built_up_area: Decimal | None = Field(default=None, gt=0)
    area_unit: str = Field(default="sqft")
    bedrooms: int | None = Field(default=None, ge=0)
    bathrooms: int | None = Field(default=None, ge=0)
    balconies: int | None = Field(default=None, ge=0)
    parking_covered: int = Field(default=0, ge=0)
    parking_open: int = Field(default=0, ge=0)
    facing: str | None = None
    is_corner: bool = False
    is_park_facing: bool = False
    is_road_facing: bool = False
    base_price: Decimal | None = Field(default=None, ge=0)
    offer_price: Decimal | None = Field(default=None, ge=0)
    price_per_unit: Decimal | None = None
    currency: str = Field(default="INR", max_length=10)
    status: str = Field(default="draft", description="property_status enum value")
    is_public: bool = False
    is_featured: bool = False
    custom_attributes: dict[str, Any] = Field(default_factory=dict)
    construction_status: str | None = Field(
        default=None, description="not_applicable/not_started/in_progress/on_hold/completed"
    )


class PropertyUpdate(BaseModel):
    """Request payload to update an existing property inventory unit."""

    property_type_id: UUID | None = None
    unit_number: str | None = Field(default=None, max_length=50)
    block: str | None = Field(default=None, max_length=50)
    floor_number: int | None = None
    plot_area: Decimal | None = Field(default=None, gt=0)
    built_up_area: Decimal | None = Field(default=None, gt=0)
    carpet_area: Decimal | None = Field(default=None, gt=0)
    super_built_up_area: Decimal | None = Field(default=None, gt=0)
    area_unit: str | None = None
    bedrooms: int | None = Field(default=None, ge=0)
    bathrooms: int | None = Field(default=None, ge=0)
    balconies: int | None = Field(default=None, ge=0)
    parking_covered: int | None = Field(default=None, ge=0)
    parking_open: int | None = Field(default=None, ge=0)
    facing: str | None = None
    is_corner: bool | None = None
    is_park_facing: bool | None = None
    is_road_facing: bool | None = None
    base_price: Decimal | None = Field(default=None, ge=0)
    offer_price: Decimal | None = Field(default=None, ge=0)
    price_per_unit: Decimal | None = None
    is_public: bool | None = None
    is_featured: bool | None = None
    custom_attributes: dict[str, Any] | None = None
    construction_status: str | None = None


class PropertyDetailLocationContext(BaseModel):
    """Structured location summary embedded in the property detail view."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    city: str


class PropertyDetailProjectContext(BaseModel):
    """Project (and, if set, structured location) summary for the property detail view."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    city: str
    state: str
    location: PropertyDetailLocationContext | None = None


class PropertyDetailMilestone(BaseModel):
    """Construction milestone summary for the property detail view."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    milestone: str
    status: str
    target_date: date | None = None
    actual_completion_date: date | None = None


KNOWN_CONSTRUCTION_MILESTONES = frozenset(
    {
        "foundation",
        "structure",
        "brickwork_and_plastering",
        "electrical_and_plumbing",
        "finishing",
        "handover",
    }
)


class ConstructionMilestoneCreate(BaseModel):
    """Request payload to register a construction milestone for a property.

    One row per (property_id, milestone) -- see
    uq_property_construction_milestones_property_stage. Creating and
    progressing a milestone are kept as separate actions (POST then PATCH),
    not an upsert: registering a planned milestone (with a target_date) is a
    different action from later progressing its status, and collapsing them
    would make a PATCH silently create data as a surprising side effect.
    """

    milestone: str = Field(
        ...,
        description=(
            "foundation/structure/brickwork_and_plastering/electrical_and_plumbing/"
            "finishing/handover"
        ),
    )
    target_date: date | None = None
    notes: str | None = None


class ConstructionMilestoneUpdate(BaseModel):
    """Request payload to progress an existing construction milestone."""

    status: str | None = Field(default=None, description="pending/in_progress/completed")
    target_date: date | None = None
    actual_completion_date: date | None = None
    verified_by: UUID | None = None
    notes: str | None = None


class ConstructionMilestoneResponse(BaseModel):
    """Construction milestone API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    milestone: str
    status: str
    target_date: date | None = None
    actual_completion_date: date | None = None
    verified_by: UUID | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class PropertyDetailCoOwner(BaseModel):
    """Co-owner summary nested under an ownership period."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: UUID
    role: str | None = None
    share_percentage: Decimal | None = None


class PropertyDetailOwnershipPeriod(BaseModel):
    """One ownership period (primary owner + co-owners) for the property detail view."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_id: UUID
    purchase_purpose: str | None = None
    previous_ownership_id: UUID | None = None
    ownership_start_date: date
    ownership_end_date: date | None = None
    ownership_status: str
    co_owners: list[PropertyDetailCoOwner] = Field(default_factory=list)


class PropertyDetailResaleListing(BaseModel):
    """Open resale listing summary for the property detail view."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    listing_status: str
    asking_price: Decimal | None = None
    listed_at: datetime


class PropertyDetailPrice(BaseModel):
    """Current price row summary for the property detail view."""

    model_config = ConfigDict(from_attributes=True)

    price_type: str
    amount: Decimal
    currency: str
    effective_from: date


class PropertyDetailResponse(BaseModel):
    """Aggregated property detail view: base fields, project/location context,
    construction status, full ownership chain (with co-owners), any open resale
    listing, and current prices -- built from a small number of targeted
    queries rather than an app-layer N+1 loop."""

    property: PropertyResponse
    project: PropertyDetailProjectContext | None = None
    construction_milestones: list[PropertyDetailMilestone] = Field(default_factory=list)
    current_owner: PropertyDetailOwnershipPeriod | None = None
    ownership_history: list[PropertyDetailOwnershipPeriod] = Field(default_factory=list)
    open_resale_listing: PropertyDetailResaleListing | None = None
    current_prices: list[PropertyDetailPrice] = Field(default_factory=list)


class PropertySearchFilter(BaseModel):
    """Structured filter parameters for property inventory database search."""

    project_id: UUID | None = None
    property_type_id: UUID | None = None
    status: str | None = Field(
        default=None, description="draft, available, reserved, hold, sold, etc."
    )
    min_budget: Decimal | None = Field(default=None, ge=0)
    max_budget: Decimal | None = Field(default=None, ge=0)
    min_area: Decimal | None = Field(default=None, ge=0)
    max_area: Decimal | None = Field(default=None, ge=0)
    bedrooms: int | None = Field(default=None, ge=0)
    facing: str | None = None
    is_corner: bool | None = None
    query: str | None = Field(
        default=None, description="Search property_code, unit_number, or block"
    )


class PropertyReserveRequest(BaseModel):
    """Request payload to reserve or hold a property unit."""

    lead_id: UUID | None = Field(default=None, description="Associated lead ID")
    call_id: UUID | None = Field(default=None, description="Associated voice call ID")
    new_status: str = Field(..., description="Target status: 'hold' or 'reserved'")
    reason: str | None = Field(default=None, description="Reservation reason")
