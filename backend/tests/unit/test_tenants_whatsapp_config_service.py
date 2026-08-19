"""Unit tests for TenantService's WhatsApp credential admin methods."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.tenants.schemas import WhatsAppConfigUpsertRequest
from app.tenants.service import TenantService


@pytest.mark.asyncio
async def test_upsert_whatsapp_config_delegates_to_repository() -> None:
    """Verify the service passes plaintext secrets straight through to the
    repository (which encrypts them) and returns a response with no secret
    fields at all."""
    mock_session = AsyncMock()
    tenant_id = uuid4()
    service = TenantService(mock_session)
    # Stub the pre-flight tenant-existence check (service.repository is a
    # real TenantRepository backed by this bare mock session -- letting its
    # actual get_by_id run would try to call .mappings().one_or_none() on a
    # mocked coroutine and blow up). Same stubbing convention as
    # test_tenant_admin_service.py's tests for update_tenant.
    service.repository.get_by_id = AsyncMock(return_value={"id": tenant_id})

    with patch(
        "app.tenants.service.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.upsert = AsyncMock(
            return_value={
                "tenant_id": tenant_id,
                "waba_id": "waba-1",
                "phone_number_id": "phone-1",
                "is_active": True,
                "created_at": "2026-08-19T00:00:00Z",
                "updated_at": "2026-08-19T00:00:00Z",
            }
        )
        result = await service.upsert_whatsapp_config(
            tenant_id,
            WhatsAppConfigUpsertRequest(
                waba_id="waba-1",
                phone_number_id="phone-1",
                verify_token="verify-1",
                access_token="plain-token",
                app_secret="plain-secret",
            ),
        )

    mock_repo_cls.return_value.upsert.assert_awaited_once_with(
        tenant_id=tenant_id,
        waba_id="waba-1",
        phone_number_id="phone-1",
        verify_token="verify-1",
        access_token_plain="plain-token",
        app_secret_plain="plain-secret",
    )
    assert not hasattr(result, "access_token")
    assert not hasattr(result, "app_secret")
    assert result.waba_id == "waba-1"


@pytest.mark.asyncio
async def test_get_whatsapp_config_raises_not_found_when_unconfigured() -> None:
    """Verify GET returns a clean 404 for a tenant with no config yet."""
    from app.core.exceptions import NotFoundError

    mock_session = AsyncMock()
    service = TenantService(mock_session)

    with patch(
        "app.tenants.service.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_public = AsyncMock(return_value=None)
        with pytest.raises(NotFoundError):
            await service.get_whatsapp_config(uuid4())
