"""Lead Domain Pydantic Schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LeadCreate(BaseModel):
    """Request payload for creating a lead opportunity."""

    customer_id: UUID = Field(..., description="Target customer ID")
    lead_source_id: UUID | None = None
    campaign_id: UUID | None = None
    property_type_id: UUID | None = None
    purpose: str | None = Field(default=None, description="investment, end_use, rental, etc.")
    preferred_city: str | None = Field(default=None, max_length=100)
    preferred_locality: str | None = Field(default=None, max_length=100)
    budget_min: Decimal | None = Field(default=None, ge=0)
    budget_max: Decimal | None = Field(default=None, ge=0)
    area_min: Decimal | None = Field(default=None, ge=0)
    area_max: Decimal | None = Field(default=None, ge=0)
    area_unit: str = Field(default="sqft", max_length=20)
    bedrooms: int | None = Field(default=None, ge=0)
    timeline: str | None = Field(default=None, max_length=100)
    finance_requirement: str | None = Field(default=None, max_length=50)
    status: str = Field(
        default="new", description="new, active, on_hold, converted, lost, do_not_contact"
    )
    sales_stage: str = Field(
        default="new", description="new, contacted, qualified, site_visit, etc."
    )
    lead_score: int = Field(default=0, ge=0, le=100)
    interest_level: str | None = Field(
        default=None, description="very_high, high, medium, low, very_low"
    )
    assigned_sales_agent_id: UUID | None = None
    notes: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LeadUpdate(BaseModel):
    """Request payload for updating lead details."""

    lead_source_id: UUID | None = None
    campaign_id: UUID | None = None
    property_type_id: UUID | None = None
    purpose: str | None = None
    preferred_city: str | None = Field(default=None, max_length=100)
    preferred_locality: str | None = Field(default=None, max_length=100)
    budget_min: Decimal | None = Field(default=None, ge=0)
    budget_max: Decimal | None = Field(default=None, ge=0)
    area_min: Decimal | None = Field(default=None, ge=0)
    area_max: Decimal | None = Field(default=None, ge=0)
    area_unit: str | None = None
    bedrooms: int | None = Field(default=None, ge=0)
    timeline: str | None = None
    finance_requirement: str | None = None
    status: str | None = None
    sales_stage: str | None = None
    lead_score: int | None = Field(default=None, ge=0, le=100)
    interest_level: str | None = None
    assigned_sales_agent_id: UUID | None = None
    next_follow_up_at: datetime | None = None
    lost_reason: str | None = None
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class LeadAssign(BaseModel):
    """Request payload for assigning a sales agent to a lead."""

    sales_agent_id: UUID = Field(..., description="Target sales agent ID")
    reason: str | None = Field(default=None, description="Reason for assignment")


class LeadResponse(BaseModel):
    """Lead API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    customer_id: UUID
    lead_number: str
    lead_source_id: UUID | None = None
    campaign_id: UUID | None = None
    property_type_id: UUID | None = None
    purpose: str | None = None
    preferred_city: str | None = None
    preferred_locality: str | None = None
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    area_min: Decimal | None = None
    area_max: Decimal | None = None
    area_unit: str
    bedrooms: int | None = None
    timeline: str | None = None
    finance_requirement: str | None = None
    status: str
    sales_stage: str
    lead_score: int
    interest_level: str | None = None
    assigned_sales_agent_id: UUID | None = None
    first_contacted_at: datetime | None = None
    last_contacted_at: datetime | None = None
    next_follow_up_at: datetime | None = None
    converted_at: datetime | None = None
    lost_at: datetime | None = None
    lost_reason: str | None = None
    notes: str | None = None
    metadata: dict[str, Any]
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime


class LeadFilter(BaseModel):
    """Filter parameters for listing leads."""

    customer_id: UUID | None = None
    status: str | None = None
    sales_stage: str | None = None
    assigned_sales_agent_id: UUID | None = None
    campaign_id: UUID | None = None
    property_type_id: UUID | None = None
    min_budget: Decimal | None = None
    max_budget: Decimal | None = None
    query: str | None = Field(
        default=None, description="Search lead_number, preferred_city, preferred_locality"
    )


class LeadPropertyInterestCreate(BaseModel):
    """Payload to add property interest to a lead."""

    project_id: UUID = Field(..., description="Target project ID")
    property_id: UUID | None = Field(default=None, description="Specific property unit ID")
    interest_level: str = Field(
        default="medium", description="very_high, high, medium, low, very_low"
    )
    is_primary: bool = False
    notes: str | None = None


class LeadPropertyInterestResponse(BaseModel):
    """Response representation for lead property interest."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    lead_id: UUID
    project_id: UUID
    property_id: UUID | None = None
    interest_level: str
    is_primary: bool
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class LeadNoteCreate(BaseModel):
    """Payload to create a note on a lead."""

    note_text: str = Field(..., min_length=1, description="Note text content")
    is_pinned: bool = False


class LeadNoteResponse(BaseModel):
    """Response representation for a lead note."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    lead_id: UUID
    customer_id: UUID
    author_id: UUID
    note_text: str
    is_pinned: bool
    created_at: datetime
    updated_at: datetime
