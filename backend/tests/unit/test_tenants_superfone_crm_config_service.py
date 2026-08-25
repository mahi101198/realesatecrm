"""Unit tests for TenantService's Superfone CRM webhook secret admin
methods."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.tenants.schemas import SuperfoneCrmConfigUpsertRequest
from app.tenants.service import TenantService


@pytest.mark.asyncio
async def test_upsert_superfone_crm_config_delegates_to_repository() -> None:
    """Verify the service passes the plaintext secret straight through to
    the repository (which hashes it) and returns a response with no secret
    or hash fields at all."""
    mock_session = AsyncMock()
    tenant_id = uuid4()
    service = TenantService(mock_session)
    service.repository.get_by_id = AsyncMock(return_value={"id": tenant_id})

    with patch("app.tenants.service.SuperfoneCrmTenantConfigRepository") as mock_repo_cls:
        mock_repo_cls.return_value.upsert = AsyncMock(
            return_value={
                "tenant_id": tenant_id,
                "is_active": True,
                "created_at": "2026-08-19T00:00:00Z",
                "updated_at": "2026-08-19T00:00:00Z",
            }
        )
        result = await service.upsert_superfone_crm_config(
            tenant_id,
            SuperfoneCrmConfigUpsertRequest(bearer_secret="a-plaintext-bearer-secret"),
        )

    mock_repo_cls.return_value.upsert.assert_awaited_once_with(
        tenant_id, "a-plaintext-bearer-secret"
    )
    assert not hasattr(result, "bearer_secret")
    assert not hasattr(result, "bearer_secret_hash")
    assert result.tenant_id == tenant_id


@pytest.mark.asyncio
async def test_upsert_superfone_crm_config_raises_not_found_for_unknown_tenant() -> None:
    """Verify the pre-flight tenant-existence check rejects an unknown
    tenant_id before ever touching the config repository."""
    from app.core.exceptions import NotFoundError

    mock_session = AsyncMock()
    service = TenantService(mock_session)
    service.repository.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError):
        await service.upsert_superfone_crm_config(
            uuid4(), SuperfoneCrmConfigUpsertRequest(bearer_secret="a-plaintext-bearer-secret")
        )


@pytest.mark.asyncio
async def test_get_superfone_crm_config_raises_not_found_when_unconfigured() -> None:
    """Verify GET returns a clean 404 for a tenant with no config yet."""
    from app.core.exceptions import NotFoundError

    mock_session = AsyncMock()
    service = TenantService(mock_session)

    with patch("app.tenants.service.SuperfoneCrmTenantConfigRepository") as mock_repo_cls:
        mock_repo_cls.return_value.get_public = AsyncMock(return_value=None)
        with pytest.raises(NotFoundError):
            await service.get_superfone_crm_config(uuid4())
