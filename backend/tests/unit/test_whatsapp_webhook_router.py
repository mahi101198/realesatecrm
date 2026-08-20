"""Unit tests for the tenant-scoped WhatsApp webhook endpoints, exercised
through the FastAPI test client (tests/conftest.py's async_client fixture).

These tests never initialize the real DB engine (tests/conftest.py's
async_client fixture builds the ASGI app without running FastAPI's
lifespan, so app.db.session.async_session_factory stays None). Since every
test here patches the WhatsAppTenantConfigRepository/WhatsAppWebhookService
classes at the module level anyway -- the actual AsyncSession object handed
to them is never used for real queries -- the get_db_session dependency is
overridden with a stub that yields a bare AsyncMock, purely so FastAPI's
dependency injection has something to hand the (mocked-out) route logic."""

import hashlib
import hmac
from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.db.session import get_db_session
from app.main import app


async def _fake_db_session() -> AsyncGenerator[AsyncMock, None]:
    yield AsyncMock()


@pytest.fixture(autouse=True)
def _override_db_session() -> Generator[None, None, None]:
    app.dependency_overrides[get_db_session] = _fake_db_session
    yield
    app.dependency_overrides.pop(get_db_session, None)


def _sign(body: bytes, secret: str) -> str:
    return f"sha256={hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}"


@pytest.mark.asyncio
async def test_get_handshake_echoes_challenge_on_valid_token(async_client) -> None:
    tenant_id = uuid4()
    with patch(
        "app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={"verify_token": "correct-token", "is_active": True}
        )
        response = await async_client.get(
            f"/api/v1/webhooks/whatsapp/{tenant_id}",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "correct-token",
                "hub.challenge": "challenge123",
            },
        )
    assert response.status_code == 200
    assert response.text == "challenge123"


@pytest.mark.asyncio
async def test_get_handshake_rejects_wrong_token(async_client) -> None:
    tenant_id = uuid4()
    with patch(
        "app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={"verify_token": "correct-token", "is_active": True}
        )
        response = await async_client.get(
            f"/api/v1/webhooks/whatsapp/{tenant_id}",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge123",
            },
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_handshake_404s_for_unconfigured_tenant(async_client) -> None:
    with patch(
        "app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(return_value=None)
        response = await async_client.get(
            f"/api/v1/webhooks/whatsapp/{uuid4()}",
            params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "y"},
        )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_post_rejects_invalid_signature(async_client) -> None:
    tenant_id = uuid4()
    body = b'{"entry": []}'
    with patch(
        "app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository"
    ) as mock_repo_cls:
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={"app_secret": "correct-secret", "is_active": True}
        )
        response = await async_client.post(
            f"/api/v1/webhooks/whatsapp/{tenant_id}",
            content=body,
            headers={"x-hub-signature-256": _sign(body, "wrong-secret")},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_post_accepts_valid_signature_and_processes_events(async_client) -> None:
    tenant_id = uuid4()
    body = b'{"entry": []}'
    with (
        patch("app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository") as mock_repo_cls,
        patch("app.webhooks.whatsapp.router.WhatsAppWebhookService"),
    ):
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={"app_secret": "correct-secret", "is_active": True}
        )
        response = await async_client.post(
            f"/api/v1/webhooks/whatsapp/{tenant_id}",
            content=body,
            headers={"x-hub-signature-256": _sign(body, "correct-secret")},
        )
    assert response.status_code == 200
    assert response.text == "EVENT_RECEIVED"


@pytest.mark.asyncio
async def test_post_returns_200_even_when_event_processing_raises(async_client) -> None:
    """A validly-signed POST always answers 200, even when processing
    throws -- sustained 5xx responses cause Meta to disable the
    subscription, which is worse than losing one event to a logged error."""
    tenant_id = uuid4()
    body = (
        b'{"entry": [{"changes": [{"field": "messages", '
        b'"value": {"messages": [{"from": "1", "id": "x", "type": "text"}]}}]}]}'
    )
    with (
        patch("app.webhooks.whatsapp.router.WhatsAppTenantConfigRepository") as mock_repo_cls,
        patch("app.webhooks.whatsapp.router.WhatsAppWebhookService") as mock_service_cls,
    ):
        mock_repo_cls.return_value.get_decrypted = AsyncMock(
            return_value={"app_secret": "correct-secret", "is_active": True}
        )
        mock_service_cls.return_value.handle_inbound_message = AsyncMock(
            side_effect=RuntimeError("boom")
        )
        response = await async_client.post(
            f"/api/v1/webhooks/whatsapp/{tenant_id}",
            content=body,
            headers={"x-hub-signature-256": _sign(body, "correct-secret")},
        )
    assert response.status_code == 200
