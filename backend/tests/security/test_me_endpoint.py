"""Security tests for GET /api/v1/me and authorization boundaries."""

from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.auth.dependencies import get_validated_security_state
from app.core.exceptions import UnauthorizedError
from app.core.request_context import SecurityScope
from app.main import app
from app.users.service import ValidatedSecurityState

SECRET = "test-jwt-secret-key-super-secret-minimum-32-chars"


def _token(sub: str = "11111111-1111-1111-1111-111111111111") -> str:
    return jwt.encode(
        {"sub": sub, "role": "authenticated", "aud": "authenticated"},
        SECRET,
        algorithm="HS256",
    )


def _state(
    *,
    role: str = "sales_agent",
    permissions: frozenset[str] = frozenset({"lead.read"}),
    is_super_admin: bool = False,
    tenant_id: UUID | None = None,
) -> ValidatedSecurityState:
    tid = tenant_id if tenant_id is not None else (None if is_super_admin else uuid4())
    return ValidatedSecurityState(
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=tid,
        email="agent@abcrealty.dev",
        name="Test Agent",
        role=role,
        permissions=permissions,
        is_super_admin=is_super_admin,
        scope=SecurityScope.GLOBAL if is_super_admin else SecurityScope.TENANT,
    )


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_me_missing_token_returns_401(client: AsyncClient) -> None:
    response = await client.get("/api/v1/me")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "MISSING_TOKEN"


@pytest.mark.asyncio
async def test_me_invalid_token_returns_401(client: AsyncClient) -> None:
    response = await client.get(
        "/api/v1/me",
        headers={"Authorization": "Bearer not-a-valid-jwt"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_returns_db_identity_not_jwt_claims(client: AsyncClient) -> None:
    """/me must expose DB-backed identity, ignoring JWT role/tenant spoofing."""
    state = _state(
        role="sales_agent",
        permissions=frozenset({"lead.read", "customer.read"}),
    )

    async def _override() -> ValidatedSecurityState:
        return state

    app.dependency_overrides[get_validated_security_state] = _override
    try:
        # Token contains spoofed claims that must not appear in the response.
        spoofed = jwt.encode(
            {
                "sub": str(state.auth_user_id),
                "role": "authenticated",
                "aud": "authenticated",
                "app_role": "super_admin",
                "tenant_id": str(uuid4()),
                "is_super_admin": True,
            },
            SECRET,
            algorithm="HS256",
        )
        response = await client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {spoofed}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(state.user_id)
        assert data["email"] == "agent@abcrealty.dev"
        assert data["role"] == "sales_agent"
        assert data["is_super_admin"] is False
        assert data["tenant_id"] == str(state.tenant_id)
        assert set(data["permissions"]) == {"customer.read", "lead.read"}
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_me_inactive_user_returns_401(client: AsyncClient) -> None:
    async def _override() -> ValidatedSecurityState:
        raise UnauthorizedError(
            message="Authentication is required to access this resource.",
            code="USER_INACTIVE",
        )

    app.dependency_overrides[get_validated_security_state] = _override
    try:
        response = await client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "USER_INACTIVE"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_me_ignores_query_user_and_tenant_params(client: AsyncClient) -> None:
    state = _state(role="viewer", permissions=frozenset({"lead.read"}))

    async def _override() -> ValidatedSecurityState:
        return state

    app.dependency_overrides[get_validated_security_state] = _override
    try:
        other_user = uuid4()
        other_tenant = uuid4()
        response = await client.get(
            f"/api/v1/me?user_id={other_user}&tenant_id={other_tenant}&role=super_admin",
            headers={"Authorization": f"Bearer {_token()}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(state.user_id)
        assert data["tenant_id"] == str(state.tenant_id)
        assert data["role"] == "viewer"
        assert data["is_super_admin"] is False
    finally:
        app.dependency_overrides.clear()
