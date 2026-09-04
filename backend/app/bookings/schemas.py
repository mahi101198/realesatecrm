"""Property Booking Domain Pydantic Schemas."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PropertyBookingCreate(BaseModel):
    """Request payload to create a token/reservation booking on a property."""

    property_id: UUID = Field(..., description="Target property unit ID")
    customer_id: UUID = Field(..., description="Customer making the booking")
    lead_id: UUID | None = Field(
        default=None, description="Lead this booking originated from, if any"
    )
    booking_date: date | None = Field(default=None, description="Defaults to today")
    booking_amount: Decimal = Field(..., ge=0, description="Token/advance amount paid")
    notes: str | None = None


class PropertyBookingUpdate(BaseModel):
    """Request payload to update a booking. Only cancellation is accepted here --
    'converted' is set automatically when a sale referencing this booking closes."""

    booking_status: str | None = Field(
        default=None, description="Only 'cancelled' may be set via this endpoint"
    )
    notes: str | None = None


class PropertyBookingResponse(BaseModel):
    """Property booking API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    property_id: UUID
    customer_id: UUID
    lead_id: UUID | None = None
    booking_date: date
    booking_amount: Decimal
    booking_status: str
    notes: str | None = None
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime

    # ── Joined human-readable display fields ────────────────────────────────
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    customer_city: str | None = None
    property_code: str | None = None
    unit_number: str | None = None
    project_name: str | None = None
    property_bedrooms: int | None = None
    property_base_price: Decimal | None = None
    lead_number: str | None = None
    lead_sales_stage: str | None = None
    created_by_name: str | None = None


class PropertyBookingFilter(BaseModel):
    """Filter parameters for listing property bookings."""

    property_id: UUID | None = None
    customer_id: UUID | None = None
    lead_id: UUID | None = None
    booking_status: str | None = None
