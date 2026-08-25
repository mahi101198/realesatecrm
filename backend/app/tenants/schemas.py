"""Tenant Domain Pydantic Schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class TenantResponse(BaseModel):
    """Tenant representation for the admin API. Includes subscription/plan
    fields as read-only info -- TenantUpdate below cannot touch them."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    domain: str | None = None
    logo_url: str | None = None
    primary_color: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str
    pincode: str | None = None
    gstin: str | None = None
    timezone: str
    locale: str
    currency: str
    plan: str
    plan_expires_at: datetime | None = None
    max_users: int
    max_properties: int
    max_ai_minutes: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TenantUpdate(BaseModel):
    """Request payload to update tenant company-profile fields.

    Deliberately excludes: slug (immutable -- public lead intake resolves
    tenants by slug, see app/public_intake/service.py), and every
    subscription/plan field (plan, plan_expires_at, max_users,
    max_properties, max_ai_minutes, is_active) and `settings`.

    This whitelist is NOT defense-in-depth -- it is the only real
    enforcement. public.trg_fn_protect_tenant_plan_settings (migration 019)
    implements the same restriction at the DB level but explicitly bypasses
    service_role (`if not (is_super_admin() or is_service_role())`), and
    this backend always connects as service_role. If the plan/limit fields
    were ever added to this schema, nothing in the database would stop a
    tenant admin from editing their own subscription limits through this API.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    domain: str | None = None
    logo_url: str | None = None
    primary_color: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    pincode: str | None = None
    gstin: str | None = None
    timezone: str | None = None
    locale: str | None = None
    currency: str | None = None


class WhatsAppConfigUpsertRequest(BaseModel):
    """Request payload to create/rotate a tenant's Meta WhatsApp
    credentials. All fields are plaintext here -- the only time these
    secrets are ever transmitted as plaintext, over HTTPS, by an
    already-authenticated super-admin. Encrypted at rest by
    WhatsAppTenantConfigRepository.upsert."""

    waba_id: str = Field(..., min_length=1)
    phone_number_id: str = Field(..., min_length=1)
    verify_token: str = Field(..., min_length=1)
    access_token: str = Field(..., min_length=1)
    app_secret: str = Field(..., min_length=1)


class WhatsAppConfigResponse(BaseModel):
    """Tenant WhatsApp config metadata -- deliberately excludes
    verify_token, access_token, and app_secret. Never add them here."""

    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    waba_id: str
    phone_number_id: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SuperfoneCrmConfigUpsertRequest(BaseModel):
    """Request payload to create/rotate a tenant's Superfone CRM webhook
    bearer secret. The caller generates the secret value itself (same as
    rotating any bearer credential) -- this backend only ever hashes and
    stores it, never generates or echoes it back."""

    bearer_secret: str = Field(..., min_length=16)


class SuperfoneCrmConfigResponse(BaseModel):
    """Tenant Superfone CRM webhook config metadata -- deliberately excludes
    the bearer secret (and even its hash). Never add them here."""

    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    is_active: bool
    created_at: datetime
    updated_at: datetime
