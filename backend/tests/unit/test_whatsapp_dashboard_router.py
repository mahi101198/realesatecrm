"""Unit tests for the call-agent trigger HTTP endpoint.

Exercised through the FastAPI test client (tests/conftest.py's
async_client fixture), which never initializes the real DB engine.
Mirrors tests/unit/test_whatsapp_webhook_router.py's pattern: the
get_db_session dependency is overridden with a stub that yields a bare
AsyncMock, purely so FastAPI's dependency injection has something to
hand the (mocked-out) route logic."""

from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, patch

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


@pytest.mark.asyncio
async def test_rejects_missing_bearer(async_client) -> None:
    response = await async_client.post(
        "/api/v1/webhooks/whatsapp-dashboard/call-agent",
        json={"phone": "+919999999999", "reason": "requested"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_accepts_valid_bearer_and_delegates_to_service(async_client) -> None:
    with (
        patch("app.webhooks.whatsapp_dashboard.router.verify_call_agent_bearer"),
        patch("app.webhooks.whatsapp_dashboard.router.CallAgentTriggerService") as mock_service_cls,
    ):
        mock_service_cls.return_value.trigger = AsyncMock(
            return_value={"success": True, "data": {}}
        )
        response = await async_client.post(
            "/api/v1/webhooks/whatsapp-dashboard/call-agent",
            json={"phone": "+919999999999", "reason": "requested"},
            headers={"Authorization": "Bearer whatever"},
        )
    assert response.status_code == 200
    assert response.json()["success"] is True
