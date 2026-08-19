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
    role: str | None = None
    share_percentage: Decimal | None = None
    created_at: datetime


class PropertyResaleListingCreate(BaseModel):
    """Request payload to record that an owner wants to resell."""

    ownership_id: UUID = Field(
        ..., description="The current owner's ownership record (ownership_end_date IS NULL)"
    )
    asking_price: Decimal | None = Field(default=None, ge=0)
    notes: str | None = None


class PropertyResaleListingUpdate(BaseModel):
    """Request payload to withdraw a resale listing. 'converted' is set
    automatically by the sale-creation transaction, never via this endpoint."""

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


class PropertyResaleListingFilter(BaseModel):
    """Filter parameters for listing resale listings."""

    ownership_id: UUID | None = None
    listing_status: str | None = None
