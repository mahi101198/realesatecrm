"""Unit tests for SuperfoneCrmTenantConfigRepository (hash-on-write,
never-store-plaintext, hash-only-read for the internal verification path)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.webhooks.superfone.repository import SuperfoneCrmTenantConfigRepository


@pytest.mark.asyncio
async def test_upsert_hashes_secret_before_insert() -> None:
    """Verify the raw SQL params passed to session.execute carry the hash,
    never the plaintext bearer_secret argument."""
    mock_session = AsyncMock()
    upsert_res = MagicMock()
    tenant_id = uuid4()
    upsert_res.mappings.return_value.one.return_value = {
        "tenant_id": tenant_id,
        "is_active": True,
    }
    mock_session.execute.return_value = upsert_res
    repo = SuperfoneCrmTenantConfigRepository(mock_session)

    with patch(
        "app.webhooks.superfone.repository.hash_secret",
        side_effect=lambda v: f"hashed:{v}",
    ) as mock_hash:
        await repo.upsert(tenant_id, "plain-bearer-secret")

    mock_hash.assert_called_once_with("plain-bearer-secret")
    params = mock_session.execute.call_args.args[1]
    assert params["bearer_secret_hash"] == "hashed:plain-bearer-secret"
    assert "plain-bearer-secret" not in params.values()


@pytest.mark.asyncio
async def test_get_public_never_returns_the_hash() -> None:
    """Verify get_public's SELECT does not fetch bearer_secret_hash at all --
    not just that the response omits it."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = {
        "tenant_id": uuid4(),
        "is_active": True,
    }
    mock_session.execute.return_value = select_res
    repo = SuperfoneCrmTenantConfigRepository(mock_session)

    result = await repo.get_public(uuid4())

    query_text = str(mock_session.execute.call_args.args[0])
    assert "bearer_secret_hash" not in query_text
    assert result is not None
    assert "is_active" in result


@pytest.mark.asyncio
async def test_get_secret_hash_returns_hash_for_active_config() -> None:
    """Verify get_secret_hash returns the stored hash for an active config."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = {
        "bearer_secret_hash": "stored-hash"
    }
    mock_session.execute.return_value = select_res
    repo = SuperfoneCrmTenantConfigRepository(mock_session)

    result = await repo.get_secret_hash(uuid4())

    assert result == "stored-hash"


@pytest.mark.asyncio
async def test_get_secret_hash_returns_none_when_no_config() -> None:
    """Verify a tenant with no config row (or an inactive one, filtered by
    the WHERE clause) returns None, not an error -- the security check
    turns this into the same rejection as a wrong token."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = None
    mock_session.execute.return_value = select_res
    repo = SuperfoneCrmTenantConfigRepository(mock_session)

    result = await repo.get_secret_hash(uuid4())

    assert result is None
