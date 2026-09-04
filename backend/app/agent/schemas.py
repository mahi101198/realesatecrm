"""Agent Domain Pydantic Schemas."""

from datetime import datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentExecutionContext(BaseModel):
    """Internal execution context for authenticated AI Agent requests."""

    request_id: str
    tenant_id: UUID
    agent_id: UUID | str = "ai_voice_agent"
    lead_id: UUID | None = None
    customer_id: UUID | None = None
    call_job_id: UUID | None = None
    call_attempt_id: UUID | None = None
    permissions: frozenset[str] = Field(default_factory=frozenset)


class AgentCustomerSummary(BaseModel):
    """Compact customer representation for AI agent context."""

    customer_id: UUID
    full_name: str
    phone: str
    alternate_phone: str | None = None
    email: str | None = None
    city: str | None = None
    preferred_language: str = "hi"


class AgentLeadRequirementSummary(BaseModel):
    """Structured requirements snapshot."""

    property_type: str | None = None
    preferred_location: str | None = None
    budget_min: Decimal | float | None = None
    budget_max: Decimal | float | None = None
    area_min: Decimal | float | None = None
    area_max: Decimal | float | None = None
    bedrooms: int | None = None
    purpose: str | None = None
    timeline: str | None = None
    financing_requirement: str | None = None


class AgentPropertyInterestSummary(BaseModel):
    """Compact property interest representation."""

    project_id: UUID
    project_name: str | None = None
    property_id: UUID | None = None
    interest_level: str
    is_primary: bool = False


class AgentRelationshipSummary(BaseModel):
    """Historical context of interactions."""

    previous_calls: int = 0
    last_outcome: str | None = None
    last_call_at: datetime | str | None = None
    open_follow_up: bool = False
    open_follow_up_at: datetime | str | None = None


class AgentSalesContextSummary(BaseModel):
    """Conversation intelligence & observations summary."""

    main_objections: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    decision_maker: str | None = None
    competitors_mentioned: list[str] = Field(default_factory=list)


class AgentAssignedAgentSummary(BaseModel):
    """Compact sales agent summary."""

    sales_agent_id: UUID
    name: str | None = None
    phone: str | None = None


class AgentPreCallContext(BaseModel):
    """Pre-call snapshot context provided to AI voice agent at call start."""

    model_config = ConfigDict(from_attributes=True)

    lead_id: UUID
    tenant_id: UUID
    lead_number: str
    status: str
    sales_stage: str
    lead_score: int
    temperature: str = "warm"  # hot, warm, cold

    customer: AgentCustomerSummary
    requirement: AgentLeadRequirementSummary
    property_interests: list[AgentPropertyInterestSummary] = Field(default_factory=list)
    relationship: AgentRelationshipSummary
    sales_context: AgentSalesContextSummary
    recent_notes: list[str] = Field(default_factory=list)
    assigned_sales_agent: AgentAssignedAgentSummary | None = None


class AgentLeadContextResponse(BaseModel):
    """Legacy/compat lead context response."""

    model_config = ConfigDict(from_attributes=True)

    lead_id: UUID
    tenant_id: UUID
    lead_number: str
    status: str
    sales_stage: str
    lead_score: int
    budget_min: Decimal | None = None
    budget_max: Decimal | None = None
    preferred_city: str | None = None
    preferred_locality: str | None = None
    property_type: str | None = None
    customer: AgentCustomerSummary
    property_interests: list[AgentPropertyInterestSummary]
    recent_notes: list[str]
    assigned_sales_agent: AgentAssignedAgentSummary | None = None


# ---------------------------------------------------------------------------
# Tool Inputs & Outputs
# ---------------------------------------------------------------------------


class ToolExecuteRequest(BaseModel):
    """Generic payload for API tool execution."""

    tool_name: str
    lead_id: UUID
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class ToolResponse(BaseModel):
    """Unified response contract for AI tools."""

    success: bool
    data: Any | None = None
    error_code: str | None = None
    message: str | None = None


