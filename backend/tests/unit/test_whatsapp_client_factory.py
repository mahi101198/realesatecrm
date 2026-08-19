"""Unit tests for the per-tenant Meta WhatsApp client factory."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundError
from app.integrations.whatsapp.factory import get_client_for_tenant


@pytest.mark.asyncio
async def test_get_client_for_tenant_builds_client_from_config() -> None:
    """Verify the factory loads the tenant's decrypted config and builds a
    MetaWhatsAppClient wired to it."""
    mock_session = AsyncMock()
    tenant_id = uuid4()

    with patch(
        "app.integrations.whatsapp.factory.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={
                "tenant_id": tenant_id,
                "waba_id": "waba-1",
                "phone_number_id": "phone-1",
                "access_token": "plain-token",
                "app_secret": "plain-secret",
                "is_active": True,
            }
        )
        client = await get_client_for_tenant(mock_session, tenant_id)

    assert client._phone_number_id == "phone-1"
    assert client._access_token == "plain-token"
    assert client._waba_id == "waba-1"


@pytest.mark.asyncio
async def test_get_client_for_tenant_raises_when_not_configured() -> None:
    """Verify a tenant with no WhatsApp config raises a clean 404, not an
    AttributeError from a None config."""
    mock_session = AsyncMock()

    with patch(
        "app.integrations.whatsapp.factory.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(return_value=None)
        with pytest.raises(NotFoundError) as exc_info:
            await get_client_for_tenant(mock_session, uuid4())

    assert exc_info.value.code == "WHATSAPP_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_get_client_for_tenant_raises_when_inactive() -> None:
    """Verify an explicitly deactivated config is treated the same as no
    config at all -- is_active is the intended kill switch for rotation."""
    mock_session = AsyncMock()

    with patch(
        "app.integrations.whatsapp.factory.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={
                "tenant_id": uuid4(),
                "waba_id": "waba-1",
                "phone_number_id": "phone-1",
                "access_token": "plain-token",
                "app_secret": "plain-secret",
                "is_active": False,
            }
        )
        with pytest.raises(NotFoundError) as exc_info:
            await get_client_for_tenant(mock_session, uuid4())

    assert exc_info.value.code == "WHATSAPP_NOT_CONFIGURED"
