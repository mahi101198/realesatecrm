"""Property Ownership, Co-Owner & Resale Listing Domain Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PropertyOwnershipResponse(BaseModel):
    """Ownership ledger row API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    customer_id: UUID
    sale_id: UUID | None = None
    purchase_purpose: str | None = None
    previous_ownership_id: UUID | None = None
    ownership_start_date: date
    ownership_end_date: date | None = None
    ownership_status: str
    created_by: UUID | None = None
    verified_by: UUID | None = None
    created_at: datetime
    updated_at: datetime

    # ── Joined human-readable display fields ────────────────────────────────
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    customer_city: str | None = None
    customer_address: str | None = None
    property_code: str | None = None
    unit_number: str | None = None
    property_block: str | None = None
    property_floor_number: int | None = None
    property_bedrooms: int | None = None
    property_bathrooms: int | None = None
    property_balconies: int | None = None
    property_carpet_area: Decimal | None = None
    property_built_up_area: Decimal | None = None
    property_super_built_up_area: Decimal | None = None
    property_facing: str | None = None
    property_is_corner: bool | None = None
    project_name: str | None = None
    project_locality: str | None = None
    project_city: str | None = None
    project_state: str | None = None
    project_developer_name: str | None = None
    project_rera_number: str | None = None
    sale_amount: Decimal | None = None
    sale_date: date | None = None
    sale_discount_amount: Decimal | None = None
    sale_tax_amount: Decimal | None = None
    sale_status: str | None = None
    verified_by_name: str | None = None
    resale_listing_id: UUID | None = None
    resale_asking_price: Decimal | None = None
    resale_listing_status: str | None = None
    resale_notes: str | None = None
    resale_listed_at: datetime | None = None


class PropertyOwnershipFilter(BaseModel):
    """Filter parameters for listing ownership records."""

    property_id: UUID | None = None
    customer_id: UUID | None = None
    ownership_status: str | None = None


class PropertyOwnershipUpdate(BaseModel):
    """Request payload to verify or reverse an ownership record.

    Never accepts customer_id / property_id -- who owns what only ever
    changes via a new property sale, not by editing an existing row.
    """

    verified_by: UUID | None = Field(default=None, description="Staff user verifying this record")
    ownership_status: str | None = Field(
        default=None, description="Only 'reversed' may be set via this endpoint"
    )


class PropertyOwnershipCoOwnerCreate(BaseModel):
    """Request payload to add a co-owner to an ownership record."""

    customer_id: UUID = Field(..., description="The co-owner")
    role: str | None = Field(default=None, max_length=100, description="e.g. spouse/partner/family")
    share_percentage: Decimal | None = Field(default=None, ge=0, le=100)


class PropertyOwnershipCoOwnerResponse(BaseModel):
    """Co-owner API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    ownership_id: UUID
    customer_id: UUID
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    role: str | None = None
    share_percentage: Decimal | None = None
    created_at: datetime


class PropertyResaleListingCreate(BaseModel):
    """Request payload to list a property for resale."""

    ownership_id: UUID = Field(..., description="The active ownership row being resold")
    asking_price: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class PropertyResaleListingUpdate(BaseModel):
    """Request payload to update or withdraw a resale listing."""

    listing_status: str | None = Field(
        default=None, description="Only 'withdrawn' may be set via this endpoint"
    )
    asking_price: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class PropertyResaleListingResponse(BaseModel):
    """Resale listing API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    ownership_id: UUID
    listed_at: datetime
    asking_price: Decimal | None = None
    listing_status: str
    notes: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime

    # ── Joined human-readable display fields ────────────────────────────────
    owner_name: str | None = None
    owner_phone: str | None = None
    owner_email: str | None = None
    property_code: str | None = None
    unit_number: str | None = None
    property_bedrooms: int | None = None
    property_built_up_area: Decimal | None = None
    project_name: str | None = None
    project_locality: str | None = None
    project_city: str | None = None


class PropertyResaleListingFilter(BaseModel):
    """Filter parameters for listing resale listings."""

    ownership_id: UUID | None = None
    listing_status: str | None = None


class CustomerOwnershipHistoryResponse(BaseModel):
    """Enriched ownership ledger row for the customer 360° view.

    Returned by GET /customers/{customer_id}/ownership-history.
    Includes denormalised property and sale fields so the frontend
    never needs additional round-trips.
    """

    model_config = ConfigDict(from_attributes=True)

    # ── Ownership core ─────────────────────────────────────────────
    id: UUID
    property_id: UUID
    sale_id: UUID | None = None
    purchase_purpose: str | None = None
    previous_ownership_id: UUID | None = None
    ownership_start_date: date
    ownership_end_date: date | None = None
    ownership_status: str
    verified_by: UUID | None = None
    created_at: datetime

    # ── Denormalised from properties ───────────────────────────────
    property_code: str
    unit_number: str | None = None
    project_name: str | None = None

    # ── Denormalised from property_sales (nullable: manual entries) ─
    sale_date: date | None = None
    sale_amount: Decimal | None = None
    sale_status: str | None = None
