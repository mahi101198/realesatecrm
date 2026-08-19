"""Authentication service — JWT verification to trusted RequestContext."""

import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.core.request_context import RequestContext, get_request_id
from app.users.repository import UserRepository
from app.users.service import UserService, ValidatedSecurityState

logger = logging.getLogger(__name__)


class AuthService:
    """Orchestrates application-user security resolution after JWT verification."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._user_service = UserService(UserRepository(session))

    async def resolve_security_state_for_auth_user(
        self,
        auth_user_id: UUID,
    ) -> ValidatedSecurityState:
        """Load validated DB security state for a verified Supabase auth subject."""
        try:
            return await self._user_service.resolve_security_state(auth_user_id)
        except UnauthorizedError as e:
            logger.warning(
                "Authentication denied",
                extra={
                    "failure_category": e.code,
                    "request_id": get_request_id(),
                },
            )
            raise

    def build_request_context(self, state: ValidatedSecurityState) -> RequestContext:
        """Build trusted RequestContext from validated DB security state."""
        request_id = get_request_id() or "unknown"
        return RequestContext(
            request_id=request_id,
            user_id=state.user_id,
            auth_user_id=state.auth_user_id,
            tenant_id=state.tenant_id,
            role=state.role,
            permissions=state.permissions,
            is_super_admin=state.is_super_admin,
            scope=state.scope,
        )
