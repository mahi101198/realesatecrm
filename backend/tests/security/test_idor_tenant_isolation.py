"""IDOR / cross-tenant resource access foundation tests."""

from uuid import UUID, uuid4

import pytest

from app.core.exceptions import NotFoundError
from app.core.permissions import ensure_tenant_resource_access, resolve_tenant_scope
from app.core.request_context import RequestContext, SecurityScope


def _tenant_user(tenant_id: UUID) -> RequestContext:
    return RequestContext(
        request_id="idor",
        user_id=uuid4(),
        auth_user_id=uuid4(),
        tenant_id=tenant_id,
        role="sales_agent",
        permissions=frozenset({"lead.read"}),
        is_super_admin=False,
        scope=SecurityScope.TENANT,
    )


def test_tenant_a_user_can_access_tenant_a_resource() -> None:
    tenant_a = uuid4()
    ensure_tenant_resource_access(_tenant_user(tenant_a), tenant_a)


def test_tenant_a_user_cannot_access_tenant_b_resource() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    with pytest.raises(NotFoundError) as exc_info:
        ensure_tenant_resource_access(_tenant_user(tenant_a), tenant_b)
    assert exc_info.value.status_code == 404


def test_tenant_b_user_cannot_access_tenant_a_resource() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    with pytest.raises(NotFoundError):
        ensure_tenant_resource_access(_tenant_user(tenant_b), tenant_a)


def test_client_supplied_tenant_id_cannot_override_scope() -> None:
    tenant_a = uuid4()
    tenant_b = uuid4()
    ctx = _tenant_user(tenant_a)
    assert resolve_tenant_scope(ctx, requested_tenant_id=tenant_b) == tenant_a
