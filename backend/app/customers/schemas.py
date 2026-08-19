"""Customer Domain Pydantic Schemas."""

import re
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


def normalize_phone(phone: str) -> str:
    """Normalize phone number format (e.g. +919876543210 or 9876543210 -> +919876543210)."""
    cleaned = re.sub(r"[\s\-\(\)]", "", phone.strip())
    if not cleaned:
        raise ValueError("Phone number cannot be empty.")
    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if not digits.isdigit():
            raise ValueError("Invalid phone number format.")
        return f"+{digits}"
    if not cleaned.isdigit():
        raise ValueError("Invalid phone number format.")
    if len(cleaned) == 10:
        return f"+91{cleaned}"
    if len(cleaned) == 12 and cleaned.startswith("91"):
        return f"+{cleaned}"
    return f"+{cleaned}"


class CustomerCreate(BaseModel):
    """Request payload for creating a customer."""

    full_name: str = Field(..., min_length=1, max_length=255, description="Full name of customer")
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str = Field(..., min_length=7, max_length=20, description="Primary contact phone number")
    alternate_phone: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = Field(default=None, description="Customer email address")
    gender: str | None = Field(default=None, max_length=20)
    date_of_birth: date | None = None
    address_line1: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str = Field(default="India", max_length=100)
    pincode: str | None = Field(default=None, max_length=20)
    preferred_language: str = Field(default="hi", max_length=10)
    preferred_contact_time: str | None = Field(default=None, max_length=100)
    do_not_call: bool = False
    do_not_whatsapp: bool = False
    do_not_email: bool = False
    whatsapp_opted_in: bool = False
    lead_source_id: UUID | None = None
    referred_by: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("phone", "alternate_phone", mode="before")
    @classmethod
    def validate_and_normalize_phone(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            return normalize_phone(v)
        return v


class CustomerUpdate(BaseModel):
    """Request payload for updating customer details."""

    full_name: str | None = Field(default=None, min_length=1, max_length=255)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, min_length=7, max_length=20)
    alternate_phone: str | None = Field(default=None, max_length=20)
    email: EmailStr | None = None
    gender: str | None = Field(default=None, max_length=20)
    date_of_birth: date | None = None
    address_line1: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, max_length=20)
    preferred_language: str | None = Field(default=None, max_length=10)
    preferred_contact_time: str | None = Field(default=None, max_length=100)
    do_not_call: bool | None = None
    do_not_whatsapp: bool | None = None
    do_not_email: bool | None = None
    whatsapp_opted_in: bool | None = None
    lead_source_id: UUID | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("phone", "alternate_phone", mode="before")
    @classmethod
    def validate_and_normalize_phone(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            return normalize_phone(v)
        return v


class CustomerResponse(BaseModel):
    """Customer API response representation."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    full_name: str
    first_name: str | None = None
    last_name: str | None = None
    phone: str
    alternate_phone: str | None = None
    email: str | None = None
    gender: str | None = None
    date_of_birth: date | None = None
    address_line1: str | None = None
    city: str | None = None
    state: str | None = None
    country: str
    pincode: str | None = None
    preferred_language: str
    preferred_contact_time: str | None = None
    do_not_call: bool
    do_not_whatsapp: bool
    do_not_email: bool
    whatsapp_opted_in: bool
    lead_source_id: UUID | None = None
    referred_by: UUID | None = None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class CustomerFilter(BaseModel):
    """Filter parameters for listing/searching customers."""

    query: str | None = Field(default=None, description="Search query for name, phone, or email")
    phone: str | None = Field(default=None, description="Exact phone match filter")
    email: str | None = Field(default=None, description="Exact email match filter")
    city: str | None = Field(default=None, description="Filter by city")
