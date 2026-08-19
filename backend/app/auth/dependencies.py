"""FastAPI authentication and authorization dependencies.

Request flow for protected endpoints:

    Authorization header
        → verify JWT (no DB)
        → load application user + roles + permissions from DB
        → RequestContext (ContextVar)
        → require_permission / role checks
"""

from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import AuthService
from app.core.exceptions import UnauthorizedError
from app.core.permissions import (
    Permission,
    check_permission,
    check_super_admin,
    check_tenant_admin,
    permission_code,
)
from app.core.request_context import (
    RequestContext,
    get_request_context,
    set_request_context,
)
from app.core.security import extract_bearer_token, verify_supabase_token
from app.db.session import get_db_session
from app.users.service import ValidatedSecurityState


async def get_auth_subject(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> UUID:
    """Verify bearer JWT and return Supabase auth user id (sub).

    Runs before any database dependency so invalid tokens fail with 401
    without requiring a DB connection.
    """
    token = extract_bearer_token(authorization)
    payload = verify_supabase_token(token)
    try:
        return UUID(str(payload["sub"]))
    except (ValueError, TypeError) as e:
        raise UnauthorizedError(
            message="Invalid authentication token.",
            code="INVALID_TOKEN_CLAIMS",
        ) from e


async def get_auth_service(
    session: AsyncSession = Depends(get_db_session),
) -> AuthService:
    """Provide AuthService bound to the current DB session."""
    return AuthService(session)


async def get_validated_security_state(
    auth_user_id: UUID = Depends(get_auth_subject),
    auth_service: AuthService = Depends(get_auth_service),
) -> ValidatedSecurityState:
    """Load and validate application user security state for the JWT subject."""
    return await auth_service.resolve_security_state_for_auth_user(auth_user_id)


async def get_current_user_id(
    state: ValidatedSecurityState = Depends(get_validated_security_state),
) -> UUID:
    """Return the authenticated application user ID."""
    return state.user_id


async def get_current_user(
    state: ValidatedSecurityState = Depends(get_validated_security_state),
) -> ValidatedSecurityState:
    """Return validated security state for the authenticated application user."""
    return state


async def get_request_context_dep(
    state: ValidatedSecurityState = Depends(get_validated_security_state),
    auth_service: AuthService = Depends(get_auth_service),
) -> RequestContext:
    """Build and cache trusted RequestContext once per request."""
    existing = get_request_context()
    if existing is not None and existing.user_id == state.user_id:
        return existing

    context = auth_service.build_request_context(state)
    set_request_context(context)
    return context


def require_permission(
    permission: Permission | str,
) -> Callable[..., Coroutine[Any, Any, RequestContext]]:
    """FastAPI dependency factory: authenticate, then require a DB permission."""

    required = permission_code(permission)

    async def _dependency(
        context: RequestContext = Depends(get_request_context_dep),
    ) -> RequestContext:
        check_permission(context, required)
        return context

    return _dependency


async def require_super_admin(
    context: RequestContext = Depends(get_request_context_dep),
) -> RequestContext:
    """Require platform super_admin from database-backed context."""
    check_super_admin(context)
    return context


async def require_tenant_admin(
    context: RequestContext = Depends(get_request_context_dep),
) -> RequestContext:
    """Require tenant admin role (DB name: admin)."""
    check_tenant_admin(context)
    return context
