"""Unit tests for permission checks and tenant/resource authorization."""

from collections.abc import Generator
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.permissions import (
    Permission,
    check_permission,
    check_super_admin,
    check_tenant_admin,
    ensure_tenant_resource_access,
    has_permission,
    resolve_tenant_scope,
)
from app.core.request_context import RequestContext, SecurityScope, clear_request_context


def _context(
    *,
    tenant_id: UUID | None,
    permissions: set[str],
    role: str = "sales_agent",
    is_super_admin: bool = False,
    scope: SecurityScope | None = None,
) -> RequestContext:
    resolved_scope = scope or (SecurityScope.GLOBAL if is_super_admin else SecurityScope.TENANT)
    return RequestContext(
        request_id="test-request",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=tenant_id,
        role=role,
        permissions=frozenset(permissions),
        is_super_admin=is_super_admin,
        scope=resolved_scope,
    )


@pytest.fixture(autouse=True)
def _clear_context() -> Generator[None, None, None]:
    clear_request_context()
    yield
    clear_request_context()


def test_has_permission_allow() -> None:
    ctx = _context(tenant_id=uuid4(), permissions={"lead.read"})
    assert has_permission(ctx, Permission.LEAD_READ) is True


def test_has_permission_deny() -> None:
    ctx = _context(tenant_id=uuid4(), permissions={"lead.read"})
    assert has_permission(ctx, Permission.LEAD_UPDATE) is False


def test_check_permission_raises_forbidden() -> None:
    ctx = _context(tenant_id=uuid4(), permissions={"lead.read"})
    with pytest.raises(ForbiddenError) as exc_info:
        check_permission(ctx, "lead.update")
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "INSUFFICIENT_PERMISSIONS"


def test_super_admin_still_needs_permission() -> None:
    """Super admin does not bypass missing permissions."""
    ctx = _context(
        tenant_id=None,
        permissions={"platform.tenant.read"},
        role="super_admin",
        is_super_admin=True,
        scope=SecurityScope.GLOBAL,
    )
    check_permission(ctx, "platform.tenant.read")
    with pytest.raises(ForbiddenError):
        check_permission(ctx, "lead.delete")


def test_check_super_admin() -> None:
    admin = _context(
        tenant_id=None,
        permissions=set(),
        role="super_admin",
        is_super_admin=True,
        scope=SecurityScope.GLOBAL,
    )
    check_super_admin(admin)

    tenant_user = _context(tenant_id=uuid4(), permissions=set(), role="admin")
    with pytest.raises(ForbiddenError):
        check_super_admin(tenant_user)


def test_check_tenant_admin_uses_db_role_name_admin() -> None:
    admin = _context(tenant_id=uuid4(), permissions={"user.update"}, role="admin")
    check_tenant_admin(admin)

    manager = _context(tenant_id=uuid4(), permissions=set(), role="manager")
    with pytest.raises(ForbiddenError):
        check_tenant_admin(manager)


def test_resolve_tenant_scope_ignores_client_tenant_for_tenant_user() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    ctx = _context(tenant_id=tenant_a, permissions={"lead.read"})
    assert resolve_tenant_scope(ctx, requested_tenant_id=tenant_b) == tenant_a


def test_resolve_tenant_scope_super_admin_may_select_tenant() -> None:
    target = uuid4()
    ctx = _context(
        tenant_id=None,
        permissions={"platform.tenant.read"},
        role="super_admin",
        is_super_admin=True,
        scope=SecurityScope.GLOBAL,
    )
    assert resolve_tenant_scope(ctx, requested_tenant_id=target) == target
    # None means global (no single-tenant filter), NOT WHERE tenant_id IS NULL.
    assert resolve_tenant_scope(ctx, requested_tenant_id=None) is None


def test_tenant_user_cannot_access_other_tenant_resource() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    ctx = _context(tenant_id=tenant_a, permissions={"lead.read"})
    ensure_tenant_resource_access(ctx, tenant_a)
    with pytest.raises(NotFoundError) as exc_info:
        ensure_tenant_resource_access(ctx, tenant_b)
    assert exc_info.value.status_code == 404


def test_super_admin_can_access_any_tenant_resource() -> None:
    ctx = _context(
        tenant_id=None,
        permissions={"lead.read"},
        role="super_admin",
        is_super_admin=True,
        scope=SecurityScope.GLOBAL,
    )
    ensure_tenant_resource_access(ctx, uuid4())
    ensure_tenant_resource_access(ctx, uuid4())


def test_super_admin_global_is_not_null_tenant_filter() -> None:
    """GLOBAL scope with tenant_id=None must not imply resource_tenant_id IS NULL."""
    ctx = _context(
        tenant_id=None,
        permissions=set(),
        role="super_admin",
        is_super_admin=True,
        scope=SecurityScope.GLOBAL,
    )
    # Accessing a real tenant resource is allowed; null resource tenant is not "owned".
    ensure_tenant_resource_access(ctx, uuid4())
