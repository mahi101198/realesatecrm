"""Unit tests for WhatsAppRepository's tenant-scoped customer lookup/
auto-create, used by the inbound webhook handler for first-contact
numbers."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.whatsapp.repository import WhatsAppRepository


@pytest.mark.asyncio
async def test_find_customer_by_phone_is_tenant_scoped() -> None:
    """Verify the lookup filters by tenant_id, unlike the removed
    cross-tenant method."""
    mock_session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    mock_session.execute.return_value = select_res
    repo = WhatsAppRepository(mock_session)
    tenant_id = uuid4()

    result = await repo.find_customer_by_phone(tenant_id, "+919999999999")

    params = mock_session.execute.call_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["phone"] == "+919999999999"
    assert result is not None


@pytest.mark.asyncio
async def test_create_minimal_customer_inserts_required_fields_only() -> None:
    """Verify auto-create for a first-contact WhatsApp sender only sets
    tenant_id/phone/full_name, letting every other column take its schema
    default."""
    mock_session = AsyncMock()
    insert_res = MagicMock()
    new_id = uuid4()
    insert_res.mappings.return_value.one.return_value = {"id": new_id, "full_name": "Jane"}
    mock_session.execute.return_value = insert_res
    repo = WhatsAppRepository(mock_session)
    tenant_id = uuid4()

    result = await repo.create_minimal_customer(tenant_id, "+919999999999", "Jane")

    params = mock_session.execute.call_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["phone"] == "+919999999999"
    assert params["full_name"] == "Jane"
    assert result["id"] == new_id
