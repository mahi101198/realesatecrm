"""Unit tests for cross-tenant phone->customer/lead resolution used only by
the whatsapp-dashboard call-agent trigger (the one legitimate remaining use
of an unscoped phone lookup, since the caller supplies no tenant)."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.webhooks.whatsapp_dashboard.repository import CallAgentTriggerRepository


@pytest.mark.asyncio
async def test_find_customer_returns_unambiguous_single_match() -> None:
    mock_session = AsyncMock()
    select_res = MagicMock()
    tenant_id = uuid4()
    customer_id = uuid4()
    select_res.mappings.return_value.all.return_value = [
        {"id": customer_id, "tenant_id": tenant_id, "phone": "+919999999999"}
    ]
    mock_session.execute.return_value = select_res
    repo = CallAgentTriggerRepository(mock_session)

    result = await repo.find_customer_by_phone_cross_tenant("+919999999999")

    assert result is not None
    assert result["id"] == customer_id


@pytest.mark.asyncio
async def test_find_customer_returns_none_on_zero_matches() -> None:
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.all.return_value = []
    mock_session.execute.return_value = select_res
    repo = CallAgentTriggerRepository(mock_session)

    assert await repo.find_customer_by_phone_cross_tenant("+919999999999") is None


@pytest.mark.asyncio
async def test_find_customer_returns_none_on_ambiguous_matches() -> None:
    """Two different tenants both having a customer with this phone number
    is treated as unattributable, never guessed."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.all.return_value = [
        {"id": uuid4(), "tenant_id": uuid4()},
        {"id": uuid4(), "tenant_id": uuid4()},
    ]
    mock_session.execute.return_value = select_res
    repo = CallAgentTriggerRepository(mock_session)

    assert await repo.find_customer_by_phone_cross_tenant("+919999999999") is None

# NOTE: the get_or_create_lead tests that used to live here moved to
# tests/unit/test_lead_resolver.py -- lead resolution is no longer this
# repository's job, it is the single shared rule in app/leads/resolver.py.
