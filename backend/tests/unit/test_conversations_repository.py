"""Unit tests for get_or_create_conversation -- deterministic, idempotent,
race-safe and tenant-scoped."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.conversations.model import ConversationChannel
from app.conversations.repository import (
    ConversationRepository,
    get_or_create_conversation,
)
from app.core.exceptions import ConflictError


@pytest.mark.asyncio
async def test_returns_existing_open_conversation() -> None:
    session = AsyncMock()
    existing_id = uuid4()

    with patch(
        "app.conversations.repository.ConversationRepository", autospec=True
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.get_open_conversation = AsyncMock(
            return_value={"id": existing_id, "lead_id": uuid4(), "external_thread_id": "919..."}
        )
        repo.create_conversation = AsyncMock()

        result = await get_or_create_conversation(
            session, uuid4(), uuid4(), ConversationChannel.WHATSAPP
        )

    assert result["id"] == existing_id
    repo.create_conversation.assert_not_called()


@pytest.mark.asyncio
async def test_backfills_lead_id_onto_existing_thread_only_when_null() -> None:
    """An open thread opened before a lead existed gets the lead attached; a
    thread that already has one is never reassigned."""
    session = AsyncMock()
    conv_id = uuid4()
    lead_id = uuid4()

    with patch(
        "app.conversations.repository.ConversationRepository", autospec=True
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.get_open_conversation = AsyncMock(
            return_value={"id": conv_id, "lead_id": None, "external_thread_id": None}
        )
        repo.backfill_conversation_details = AsyncMock(
            return_value={"id": conv_id, "lead_id": lead_id}
        )

        result = await get_or_create_conversation(
            session,
            uuid4(),
            uuid4(),
            ConversationChannel.WHATSAPP,
            external_thread_id="919999999999",
            lead_id=lead_id,
        )

    assert result["lead_id"] == lead_id
    repo.backfill_conversation_details.assert_awaited_once()


@pytest.mark.asyncio
async def test_creates_conversation_when_none_open() -> None:
    session = AsyncMock()
    new_id = uuid4()

    with patch(
        "app.conversations.repository.ConversationRepository", autospec=True
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.get_open_conversation = AsyncMock(return_value=None)
        repo.create_conversation = AsyncMock(return_value={"id": new_id})

        result = await get_or_create_conversation(
            session, uuid4(), uuid4(), ConversationChannel.VOICE
        )

    assert result["id"] == new_id


@pytest.mark.asyncio
async def test_survives_concurrent_open_race() -> None:
    """Two inbound messages arriving at once must not open two threads: the
    loser re-fetches the winner's row."""
    session = AsyncMock()
    winner_id = uuid4()

    with patch(
        "app.conversations.repository.ConversationRepository", autospec=True
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.get_open_conversation = AsyncMock(side_effect=[None, {"id": winner_id}])
        repo.create_conversation = AsyncMock(return_value=None)

        result = await get_or_create_conversation(
            session, uuid4(), uuid4(), ConversationChannel.WHATSAPP
        )

    assert result["id"] == winner_id


@pytest.mark.asyncio
async def test_raises_conflict_if_race_unresolvable() -> None:
    session = AsyncMock()

    with patch(
        "app.conversations.repository.ConversationRepository", autospec=True
    ) as repo_cls:
        repo = repo_cls.return_value
        repo.get_open_conversation = AsyncMock(side_effect=[None, None])
        repo.create_conversation = AsyncMock(return_value=None)

        with pytest.raises(ConflictError) as exc_info:
            await get_or_create_conversation(
                session, uuid4(), uuid4(), ConversationChannel.WHATSAPP
            )

    assert exc_info.value.code == "CONVERSATION_CREATE_CONFLICT"


@pytest.mark.asyncio
async def test_lookup_is_tenant_scoped() -> None:
    session = AsyncMock()
    res = MagicMock()
    res.mappings.return_value.one_or_none.return_value = None
    session.execute.return_value = res
    repo = ConversationRepository(session)
    tenant_id = uuid4()
    contact_id = uuid4()

    await repo.get_open_conversation(tenant_id, contact_id, "whatsapp")

    params = session.execute.await_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["contact_id"] == contact_id
    assert "tenant_id = :tenant_id" in str(session.execute.await_args.args[0])


@pytest.mark.asyncio
async def test_insert_names_the_partial_index_predicate() -> None:
    """Without repeating `WHERE status = 'open'` Postgres cannot infer the
    partial unique index as the ON CONFLICT arbiter and raises 42P10 -- the
    same trap migration 016 documents for webhook_events."""
    session = AsyncMock()
    res = MagicMock()
    res.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    session.execute.return_value = res
    repo = ConversationRepository(session)

    await repo.create_conversation(uuid4(), uuid4(), "whatsapp")

    sql = str(session.execute.await_args.args[0])
    assert "ON CONFLICT (tenant_id, contact_id, channel)" in sql
    assert "WHERE status = 'open'::public.conversation_status" in sql
