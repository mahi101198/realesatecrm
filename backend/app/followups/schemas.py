"""Follow-up Domain Pydantic Schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FollowUpCreate(BaseModel):
    """Request payload to create a follow-up task."""

    lead_id: UUID = Field(..., description="Target lead ID")
    follow_up_type: str = Field(
        default="ai_call", description="ai_call, human_call, whatsapp, email, sms, in_person"
    )
    scheduled_at: datetime = Field(..., description="Scheduled follow-up timestamp")
    reason: str | None = None
    notes: str | None = None
    assigned_sales_agent_id: UUID | None = None
    related_call_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FollowUpUpdate(BaseModel):
    """Request payload to update a follow-up task."""

    follow_up_type: str | None = None
    scheduled_at: datetime | None = None
    status: str | None = Field(
        default=None, description="pending, in_progress, completed, cancelled, overdue, skipped"
    )
    reason: str | None = None
    notes: str | None = None
    assigned_sales_agent_id: UUID | None = None


class FollowUpCompleteRequest(BaseModel):
    """Request payload to mark a follow-up completed."""

    completion_notes: str | None = Field(default=None, description="Notes on completion outcome")


class FollowUpResponse(BaseModel):
    """Follow-up task API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    lead_id: UUID
    customer_id: UUID
    related_call_id: UUID | None = None
    follow_up_type: str
    scheduled_at: datetime
    status: str
    reason: str | None = None
    notes: str | None = None
    assigned_sales_agent_id: UUID | None = None
    created_by: UUID | None = None
    completed_at: datetime | None = None
    completed_by: UUID | None = None
    completion_notes: str | None = None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    # ── Joined human-readable display fields ────────────────────────────────
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_city: str | None = None
    lead_number: str | None = None
    lead_sales_stage: str | None = None
    lead_status: str | None = None
    lead_purpose: str | None = None
    lead_preferred_city: str | None = None
    lead_budget_min: str | None = None
    lead_budget_max: str | None = None
    assigned_agent_name: str | None = None


class FollowUpFilter(BaseModel):
    """Filter parameters for listing follow-ups."""

    lead_id: UUID | None = None
    customer_id: UUID | None = None
    assigned_sales_agent_id: UUID | None = None
    status: str | None = None
    follow_up_type: str | None = None
    scheduled_before: datetime | None = None
    scheduled_after: datetime | None = None
