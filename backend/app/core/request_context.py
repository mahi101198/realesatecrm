"""Request Context Infrastructure & ContextVar Storage."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

# ContextVar storage for request correlation ID
_request_id_ctx_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# ContextVar storage for RequestContext
_request_context_ctx_var: ContextVar["RequestContext | None"] = ContextVar(
    "request_context", default=None
)


class SecurityScope(StrEnum):
    """Authorization scope derived from the authenticated application user."""

    GLOBAL = "GLOBAL"  # Platform super_admin — no tenant affiliation
    TENANT = "TENANT"  # Normal tenant-scoped user


def set_request_id(request_id: str) -> None:
    """Set correlation request ID for the current context task."""
    _request_id_ctx_var.set(request_id)


def get_request_id() -> str | None:
    """Retrieve correlation request ID for current context task."""
    return _request_id_ctx_var.get()


@dataclass(frozen=True)
class RequestContext:
    """Trusted authenticated security state for the current request.

    Built only from verified JWT + database state. Never constructed from
    client-supplied user_id, tenant_id, role, or permissions.
    """

    request_id: str
    user_id: UUID
    auth_user_id: UUID
    tenant_id: UUID | None
    role: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    is_super_admin: bool = False
    scope: SecurityScope = SecurityScope.TENANT
    metadata: dict[str, Any] = field(default_factory=dict)


def set_request_context(ctx: RequestContext) -> None:
    """Store active RequestContext for current context task."""
    _request_context_ctx_var.set(ctx)


def get_request_context() -> RequestContext | None:
    """Retrieve active RequestContext for current context task."""
    return _request_context_ctx_var.get()


def clear_request_context() -> None:
    """Clear active RequestContext (useful in tests)."""
    _request_context_ctx_var.set(None)
