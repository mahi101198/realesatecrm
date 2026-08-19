"""Unit tests for user security validation and role precedence."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import UnauthorizedError
from app.core.request_context import SecurityScope
from app.users.repository import SecurityIdentity
from app.users.service import UserService, select_primary_role


def _identity(
    *,
    status: str = "active",
    tenant_id: UUID | None = None,
    role_names: tuple[str, ...] = ("sales_agent",),
    permissions: frozenset[str] = frozenset({"lead.read"}),
    is_super_admin: bool = False,
    deleted_at: datetime | None = None,
    tenant_is_active: bool | None = True,
    tenant_deleted_at: datetime | None = None,
) -> SecurityIdentity:
    auth_id = uuid4()
    return SecurityIdentity(
        user_id=uuid4(),
        auth_user_id=auth_id,
        tenant_id=tenant_id if tenant_id is not None else (None if is_super_admin else uuid4()),
        email="user@example.com",
        name="Test User",
        status=status,
        deleted_at=deleted_at,
        tenant_is_active=tenant_is_active if not is_super_admin else None,
        tenant_deleted_at=tenant_deleted_at,
        role_names=role_names,
        permission_codes=permissions,
        is_super_admin=is_super_admin,
    )


def test_select_primary_role_uses_highest_precedence() -> None:
    assert select_primary_role(("viewer", "sales_manager", "admin")) == "admin"
    assert select_primary_role(("sales_agent", "viewer")) == "sales_agent"
    assert select_primary_role(("super_admin",)) == "super_admin"


def test_select_primary_role_empty_raises() -> None:
    with pytest.raises(UnauthorizedError) as exc_info:
        select_primary_role(())
    assert exc_info.value.code == "NO_ACTIVE_ROLE"


def test_validate_active_tenant_user() -> None:
    service = UserService(repository=None)  # type: ignore[arg-type]
    identity = _identity(
        role_names=("sales_agent", "viewer"),
        permissions=frozenset({"lead.read", "property.read"}),
    )
    state = service.validate_identity(identity)
    assert state.role == "sales_agent"
    assert state.permissions == frozenset({"lead.read", "property.read"})
    assert state.scope == SecurityScope.TENANT
    assert state.is_super_admin is False
    assert state.tenant_id is not None


def test_validate_inactive_user_denied() -> None:
    service = UserService(repository=None)  # type: ignore[arg-type]
    identity = _identity(status="inactive")
    with pytest.raises(UnauthorizedError) as exc_info:
        service.validate_identity(identity)
    assert exc_info.value.code == "USER_INACTIVE"
    assert exc_info.value.status_code == 401


def test_validate_suspended_user_denied() -> None:
    service = UserService(repository=None)  # type: ignore[arg-type]
    with pytest.raises(UnauthorizedError) as exc_info:
        service.validate_identity(_identity(status="suspended"))
    assert exc_info.value.code == "USER_INACTIVE"


def test_validate_deleted_user_denied() -> None:
    service = UserService(repository=None)  # type: ignore[arg-type]
    with pytest.raises(UnauthorizedError) as exc_info:
        service.validate_identity(_identity(deleted_at=datetime.now(UTC)))
    assert exc_info.value.code == "USER_INACTIVE"


def test_validate_inactive_tenant_denied() -> None:
    service = UserService(repository=None)  # type: ignore[arg-type]
    with pytest.raises(UnauthorizedError) as exc_info:
        service.validate_identity(_identity(tenant_is_active=False))
    assert exc_info.value.code == "TENANT_INACTIVE"


def test_validate_super_admin_global_scope() -> None:
    service = UserService(repository=None)  # type: ignore[arg-type]
    identity = _identity(
        is_super_admin=True,
        tenant_id=None,
        role_names=("super_admin",),
        permissions=frozenset({"platform.tenant.read", "lead.read"}),
        tenant_is_active=None,
    )
    state = service.validate_identity(identity)
    assert state.scope == SecurityScope.GLOBAL
    assert state.tenant_id is None
    assert state.is_super_admin is True
    assert state.role == "super_admin"


def test_validate_super_admin_with_tenant_id_rejected() -> None:
    service = UserService(repository=None)  # type: ignore[arg-type]
    identity = _identity(
        is_super_admin=True,
        tenant_id=uuid4(),
        role_names=("super_admin",),
        tenant_is_active=True,
    )
    with pytest.raises(UnauthorizedError) as exc_info:
        service.validate_identity(identity)
    assert exc_info.value.code == "INVALID_USER_STATE"


def test_validate_no_roles_denied() -> None:
    service = UserService(repository=None)  # type: ignore[arg-type]
    with pytest.raises(UnauthorizedError) as exc_info:
        service.validate_identity(_identity(role_names=()))
    assert exc_info.value.code == "NO_ACTIVE_ROLE"
