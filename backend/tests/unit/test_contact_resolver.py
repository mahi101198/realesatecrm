"""Unit tests for the shared ContactResolver -- the single find-or-create path
for contacts (public.customers).

Covers the behaviour that previously lived in
PublicIntakeService._find_or_create_customer and
WhatsAppRepository.find_customer_by_phone/create_minimal_customer, plus the
tenant-scoping invariant that must hold on every query.
"""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import ConflictError, ValidationError
from app.customers.resolver import ContactResolver


def _resolver() -> ContactResolver:
    return ContactResolver(AsyncMock())


@pytest.mark.asyncio
async def test_returns_existing_contact_without_creating() -> None:
    """A returning phone number reuses the existing row -- not a 409, and no
    INSERT attempted (this is the contract public intake and inbound WhatsApp
    both depend on)."""
    resolver = _resolver()
    tenant_id = uuid4()
    existing_id = uuid4()
    resolver._find = AsyncMock(return_value={"id": existing_id, "phone": "+919876543210"})
    resolver._create = AsyncMock()

    result = await resolver.resolve_contact(tenant_id, phone="9876543210")

    assert result["id"] == existing_id
    resolver._create.assert_not_called()


@pytest.mark.asyncio
async def test_creates_contact_when_phone_unseen() -> None:
    """A first-time phone number creates the contact and emits CONTACT_CREATED."""
    resolver = _resolver()
    tenant_id = uuid4()
    new_id = uuid4()
    resolver._find = AsyncMock(return_value=None)
    resolver._create = AsyncMock(return_value={"id": new_id})
    # publish_event() issues its own session.execute(); an unconfigured
    # AsyncMock's attribute chain returns coroutines all the way down, so
    # `.mappings().one_or_none()` needs an explicit synchronous MagicMock.
    event_res = MagicMock()
    event_res.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    resolver.session.execute.return_value = event_res

    result = await resolver.resolve_contact(
        tenant_id, phone="9123456780", defaults={"full_name": "New Enquirer"}
    )

    assert result["id"] == new_id
    resolver._create.assert_awaited_once()
    # The CONTACT_CREATED insert goes through the same session.
    assert resolver.session.execute.await_count >= 1


@pytest.mark.asyncio
async def test_normalizes_phone_before_lookup() -> None:
    """A bare 10-digit Indian number and its +91 form must resolve to the same
    row -- otherwise WhatsApp and the web form would create two contacts for
    one person."""
    resolver = _resolver()
    tenant_id = uuid4()
    resolver._find = AsyncMock(return_value={"id": uuid4()})

    await resolver.resolve_contact(tenant_id, phone="98765 43210")

    assert resolver._find.call_args.args[1] == "+919876543210"


@pytest.mark.asyncio
async def test_survives_concurrent_create_race() -> None:
    """Losing the ON CONFLICT DO NOTHING race is not a failure: the winner's
    row is re-fetched and returned, so the caller still completes normally."""
    resolver = _resolver()
    tenant_id = uuid4()
    winner_id = uuid4()
    resolver._find = AsyncMock(side_effect=[None, {"id": winner_id}])
    resolver._create = AsyncMock(return_value=None)

    result = await resolver.resolve_contact(tenant_id, phone="9123456790")

    assert result["id"] == winner_id
    assert resolver._find.await_count == 2


@pytest.mark.asyncio
async def test_raises_conflict_if_race_unresolvable() -> None:
    """Lost the race AND the re-fetch found nothing (e.g. the winning row was
    soft-deleted in between) -> a clean typed ConflictError, never a None
    leaking downstream."""
    resolver = _resolver()
    resolver._find = AsyncMock(side_effect=[None, None])
    resolver._create = AsyncMock(return_value=None)

    with pytest.raises(ConflictError) as exc_info:
        await resolver.resolve_contact(uuid4(), phone="9123456791")
    assert exc_info.value.code == "CUSTOMER_CREATE_CONFLICT"


@pytest.mark.asyncio
async def test_rejects_call_with_no_identifier() -> None:
    """No phone, no email, no external_id -> a typed validation error rather
    than a full-table scan or an untenanted create."""
    with pytest.raises(ValidationError) as exc_info:
        await _resolver().resolve_contact(uuid4())
    assert exc_info.value.code == "CONTACT_IDENTIFIER_REQUIRED"


@pytest.mark.asyncio
async def test_rejects_malformed_phone_with_typed_error() -> None:
    """A malformed number surfaces as ValidationError, not a raw ValueError
    escaping into a webhook handler."""
    with pytest.raises(ValidationError) as exc_info:
        await _resolver().resolve_contact(uuid4(), phone="not-a-number")
    assert exc_info.value.code == "INVALID_PHONE_NUMBER"


@pytest.mark.asyncio
async def test_email_only_lookup_cannot_create() -> None:
    """customers.phone is NOT NULL: an email-only caller may find a contact but
    must never conjure one."""
    resolver = _resolver()
    resolver._find = AsyncMock(return_value=None)

    with pytest.raises(ValidationError) as exc_info:
        await resolver.resolve_contact(uuid4(), email="someone@example.com")
    assert exc_info.value.code == "CONTACT_PHONE_REQUIRED"


@pytest.mark.asyncio
async def test_every_lookup_query_is_tenant_scoped() -> None:
    """The single most important invariant: tenant_id is bound into every
    lookup this resolver issues."""
    session = AsyncMock()
    select_res = MagicMock()
    select_res.mappings.return_value.one_or_none.return_value = None
    session.execute.return_value = select_res
    resolver = ContactResolver(session)
    tenant_id = uuid4()

    await resolver._find(tenant_id, "+919999999999", "a@b.com", "ext-1")

    assert session.execute.await_count == 3
    for call in session.execute.await_args_list:
        assert call.args[1]["tenant_id"] == tenant_id


@pytest.mark.asyncio
async def test_create_is_tenant_scoped_and_race_safe() -> None:
    """The INSERT binds tenant_id and names the partial unique index's
    predicate so Postgres can infer it as the ON CONFLICT arbiter."""
    session = AsyncMock()
    insert_res = MagicMock()
    new_id = uuid4()
    insert_res.mappings.return_value.one_or_none.return_value = {"id": new_id}
    session.execute.return_value = insert_res
    resolver = ContactResolver(session)
    tenant_id = uuid4()

    result = await resolver._create(
        tenant_id, "+919999999999", None, "ext-9", {"full_name": "Jane"}
    )

    sql = str(session.execute.await_args.args[0])
    assert "ON CONFLICT (tenant_id, phone) WHERE deleted_at IS NULL DO NOTHING" in sql
    params = session.execute.await_args.args[1]
    assert params["tenant_id"] == tenant_id
    assert params["phone"] == "+919999999999"
    assert params["full_name"] == "Jane"
    # jsonb params are bound as pre-serialized JSON strings (asyncpg's jsonb
    # codec requires this) -- parse before asserting.
    assert json.loads(params["metadata"])["external_id"] == "ext-9"
    assert result["id"] == new_id


@pytest.mark.asyncio
async def test_create_falls_back_to_phone_as_full_name() -> None:
    """customers.full_name is NOT NULL; a first-contact stranger with no name
    is labelled by their number rather than blocked or given a fake name."""
    session = AsyncMock()
    insert_res = MagicMock()
    insert_res.mappings.return_value.one_or_none.return_value = {"id": uuid4()}
    session.execute.return_value = insert_res
    resolver = ContactResolver(session)

    await resolver._create(uuid4(), "+919999999999", None, None, {})

    assert session.execute.await_args.args[1]["full_name"] == "+919999999999"
