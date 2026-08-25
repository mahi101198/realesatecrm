"""Unit tests for the domain event publisher and the event-type vocabulary."""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.events.model import EventType
from app.events.publisher import publish_event


@pytest.mark.asyncio
async def test_publish_event_inserts_tenant_scoped_row() -> None:
    session = AsyncMock()
    result = MagicMock()
    event_id = uuid4()
    result.mappings.return_value.one_or_none.return_value = {"id": event_id}
    session.execute.return_value = result

    tenant_id = uuid4()
    contact_id = uuid4()
    lead_id = uuid4()
    conversation_id = uuid4()

    returned = await publish_event(
        session,
        tenant_id=tenant_id,
        event_type=EventType.MESSAGE_RECEIVED,
        contact_id=contact_id,
        lead_id=lead_id,
        conversation_id=conversation_id,
        payload={"channel": "whatsapp"},
    )

    assert returned == event_id
    params = session.execute.await_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["event_type"] == "message_received"
    assert params["contact_id"] == contact_id
    assert params["lead_id"] == lead_id
    assert params["conversation_id"] == conversation_id
    # jsonb params are bound as pre-serialized JSON strings (asyncpg's jsonb
    # codec requires this) -- parse before asserting.
    assert json.loads(params["payload"]) == {"channel": "whatsapp"}


@pytest.mark.asyncio
async def test_publish_event_defaults_payload_to_empty_object() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    session.execute.return_value = result

    await publish_event(session, tenant_id=uuid4(), event_type=EventType.CONTACT_CREATED)

    assert json.loads(session.execute.await_args.args[1]["payload"]) == {}


@pytest.mark.asyncio
async def test_publish_event_refuses_to_write_an_untenanted_row() -> None:
    """public.events.tenant_id is NOT NULL and every row belongs to exactly one
    tenant. A missing tenant is a defensive no-op, never a guessed value."""
    session = AsyncMock()

    assert await publish_event(session, tenant_id=None, event_type=EventType.LEAD_CREATED) is None
    session.execute.assert_not_called()


def test_event_type_vocabulary_is_complete() -> None:
    """The orchestrator being built on top of this layer depends on these
    names; pin them so a rename is a deliberate, visible change."""
    assert {e.name for e in EventType} == {
        "CONTACT_CREATED",
        "LEAD_CREATED",
        "LEAD_UPDATED",
        "QUALIFICATION_COMPLETED",
        "MESSAGE_RECEIVED",
        "MESSAGE_SENT",
        "CALL_REQUESTED",
        "CALL_SCHEDULED",
        "CALL_STARTED",
        "CALL_COMPLETED",
        "CALL_FAILED",
        "HUMAN_HANDOFF_REQUESTED",
        "VISIT_REQUESTED",
        "VISIT_SCHEDULED",
        "VISIT_COMPLETED",
        "BOOKING_CREATED",
    }
