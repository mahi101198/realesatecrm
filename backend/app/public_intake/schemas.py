"""Public Lead Intake Pydantic Schemas.

Deliberately minimal -- this is the anonymous website/campaign entry point,
not the staff-facing customer/lead schema. No tenant_id, no internal IDs
beyond what's needed, no staff-only fields (assigned_sales_agent_id, scoring,
etc.).
"""

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.customers.schemas import normalize_phone


class PublicLeadIntakeRequest(BaseModel):
    """Request payload for an anonymous website/campaign enquiry."""

    name: str = Field(..., min_length=1, max_length=255, description="Enquirer's full name")
    phone: str = Field(..., min_length=7, max_length=20, description="Contact phone number")
    email: EmailStr | None = Field(default=None)
    source_code: str | None = Field(
        default=None,
        max_length=50,
        description='Origin code, e.g. "website", "facebook", "google" -- maps to lead_sources.',
    )
    message: str | None = Field(default=None, max_length=2000, description="Free-text enquiry")
    property_id: str | None = Field(
        default=None,
        description=(
            "Property the enquirer is interested in, if known. Typed as a loose "
            "string (not UUID) deliberately: a malformed value from a flaky "
            "website widget should not 422-reject the whole enquiry -- it is "
            "parsed defensively and silently ignored if invalid."
        ),
    )

    @field_validator("phone", mode="before")
    @classmethod
    def _normalize_phone(cls, v: str) -> str:
        return normalize_phone(v)


class PublicLeadIntakeResponse(BaseModel):
    """Minimal acknowledgement response -- no internal customer/lead UUIDs or
    tenant-internal state exposed to anonymous callers."""

    status: str = "received"
    reference: str = Field(..., description="Human-readable lead reference, e.g. LD-000123")
