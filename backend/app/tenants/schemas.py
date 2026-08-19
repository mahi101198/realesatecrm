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
