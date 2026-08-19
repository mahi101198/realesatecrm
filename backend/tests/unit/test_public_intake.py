"""Unit tests for public lead intake: tenant resolution (fail-closed), the
genuine find-or-create-by-phone flow, and Redis-backed rate limiting."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import NotFoundError
from app.public_intake.rate_limit import (
    RateLimiterUnavailableError,
    RateLimitExceededError,
    enforce_public_intake_rate_limit,
)
from app.public_intake.schemas import PublicLeadIntakeRequest
from app.public_intake.service import PublicIntakeService


def _session_stub() -> AsyncMock:
    return AsyncMock()


@asynccontextmanager
async def _noop_atomic(_session):  # type: ignore[no-untyped-def]
    """No-op stand-in for app.db.transaction.atomic() -- same pattern as
    tests/unit/test_sale_ownership_transfer.py. The transaction wrapper itself
    is generic infrastructure; these tests focus on the intake business logic."""
    yield _session


@pytest.mark.asyncio
async def test_resolve_tenant_id_fails_closed_on_unknown_slug() -> None:
    """Verify an unknown tenant_slug raises NotFoundError (404), not a 500 or
    a silent fallback to some default tenant."""
    session = _session_stub()
    service = PublicIntakeService(session)
    service.repository.resolve_tenant_by_slug = AsyncMock(return_value=None)

    with pytest.raises(NotFoundError) as exc_info:
        await service.resolve_tenant_id("unknown-slug")
    assert exc_info.value.code == "INTAKE_LINK_NOT_FOUND"


@pytest.mark.asyncio
async def test_resolve_tenant_id_never_trusts_client_supplied_tenant() -> None:
    """Verify tenant resolution only ever comes from the slug lookup result --
    there is no code path that accepts a client-supplied tenant_id at all."""
    session = _session_stub()
    service = PublicIntakeService(session)
    resolved_id = uuid4()
    service.repository.resolve_tenant_by_slug = AsyncMock(
        return_value={"id": resolved_id, "is_active": True}
    )

    result = await service.resolve_tenant_id("acme-realty")
    assert result == resolved_id
    service.repository.resolve_tenant_by_slug.assert_awaited_once_with("acme-realty")


@pytest.mark.asyncio
async def test_submit_enquiry_finds_existing_customer_by_phone_not_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a returning phone number reuses the existing customer instead of
    raising a duplicate-phone conflict (unlike CustomerService.create_customer)."""
    monkeypatch.setattr("app.public_intake.service.atomic", _noop_atomic)
    session = _session_stub()
    service = PublicIntakeService(session)
    tenant_id = uuid4()
    existing_customer_id = uuid4()

    service.repository.resolve_lead_source_id = AsyncMock(return_value=None)
    service.repository.get_customer_by_phone = AsyncMock(
        return_value={"id": existing_customer_id, "phone": "+919876543210"}
    )
    create_customer_mock = AsyncMock()
    service.repository.create_minimal_customer = create_customer_mock
    service.repository.create_minimal_lead = AsyncMock(
        return_value={"id": uuid4(), "lead_number": "LD-000042"}
    )

    data = PublicLeadIntakeRequest(name="Rajesh Kumar", phone="9876543210")
    result = await service.submit_enquiry(tenant_id, data)

    create_customer_mock.assert_not_called()
    assert result.reference == "LD-000042"


@pytest.mark.asyncio
async def test_submit_enquiry_creates_new_customer_when_phone_unseen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a first-time phone number creates a new minimal customer record."""
    monkeypatch.setattr("app.public_intake.service.atomic", _noop_atomic)
    session = _session_stub()
    service = PublicIntakeService(session)
    tenant_id = uuid4()
    new_customer_id = uuid4()

    service.repository.resolve_lead_source_id = AsyncMock(return_value=None)
    service.repository.get_customer_by_phone = AsyncMock(return_value=None)
    service.repository.create_minimal_customer = AsyncMock(return_value={"id": new_customer_id})
    service.repository.create_minimal_lead = AsyncMock(
        return_value={"id": uuid4(), "lead_number": "LD-000043"}
    )

    data = PublicLeadIntakeRequest(name="New Enquirer", phone="9123456780")
    result = await service.submit_enquiry(tenant_id, data)

    service.repository.create_minimal_customer.assert_awaited_once()
    assert result.reference == "LD-000043"


@pytest.mark.asyncio
async def test_submit_enquiry_survives_concurrent_create_customer_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify losing the create-customer race (ON CONFLICT DO NOTHING returns
    None because a concurrent request's INSERT won first) does NOT surface as
    an error -- the winning row is re-fetched and the flow continues normally,
    still returning a lead reference to the visitor."""
    monkeypatch.setattr("app.public_intake.service.atomic", _noop_atomic)
    session = _session_stub()
    service = PublicIntakeService(session)
    tenant_id = uuid4()
    winning_customer_id = uuid4()

    service.repository.resolve_lead_source_id = AsyncMock(return_value=None)
    # First call (pre-check): nothing found. Second call (post-race re-fetch):
    # the concurrent request's customer row is now visible.
    service.repository.get_customer_by_phone = AsyncMock(
        side_effect=[None, {"id": winning_customer_id, "phone": "+919123456790"}]
    )
    # ON CONFLICT DO NOTHING: our own insert lost the race, so it returns None.
    service.repository.create_minimal_customer = AsyncMock(return_value=None)
    service.repository.create_minimal_lead = AsyncMock(
        return_value={"id": uuid4(), "lead_number": "LD-000050"}
    )

    data = PublicLeadIntakeRequest(name="Racer", phone="9123456790")
    result = await service.submit_enquiry(tenant_id, data)

    assert service.repository.get_customer_by_phone.await_count == 2
    assert result.reference == "LD-000050"
    # The lead must be created against the WINNING customer's id, not a phantom.
    lead_call_kwargs = service.repository.create_minimal_lead.call_args.kwargs
    assert lead_call_kwargs["customer_id"] == winning_customer_id


