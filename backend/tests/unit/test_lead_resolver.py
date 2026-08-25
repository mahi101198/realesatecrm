"""Unit tests for the shared LeadResolver -- the single deterministic
find-or-create rule for a contact's active lead.

The rule under test: reuse the most recently created OPEN lead
(status NOT IN converted/lost/do_not_contact, not soft-deleted); otherwise
open a new one. This is deliberately a placeholder for a future AI-assisted
matcher -- these tests pin the deterministic behaviour that matcher must not
silently change.
"""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.leads.resolver import CLOSED_LEAD_STATUSES, LeadResolver


def _resolver() -> LeadResolver:
    return LeadResolver(AsyncMock())


@pytest.mark.asyncio
async def test_reuses_existing_open_lead() -> None:
    resolver = _resolver()
    tenant_id = uuid4()
    contact_id = uuid4()
    open_lead_id = uuid4()
    resolver._find_open_lead = AsyncMock(return_value={"id": open_lead_id})
    resolver._create_lead = AsyncMock()

    result = await resolver.resolve_lead(tenant_id, contact_id)

    assert result["id"] == open_lead_id
    resolver._create_lead.assert_not_called()


@pytest.mark.asyncio
async def test_creates_lead_when_none_open() -> None:
    resolver = _resolver()
    tenant_id = uuid4()
    contact_id = uuid4()
    new_lead_id = uuid4()
    resolver._find_open_lead = AsyncMock(return_value=None)
    resolver._create_lead = AsyncMock(
        return_value={"id": new_lead_id, "lead_number": "LD-000099"}
    )
    # publish_event() issues its own session.execute(); an unconfigured
    # AsyncMock's attribute chain returns coroutines all the way down, so
    # `.mappings().one_or_none()` needs an explicit synchronous MagicMock.
    event_res = MagicMock()
    event_res.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    resolver.session.execute.return_value = event_res

    result = await resolver.resolve_lead(
        tenant_id, contact_id, context={"source": "whatsapp"}
    )

    assert result["id"] == new_lead_id
    resolver._create_lead.assert_awaited_once()
    # LEAD_CREATED is written on the same session.
    assert resolver.session.execute.await_count >= 1


@pytest.mark.asyncio
async def test_lookup_is_tenant_scoped_and_excludes_closed_leads() -> None:
    """The lookup must filter on tenant_id AND skip converted/lost/
    do_not_contact leads -- reviving a closed lead to hang a new conversation
    off it would corrupt the sales pipeline."""
    session = AsyncMock()
    res = MagicMock()
    res.mappings.return_value.one_or_none.return_value = None
    session.execute.return_value = res
    resolver = LeadResolver(session)
    tenant_id = uuid4()
    contact_id = uuid4()

    await resolver._find_open_lead(tenant_id, contact_id)

    sql = str(session.execute.await_args.args[0])
    params = session.execute.await_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["contact_id"] == contact_id
    assert "tenant_id = :tenant_id" in sql
    assert "deleted_at IS NULL" in sql
    for closed in CLOSED_LEAD_STATUSES:
        assert f"'{closed}'" in sql


@pytest.mark.asyncio
async def test_create_seeds_source_and_keeps_context_in_metadata() -> None:
    """`context` seeds the lead on creation: a known source code becomes a real
    lead_source_id, and everything else is preserved under
    metadata->resolver_context for the future matcher to inspect."""
    session = AsyncMock()
    source_res = MagicMock()
    source_id = uuid4()
    source_res.scalar_one_or_none.return_value = source_id
    insert_res = MagicMock()
    insert_res.mappings.return_value.one.return_value = {"id": uuid4()}
    session.execute.side_effect = [source_res, insert_res]

    resolver = LeadResolver(session)
    tenant_id = uuid4()
    contact_id = uuid4()

    await resolver._create_lead(
        tenant_id,
        contact_id,
        {"source": "whatsapp", "project_interest": "Skyline Towers", "notes": "asked for 3BHK"},
    )

    params = session.execute.await_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["contact_id"] == contact_id
    assert params["lead_source_id"] == source_id
    assert params["notes"] == "asked for 3BHK"
    # jsonb params are bound as pre-serialized JSON strings (asyncpg's jsonb
    # codec requires this) -- parse before asserting.
    metadata = json.loads(params["metadata"])
    assert metadata["resolver_context"]["project_interest"] == "Skyline Towers"


@pytest.mark.asyncio
async def test_create_uses_explicit_lead_source_id_without_lookup() -> None:
    session = AsyncMock()
    insert_res = MagicMock()
    insert_res.mappings.return_value.one.return_value = {"id": uuid4()}
    session.execute.return_value = insert_res
    resolver = LeadResolver(session)
    explicit_source = uuid4()

    await resolver._create_lead(uuid4(), uuid4(), {"lead_source_id": explicit_source})

    # Exactly one statement: the INSERT. No lead_sources lookup.
    assert session.execute.await_count == 1
    assert session.execute.await_args.args[1]["lead_source_id"] == explicit_source