class RecordObservationInput(BaseModel):
    """Input for recording call observation tool."""

    observation_type: str
    observation_value: str
    confidence: float = 1.0
    source_call_attempt_id: UUID | None = None


class UpdateLeadRequirementsInput(BaseModel):
    """Input for update lead requirements tool."""

    property_type: str | None = None
    preferred_location: str | None = None
    preferred_project_id: UUID | None = None
    budget_min: Decimal | float | None = None
    budget_max: Decimal | float | None = None
    area_min: Decimal | float | None = None
    area_max: Decimal | float | None = None
    purpose: str | None = None
    purchase_timeline: str | None = None
    financing_requirement: str | None = None


class CreateFollowUpInput(BaseModel):
    """Input for create follow-up tool."""

    scheduled_at: datetime
    follow_up_type: str = "ai_call"
    reason: str | None = None
    notes: str | None = None
    preferred_start_time: time | None = None
    preferred_end_time: time | None = None
    idempotency_key: str | None = None


class RescheduleFollowUpInput(BaseModel):
    """Input for reschedule follow-up tool."""

    follow_up_id: UUID
    scheduled_at: datetime
    reason: str | None = None


class CancelFollowUpInput(BaseModel):
    """Input for cancel follow-up tool."""

    follow_up_id: UUID
    cancel_reason: str | None = None


class CallJobCreateInput(BaseModel):
    """Input for creating a call job."""

    lead_id: UUID
    job_type: str = "initial_lead_call"
    priority: int = 5
    scheduled_at: datetime | None = None
    timezone: str = "Asia/Kolkata"


# ---------------------------------------------------------------------------
# Phase 5 Lifecycle API Schemas
# ---------------------------------------------------------------------------


class CallPrepareInput(BaseModel):
    """Input payload for preparing a call job."""

    call_job_id: UUID


class CallCompleteInput(BaseModel):
    """Input payload for recording call completion and outcome."""

    call_job_id: UUID
    call_attempt_id: UUID
    # connected, no_answer, busy, rejected, wrong_number,
    # customer_requested_callback, customer_not_interested,
    # site_visit_scheduled, human_transfer, technical_failure
    outcome: str
    duration_seconds: int | None = None
    call_summary: str | None = None
    provider_call_id: str | None = None
    termination_reason: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None


class CallFilter(BaseModel):
    """Filter parameters for listing calls."""

    lead_id: UUID | None = None
    customer_id: UUID | None = None
    status: str | None = None
    outcome: str | None = None


class CallResponse(BaseModel):
    """Call record API response representation, for dashboard/reporting reads."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    lead_id: UUID | None = None
    customer_id: UUID
    direction: str
    provider: str
    phone_from: str | None = None
    phone_to: str
    status: str
    outcome: str | None = None
    initiated_at: datetime
    answered_at: datetime | None = None
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    recording_url: str | None = None
    call_summary: str | None = None
    created_at: datetime

    # Joined human-readable fields
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_city: str | None = None
    lead_number: str | None = None
    lead_status: str | None = None
    assigned_agent_name: str | None = None


class SalesHandoffFilter(BaseModel):
    """Filter parameters for listing sales handoffs."""

    lead_id: UUID | None = None
    status: str | None = None
    assigned_user_id: UUID | None = None


class SalesHandoffResponse(BaseModel):
    """Sales handoff API response representation, for dashboard/reporting reads."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    lead_id: UUID
    customer_id: UUID
    reason: str | None = None
    priority: int
    status: str
    assigned_user_id: UUID | None = None
    requested_at: datetime
    accepted_at: datetime | None = None
    completed_at: datetime | None = None
    notes: str | None = None
    created_at: datetime

    # Joined human-readable fields
    customer_name: str | None = None
    customer_phone: str | None = None
    customer_email: str | None = None
    customer_city: str | None = None
    lead_number: str | None = None
    lead_status: str | None = None
    lead_budget_min: Decimal | float | None = None
    lead_budget_max: Decimal | float | None = None
    assigned_user_name: str | None = None
    assigned_user_email: str | None = None
    assigned_user_phone: str | None = None
    conversation_summary: str | None = None