@pytest.mark.asyncio
async def test_find_or_create_customer_raises_conflict_if_race_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the vanishingly-unlikely case (lost the race AND the re-fetch
    also finds nothing, e.g. the winning row was soft-deleted in between)
    raises a clean, typed ConflictError rather than proceeding with no
    customer or leaking a raw None downstream."""
    monkeypatch.setattr("app.public_intake.service.atomic", _noop_atomic)
    session = _session_stub()
    service = PublicIntakeService(session)
    tenant_id = uuid4()

    service.repository.get_customer_by_phone = AsyncMock(side_effect=[None, None])
    service.repository.create_minimal_customer = AsyncMock(return_value=None)

    data = PublicLeadIntakeRequest(name="Edge Case", phone="9123456791")

    from app.core.exceptions import ConflictError

    with pytest.raises(ConflictError) as exc_info:
        await service._find_or_create_customer(tenant_id, data, None)
    assert exc_info.value.code == "CUSTOMER_CREATE_CONFLICT"


@pytest.mark.asyncio
async def test_submit_enquiry_ignores_malformed_property_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a malformed property_id does not fail the whole enquiry."""
    monkeypatch.setattr("app.public_intake.service.atomic", _noop_atomic)
    session = _session_stub()
    service = PublicIntakeService(session)
    tenant_id = uuid4()

    service.repository.resolve_lead_source_id = AsyncMock(return_value=None)
    service.repository.get_customer_by_phone = AsyncMock(return_value={"id": uuid4()})
    service.repository.create_minimal_lead = AsyncMock(
        return_value={"id": uuid4(), "lead_number": "LD-000044"}
    )
    add_interest_mock = AsyncMock()
    service.repository.add_property_interest = add_interest_mock

    data = PublicLeadIntakeRequest(
        name="Enquirer", phone="9123456781", property_id="not-a-real-uuid"
    )
    result = await service.submit_enquiry(tenant_id, data)

    add_interest_mock.assert_not_called()
    assert result.reference == "LD-000044"


@pytest.mark.asyncio
async def test_rate_limit_allows_requests_under_the_limit() -> None:
    """Verify requests under the configured limit pass through cleanly."""
    fake_redis = AsyncMock()
    fake_redis.incr.return_value = 1

    with patch("app.public_intake.rate_limit.get_redis_client", return_value=fake_redis):
        await enforce_public_intake_rate_limit("acme-realty", "1.2.3.4")

    fake_redis.expire.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_limit_rejects_requests_over_the_limit() -> None:
    """Verify exceeding the per-minute limit raises RateLimitExceededError (429)."""
    fake_redis = AsyncMock()
    fake_redis.incr.return_value = 999

    with (
        patch("app.public_intake.rate_limit.get_redis_client", return_value=fake_redis),
        pytest.raises(RateLimitExceededError) as exc_info,
    ):
        await enforce_public_intake_rate_limit("acme-realty", "1.2.3.4")

    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_fails_closed_when_redis_unavailable() -> None:
    """Verify the rate limiter fails CLOSED (503) when Redis is unreachable --
    this is the only abuse guard on an unauthenticated write endpoint, so
    'limiter down' must not silently mean 'no limit'."""
    with (
        patch("app.public_intake.rate_limit.get_redis_client", return_value=None),
        pytest.raises(RateLimiterUnavailableError) as exc_info,
    ):
        await enforce_public_intake_rate_limit("acme-realty", "1.2.3.4")

    assert exc_info.value.status_code == 503


def test_client_ip_ignores_x_forwarded_for_by_default() -> None:
    """Verify the client-IP helper does NOT trust X-Forwarded-For unless
    settings.TRUST_PROXY_HEADERS is explicitly True -- the fail-safe default
    must not let a caller spoof a new IP per request and defeat the rate
    limiter on a direct (non-proxied) deployment."""
    from app.public_intake.router import _client_ip

    request = MagicMock()
    request.headers = {"x-forwarded-for": "203.0.113.5, 10.0.0.1"}
    request.client.host = "198.51.100.7"

    with patch("app.public_intake.router.settings") as mock_settings:
        mock_settings.TRUST_PROXY_HEADERS = False
        assert _client_ip(request) == "198.51.100.7"


def test_client_ip_prefers_x_forwarded_for_first_hop_when_proxy_trusted() -> None:
    """Verify the client-IP helper only honours X-Forwarded-For's first hop
    when settings.TRUST_PROXY_HEADERS has been explicitly enabled."""
    from app.public_intake.router import _client_ip

    request = MagicMock()
    request.headers = {"x-forwarded-for": "203.0.113.5, 10.0.0.1"}

    with patch("app.public_intake.router.settings") as mock_settings:
        mock_settings.TRUST_PROXY_HEADERS = True
        assert _client_ip(request) == "203.0.113.5"


def test_client_ip_falls_back_to_direct_connection() -> None:
    """Verify the client-IP helper falls back to the direct connection address
    when no X-Forwarded-For header is present, regardless of trust setting."""
    from app.public_intake.router import _client_ip

    request = MagicMock()
    request.headers = {}
    request.client.host = "198.51.100.7"

    with patch("app.public_intake.router.settings") as mock_settings:
        mock_settings.TRUST_PROXY_HEADERS = True
        assert _client_ip(request) == "198.51.100.7"
