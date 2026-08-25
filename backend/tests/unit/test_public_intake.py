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
async def test_submit_enquiry_delegates_find_or_create_to_contact_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the intake flow no longer carries its own find-or-create: it
    delegates to the shared ContactResolver, passing the server-resolved
    tenant_id and the submitted phone, with name/email/lead_source only as
    CREATE-time defaults (email must NOT become a lookup key here)."""
    monkeypatch.setattr("app.public_intake.service.atomic", _noop_atomic)
    session = _session_stub()
    service = PublicIntakeService(session)
    tenant_id = uuid4()
    existing_customer_id = uuid4()
    lead_source_id = uuid4()

    service.repository.resolve_lead_source_id = AsyncMock(return_value=lead_source_id)
    service.contact_resolver.resolve_contact = AsyncMock(
        return_value={"id": existing_customer_id, "phone": "+919876543210"}
    )
    service.repository.create_minimal_lead = AsyncMock(
        return_value={"id": uuid4(), "lead_number": "LD-000042"}
    )

    data = PublicLeadIntakeRequest(name="Rajesh Kumar", phone="9876543210")
    result = await service.submit_enquiry(tenant_id, data)

    assert result.reference == "LD-000042"
    call = service.contact_resolver.resolve_contact.call_args
    assert call.args[0] == tenant_id
    assert call.kwargs["phone"] == "+919876543210"
    assert "email" not in call.kwargs
    assert call.kwargs["defaults"]["full_name"] == "Rajesh Kumar"
    assert call.kwargs["defaults"]["lead_source_id"] == lead_source_id


@pytest.mark.asyncio
async def test_submit_enquiry_creates_lead_against_resolved_customer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the lead is always created against the id the resolver returned
    -- including when the resolver had to re-fetch a concurrent request's
    winning row -- never against a phantom id."""
    monkeypatch.setattr("app.public_intake.service.atomic", _noop_atomic)
    session = _session_stub()
    service = PublicIntakeService(session)
    tenant_id = uuid4()
    winning_customer_id = uuid4()

    service.repository.resolve_lead_source_id = AsyncMock(return_value=None)
    service.contact_resolver.resolve_contact = AsyncMock(
        return_value={"id": winning_customer_id, "phone": "+919123456790"}
    )
    service.repository.create_minimal_lead = AsyncMock(
        return_value={"id": uuid4(), "lead_number": "LD-000050"}
    )

    data = PublicLeadIntakeRequest(name="Racer", phone="9123456790")
    result = await service.submit_enquiry(tenant_id, data)

    assert result.reference == "LD-000050"
    lead_call_kwargs = service.repository.create_minimal_lead.call_args.kwargs
    assert lead_call_kwargs["customer_id"] == winning_customer_id
    assert lead_call_kwargs["tenant_id"] == tenant_id


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
    service.contact_resolver.resolve_contact = AsyncMock(return_value={"id": uuid4()})
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
