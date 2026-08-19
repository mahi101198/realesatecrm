"""Unit tests for TenantService: subscription/plan field protection (the DB
trigger for this, trg_fn_protect_tenant_plan_settings, also explicitly
bypasses service_role, so this app-layer guard is the real enforcement) and
basic get/update behavior."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundError, ValidationError
from app.tenants.schemas import TenantUpdate
from app.tenants.service import TenantService


def _service() -> TenantService:
    return TenantService(AsyncMock())


def test_tenant_update_schema_has_no_subscription_fields() -> None:
    """Primary defense: prove the whitelist itself -- TenantUpdate must not
    declare any subscription/plan/slug field at the Pydantic level."""
    forbidden = {
        "slug",
        "plan",
        "plan_expires_at",
        "max_users",
        "max_properties",
        "max_ai_minutes",
        "is_active",
        "settings",
    }
    assert forbidden.isdisjoint(TenantUpdate.model_fields.keys())


@pytest.mark.asyncio
async def test_get_tenant_raises_not_found() -> None:
    """Verify fetching an unknown tenant 404s."""
    service = _service()
    service.repository.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError) as exc_info:
        await service.get_tenant(uuid4())
    assert exc_info.value.code == "TENANT_NOT_FOUND"


@pytest.mark.asyncio
async def test_update_tenant_raises_not_found_for_unknown_tenant() -> None:
    """Verify updating an unknown tenant 404s before attempting any write."""
    service = _service()
    service.repository.get_by_id = AsyncMock(return_value=None)
    update_mock = AsyncMock()
    service.repository.update = update_mock

    with pytest.raises(NotFoundError):
        await service.update_tenant(uuid4(), TenantUpdate(name="New Name"))
    update_mock.assert_not_called()


@pytest.mark.asyncio
async def test_update_tenant_success_with_safe_fields() -> None:
    """Verify updating ordinary company-profile fields succeeds."""
    service = _service()
    tenant_id = uuid4()
    service.repository.get_by_id = AsyncMock(return_value={"id": tenant_id})
    service.repository.update = AsyncMock(
        return_value={
            "id": tenant_id,
            "name": "New Name",
            "slug": "acme",
            "domain": None,
            "logo_url": None,
            "primary_color": None,
            "phone": None,
            "email": None,
            "address": None,
            "city": None,
            "state": None,
            "country": "India",
            "pincode": None,
            "gstin": None,
            "timezone": "Asia/Kolkata",
            "locale": "en-IN",
            "currency": "INR",
            "plan": "trial",
            "plan_expires_at": None,
            "max_users": 5,
            "max_properties": 500,
            "max_ai_minutes": 1000,
            "is_active": True,
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )

    result = await service.update_tenant(tenant_id, TenantUpdate(name="New Name"))
    assert result.name == "New Name"
    # Plan/limits are unaffected -- returned as-is from the DB, never sent by us.
    assert result.plan == "trial"
    assert result.max_users == 5


@pytest.mark.asyncio
async def test_update_tenant_rejects_forbidden_field_via_runtime_guard() -> None:
    """Verify the service-layer runtime guard rejects a forbidden
    subscription field even if it somehow reached model_dump() -- simulating
    a future careless schema edit that re-adds one of these fields, since
    the DB-level trigger protecting this table bypasses service_role and
    cannot be relied on to catch it."""
    service = _service()
    tenant_id = uuid4()
    service.repository.get_by_id = AsyncMock(return_value={"id": tenant_id})
    update_mock = AsyncMock()
    service.repository.update = update_mock

    malicious_payload = MagicMock(spec=TenantUpdate)
    malicious_payload.model_dump.return_value = {"name": "ok", "max_users": 999999}

    with pytest.raises(ValidationError) as exc_info:
        await service.update_tenant(tenant_id, malicious_payload)
    assert exc_info.value.code == "TENANT_PLAN_FIELD_FORBIDDEN"
    update_mock.assert_not_called()
