"""Appointment Domain Pydantic Schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AppointmentCreate(BaseModel):
    """Payload to schedule an appointment / site visit."""

    customer_id: UUID = Field(..., description="Target customer ID")
    lead_id: UUID | None = Field(default=None, description="Associated lead ID")
    project_id: UUID | None = Field(default=None, description="Associated project ID")
    property_id: UUID | None = Field(default=None, description="Associated property ID")
    sales_agent_id: UUID | None = Field(default=None, description="Assigned sales agent ID")
    scheduled_at: datetime = Field(..., description="Scheduled start timestamp (must be in future)")
    duration_minutes: int = Field(default=60, ge=5, le=480, description="Duration in minutes")
    source: str = Field(
        default="admin", description="ai_agent, sales_agent, admin, website, customer_portal"
    )
    related_call_id: UUID | None = None
    notes: str | None = None


class AppointmentUpdate(BaseModel):
    """Payload to update an existing appointment."""

    sales_agent_id: UUID | None = None
    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    status: str | None = Field(
        default=None, description="pending, confirmed, completed, cancelled, rescheduled, no_show"
    )
    notes: str | None = None
    internal_notes: str | None = None


class AppointmentCancelRequest(BaseModel):
    """Payload to cancel an appointment."""

    reason: str | None = Field(default=None, description="Cancellation reason")


class AppointmentResponse(BaseModel):
    """Appointment API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    customer_id: UUID
    lead_id: UUID | None = None
    project_id: UUID | None = None
    property_id: UUID | None = None
    sales_agent_id: UUID | None = None
    related_call_id: UUID | None = None
    appointment_type: str
    source: str
    scheduled_at: datetime
    duration_minutes: int
    status: str
    notes: str | None = None
    internal_notes: str | None = None
    confirmed_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancellation_reason: str | None = None
    rescheduled_from_id: UUID | None = None
    metadata: dict[str, Any]
    created_by: UUID | None = None
    created_at: datetime
    updated_at: datetime

    # ── Joined human-readable display fields ────────────────────────────────
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    customer_city: str | None = None
    project_name: str | None = None
    project_locality: str | None = None
    project_city: str | None = None
    project_address: str | None = None
    property_code: str | None = None
    unit_number: str | None = None
    property_bedrooms: int | None = None
    property_base_price: str | None = None
    lead_number: str | None = None
    lead_sales_stage: str | None = None
    lead_budget_min: str | None = None
    lead_budget_max: str | None = None
    sales_agent_name: str | None = None
    sales_agent_phone: str | None = None
    created_by_name: str | None = None


class AppointmentFilter(BaseModel):
    """Filter parameters for listing appointments."""

    customer_id: UUID | None = None
    lead_id: UUID | None = None
    sales_agent_id: UUID | None = None
    project_id: UUID | None = None
    status: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
